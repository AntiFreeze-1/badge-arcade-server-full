@echo off
rem Opens the Badge Arcade Manager window (setup, server, proxy, hotspot, machines,
rem free plays, saves). Runs the one-time setup first if it hasn't been done.
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.12 or newer is needed: https://www.python.org/downloads/ & pause & exit /b 1)
python -c "import Crypto, nintendo" 2>nul || (python install.py || (pause & exit /b 1))
where pythonw >nul 2>nul && (start "" pythonw manager.py) || (python manager.py || pause)
