@echo off
echo ============================================
echo   PrankGuard - Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.10+ first.@echo off
echo ============================================
echo   PrankGuard - Setup
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.12 first.
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment...
python -m venv venv312
if errorlevel 1 (
    echo [ERROR] Failed to create venv
    pause
    exit /b 1
)

echo [2/3] Activating venv...
call venv312\Scripts\activate.bat

echo [3/3] Installing dependencies...
pip install --upgrade pip
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed!
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Setup Complete!
echo ============================================
echo.
echo Next steps:
echo   1. Run: enroll.bat    (register your face)
echo   2. Run: run.bat       (start monitoring)
echo.
pause
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo [ERROR] Failed to create venv
    pause
    exit /b 1
)

echo [2/3] Activating venv...
call venv\Scripts\activate.bat

echo [3/3] Installing dependencies...
echo This may take a few minutes (dlib compilation)...
pip install --upgrade pip
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed!
    echo.
    echo If dlib fails, try:
    echo   1. Install Visual Studio Build Tools
    echo   2. Or: pip install dlib-bin
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Setup Complete!
echo ============================================
echo.
echo Next steps:
echo   1. Run: enroll.bat    (register your face)
echo   2. Run: run.bat       (start monitoring)
echo.
pause
