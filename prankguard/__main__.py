"""Point d'entrée : python -m prankguard [--background] [--headless] [--debug]"""
import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="prankguard")
    ap.add_argument("--background", action="store_true", help="démarrer réduit dans la zone de notification")
    ap.add_argument("--headless", action="store_true", help="sans fenêtre (serveur + moteur seulement)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="simule les verrouillages (tests)")
    args = ap.parse_args()

    from . import log, winsys
    logger = log.setup(args.debug)

    from .app import PrankGuardApp, signal_existing_instance
    if not winsys.acquire_single_instance():
        logger.info("Instance déjà active — affichage de sa fenêtre")
        signal_existing_instance()
        return 0
    try:
        PrankGuardApp(background=args.background, headless=args.headless, debug=args.debug,
                      dry_run=args.dry_run).run()
    except Exception:
        logger.exception("Erreur fatale")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
