@echo off
setlocal
title OwnDesk Server
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\windows_bootstrap.ps1"
if errorlevel 1 (
  echo OwnDesk Server setup failed or was cancelled. Your data has been retained.
  pause
)
endlocal
