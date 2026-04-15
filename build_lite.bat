@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul
title PrankGuard — Build Lite

echo ============================================
echo  PrankGuard — Build Lite (PyInstaller)
echo  One-dir, UAC admin, Windows 64-bit
echo ============================================
echo.

REM Verifier presence venv312
if not exist "venv312\Scripts\activate.bat" (
    echo [ERREUR] venv312 introuvable.
    echo Lancer ce script depuis la racine du projet PrankGuard.
    pause & exit /b 1
)

REM Activation venv
echo [1/4] Activation venv312...
call venv312\Scripts\activate.bat
if errorlevel 1 (
    echo [ERREUR] Echec activation venv312.
    pause & exit /b 1
)

REM Verifier / installer PyInstaller
echo [2/4] Verification PyInstaller...
python -c "import PyInstaller; print('       PyInstaller', PyInstaller.__version__)" 2>nul
if errorlevel 1 (
    echo       PyInstaller absent — installation...
    pip install --quiet pyinstaller
    if errorlevel 1 (
        echo [ERREUR] Impossible d'installer PyInstaller.
        pause & exit /b 1
    )
)

REM Nettoyage builds precedents
echo [3/4] Nettoyage builds precedents...
if exist "build" rmdir /s /q "build"
if exist "dist\PrankGuard" rmdir /s /q "dist\PrankGuard"

REM Build PyInstaller
echo [4/4] Build en cours (2-5 min selon la machine)...
echo.
pyinstaller build_lite.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo [ECHEC] Build PyInstaller echoue. Voir les logs ci-dessus.
    pause & exit /b 1
)

echo.
echo ============================================
echo  Build OK : dist\PrankGuard\PrankGuard.exe
echo.
echo  Taille estimee : 200-350 MB (one-dir)
echo  Tester : cd dist\PrankGuard ^&^& PrankGuard.exe
echo ============================================
pause
