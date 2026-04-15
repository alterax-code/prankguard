"""
Point d'entrée PyInstaller pour PrankGuard.
Wrapper nécessaire : src/main.py utilise 'from src.X import Y' qui casse
si PyInstaller lance le script comme __main__ depuis src/.
"""
import sys
import os
import traceback

# Frozen exe : forcer le CWD à côté de l'exe pour que les chemins relatifs
# (data/, config, intrusion_log.txt) fonctionnent correctement.
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))

# Frozen exe : sys._MEIPASS est déjà dans sys.path.
# Dev (python run_app.py) : dirname(__file__) = racine projet, déjà dans sys.path.
# Ce bloc est une sécurité explicite pour les deux cas.
_root = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

if __name__ == '__main__':
    try:
        from src.main import main
        main()
    except Exception:
        # En cas de crash non catchée — écrire le traceback dans un fichier log
        _log_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else '.'
        _log_path = os.path.join(_log_dir, 'prankguard_error.log')
        with open(_log_path, 'w', encoding='utf-8') as _f:
            traceback.print_exc(file=_f)
        raise
