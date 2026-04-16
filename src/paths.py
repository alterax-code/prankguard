"""
Centralisation de tous les chemins de PrankGuard.
%APPDATA%\\PrankGuard\\ sur Windows, ~/.prankguard/ fallback.
"""
import os
import sys
from pathlib import Path


def get_app_data_dir() -> Path:
    """Retourne le répertoire de données applicatif, le crée si nécessaire."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        app_dir = Path(base) / "PrankGuard" if base else Path.home() / ".prankguard"
    else:
        app_dir = Path.home() / ".prankguard"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


APP_DATA      = get_app_data_dir()
CONFIG_FILE   = APP_DATA / "config.json"
USERS_DIR     = APP_DATA / "users"
USERS_FILE    = USERS_DIR / "authorized_users.npz"
LOGS_DIR      = APP_DATA / "logs"
LOG_FILE      = LOGS_DIR / "prankguard.log"
INTRUSION_LOG = LOGS_DIR / "intrusion_reports.json"
SHUTDOWN_FLAG = APP_DATA / "watchdog_shutdown.flag"

# Créer les sous-répertoires immédiatement
USERS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def migrate_legacy_data() -> None:
    """
    Migration des données legacy vers APP_DATA :
    - ~/.prankguard/config.json → APP_DATA/config.json (Windows uniquement)
    - data/owner_faces/*.npz   → APP_DATA/users/authorized_users.npz
    - Suppression des .npy et .pkl obsolètes
    """
    import shutil

    # 1. Migrer l'ancienne config ~/.prankguard (si différent de APP_DATA)
    old_dir = Path.home() / ".prankguard"
    if old_dir.resolve() != APP_DATA.resolve() and old_dir.exists():
        old_cfg = old_dir / "config.json"
        if old_cfg.exists() and not CONFIG_FILE.exists():
            try:
                shutil.copy2(str(old_cfg), str(CONFIG_FILE))
                print(f"[PrankGuard] Migration config: {old_cfg} → {CONFIG_FILE}")
            except Exception as e:
                print(f"[PrankGuard] Avertissement migration config: {e}")

    # 2. Migrer les anciens encodings depuis data/owner_faces/
    if getattr(sys, "frozen", False):
        _base = Path(sys.executable).parent
    else:
        _base = Path(__file__).parent.parent  # src/../ = racine du projet

    old_data_dir = _base / "data" / "owner_faces"
    if old_data_dir.exists():
        for fname in list(old_data_dir.iterdir()):
            if fname.suffix == ".npz":
                if not USERS_FILE.exists():
                    try:
                        shutil.copy2(str(fname), str(USERS_FILE))
                        print(f"[PrankGuard] Migration encodings: {fname.name} → {USERS_FILE}")
                    except Exception as e:
                        print(f"[PrankGuard] Avertissement migration npz: {e}")
            elif fname.suffix in (".npy", ".pkl"):
                try:
                    fname.unlink()
                    print(f"[PrankGuard] Suppression obsolète: {fname.name}")
                except Exception:
                    pass
