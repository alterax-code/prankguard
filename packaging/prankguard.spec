# PyInstaller — build "one-dir" sans console : pyinstaller packaging/prankguard.spec
from pathlib import Path

root = Path(SPECPATH).parent
pkg = root / "prankguard"

a = Analysis(
    [str(root / "packaging" / "launcher.py")],
    pathex=[str(root)],
    datas=[
        (str(pkg / "web"), "prankguard/web"),
        (str(pkg / "models"), "prankguard/models"),
    ],
    hiddenimports=["pystray._win32", "webview.platforms.winforms", "webview.platforms.edgechromium"],
    excludes=["tkinter", "matplotlib", "pytest", "IPython", "PyQt5", "PySide6", "cefpython3"],
    noarchive=False,
)
# Inutile ici : la capture passe par MSMF/DSHOW (pas FFmpeg), et aucune image AVIF n'est lue.
_DROP = ("opencv_videoio_ffmpeg", "_avif")
a.binaries = [b for b in a.binaries if not any(d in b[0] for d in _DROP)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PrankGuard",
    icon=str(root / "packaging" / "prankguard.ico"),
    console=False,
    upx=False,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PrankGuard", upx=False)
