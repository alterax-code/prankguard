"""Journalisation fichier tournant + console."""
import logging
import logging.handlers
import sys

from . import paths


def setup(debug: bool = False) -> logging.Logger:
    paths.ensure_dirs()
    root = logging.getLogger("prankguard")
    if root.handlers:
        return root
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    fh = logging.handlers.RotatingFileHandler(paths.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:   # absent sous pythonw.exe
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    return root


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"prankguard.{name}")
