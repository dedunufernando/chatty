@echo off
rem Builds DeskPet.exe, a real app you can double-click (no Python needed to run it).
rem Run this once on a Windows PC that has Python. It takes a few minutes.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install it from python.org first, and tick "Add python.exe to PATH".
  pause
  exit /b
)
if not exist ".buildenv\Scripts\python.exe" python -m venv .buildenv
".buildenv\Scripts\python.exe" -m pip install --upgrade pip
".buildenv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo Install failed. Check the internet connection and run this file again.
  pause
  exit /b
)
set ICON=assets\fire.ico
if exist gift_config.json set ICON=assets\water.ico
".buildenv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --name DeskPet --icon "%ICON%" --add-data "assets;assets" main.py
if errorlevel 1 (
  echo Build failed. Scroll up to see the error.
  pause
  exit /b
)
if exist gift_config.json copy /y gift_config.json "dist\DeskPet\gift_config.json" >nul
if exist READ_ME_EXE.html copy /y READ_ME_EXE.html "dist\DeskPet\READ_ME_FIRST.html" >nul
echo.
echo Done! Your app is in dist\DeskPet\DeskPet.exe
echo Zip the whole dist\DeskPet folder to share it.
pause
