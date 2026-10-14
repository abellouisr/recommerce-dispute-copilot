@echo off
cd /d "%~dp0"
title Dispute Copilot
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 run.py
    goto end
)
where python >nul 2>nul
if %errorlevel%==0 (
    python run.py
    goto end
)
echo.
echo Python was not found on this computer.
echo Install Python 3.10 or newer from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during installation. Then run this file again.
:end
echo.
pause
