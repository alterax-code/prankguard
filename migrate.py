# Script de nettoyage et migration PrankGuard.
# A executer UNE SEULE FOIS sur ta machine pour :
#   1. Nettoyer main (photos trackees, fichiers inutiles)
#   2. Installer les nouveaux modules src/
#
# Usage :
#   cd C:\Users\lucas\Desktop\CoursEpitech\prankguard
#   python migrate.py

import os
import subprocess
import shutil
import sys


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def run(cmd, description=""):
    print("")
    print("=" * 60)
    print("  " + description)
    print("  > " + cmd)
    print("=" * 60)
    result = subprocess.run(cmd, shell=True, cwd=PROJECT_ROOT, capture_output=True, text=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0 and result.stderr.strip():
        print("  [WARN] " + result.stderr.strip())
    return result.returncode == 0


def main():
    print("")
    print("=" * 60)
    print("  PrankGuard - Script de migration v18 vers src/")
    print("=" * 60)

    # Verifier qu'on est dans le bon dossier
    if not os.path.exists(os.path.join(PROJECT_ROOT, "prankguard.py")):
        print("[ERREUR] prankguard.py introuvable ! Lance ce script depuis la racine du projet.")
        sys.exit(1)

    # Verifier qu'on est sur main
    result = subprocess.run("git branch --show-current", shell=True, cwd=PROJECT_ROOT,
                            capture_output=True, text=True)
    current_branch = result.stdout.strip()
    print("")
    print("  Branche actuelle : " + current_branch)

    if current_branch != "main":
        print("  [INFO] Passage sur main...")
        run("git checkout main", "Checkout main")

    # -- ETAPE 1 : Nettoyer main --

    print("")
    print("=" * 60)
    print("  ETAPE 1 - Nettoyage de main")
    print("=" * 60)

    # Retirer les photos du tracking git (elles restent sur disque)
    run("git rm --cached data/owner_faces/*.jpg 2>nul", "Retirer les .jpg du tracking git")
    run("git rm --cached data/owner_faces/*.png 2>nul", "Retirer les .png du tracking git")

    # Supprimer le doublon legacy/prankguard.py
    legacy_dup = os.path.join(PROJECT_ROOT, "legacy", "prankguard.py")
    if os.path.exists(legacy_dup):
        os.remove(legacy_dup)
        run('git rm --cached "legacy/prankguard.py" 2>nul', "Supprimer legacy/prankguard.py")
        print("  [OK] Supprime : legacy/prankguard.py")

    # -- ETAPE 2 : Copier les nouveaux fichiers --

    print("")
    print("=" * 60)
    print("  ETAPE 2 - Copie des fichiers corriges")
    print("=" * 60)

    dist_dir = os.path.join(PROJECT_ROOT, "_migration_files")
    if not os.path.exists(dist_dir):
        print("  [ERREUR] Dossier _migration_files/ introuvable !")
        print("  Decompresse le zip dans le dossier du projet d'abord.")
        sys.exit(1)

    # Copier .gitignore, requirements.txt, .bat
    for fname in [".gitignore", "requirements.txt", "run.bat", "setup.bat"]:
        src = os.path.join(dist_dir, fname)
        dst = os.path.join(PROJECT_ROOT, fname)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print("  [OK] Copie : " + fname)

    # Copier src/ (les nouveaux modules)
    src_src = os.path.join(dist_dir, "src")
    src_dst = os.path.join(PROJECT_ROOT, "src")

    if os.path.exists(src_src):
        # Supprimer l'ancien src/ s'il existe
        if os.path.exists(src_dst):
            shutil.rmtree(src_dst)
            print("  [OK] Ancien src/ supprime")

        shutil.copytree(src_src, src_dst)
        print("  [OK] Nouveau src/ copie (11 modules)")

    # -- ETAPE 3 : Commit sur main --

    print("")
    print("=" * 60)
    print("  ETAPE 3 - Commit du nettoyage")
    print("=" * 60)

    run("git add -A", "git add -A")
    run('git commit -m "feat: migration v18 vers src/ modulaire avec 10 FIX"', "Commit")

    # -- ETAPE 4 : Resume --

    print("")
    print("=" * 60)
    print("  MIGRATION TERMINEE !")
    print("=" * 60)
    print("")
    print("  Prochaines etapes :")
    print("    1. git push origin main")
    print("    2. Activer le venv : .\\venv312\\Scripts\\activate")
    print("    3. Lancer : python -m src.main")
    print("")


if __name__ == "__main__":
    main()
