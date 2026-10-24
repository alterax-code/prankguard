"""
Blocage de peripheriques via registre Windows et services.
Blocage USB, Bluetooth, et reseau effectifs.
"""
import subprocess
import time
from typing import Optional, List

from src.logger import logger

_NO_WINDOW = subprocess.CREATE_NO_WINDOW


def _run_cmd(cmd: list, description: str = "") -> bool:
    """Execute une commande systeme silencieusement."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, creationflags=_NO_WINDOW, timeout=10
        )
        if result.returncode != 0 and description:
            stderr = result.stderr.decode(errors="ignore").strip()
            if stderr:
                logger.error(f"Echec {description}: {stderr}")
        return result.returncode == 0
    except Exception as e:
        if description:
            logger.error(f"Erreur {description}: {e}")
        return False


# -- USB --

def block_usb(mode: str = "DESKTOP"):
    """Bloque les ports USB via le registre."""
    if mode == "DESKTOP":
        services = ["USBSTOR"]
    else:
        services = ["USBHUB3", "USBXHCI", "USBSTOR"]

    for svc in services:
        _run_cmd(
            ["reg", "add",
             f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{svc}",
             "/v", "Start", "/t", "REG_DWORD", "/d", "4", "/f"],
            f"block {svc}"
        )
    logger.lock(f"USB bloque (mode {mode})")


def unblock_usb():
    """Debloque tous les ports USB."""
    for svc in ["USBHUB3", "USBXHCI", "USBSTOR"]:
        _run_cmd(
            ["reg", "add",
             f"HKLM\\SYSTEM\\CurrentControlSet\\Services\\{svc}",
             "/v", "Start", "/t", "REG_DWORD", "/d", "3", "/f"],
            f"unblock {svc}"
        )
    logger.unlock("USB debloque")


# -- Bluetooth --

def block_bluetooth():
    """Bloque le Bluetooth (service + registre)."""
    _run_cmd(["sc", "stop", "bthserv"], "stop bthserv")
    _run_cmd(["sc", "config", "bthserv", "start=", "disabled"], "disable bthserv")
    _run_cmd(
        ["reg", "add",
         "HKLM\\SYSTEM\\CurrentControlSet\\Services\\BTHUSB",
         "/v", "Start", "/t", "REG_DWORD", "/d", "4", "/f"],
        "block BTHUSB"
    )
    logger.lock("Bluetooth bloque")


def unblock_bluetooth():
    """Restaure le Bluetooth."""
    _run_cmd(
        ["reg", "add",
         "HKLM\\SYSTEM\\CurrentControlSet\\Services\\BTHUSB",
         "/v", "Start", "/t", "REG_DWORD", "/d", "3", "/f"],
        "unblock BTHUSB"
    )
    _run_cmd(["sc", "config", "bthserv", "start=", "demand"], "enable bthserv")
    _run_cmd(["sc", "start", "bthserv"], "start bthserv")
    logger.unlock("Bluetooth restaure")


# -- Reseau --

def _get_active_interfaces() -> List[str]:
    """Recupere la liste des interfaces reseau actives (FR + EN compatible)."""
    interfaces = []
    try:
        result = subprocess.run(
            ["netsh", "interface", "show", "interface"],
            capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=5
        )
        for line in result.stdout.splitlines():
            lower = line.lower()
            # Detecter les interfaces connectees (FR: "connect", EN: "connected")
            if "connect" in lower and "disconnect" not in lower:
                parts = line.split()
                if len(parts) >= 4:
                    # Le nom de l'interface est tout apres le 3eme champ
                    name = " ".join(parts[3:])
                    # Ignorer les headers
                    if name.lower() not in ["name", "nom", "interface name"]:
                        interfaces.append(name)
    except Exception as e:
        logger.error(f"Erreur listing interfaces: {e}")
    return interfaces


def block_network(interface_name: Optional[str] = None):
    """
    Desactive une ou toutes les interfaces reseau via netsh.
    Bloque EFFECTIVEMENT la connexion.
    """
    if interface_name:
        targets = [interface_name]
    else:
        targets = _get_active_interfaces()

    blocked = []
    for name in targets:
        success = _run_cmd(
            ["netsh", "interface", "set", "interface", name, "admin=disable"],
            f"disable reseau {name}"
        )
        if success:
            blocked.append(name)

    if blocked:
        logger.lock(f"Reseau bloque : {', '.join(blocked)}")
    else:
        logger.error("Aucune interface reseau bloquee (droits admin ?)")

    return blocked


def unblock_network(interface_name: Optional[str] = None):
    """Reactive une ou toutes les interfaces reseau."""
    if interface_name:
        targets = [interface_name]
    else:
        # Lister TOUTES les interfaces (meme desactivees)
        targets = []
        try:
            result = subprocess.run(
                ["netsh", "interface", "show", "interface"],
                capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=5
            )
            for line in result.stdout.splitlines():
                lower = line.lower()
                if "disconnect" in lower or "desactiv" in lower or "disabled" in lower:
                    parts = line.split()
                    if len(parts) >= 4:
                        name = " ".join(parts[3:])
                        if name.lower() not in ["name", "nom", "interface name"]:
                            targets.append(name)
        except Exception:
            pass

    for name in targets:
        _run_cmd(
            ["netsh", "interface", "set", "interface", name, "admin=enable"],
            f"enable reseau {name}"
        )

    if targets:
        logger.unlock(f"Reseau restaure : {', '.join(targets)}")
