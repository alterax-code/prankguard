@echo off
setlocal EnableDelayedExpansion
title PrankGuard - Build Full (Inno Setup)

echo ============================================
echo  PrankGuard - Build Full (Installer .exe)
echo ============================================
echo.

REM Check Lite build exists
if not exist "dist\PrankGuard\PrankGuard.exe" (
    echo [INFO] Lite build missing - running build_lite.bat first...
    echo.
    call build_lite.bat
    if errorlevel 1 (
        echo [ERROR] Lite build failed. Stopping.
        pause & exit /b 1
    )
)

REM Find ISCC.exe
set "ISCC="
for %%P in (
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    "C:\Program Files\Inno Setup 6\ISCC.exe"
    "C:\Program Files (x86)\Inno Setup 5\ISCC.exe"
    "C:\Program Files\Inno Setup 5\ISCC.exe"
) do (
    if exist %%P (
        if "!ISCC!"=="" set "ISCC=%%~P"
    )
)

if "!ISCC!"=="" (
    echo [ERROR] Inno Setup not found.
    echo.
    echo Download Inno Setup 6:
    echo   https://jrsoftware.org/isdl.php
    echo.
    echo Then run this script again.
    pause & exit /b 1
)

echo [INFO] Inno Setup: !ISCC!
echo.

REM Create output dir
if not exist "output" mkdir output

REM Compile
echo [BUILD] Compiling Inno Setup script...
"!ISCC!" "build_full.iss"

if errorlevel 1 (
    echo.
    echo [FAILED] Inno Setup compilation failed. Check logs above.
    pause & exit /b 1
)

echo.
echo ============================================
for %%F in (output\PrankGuard_Setup_*.exe) do (
    echo  Installer: %%F
)
echo ============================================
pause
