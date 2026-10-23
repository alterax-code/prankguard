@echo off
rem Construit dist\PrankGuard\PrankGuard.exe puis, si Inno Setup est présent, l'installeur.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Lancez d'abord setup.bat & pause & exit /b 1)

echo [1/4] Outils de build...
".venv\Scripts\python.exe" -m pip install -q "pyinstaller>=6" || exit /b 1
echo [2/4] Tests...
".venv\Scripts\python.exe" -m pytest -q || (echo [ERREUR] tests en echec & pause & exit /b 1)
echo [3/4] Executable...
".venv\Scripts\python.exe" packaging\make_icon.py >nul || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller packaging\prankguard.spec --noconfirm --clean --distpath dist --workpath build || exit /b 1

echo [4/4] Installeur...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
    "%ISCC%" /Q packaging\installer.iss && echo Installeur : dist\PrankGuard-Setup-3.0.0.exe
) else (
    echo Inno Setup absent : installeur ignore ^(dist\PrankGuard\ reste utilisable tel quel^).
)
echo Termine.
