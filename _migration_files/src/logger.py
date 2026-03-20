"""
Système de logs détaillés.
FIX 8 — Chaque événement est loggé avec timestamp, type, infos device, action.
Format : [HH:MM:SS] ICON message
"""
import os
import logging
from datetime import datetime
from typing import Optional, Callable


LOG_DIR = os.path.join(os.path.expanduser("~"), ".prankguard", "logs")

# Icônes par catégorie
ICONS = {
    "device":  "⚠",
    "lock":    "🔒",
    "unlock":  "🔓",
    "face":    "👤",
    "mode":    "⚙",
    "toggle":  "🔧",
    "camera":  "📷",
    "info":    "ℹ",
    "error":   "❌",
    "start":   "🚀",
}


class PrankLogger:
    """Logger centralisé : écrit dans un fichier + callback GUI."""

    def __init__(self, gui_callback: Optional[Callable[[str], None]] = None):
        self._gui_callback = gui_callback
        self._file_logger = self._setup_file_logger()

    def _setup_file_logger(self) -> logging.Logger:
        """Crée un logger fichier avec rotation journalière."""
        os.makedirs(LOG_DIR, exist_ok=True)
        logger = logging.getLogger("prankguard")
        logger.setLevel(logging.DEBUG)

        if not logger.handlers:
            today = datetime.now().strftime("%Y-%m-%d")
            fh = logging.FileHandler(
                os.path.join(LOG_DIR, f"prankguard_{today}.log"),
                encoding="utf-8"
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(fh)

        return logger

    def set_gui_callback(self, callback: Callable[[str], None]):
        """Branche le callback GUI (appelé depuis le main thread)."""
        self._gui_callback = callback

    def log(self, message: str, category: str = "info"):
        """Log un message avec timestamp et icône."""
        icon = ICONS.get(category, "ℹ")
        ts = datetime.now().strftime("%H:%M:%S")
        formatted = f"[{ts}] {icon} {message}"

        # Fichier
        self._file_logger.info(formatted)

        # GUI (si branchée)
        if self._gui_callback:
            try:
                self._gui_callback(formatted)
            except Exception:
                pass

    # -- Raccourcis par catégorie --

    def device(self, msg: str):
        self.log(msg, "device")

    def lock(self, msg: str):
        self.log(msg, "lock")

    def unlock(self, msg: str):
        self.log(msg, "unlock")

    def face(self, msg: str):
        self.log(msg, "face")

    def mode(self, msg: str):
        self.log(msg, "mode")

    def toggle(self, msg: str):
        self.log(msg, "toggle")

    def camera(self, msg: str):
        self.log(msg, "camera")

    def info(self, msg: str):
        self.log(msg, "info")

    def error(self, msg: str):
        self.log(msg, "error")

    def start(self, msg: str):
        self.log(msg, "start")


# Instance globale partagée
logger = PrankLogger()
