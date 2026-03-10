# -*- coding: utf-8 -*-
"""
PrankGuard — Preparation de release

Automatise les etapes de publication :
  1. Bump de version dans pyproject.toml
  2. Generation du changelog depuis les commits
  3. Creation du tag git
  4. Instructions pour l'upload sur GitHub Releases

Usage :
    python scripts/prepare_release.py 3.1.0
    python scripts/prepare_release.py --bump patch
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"


def get_current_version() -> str:
    """Lit la version courante depuis pyproject.toml."""
    content = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'version\s*=\s*"([^"]+)"', content)
    if not match:
        raise ValueError("Version introuvable dans pyproject.toml")
    return match.group(1)


def bump_version(current: str, bump_type: str) -> str:
    """Incremente la version selon le type (major, minor, patch)."""
    parts = current.split(".")
    if len(parts) != 3:
        raise ValueError(f"Format de version invalide : {current}")

    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "patch":
        patch += 1
    else:
        raise ValueError(f"Type de bump invalide : {bump_type}")

    return f"{major}.{minor}.{patch}"


def set_version(new_version: str) -> None:
    """Met a jour la version dans pyproject.toml."""
    content = PYPROJECT.read_text(encoding="utf-8")
    updated = re.sub(
        r'version\s*=\s*"[^"]+"',
        f'version = "{new_version}"',
        content,
    )
    PYPROJECT.write_text(updated, encoding="utf-8")
    print(f"  pyproject.toml : version = \"{new_version}\"")


def generate_changelog(since_tag: str | None = None) -> str:
    """Genere le changelog depuis le dernier tag ou les N derniers commits."""
    cmd = ["git", "log", "--pretty=format:- %s (%h)", "--no-merges"]
    if since_tag:
        cmd.append(f"{since_tag}..HEAD")
    else:
        cmd.append("-20")

    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(ROOT)
    )
    return result.stdout.strip()


def get_last_tag() -> str | None:
    """Retourne le dernier tag git."""
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def create_tag(version: str) -> None:
    """Cree un tag git."""
    tag = f"v{version}"
    subprocess.run(
        ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
        check=True, cwd=str(ROOT),
    )
    print(f"  Tag git cree : {tag}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare une release PrankGuard")
    parser.add_argument(
        "version", nargs="?",
        help="Nouvelle version (ex: 3.1.0) ou utiliser --bump",
    )
    parser.add_argument(
        "--bump", choices=["major", "minor", "patch"],
        help="Type d'increment automatique",
    )
    parser.add_argument(
        "--no-tag", action="store_true",
        help="Ne pas creer de tag git",
    )
    args = parser.parse_args()

    current = get_current_version()
    print(f"Version courante : {current}")

    # Determiner la nouvelle version
    if args.version:
        new_version = args.version
    elif args.bump:
        new_version = bump_version(current, args.bump)
    else:
        print("ERREUR : specifier une version ou --bump (major|minor|patch)")
        return 1

    print(f"Nouvelle version : {new_version}")
    print()

    # 1. Bump version
    print("[1/3] Mise a jour de pyproject.toml...")
    set_version(new_version)

    # 2. Changelog
    print("[2/3] Generation du changelog...")
    last_tag = get_last_tag()
    changelog = generate_changelog(last_tag)
    print()
    print("--- CHANGELOG ---")
    print(changelog)
    print("-----------------")
    print()

    # 3. Tag git
    if not args.no_tag:
        print("[3/3] Creation du tag git...")
        create_tag(new_version)
    else:
        print("[3/3] Tag git : ignore (--no-tag)")

    # Instructions finales
    print()
    print("=" * 60)
    print("RELEASE PRETE")
    print("=" * 60)
    print()
    print("Etapes suivantes :")
    print(f"  1. Verifier les changements : git diff")
    print(f"  2. Committer : git commit -am \"release: v{new_version}\"")
    print(f"  3. Pousser le tag : git push origin v{new_version}")
    print(f"  4. Build lite : python build_lite.py")
    print(f"  5. Upload sur GitHub Releases :")
    print(f"     gh release create v{new_version} dist/PrankGuard.exe \\")
    print(f"       --title \"PrankGuard v{new_version}\" \\")
    print(f"       --notes-file CHANGELOG.md")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
