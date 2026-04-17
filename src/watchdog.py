"""
Watchdog PrankGuard — Vague 5.
- WatchdogThread  : thread daemon heartbeat (écrit toutes les 5s)
- start_external_watchdog : génère et lance watchdog.bat pour surveiller le PID
- main() : processus externe de relance (lancé par watchdog.bat)
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import time

from src import paths

# Fichier-flag posé par l'app lors d'une fermeture normale
SHUTDOWN_FLAG = str(paths.SHUTDOWN_FLAG)
CONFIG_FILE = str(paths.CONFIG_FILE)

PROCESS_QUERY_INFORMATION = 0x0400
STILL_ACTIVE = 259


class WatchdogThread(threading.Thread):
    """Thread daemon — écrit un heartbeat toutes les 5s dans APP_DATA."""

    HEARTBEAT_INTERVAL = 5.0
    _HB_FILE = paths.APP_DATA / "watchdog_heartbeat.flag"

    def __init__(self):
        super().__init__(daemon=True, name="PrankGuard-Watchdog")
        self._stop_event = threading.Event()

    def run(self):
        while not self._stop_event.wait(self.HEARTBEAT_INTERVAL):
            try:
                self._HB_FILE.write_text(str(time.time()), encoding="utf-8")
            except Exception:
                pass

    def stop(self):
        self._stop_event.set()


def start_external_watchdog() -> None:
    """Génère watchdog.bat et le lance pour surveiller le PID courant."""
    pid = os.getpid()
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    python_exe = sys.executable
    bat_path = paths.APP_DATA / "watchdog_restart.bat"
    shutdown_flag = str(paths.SHUTDOWN_FLAG)

    bat_content = (
        f"@echo off\n"
        f":loop\n"
        f"timeout /t 5 /nobreak >nul\n"
        f"tasklist /fi \"PID eq {pid}\" 2>nul | find /i \"{pid}\" >nul\n"
        f"if errorlevel 1 goto check_flag\n"
        f"goto loop\n"
        f":check_flag\n"
        f"if exist \"{shutdown_flag}\" exit /b 0\n"
        f"\"{python_exe}\" -m src.watchdog {pid} \"{project_root}\"\n"
    )
    bat_path.write_text(bat_content, encoding="utf-8")
    subprocess.Popen(
        ["cmd.exe", "/c", str(bat_path)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        cwd=project_root,
    )


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
