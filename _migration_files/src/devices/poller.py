"""
Surveillance WMI polling de 7 types de périphériques.
FIX 7/8 — Requête WMI détaillée pour récupérer les infos du device.
"""
import threading
import time
import pythoncom
import wmi as wmi_module
from typing import Callable, Optional, Dict

from src.logger import logger


class PollingWatcher:
    """Surveillance périodique des périphériques via WMI."""

    def __init__(self, callback: Callable[[str, Optional[str]], None]):
        """
        callback(device_type, device_info) — appelé quand un nouveau device est détecté.
        device_info contient le nom/fabricant si disponible.
        """
        self.callback = callback
        self.running = True
        self.paused = False
        self.baselines: Dict[str, int] = {}
        self._wmi = None

        # Toggles de surveillance
        self.watch_usb_hid = True
        self.watch_monitors = False
        self.watch_network = False
        self.watch_printers = False
        self.watch_bluetooth = False
        self.watch_audio = False

    def start(self):
        """Démarre le polling dans un thread dédié."""
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        """Boucle de polling WMI (300ms)."""
        pythoncom.CoInitialize()
        try:
            self._wmi = wmi_module.WMI()
            self._init_baselines()
            logger.start("Polling Watcher actif — 7 types de périphériques")

            while self.running:
                if not self.paused:
                    self._check_all()
                time.sleep(0.3)
        except Exception as e:
            logger.error(f"Erreur Polling Watcher: {e}")
        finally:
            pythoncom.CoUninitialize()

    def _init_baselines(self):
        """Initialise les compteurs de référence."""
        try:
            self.baselines = {
                "usb_hid": len(self._wmi.Win32_USBControllerDevice()),
                "monitors": self._count("monitors"),
                "network": self._count("network"),
                "printers": self._count("printers"),
                "bluetooth": self._count("bluetooth"),
                "audio": self._count("audio"),
            }
        except Exception:
            self.baselines = {k: 0 for k in [
                "usb_hid", "monitors", "network", "printers", "bluetooth", "audio"
            ]}

    def _count(self, dtype: str) -> int:
        """Compte les devices d'un type donné."""
        try:
            if dtype == "monitors":
                return len(self._wmi.Win32_DesktopMonitor())
            elif dtype == "network":
                return len([n for n in self._wmi.Win32_NetworkAdapter()
                           if n.NetConnectionStatus == 2])
            elif dtype == "printers":
                return len(self._wmi.Win32_Printer())
            elif dtype == "bluetooth":
                return len([p for p in self._wmi.Win32_PnPEntity()
                           if p.Name and "bluetooth" in p.Name.lower()])
            elif dtype == "audio":
                return len(self._wmi.Win32_SoundDevice())
        except Exception:
            pass
        return 0

    def _get_device_info(self, dtype: str) -> str:
        """FIX 8 — Récupère les infos détaillées du dernier device connecté."""
        try:
            if dtype == "USB HID":
                devices = self._wmi.Win32_USBControllerDevice()
                if devices:
                    dep = devices[-1].Dependent
                    did = dep.split('"')[1] if '"' in dep else ""
                    if did:
                        pnps = self._wmi.Win32_PnPEntity(
                            DeviceID=did.replace("\\\\", "\\")
                        )
                        if pnps:
                            p = pnps[0]
                            return f"{p.Name or 'Inconnu'} [{p.Manufacturer or '?'}]"
                return "Périphérique USB HID"

            elif dtype == "Monitor":
                monitors = self._wmi.Win32_DesktopMonitor()
                if monitors:
                    m = monitors[-1]
                    return f"{m.Name or 'Moniteur'} [{m.MonitorManufacturer or '?'}]"

            elif dtype == "Network":
                adapters = [n for n in self._wmi.Win32_NetworkAdapter()
                           if n.NetConnectionStatus == 2]
                if adapters:
                    a = adapters[-1]
                    return f"{a.Name or 'Réseau'} [{a.Manufacturer or '?'}] MAC:{a.MACAddress or '?'}"

            elif dtype == "Printer":
                printers = self._wmi.Win32_Printer()
                if printers:
                    p = printers[-1]
                    return f"{p.Name or 'Imprimante'} [Driver: {p.DriverName or '?'}]"

            elif dtype == "Bluetooth":
                bts = [p for p in self._wmi.Win32_PnPEntity()
                       if p.Name and "bluetooth" in p.Name.lower()]
                if bts:
                    b = bts[-1]
                    return f"{b.Name or 'Bluetooth'} [{b.Manufacturer or '?'}]"

            elif dtype == "Audio":
                sounds = self._wmi.Win32_SoundDevice()
                if sounds:
                    s = sounds[-1]
                    return f"{s.Name or 'Audio'} [{s.Manufacturer or '?'}]"

        except Exception:
            pass
        return dtype

    def _check_all(self):
        """Vérifie tous les types de périphériques activés."""
        checks = [
            ("usb_hid",   "USB HID",   self.watch_usb_hid),
            ("monitors",  "Monitor",   self.watch_monitors),
            ("network",   "Network",   self.watch_network),
            ("printers",  "Printer",   self.watch_printers),
            ("bluetooth", "Bluetooth", self.watch_bluetooth),
            ("audio",     "Audio",     self.watch_audio),
        ]

        try:
            for key, dtype, enabled in checks:
                if not enabled:
                    continue

                if key == "usb_hid":
                    count = len(self._wmi.Win32_USBControllerDevice())
                else:
                    count = self._count(key)

                if count > self.baselines.get(key, 0):
                    # FIX 8 — Récupérer les infos détaillées
                    info = self._get_device_info(dtype)
                    self.callback(dtype, info)

                self.baselines[key] = count

        except Exception:
            pass

    def reset_baselines(self):
        """Réinitialise les compteurs (après changement de config)."""
        if self._wmi:
            self._init_baselines()

    def stop(self):
        """Arrête le polling proprement."""
        self.running = False
