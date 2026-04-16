# -*- mode: python ; coding: utf-8 -*-
"""
PrankGuard — spec PyInstaller, version Lite (one-dir).
Cible : Windows 64-bit, Python 3.12, dlib/face_recognition, customtkinter.

Usage : pyinstaller build_lite.spec --clean --noconfirm
Ou     : build_lite.bat
"""
from PyInstaller.utils.hooks import collect_data_files
import os

block_cipher = None

# Assets customtkinter — thèmes JSON + images PNG
ctk_datas = collect_data_files('customtkinter')

# Modèles dlib packagés via face_recognition_models (.dat — ~130 MB total)
frm_datas = collect_data_files('face_recognition_models')

# Modèle YuNet (téléchargé à la demande — inclus s'il est présent au moment du build)
yunet_datas = []
if os.path.exists(os.path.join('data', 'models', 'yunet.onnx')):
    yunet_datas = [(os.path.join('data', 'models', 'yunet.onnx'),
                    os.path.join('data', 'models'))]

a = Analysis(
    ['run_app.py'],
    pathex=['.'],
    binaries=[],
    datas=ctk_datas + frm_datas + yunet_datas,
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
        # Numpy (encodings .npz)
        'numpy', 'numpy.core', 'numpy.lib', 'numpy.random',
        # Systray (Sprint 2 — Feature 1)
        'pystray', 'pystray._win32',
        # Chiffrement AES-256 (Sprint 2 — Feature 5)
        'cryptography',
        'cryptography.hazmat.primitives.ciphers.aead',
        'cryptography.hazmat.primitives.kdf.pbkdf2',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.backends',
        'cryptography.hazmat.backends.openssl',
        'cryptography.hazmat.backends.openssl.backend',
        # Argon2id (Vague 2)
        'argon2', 'argon2.low_level',
        'argon2._utils', 'argon2.exceptions',
        # Modules src/
        'src.paths',
        'src.systray',
        'src.anti_spoof',
        'src.intrusion_report',
        'src.email_alert',
        'src.crypto',
        'src.audit',
        'src.watchdog',
        'src.security.hardening',
        # Vague 3
        'src.motion_detector',
        'src.events_db',
        'src.hardware_benchmark',
        # Vague 4
        'src.device_inventory',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Exclure les gros packages scientifiques qui peuvent être tirés transitoirement
    excludes=['matplotlib', 'scipy', 'pandas', 'IPython', 'notebook',
              'pytest', 'setuptools', 'mediapipe'],
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
