@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo.
echo ========================================
echo   DEPLOY - Aenderungen live bringen
echo ========================================
echo.
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 ( echo [FEHLER] Kein Git-Repo hier. & pause & exit /b 1 )
for /f "tokens=*" %%b in ('git rev-parse --abbrev-ref HEAD') do set BRANCH=%%b
echo Branch: !BRANCH!
echo.
echo Deine Aenderungen:
git status --short
echo.
git add -A
git diff --cached --quiet
if not errorlevel 1 ( echo [INFO] Nichts Neues zum Hochladen. & pause & exit /b 0 )
set /p MSG="Kurz beschreiben was gemacht wurde (Enter = 'Update'): "
if "!MSG!"=="" set MSG=Update
git commit -m "!MSG!"
echo.
echo [..] Lade zu GitHub (Branch !BRANCH!) ...
git push origin HEAD
if errorlevel 1 ( echo. & echo [FEHLER] Push fehlgeschlagen. Internet da? & pause & exit /b 1 )
echo.
echo [OK] Auf GitHub gepusht. Der Strato-Server zieht sich das automatisch.
echo.
pause
