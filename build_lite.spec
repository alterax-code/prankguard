# -*- mode: python ; coding: utf-8 -*-
"""
PrankGuard — spec PyInstaller, version Lite (one-dir).
Cible : Windows 64-bit, Python 3.12, dlib/face_recognition, customtkinter.

Usage : pyinstaller build_lite.spec --clean --noconfirm
Ou     : build_lite.bat
"""
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# Assets customtkinter — thèmes JSON + images PNG
ctk_datas = collect_data_files('customtkinter')

# Modèles dlib packagés via face_recognition_models (.dat — ~130 MB total)
frm_datas = collect_data_files('face_recognition_models')

a = Analysis(
    ['run_app.py'],
    pathex=['.'],
    binaries=[],
    datas=ctk_datas + frm_datas,
    hiddenimports=[
        # pywin32 — ctypes + WMI dans src/devices/
        'win32api', 'win32con', 'win32gui', 'win32process',
        'win32security', 'win32service', 'win32event',
        'win32com', 'win32com.client',
        'pywintypes',
        # WMI polling (src/devices/poller.py)
        'wmi',
        # Vision
        'dlib',
        'face_recognition',
        'face_recognition_models',
        'cv2',
        # GUI
        'customtkinter',
        'PIL', 'PIL.Image', 'PIL.ImageTk',
        # Keyboard hook
        'keyboard',
        # Stdlib Windows
        'winsound',
        # Numpy (encodings .npy)
        'numpy', 'numpy.core', 'numpy.lib', 'numpy.random',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclure les gros packages scientifiques qui peuvent être tirés transitoirement
    excludes=['matplotlib', 'scipy', 'pandas', 'IPython', 'notebook',
              'pytest', 'setuptools'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PrankGuard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX désactivé — évite les faux positifs antivirus sur .exe sécurité
    console=False,      # Pas de fenêtre console (app GUI)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,     # Manifeste UAC : requiert droits administrateur
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,          # UPX désactivé pour les DLL/PYD — fiabilité > compression
    name='PrankGuard',
    # PyInstaller 6.x : par défaut contents_directory='_internal'.
    # Laisser le défaut — Inno Setup copie tout récursivement.
)
