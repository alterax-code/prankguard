"""
Point d'entrée principal de PrankGuard.
FIX 1 — Auto-elevation admin (UAC) pour le blocage USB/BT/réseau.
"""
import ctypes
import sys
import os


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
