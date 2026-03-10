# -*- coding: utf-8 -*-
"""
PrankGuard — Script de build lite

Lance PyInstaller avec le fichier .spec pour generer PrankGuard.exe.
Version Lite : ~50-80 MB, modeles IA telecharges au premier lancement.

Usage :
    python build_lite.py
"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).parent
    spec_file = root / "build_lite.spec"

    if not spec_file.exists():
        print(f"ERREUR : {spec_file} introuvable")
        return 1

    # Verifier que PyInstaller est installe
    try:
        import PyInstaller
        print(f"PyInstaller {PyInstaller.__version__} detecte")
    except ImportError:
        print("PyInstaller non installe. Installation...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    print("=" * 60)
    print("PrankGuard — Build Lite")
    print("=" * 60)
    print(f"  Spec file : {spec_file}")
    print(f"  Sortie    : dist/PrankGuard.exe")
    print()

    # Lancer PyInstaller
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(spec_file), "--clean"],
        cwd=str(root),
    )

    if result.returncode == 0:
        exe_path = root / "dist" / "PrankGuard.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print()
            print("=" * 60)
            print(f"BUILD REUSSI")
            print(f"  Executable : {exe_path}")
            print(f"  Taille     : {size_mb:.1f} MB")
            print("=" * 60)
        return 0
    else:
        print()
        print("ERREUR : le build a echoue")
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
