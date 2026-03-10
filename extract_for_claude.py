"""
PrankGuard — Extraction des fichiers pour revue Claude
Lance depuis la racine du projet : python extract_for_claude.py
"""

import shutil
from pathlib import Path

DEST = Path("partage_claude_doc")

# Nettoyage + création
if DEST.exists():
    shutil.rmtree(DEST)
DEST.mkdir()

# Liste des fichiers à extraire : (source, nom_destination)
FILES = [
    # Tâche 1 — Bugfix grace period
    ("src/agents/decision_agent.py",          "src_agents__decision_agent.py"),
    ("src/prankguard.py",                     "src__prankguard.py"),

    # Tâche 2 — Features
    ("src/agents/face_recognition_agent.py",  "src_agents__face_recognition_agent.py"),
    ("src/agents/device_monitor.py",          "src_agents__device_monitor.py"),
    ("src/gui/systray.py",                    "src_gui__systray.py"),
    ("src/gui/gui.py",                        "src_gui__gui.py"),
    ("src/security/audit_trail.py",           "src_security__audit_trail.py"),
    ("src/security/encryption.py",            "src_security__encryption.py"),
    ("src/core/model_downloader.py",          "src_core__model_downloader.py"),
    ("src/core/hardware_profiler.py",         "src_core__hardware_profiler.py"),

    # Tests
    ("tests/test_decision_agent.py",          "tests__test_decision_agent.py"),
    ("tests/conftest.py",                     "tests__conftest.py"),

    # Distribution
    ("build_lite.py",                         "build_lite.py"),
    ("build_lite.spec",                       "build_lite.spec"),
    ("installer/prankguard.iss",              "installer__prankguard.iss"),
    ("scripts/prepare_release.py",            "scripts__prepare_release.py"),

    # Config
    ("requirements.txt",                      "requirements.txt"),
    ("pyproject.toml",                        "pyproject.toml"),

    # Agents supplémentaires (contexte)
    ("src/agents/motion_agent.py",            "src_agents__motion_agent.py"),
    ("src/agents/head_pose_agent.py",         "src_agents__head_pose_agent.py"),
    ("src/agents/gaze_estimation_agent.py",   "src_agents__gaze_estimation_agent.py"),
    ("src/agents/trajectory_agent.py",        "src_agents__trajectory_agent.py"),
    ("src/agents/auto_throttle.py",           "src_agents__auto_throttle.py"),
    ("src/agents/__init__.py",                "src_agents____init__.py"),
    ("src/gui/__init__.py",                   "src_gui____init__.py"),
    ("src/security/rgpd.py",                  "src_security__rgpd.py"),
]

count = 0
missing = []

for src, dst in FILES:
    src_path = Path(src)
    if src_path.exists():
        shutil.copy2(src_path, DEST / dst)
        count += 1
        print(f"  OK  {src}")
    else:
        missing.append(src)
        print(f"  --  {src} (introuvable)")

print()
print("=" * 50)
print(f"  {count} fichiers extraits dans {DEST}/")
if missing:
    print(f"  {len(missing)} fichiers introuvables")
print("=" * 50)
print()
print("  → Ajoute tout le contenu de ce dossier")
print("    dans le 'Project knowledge' de Claude.")
