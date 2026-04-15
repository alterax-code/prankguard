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

    import customtkinter as ctk
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    config = Config.load()

    if not check_enrollment(config.encodings_path):
        # Lancer l'enrollment puis l'app
        EnrollmentWindow(
            encodings_path=config.encodings_path,
            on_complete=lambda: PrankGuardApp(config).mainloop()
        ).mainloop()
    else:
        PrankGuardApp(config).mainloop()


if __name__ == "__main__":
    main()
