@echo off
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish.ps1"
if errorlevel 1 (
    echo.
    echo Push failed. See the error above.
    pause
    exit /b 1
)
echo.
echo Done.
pause
