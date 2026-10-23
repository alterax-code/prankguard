@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo   PrankGuard v3 - installation
echo ============================================

where py >nul 2>&1 && (set "PY=py -3.12") || (set "PY=python")
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo [ERREUR] Python 3.11+ introuvable. Installez-le depuis python.org.
    pause & exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creation de l'environnement virtuel...
    %PY% -m venv .venv || (echo [ERREUR] venv & pause & exit /b 1)
)
echo [2/3] Installation des dependances...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || (echo [ERREUR] pip & pause & exit /b 1)

echo [3/3] Verification des modeles...
".venv\Scripts\python.exe" -c "from prankguard.vision import models as m; [print('  ok', x.filename) for x in m.ALL if m.resolve(x)]" || (echo [ERREUR] modeles & pause & exit /b 1)

echo.
echo Termine. Lancez run.bat
pause
