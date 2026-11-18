@echo off
setlocal
title OwnDesk
set "OWNDESK_PYTHON="
for %%P in (
  "%~dp0runtime\python.exe"
  "%~dp0.runtime\venv\Scripts\python.exe"
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%ProgramFiles%\Python312\python.exe"
  "%UserProfile%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
) do (
  if not defined OWNDESK_PYTHON if exist "%%~P" (
    "%%~P" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
    if not errorlevel 1 set "OWNDESK_PYTHON=%%~P"
  )
)
if defined OWNDESK_PYTHON goto run
py -3.12 -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 goto python_launcher
echo Python 3.12 was not found. Install Python 3.12 from https://www.python.org/downloads/windows/
echo Then open Setup-OwnDesk.cmd again.
goto finished

:run
for %%P in ("%OWNDESK_PYTHON%") do set "OWNDESK_PYTHONW=%%~dpPpythonw.exe"
if exist "%OWNDESK_PYTHONW%" (
  "%OWNDESK_PYTHON%" -c "import tkinter" >nul 2>&1
  if not errorlevel 1 (
    start "" "%OWNDESK_PYTHONW%" "%~dp0setup_window.py"
    exit /b 0
  )
)
"%OWNDESK_PYTHON%" "%~dp0setup_window.py"
goto finished

:python_launcher
py -3.12 "%~dp0setup_window.py"

:finished
echo.
echo OwnDesk stopped. Startup log: "%~dp0.runtime\startup.log"
pause
endlocal
