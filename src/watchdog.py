"""
Processus watchdog : surveille le PID parent et relance PrankGuard si
celui-ci se ferme sans avoir écrit le flag d'arrêt propre.
Lancé par main.py uniquement si close_protection_enabled = True.
"""
import ctypes
import json
import os
import subprocess
import sys
import time

# Fichier-flag posé par l'app lors d'une fermeture normale
SHUTDOWN_FLAG = os.path.join(os.path.expanduser("~"), ".prankguard", "watchdog_shutdown.flag")
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".prankguard", "config.json")

PROCESS_QUERY_INFORMATION = 0x0400
STILL_ACTIVE = 259


def _is_alive(pid: int) -> bool:
    """Retourne True si le processus PID est encore en cours d'exécution."""
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong(0)
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == STILL_ACTIVE


def _protection_enabled() -> bool:
    """Lit la config pour savoir si la protection est active."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("close_protection_enabled", False))
    except Exception:
        return False


def main():
    if len(sys.argv) < 3:
        print("[watchdog] Usage: python -m src.watchdog <parent_pid> <project_root>")
        return

    parent_pid = int(sys.argv[1])
    project_root = sys.argv[2]

    # Surveiller le processus parent
    while _is_alive(parent_pid):
        if os.path.exists(SHUTDOWN_FLAG):
            return  # Fermeture propre — ne pas relancer
        time.sleep(1)

    # Parent terminé — vérifier le flag et la config
    if os.path.exists(SHUTDOWN_FLAG):
        return

    if not _protection_enabled():
        return

    # Supprimer le flag résiduel si présent
    try:
        os.remove(SHUTDOWN_FLAG)
    except Exception:
        pass

    # Relancer PrankGuard
    python_exe = sys.executable
    subprocess.Popen(
        [python_exe, "-m", "src.main"],
        cwd=project_root,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


if __name__ == "__main__":
    main()
