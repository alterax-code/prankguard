@echo off
chcp 65001 >nul
echo ============================================
echo  PrankGuard — Extraction pour revue Claude
echo ============================================
echo.

:: Dossier de destination
set DEST=%~dp0partage_claude_doc
if exist "%DEST%" rmdir /s /q "%DEST%"
mkdir "%DEST%"

:: ─────────────────────────────────────────────
:: FICHIERS MODIFIES (Tâche 1 — Bugfix grace period)
:: ─────────────────────────────────────────────
echo [1/6] Fichiers decision + orchestrateur...
if exist "src\agents\decision_agent.py"          copy "src\agents\decision_agent.py"          "%DEST%\src_agents__decision_agent.py" >nul
if exist "src\prankguard.py"                      copy "src\prankguard.py"                      "%DEST%\src__prankguard.py" >nul

:: ─────────────────────────────────────────────
:: FICHIERS MODIFIES (Tâche 2 — Features)
:: ─────────────────────────────────────────────
echo [2/6] Agents modifies...
if exist "src\agents\face_recognition_agent.py"   copy "src\agents\face_recognition_agent.py"   "%DEST%\src_agents__face_recognition_agent.py" >nul
if exist "src\agents\device_monitor.py"           copy "src\agents\device_monitor.py"           "%DEST%\src_agents__device_monitor.py" >nul

echo [3/6] Nouveaux modules...
if exist "src\gui\systray.py"                     copy "src\gui\systray.py"                     "%DEST%\src_gui__systray.py" >nul
if exist "src\gui\gui.py"                         copy "src\gui\gui.py"                         "%DEST%\src_gui__gui.py" >nul
if exist "src\security\audit_trail.py"            copy "src\security\audit_trail.py"            "%DEST%\src_security__audit_trail.py" >nul
if exist "src\security\encryption.py"             copy "src\security\encryption.py"             "%DEST%\src_security__encryption.py" >nul
if exist "src\core\model_downloader.py"           copy "src\core\model_downloader.py"           "%DEST%\src_core__model_downloader.py" >nul
if exist "src\core\hardware_profiler.py"          copy "src\core\hardware_profiler.py"          "%DEST%\src_core__hardware_profiler.py" >nul

:: ─────────────────────────────────────────────
:: TESTS
:: ─────────────────────────────────────────────
echo [4/6] Tests...
if exist "tests\test_decision_agent.py"           copy "tests\test_decision_agent.py"           "%DEST%\tests__test_decision_agent.py" >nul
if exist "tests\conftest.py"                      copy "tests\conftest.py"                      "%DEST%\tests__conftest.py" >nul

:: ─────────────────────────────────────────────
:: DISTRIBUTION
:: ─────────────────────────────────────────────
echo [5/6] Distribution...
if exist "build_lite.py"                          copy "build_lite.py"                          "%DEST%\build_lite.py" >nul
if exist "build_lite.spec"                        copy "build_lite.spec"                        "%DEST%\build_lite.spec" >nul
if exist "installer\prankguard.iss"               copy "installer\prankguard.iss"               "%DEST%\installer__prankguard.iss" >nul
if exist "scripts\prepare_release.py"             copy "scripts\prepare_release.py"             "%DEST%\scripts__prepare_release.py" >nul

:: ─────────────────────────────────────────────
:: CONFIG / DEPS
:: ─────────────────────────────────────────────
echo [6/6] Config et dependances...
if exist "requirements.txt"                       copy "requirements.txt"                       "%DEST%\requirements.txt" >nul
if exist "pyproject.toml"                         copy "pyproject.toml"                         "%DEST%\pyproject.toml" >nul

:: ─────────────────────────────────────────────
:: AGENTS SUPPLEMENTAIRES (contexte complet)
:: ─────────────────────────────────────────────
if exist "src\agents\motion_agent.py"             copy "src\agents\motion_agent.py"             "%DEST%\src_agents__motion_agent.py" >nul
if exist "src\agents\head_pose_agent.py"          copy "src\agents\head_pose_agent.py"          "%DEST%\src_agents__head_pose_agent.py" >nul
if exist "src\agents\gaze_estimation_agent.py"    copy "src\agents\gaze_estimation_agent.py"    "%DEST%\src_agents__gaze_estimation_agent.py" >nul
if exist "src\agents\trajectory_agent.py"         copy "src\agents\trajectory_agent.py"         "%DEST%\src_agents__trajectory_agent.py" >nul
if exist "src\agents\auto_throttle.py"            copy "src\agents\auto_throttle.py"            "%DEST%\src_agents__auto_throttle.py" >nul
if exist "src\agents\__init__.py"                 copy "src\agents\__init__.py"                 "%DEST%\src_agents____init__.py" >nul
if exist "src\gui\__init__.py"                    copy "src\gui\__init__.py"                    "%DEST%\src_gui____init__.py" >nul
if exist "src\security\rgpd.py"                   copy "src\security\rgpd.py"                   "%DEST%\src_security__rgpd.py" >nul

echo.
echo ============================================
echo  Extraction terminee !
echo ============================================
echo.
echo  Dossier : %DEST%
echo.

:: Compter les fichiers extraits
set count=0
for %%f in ("%DEST%\*") do set /a count+=1
echo  %count% fichiers extraits.
echo.
echo  → Ajoute tout le contenu de ce dossier
echo    dans le "Project knowledge" de Claude.
echo.
pause
