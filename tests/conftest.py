"""Configuration pytest : isole TOUT l'état utilisateur dans un dossier temporaire.

PRANKGUARD_HOME doit être défini AVANT le premier import de `prankguard` (paths.py calcule les
chemins à l'import). Aucun test ne doit toucher au vrai %APPDATA%, au registre, à la webcam ni
verrouiller la vraie session Windows.
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

_HOME = tempfile.mkdtemp(prefix="pgtest_home_")
os.environ["PRANKGUARD_HOME"] = _HOME
atexit.register(shutil.rmtree, _HOME, ignore_errors=True)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import prankguard.paths as _paths  # noqa: E402

if Path(_paths.HOME) != Path(_HOME):          # garde-fou : jamais de tests sur les vraies données
    pytest.exit("PRANKGUARD_HOME non isolé — abandon pour protéger les données utilisateur", returncode=2)

ASSETS = Path(__file__).resolve().parent / "assets"


@pytest.fixture(autouse=True)
def _forbid_dangerous_side_effects(monkeypatch):
    """Filet de sécurité : si un test (ou le code testé) tente de verrouiller la session ou d'écrire
    l'autostart, on échoue bruyamment au lieu de le faire pour de vrai."""
    from prankguard import winsys

    def _boom(*_a, **_k):
        raise AssertionError("appel interdit pendant les tests (effet de bord système réel)")

    monkeypatch.setattr(winsys, "lock_workstation", _boom)
    monkeypatch.setattr(winsys, "set_autostart", _boom)


@pytest.fixture
def assets_dir() -> Path:
    return ASSETS
