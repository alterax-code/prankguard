"""Emplacements des fichiers : données utilisateur dans %APPDATA%\\PrankGuard\\v3."""
import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    override = os.environ.get("PRANKGUARD_HOME")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) / "PrankGuard" if appdata else Path.home() / ".prankguard"
    # v3 : embeddings SFace incompatibles avec les anciens encodings dlib -> dossier séparé
    return root / "v3"


def resource_dir() -> Path:
    """Dossier des ressources embarquées (web/, modèles livrés) — compatible PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "prankguard"
    return Path(__file__).resolve().parent


HOME = _base_dir()
CONFIG_FILE = HOME / "config.json"
GALLERY_FILE = HOME / "faces.bin"
EVENTS_DB = HOME / "events.sqlite3"
SNAPSHOTS_DIR = HOME / "snapshots"
LOGS_DIR = HOME / "logs"
LOG_FILE = LOGS_DIR / "prankguard.log"
MODELS_DIR = HOME / "models"
INSTANCE_FILE = HOME / "instance.json"
WEB_DIR = resource_dir() / "web"
BUNDLED_MODELS_DIR = resource_dir() / "models"


def ensure_dirs() -> None:
    for d in (HOME, SNAPSHOTS_DIR, LOGS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
