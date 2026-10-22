@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Chatterbox Studio - Setup / Recovery

if not exist "%~dp0setup_chatterbox.py" (
    echo [FAILED] setup_chatterbox.py is missing.
    pause
    exit /b 2
)

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
    where py >nul 2>&1
    if errorlevel 1 (
        echo [FAILED] Python 3 launcher was not found. Install Python 3 first.
        pause
        exit /b 9009
    )
    set "PYTHON=py -3"
)

echo This is the explicit setup/recovery path. It may initialize or test model files.
echo Your verified backup is not deleted by this launcher.
echo.
%PYTHON% "%~dp0setup_chatterbox.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [FAILED] Setup/recovery exited with code %RC%.
    pause
) else (
    echo.
    echo [OK] Setup/recovery script finished.
    pause
)
exit /b %RC%
