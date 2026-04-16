"""
Point d'entrée principal de PrankGuard.
FIX 1 — Auto-elevation admin (UAC) pour le blocage USB/BT/réseau.
"""
import ctypes
import sys
import os
import types
from pathlib import Path

# Injecter un stub pkg_resources AVANT face_recognition_models pour supprimer la
# DeprecationWarning de setuptools (Python 3.12+). Le stub fournit resource_filename
# via importlib.util sans passer par pkg_resources.
if "pkg_resources" not in sys.modules:
    _pkg_stub = types.ModuleType("pkg_resources")
    def _resource_filename(pkg, resource):
        import importlib.util
        spec = importlib.util.find_spec(pkg)
        return str(Path(spec.origin).parent / resource) if (spec and spec.origin) else resource
    _pkg_stub.resource_filename = _resource_filename
    sys.modules["pkg_resources"] = _pkg_stub

import face_recognition_models
_models_dir = Path(face_recognition_models.__file__).parent / "models"
face_recognition_models.pose_predictor_model_location = lambda: str(_models_dir / "shape_predictor_68_face_landmarks.dat")
face_recognition_models.pose_predictor_five_point_model_location = lambda: str(_models_dir / "shape_predictor_5_face_landmarks.dat")
face_recognition_models.face_recognition_model_location = lambda: str(_models_dir / "dlib_face_recognition_resnet_model_v1.dat")
face_recognition_models.cnn_face_detector_model_location = lambda: str(_models_dir / "mmod_human_face_detector.dat")


def is_admin() -> bool:
    """Vérifie si le processus tourne en admin."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def elevate():
    """Relance le script avec les droits administrateur (UAC)."""
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        f'"{os.path.abspath(sys.argv[0])}"', None, 1
    )
    sys.exit(0)


def _migrate_npy_to_npz(config) -> None:
    """Convertit encodings.npy → authorized_users.npz si nécessaire (Sprint 2)."""
    # Chemin par défaut de l'ancienne version
    old_npy = os.path.join("data", "owner_faces", "encodings.npy")
    new_npz = config.encodings_path  # Maintenant authorized_users.npz

    if not Path(old_npy).exists():
        return
    if Path(new_npz).exists():
        # Déjà migré — supprimer l'ancien
        Path(old_npy).unlink(missing_ok=True)
        return
    try:
        import numpy as np
        data = np.load(old_npy, allow_pickle=False)
        os.makedirs(os.path.dirname(new_npz), exist_ok=True)
        np.savez(new_npz, owner=data)
        Path(old_npy).unlink(missing_ok=True)
        print(f"[PrankGuard] Migration encodings.npy → authorized_users.npz ({len(data)} encodings)")
    except Exception as e:
        print(f"[PrankGuard] Avertissement migration npy→npz: {e}")


def _migrate_pkl_to_npy(encodings_path: str) -> None:
    """Convertit encodings.pkl → encodings.npy si le .pkl existe encore."""
    # Garde : si le path ne contient pas .npy, la migration pkl→npy est sans objet
    if ".npy" not in encodings_path:
        return
    pkl_path = encodings_path.replace(".npy", ".pkl")
    if not Path(pkl_path).exists():
        return
    if Path(encodings_path).exists():
        # .npy déjà présent — supprimer le .pkl orphelin
        Path(pkl_path).unlink(missing_ok=True)
        return
    try:
        import pickle
        import numpy as np
        with open(pkl_path, "rb") as f:
            data = pickle.load(f)
        # data peut être list[ndarray] ou ndarray 2D
        if isinstance(data, list):
            arr = np.array(data) if data else np.empty((0, 128), dtype=np.float64)
        else:
            arr = np.array(data)
        os.makedirs(os.path.dirname(encodings_path), exist_ok=True)
        np.save(encodings_path, arr)
        Path(pkl_path).unlink(missing_ok=True)
        print(f"[PrankGuard] Migration encodings.pkl → .npy ({len(arr)} encodings)")
    except Exception as e:
        print(f"[PrankGuard] Avertissement migration pkl: {e}")


def main():
    """Lancement principal : admin check → enrollment check → app."""
    # FIX 1 — Demander les droits admin si pas déjà admin
    if not is_admin():
        print("[PrankGuard] Droits admin requis — lancement UAC...")
        elevate()

    # Imports après le check admin (évite de charger tout si on relance)
    from src.config import Config
    from src.enrollment import check_enrollment, EnrollmentWindow
    from src.gui.app import PrankGuardApp

    import subprocess
    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    config = Config.load()

    # Lancer le watchdog si la protection anti-fermeture est activée
    if config.close_protection_enabled:
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        subprocess.Popen(
            [sys.executable, "-m", "src.watchdog", str(os.getpid()), project_root],
            cwd=project_root,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    # Migration rétrocompat pkl → npy → npz au premier lancement
    _migrate_pkl_to_npy(config.encodings_path)
    _migrate_npy_to_npz(config)

    if not check_enrollment(config.encodings_path):
        # Enrollment requis — _finish() relancera le process automatiquement
        EnrollmentWindow(
            encodings_path=config.encodings_path,
            encrypt_enabled=config.encryption_enabled,
        ).mainloop()
    else:
        PrankGuardApp(config).mainloop()


if __name__ == "__main__":
    main()
