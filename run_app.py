"""
Point d'entrée PyInstaller pour PrankGuard.
Wrapper nécessaire : src/main.py utilise 'from src.X import Y' qui casse
si PyInstaller lance le script comme __main__ depuis src/.
"""
import sys
import os

# Frozen exe : sys._MEIPASS est déjà dans sys.path.
# Dev (python run_app.py) : dirname(__file__) = racine projet, déjà dans sys.path.
# Ce bloc est une sécurité explicite pour les deux cas.
_root = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from src.main import main

if __name__ == '__main__':
    main()
