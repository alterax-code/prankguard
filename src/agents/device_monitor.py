# -*- coding: utf-8 -*-
"""
Device Monitor — PrankGuard v3.0

Surveille les connexions materielles via deux mecanismes complementaires :
  1. WM_DEVICECHANGE (instantane, < 100ms) — messages systeme Windows
  2. WMI Polling (complet, toutes les 0.5s) — inventaire des peripheriques

Categories surveillees : USB/HID, moniteurs, reseau, Bluetooth, audio, imprimantes.

Cooldown anti-faux positifs de 5 a 8 secondes apres modification de la
configuration de monitoring.

Thread : permanent (tourne tant que l'application est active).
Dependances : pywin32, wmi (optionnel pour le polling complet)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("prankguard.device_monitor")


# ---------------------------------------------------------------------------
# Constantes (section 4.8 / table 5 du plan v3)
# ---------------------------------------------------------------------------

class DeviceCategory(str, Enum):
    """Categories de peripheriques surveilles."""
    USB_HID = "USB_HID"        # Cles USB, souris, claviers, dongles
    MONITOR = "MONITOR"        # HDMI, DisplayPort, VGA
    NETWORK = "NETWORK"        # Ethernet, WiFi
    BLUETOOTH = "BLUETOOTH"    # Appairage BT
    AUDIO = "AUDIO"            # Casque, micro, haut-parleurs
    PRINTER = "PRINTER"        # Imprimante reseau ou USB


# Cooldown anti-faux positifs (en secondes)
_COOLDOWN_AFTER_CONFIG_CHANGE_S = 6.0  # 5-8 secondes, on prend 6

# Intervalle de polling WMI (en secondes)
_WMI_POLL_INTERVAL_S = 0.5

# Repertoire de configuration
_DEFAULT_CONFIG_DIR = Path(
    os.environ.get("APPDATA", Path.home() / ".config")
) / "PrankGuard"

_WHITELIST_FILENAME = "device_whitelist.json"

# Classes WMI par categorie
_WMI_CLASSES: dict[DeviceCategory, str] = {
    DeviceCategory.USB_HID: "Win32_USBHub",
    DeviceCategory.MONITOR: "Win32_DesktopMonitor",
    DeviceCategory.NETWORK: "Win32_NetworkAdapter",
    DeviceCategory.BLUETOOTH: "Win32_PnPEntity",
    DeviceCategory.AUDIO: "Win32_SoundDevice",
    DeviceCategory.PRINTER: "Win32_Printer",
}

# Filtre pour les peripheriques Bluetooth dans Win32_PnPEntity
_BT_PNP_PREFIX = "BTHENUM"


# ---------------------------------------------------------------------------
# Structures de donnees
# ---------------------------------------------------------------------------

@dataclass
class DeviceEvent:
    """Evenement de changement de peripherique."""
    category: DeviceCategory
    device_id: str
    device_name: str
    is_new: bool = True        # True = branche, False = debranche
    whitelisted: bool = False  # True si le peripherique est dans la whitelist
    timestamp: float = 0.0


@dataclass
class DeviceSnapshot:
    """Instantane de tous les peripheriques connectes."""
    devices: dict[DeviceCategory, set[str]] = field(default_factory=dict)
    timestamp: float = 0.0


# Type des callbacks
DeviceCallback = Callable[[DeviceEvent], None]


# ---------------------------------------------------------------------------
# Device Monitor
# ---------------------------------------------------------------------------

class DeviceMonitor:
    """
    Moniteur de peripheriques materiels.

    Combine WM_DEVICECHANGE (instantane) et WMI polling (complet)
    pour detecter tout changement de peripherique. Gere une whitelist
    et un cooldown anti-faux positifs.

    Utilisation :
        monitor = DeviceMonitor()
        monitor.on_device_change(callback)
        monitor.start()
        ...
        monitor.stop()
    """

    def __init__(
        self,
        poll_interval_s: float = _WMI_POLL_INTERVAL_S,
        cooldown_s: float = _COOLDOWN_AFTER_CONFIG_CHANGE_S,
        config_dir: Optional[Path] = None,
        enabled_categories: Optional[set[DeviceCategory]] = None,
    ) -> None:
        self._poll_interval = poll_interval_s
        self._cooldown_duration = cooldown_s
        self._config_dir = config_dir or _DEFAULT_CONFIG_DIR

        # Categories activees (toutes par defaut)
        self._enabled_categories = enabled_categories or set(DeviceCategory)

        # Etat interne
        self._running = False
        self._wmi_thread: Optional[threading.Thread] = None
        self._win32_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Cooldown
        self._cooldown_until: float = 0.0

        # Snapshot des peripheriques (pour detecter les changements)
        self._last_snapshot: DeviceSnapshot = DeviceSnapshot()

        # Whitelist
        self._whitelist: set[str] = set()

        # Callbacks
        self._device_callbacks: list[DeviceCallback] = []

    # ----- Proprietes publiques -----

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def enabled_categories(self) -> set[DeviceCategory]:
        return self._enabled_categories.copy()

    # ----- Callbacks -----

    def on_device_change(self, callback: DeviceCallback) -> None:
        """Enregistre un callback appele a chaque changement de peripherique."""
        self._device_callbacks.append(callback)

    # ----- Whitelist -----

    def load_whitelist(self) -> int:
        """Charge la whitelist depuis le fichier JSON. Retourne le nombre d'entrees."""
        path = self._config_dir / _WHITELIST_FILENAME
        if not path.exists():
            return 0

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._whitelist = set(data.get("devices", []))
            logger.info("Whitelist chargee : %d peripheriques", len(self._whitelist))
            return len(self._whitelist)
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Whitelist corrompue : %s", exc)
            return 0

    def save_whitelist(self) -> Path:
        """Sauvegarde la whitelist dans un fichier JSON."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        path = self._config_dir / _WHITELIST_FILENAME

        with open(path, "w", encoding="utf-8") as f:
            json.dump({"devices": sorted(self._whitelist)}, f, indent=2)

        logger.info("Whitelist sauvegardee : %d peripheriques", len(self._whitelist))
        return path

    def whitelist_current_devices(self) -> int:
        """
        Ajoute tous les peripheriques actuellement connectes a la whitelist.
        Utile lors du premier lancement.
        """
        snapshot = self._take_wmi_snapshot()
        count = 0
        for category, device_ids in snapshot.devices.items():
            for dev_id in device_ids:
                if dev_id not in self._whitelist:
                    self._whitelist.add(dev_id)
                    count += 1

        if count > 0:
            self.save_whitelist()
            logger.info("%d peripheriques ajoutes a la whitelist", count)

        return count

    def add_to_whitelist(self, device_id: str) -> None:
        """Ajoute un peripherique a la whitelist."""
        self._whitelist.add(device_id)

    def remove_from_whitelist(self, device_id: str) -> None:
        """Retire un peripherique de la whitelist."""
        self._whitelist.discard(device_id)

    def is_whitelisted(self, device_id: str) -> bool:
        """Verifie si un peripherique est dans la whitelist."""
        return device_id in self._whitelist

    # ----- Configuration des categories -----

    def set_enabled_categories(self, categories: set[DeviceCategory]) -> None:
        """
        Modifie les categories surveillees.
        Active le cooldown anti-faux positifs.
        """
        with self._lock:
            self._enabled_categories = categories
            self._cooldown_until = time.monotonic() + self._cooldown_duration

        logger.info(
            "Categories modifiees : %s (cooldown %.0f s)",
            [c.value for c in categories],
            self._cooldown_duration,
        )

    def activate_cooldown(self) -> None:
        """Active manuellement le cooldown (ex: apres modification des parametres)."""
        with self._lock:
            self._cooldown_until = time.monotonic() + self._cooldown_duration
        logger.info("Cooldown active (%.0f s)", self._cooldown_duration)

    # ----- Controle du cycle de vie -----

    def start(self) -> None:
        """Demarre les deux mecanismes de surveillance."""
        if self._running:
            logger.warning("Device monitor deja en cours")
            return

        self._running = True

        # Prendre un snapshot initial
        self._last_snapshot = self._take_wmi_snapshot()

        # Thread WMI Polling
        self._wmi_thread = threading.Thread(
            target=self._wmi_poll_loop,
            name="DeviceMonitor-WMI",
            daemon=True,
        )
        self._wmi_thread.start()

        # Thread WM_DEVICECHANGE
        self._win32_thread = threading.Thread(
            target=self._win32_message_loop,
            name="DeviceMonitor-Win32",
            daemon=True,
        )
        self._win32_thread.start()

        logger.info("Device monitor demarre (WMI + Win32)")

    def stop(self) -> None:
        """Arrete les deux threads de surveillance."""
        self._running = False

        if self._wmi_thread is not None:
            self._wmi_thread.join(timeout=5.0)
            self._wmi_thread = None

        # Le thread Win32 est daemon, il s'arrete avec le processus
        self._win32_thread = None

        logger.info("Device monitor arrete")

    # ----- WMI Polling -----

    def _wmi_poll_loop(self) -> None:
        """Boucle de polling WMI : compare les snapshots pour detecter les changements."""
        while self._running:
            time.sleep(self._poll_interval)

            if not self._running:
                break

            # Verifier le cooldown
            with self._lock:
                if time.monotonic() < self._cooldown_until:
                    continue

            try:
                new_snapshot = self._take_wmi_snapshot()
                self._compare_snapshots(self._last_snapshot, new_snapshot)
                self._last_snapshot = new_snapshot
            except Exception as exc:
                logger.error("Erreur WMI polling : %s", exc)

    def _take_wmi_snapshot(self) -> DeviceSnapshot:
        """Prend un instantane de tous les peripheriques via WMI."""
        snapshot = DeviceSnapshot(timestamp=time.monotonic())

        try:
            import wmi as wmi_module
            w = wmi_module.WMI()
        except ImportError:
            logger.warning("Module wmi non disponible, polling desactive")
            return snapshot
        except Exception as exc:
            logger.error("Erreur connexion WMI : %s", exc)
            return snapshot

        with self._lock:
            enabled = self._enabled_categories.copy()

        for category in enabled:
            device_ids = set()
            try:
                wmi_class = _WMI_CLASSES[category]

                if category == DeviceCategory.BLUETOOTH:
                    # Filtrer les entites PnP Bluetooth
                    entities = w.query(
                        f"SELECT DeviceID, Name FROM {wmi_class} "
                        f"WHERE DeviceID LIKE '%{_BT_PNP_PREFIX}%'"
                    )
                    for entity in entities:
                        dev_id = getattr(entity, "DeviceID", "")
                        if dev_id:
                            device_ids.add(dev_id)

                elif category == DeviceCategory.NETWORK:
                    # Filtrer les adaptateurs physiques
                    adapters = w.query(
                        f"SELECT DeviceID, Name, PhysicalAdapter FROM {wmi_class} "
                        f"WHERE PhysicalAdapter = TRUE"
                    )
                    for adapter in adapters:
                        dev_id = getattr(adapter, "DeviceID", "")
                        if dev_id:
                            device_ids.add(str(dev_id))

                else:
                    items = getattr(w, wmi_class)()
                    for item in items:
                        dev_id = getattr(item, "DeviceID", "") or getattr(item, "Name", "")
                        if dev_id:
                            device_ids.add(str(dev_id))

            except Exception as exc:
                logger.debug("WMI query %s echouee : %s", category.value, exc)

            snapshot.devices[category] = device_ids

        return snapshot

    def _compare_snapshots(
        self, old: DeviceSnapshot, new: DeviceSnapshot
    ) -> None:
        """Compare deux snapshots et emet des evenements pour les differences."""
        with self._lock:
            enabled = self._enabled_categories.copy()

        for category in enabled:
            old_devices = old.devices.get(category, set())
            new_devices = new.devices.get(category, set())

            # Nouveaux peripheriques
            added = new_devices - old_devices
            for dev_id in added:
                whitelisted = self.is_whitelisted(dev_id)
                event = DeviceEvent(
                    category=category,
                    device_id=dev_id,
                    device_name=dev_id,  # WMI ne donne pas toujours un nom lisible
                    is_new=True,
                    whitelisted=whitelisted,
                    timestamp=time.monotonic(),
                )

                if not whitelisted:
                    logger.warning(
                        "NOUVEAU peripherique detecte [%s] : %s",
                        category.value, dev_id,
                    )
                    self._emit_event(event)
                else:
                    logger.debug(
                        "Peripherique whiteliste reconnecte [%s] : %s",
                        category.value, dev_id,
                    )

            # Peripheriques retires
            removed = old_devices - new_devices
            for dev_id in removed:
                event = DeviceEvent(
                    category=category,
                    device_id=dev_id,
                    device_name=dev_id,
                    is_new=False,
                    whitelisted=self.is_whitelisted(dev_id),
                    timestamp=time.monotonic(),
                )
                logger.info(
                    "Peripherique deconnecte [%s] : %s",
                    category.value, dev_id,
                )
                # On n'emet pas d'alerte pour les deconnexions

    # ----- WM_DEVICECHANGE (Win32 Messages) -----

    def _win32_message_loop(self) -> None:
        """
        Ecoute les messages WM_DEVICECHANGE de Windows pour une detection
        instantanee des peripheriques USB.
        """
        try:
            import win32gui
            import win32con
        except ImportError:
            logger.warning(
                "pywin32 non disponible, WM_DEVICECHANGE desactive. "
                "Le polling WMI reste actif."
            )
            return

        WM_DEVICECHANGE = 0x0219
        DBT_DEVICEARRIVAL = 0x8000

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_DEVICECHANGE and wparam == DBT_DEVICEARRIVAL:
                # Verifier le cooldown
                with self._lock:
                    if time.monotonic() < self._cooldown_until:
                        return 0

                logger.info("WM_DEVICECHANGE : nouveau peripherique detecte (instantane)")
                # Emettre un evenement generique — le WMI polling fournira les details
                event = DeviceEvent(
                    category=DeviceCategory.USB_HID,
                    device_id="WM_DEVICECHANGE",
                    device_name="Peripherique USB (detection instantanee)",
                    is_new=True,
                    whitelisted=False,
                    timestamp=time.monotonic(),
                )
                self._emit_event(event)

            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        # Creer une fenetre invisible pour recevoir les messages
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = _wnd_proc
        wc.lpszClassName = "PrankGuardDeviceMonitor"

        try:
            class_atom = win32gui.RegisterClass(wc)
            hwnd = win32gui.CreateWindow(
                class_atom, "PrankGuard Device Monitor",
                0, 0, 0, 0, 0, 0, 0, 0, None,
            )

            logger.info("WM_DEVICECHANGE listener actif (hwnd=%s)", hwnd)

            # Boucle de messages Windows
            while self._running:
                # PeekMessage avec timeout pour pouvoir verifier self._running
                try:
                    result = win32gui.PumpWaitingMessages()
                except Exception:
                    pass
                time.sleep(0.1)

            win32gui.DestroyWindow(hwnd)
            win32gui.UnregisterClass(class_atom, None)

        except Exception as exc:
            logger.error("Erreur Win32 message loop : %s", exc)

    # ----- Emission d'evenements -----

    def _emit_event(self, event: DeviceEvent) -> None:
        """Notifie tous les callbacks d'un changement de peripherique."""
        for cb in self._device_callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("Erreur callback device : %s", exc)


# ---------------------------------------------------------------------------
# Execution directe (pour tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s -- %(message)s",
    )

    print("Device Monitor -- test en direct (Ctrl+C pour quitter)")
    print("=" * 55)

    def _on_device(event: DeviceEvent) -> None:
        action = "BRANCHE" if event.is_new else "DEBRANCHE"
        wl = " [whitelist]" if event.whitelisted else " [ALERTE]"
        print(
            f"  [{event.category.value:10s}] {action} : "
            f"{event.device_name}{wl}"
        )

    monitor = DeviceMonitor()
    monitor.on_device_change(_on_device)
    monitor.load_whitelist()

    # Whitelister les peripheriques actuels
    count = monitor.whitelist_current_devices()
    print(f"  {count} peripheriques actuels ajoutes a la whitelist")

    monitor.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nArret...")
        monitor.stop()
        print("Termine.")
