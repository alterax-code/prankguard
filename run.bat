@echo off
rem Lance PrankGuard sans console. Ajoutez --dry-run pour simuler les verrouillages.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Environnement absent : lancez d'abord setup.bat
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m prankguard %*
