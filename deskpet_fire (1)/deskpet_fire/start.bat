@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed yet. Opening the download page...
  echo Please tick the box that says Add python.exe to PATH during install, then run this file again.
  start https://www.python.org/downloads/
  pause
  exit /b
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo Setting things up for the first time, one moment...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
start "" ".venv\Scripts\pythonw.exe" main.py
