"""
Blocage de périphériques via registre Windows et services.
FIX 7 — Blocage effectif BT (service bthserv) et réseau (netsh).
FIX 5 — Mode DESKTOP (USBSTOR seul) vs LAPTOP (tout USB).
"""
import re
import subprocess
from typing import Optional

from src.logger import logger

# Whitelist: lettres, chiffres, espaces, tirets, points, underscores
_IFACE_RE = re.compile(r'^[\w\s\-\.]+$')

# Flag pour masquer la fenêtre cmd
_NO_WINDOW = subprocess.CREATE_NO_WINDOW


def _run_cmd(cmd: list, description: str = "") -> bool:
    """Exécute une commande système silencieusement."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, creationflags=_NO_WINDOW, timeout=10
        )
        if result.returncode != 0 and description:
            logger.error(f"Échec {description}: {result.stderr.decode(errors='ignore').strip()}")
        return result.returncode == 0
    except Exception as e:
        if description:
            logger.error(f"Erreur {description}: {e}")
        return False


# ── USB ──────────────────────────────────────────────────────────────

def block_usb(mode: str = "DESKTOP"):
    """
    Bloque les ports USB via le registre.
    FIX 5 — DESKTOP: stockage seul | LAPTOP: tout USB.
    """
    if mode == "DESKTOP":
        services = ["USBSTOR"]
    else:
        services = ["USBHUB3", "USBXHCI", "USBSTOR"]

    for svc in services:
        _run_cmd(
            ["reg", "add", f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{svc}",
             "/v", "Start", "/t", "REG_DWORD", "/d", "4", "/f"],
            f"block {svc}"
        )
    logger.lock(f"USB bloqué (mode {mode}) — services: {', '.join(services)}")


def unblock_usb():
    """Débloque tous les ports USB."""
    for svc in ["USBHUB3", "USBXHCI", "USBSTOR"]:
        _run_cmd(
            ["reg", "add", f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{svc}",
             "/v", "Start", "/t", "REG_DWORD", "/d", "3", "/f"],
            f"unblock {svc}"
        )
    logger.unlock("USB débloqué — tous les services restaurés")


# ── Bluetooth ────────────────────────────────────────────────────────

def block_bluetooth():
    """
    FIX 7 — Bloque le Bluetooth AVANT que la connexion ne s'établisse.
    Arrête le service bthserv + désactive BTHUSB dans le registre.
    """
    _run_cmd(["sc", "stop", "bthserv"], "stop bthserv")
    _run_cmd(["sc", "config", "bthserv", "start=", "disabled"], "disable bthserv")
    _run_cmd(
        ["reg", "add", "HKLM\\SYSTEM\\CurrentControlSet\\Services\\BTHUSB",
         "/v", "Start", "/t", "REG_DWORD", "/d", "4", "/f"],
        "block BTHUSB"
    )
    logger.lock("Bluetooth bloqué — service bthserv arrêté + BTHUSB désactivé")


def unblock_bluetooth():
    """Restaure le Bluetooth."""
    _run_cmd(
        ["reg", "add", "HKLM\\SYSTEM\\CurrentControlSet\\Services\\BTHUSB",
         "/v", "Start", "/t", "REG_DWORD", "/d", "3", "/f"],
        "unblock BTHUSB"
    )
    _run_cmd(["sc", "config", "bthserv", "start=", "demand"], "enable bthserv")
    _run_cmd(["sc", "start", "bthserv"], "start bthserv")
    logger.unlock("Bluetooth restauré — service bthserv redémarré")


# ── Réseau ───────────────────────────────────────────────────────────

def block_network(interface_name: Optional[str] = None):
    """
    FIX 7 — Désactive une interface réseau via netsh.
    Si aucun nom n'est donné, désactive toutes les interfaces connectées.
    """
    if interface_name:
        if not _IFACE_RE.match(interface_name):
            logger.error(f"Nom d'interface invalide refusé: {interface_name!r}")
            return
        _run_cmd(
            ["netsh", "interface", "set", "interface", interface_name, "admin=disable"],
            f"disable réseau {interface_name}"
        )
        logger.lock(f"Réseau bloqué — interface: {interface_name}")
    else:
        # Désactiver toutes les interfaces actives
        try:
            result = subprocess.run(
                ["netsh", "interface", "show", "interface"],
                capture_output=True, text=True, creationflags=_NO_WINDOW
            )
            for line in result.stdout.splitlines():
                if "Connected" in line or "Connecté" in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        name = " ".join(parts[3:])
                        if not _IFACE_RE.match(name):
                            logger.error(f"Nom d'interface invalide ignoré: {name!r}")
                            continue
                        _run_cmd(
                            ["netsh", "interface", "set", "interface", name, "admin=disable"],
                            f"disable réseau {name}"
                        )
            logger.lock("Réseau bloqué — toutes les interfaces désactivées")
        except Exception as e:
            logger.error(f"Erreur blocage réseau: {e}")


def unblock_network(interface_name: Optional[str] = None):
    """Réactive une interface réseau."""
    if interface_name:
        if not _IFACE_RE.match(interface_name):
            logger.error(f"Nom d'interface invalide refusé: {interface_name!r}")
            return
        _run_cmd(
            ["netsh", "interface", "set", "interface", interface_name, "admin=enable"],
            f"enable réseau {interface_name}"
        )
        logger.unlock(f"Réseau restauré — interface: {interface_name}")
