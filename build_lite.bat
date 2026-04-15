@echo off
setlocal EnableDelayedExpansion
title PrankGuard - Build Lite

echo ============================================
echo  PrankGuard - Build Lite (PyInstaller)
echo  One-dir, UAC admin, Windows 64-bit
echo ============================================
echo.

REM Check venv312
if not exist "venv312\Scripts\activate.bat" (
    echo [ERROR] venv312 not found.
    echo Run this script from the PrankGuard project root.
    pause & exit /b 1
)

REM Activate venv
echo [1/4] Activating venv312...
call venv312\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate venv312.
    pause & exit /b 1
)

REM Check / install PyInstaller
echo [2/4] Checking PyInstaller...
venv312\Scripts\python.exe -c "import PyInstaller; print('       PyInstaller', PyInstaller.__version__)" 2>nul
if errorlevel 1 (
    echo       PyInstaller not found - installing...
    venv312\Scripts\pip.exe install --quiet pyinstaller
    if errorlevel 1 (
        echo [ERROR] Failed to install PyInstaller.
        pause & exit /b 1
    )
)

REM Clean previous build
echo [3/4] Cleaning previous build...
if exist "build" rmdir /s /q "build"
if exist "dist\PrankGuard" rmdir /s /q "dist\PrankGuard"

REM Build
echo [4/4] Building (2-5 min)...
echo.
venv312\Scripts\pyinstaller.exe build_lite.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo [FAILED] PyInstaller build failed. Check logs above.
    pause & exit /b 1
)

echo.
echo ============================================
echo  Build OK: dist\PrankGuard\PrankGuard.exe
echo  Size estimate: 200-350 MB (one-dir)
echo  Test: cd dist\PrankGuard ^&^& PrankGuard.exe
echo ============================================
pause
