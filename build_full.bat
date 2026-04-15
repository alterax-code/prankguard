@echo off
setlocal EnableDelayedExpansion
chcp 65001 > nul
title PrankGuard — Build Full (Inno Setup)

echo ============================================
echo  PrankGuard — Build Full (Installeur .exe)
echo ============================================
echo.

REM Verifier que le build Lite existe
if not exist "dist\PrankGuard\PrankGuard.exe" (
    echo [INFO] Build Lite absent — lancement de build_lite.bat d'abord...
    echo.
    call build_lite.bat
    if errorlevel 1 (
        echo [ERREUR] Build Lite echoue. Arrêt.
        pause & exit /b 1
    )
)

REM Chercher ISCC.exe dans les emplacements standards
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
    echo [ERREUR] Inno Setup introuvable.
    echo.
    echo Telecharger Inno Setup 6 :
    echo   https://jrsoftware.org/isdl.php
    echo.
    echo Puis relancer ce script.
    pause & exit /b 1
)

echo [INFO] Inno Setup : !ISCC!
echo.

REM Creer le dossier output si absent
if not exist "output" mkdir output

REM Compiler le script Inno Setup
echo [BUILD] Compilation en cours...
"!ISCC!" "build_full.iss"

if errorlevel 1 (
    echo.
    echo [ECHEC] Compilation Inno Setup echouee. Voir les logs ci-dessus.
    pause & exit /b 1
)

echo.
echo ============================================
for %%F in (output\PrankGuard_Setup_*.exe) do (
    echo  Installeur : %%F
)
echo ============================================
pause
