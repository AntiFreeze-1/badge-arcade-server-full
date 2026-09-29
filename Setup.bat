@echo off
rem Installs the Badge Arcade server's packages, creates server\config.json and
rem sets up the 3DS proxy. Safe to run again.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.12 or newer is needed: https://www.python.org/downloads/ & pause & exit /b 1)
python update.py
python install.py
pause
