@echo off
echo ============================================
echo   PrankGuard - Setup
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python non trouvé ! Installez Python 3.12 d'abord.
    pause
    exit /b 1
)

echo [1/3] Création de l'environnement virtuel...
python -m venv venv312
if errorlevel 1 (
    echo [ERROR] Échec de la création du venv
    pause
    exit /b 1
)

echo [2/3] Activation du venv...
call venv312\Scripts\activate.bat

echo [3/3] Installation des dépendances...
echo Cela peut prendre quelques minutes (compilation de dlib)...
pip install --upgrade pip
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo [ERROR] L'installation a échoué !
    echo.
    echo Si dlib échoue, essayez :
    echo   1. Installer Visual Studio Build Tools
    echo   2. Ou : pip install dlib-bin
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Setup terminé !
echo ============================================
echo.
echo Prochaines étapes :
echo   1. Exécutez : enroll.bat    (enregistrer votre visage)
echo   2. Exécutez : run.bat       (démarrer la surveillance)
echo.
pause
