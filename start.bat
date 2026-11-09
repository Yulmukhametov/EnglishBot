@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Install the project first. See README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m englishbot
pause
