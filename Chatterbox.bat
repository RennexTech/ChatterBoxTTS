@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Chatterbox Studio - Launcher

set "SILENT=0"
if /i "%~1"=="--silent" (
    set "SILENT=1"
    shift
)
set "PROJECT_PY=%~dp0.venv\Scripts\python.exe"
if exist "%PROJECT_PY%" (
    "%PROJECT_PY%" "%~dp0launcher.py" %*
    set "RC=%ERRORLEVEL%"
) else (
    where py >nul 2>&1
    if errorlevel 1 (
        echo [FAILED] Neither the project venv nor the Windows Python Launcher was found.
        echo Run the separate setup/recovery procedure first.
        set "RC=9009"
    ) else (
        py -3 "%~dp0launcher.py" %*
        set "RC=%ERRORLEVEL%"
    )
)

if not "%RC%"=="0" (
    echo.
    echo [FAILED] Chatterbox exited with code %RC%.
    echo Check logs\launcher.log for details.
    if "%SILENT%"=="0" pause
)
exit /b %RC%
