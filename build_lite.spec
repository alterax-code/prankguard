# -*- mode: python ; coding: utf-8 -*-
"""
PrankGuard — Build Lite (PyInstaller .spec)

Genere un .exe standalone (~50-80 MB) SANS les modeles IA.
Les modeles sont telecharges au premier lancement via model_downloader.

Usage :
    pip install pyinstaller
    pyinstaller build_lite.spec

Sortie : dist/PrankGuard.exe
"""

import sys
from pathlib import Path

block_cipher = None

# Repertoire racine du projet
ROOT = Path(SPECPATH)

a = Analysis(
    [str(ROOT / 'src' / 'main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Inclure les fichiers de donnees necessaires
        (str(ROOT / 'data'), 'data'),
    ],
    hiddenimports=[
        'customtkinter',
        'PIL',
        'cv2',
        'numpy',
        'mediapipe',
        'insightface',
        'onnxruntime',
        'cryptography',
        'pystray',
        'win32gui',
        'win32con',
        'wmi',
        'psutil',
        'src',
        'src.agents',
        'src.agents.decision_agent',
        'src.agents.face_recognition_agent',
        'src.agents.motion_agent',
        'src.agents.head_pose_agent',
        'src.agents.trajectory_agent',
        'src.agents.gaze_estimation_agent',
        'src.agents.auto_throttle',
        'src.agents.device_monitor',
        'src.core',
        'src.core.hardware_profiler',
        'src.core.model_downloader',
        'src.gui',
        'src.gui.gui',
        'src.gui.systray',
        'src.security',
        'src.security.encryption',
        'src.security.audit_trail',
        'src.security.locker',
        'src.security.rgpd',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclure les modeles lourds (telecharges au premier lancement)
        'insightface.model_zoo',
        'tensorflow',
        'torch',
        'torchvision',
        'scipy',
        'matplotlib',
        'pandas',
        'jupyter',
        'IPython',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PrankGuard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # Application GUI, pas de console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # TODO: ajouter une icone .ico
    version=None,
)
