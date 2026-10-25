@echo off
setlocal
title TouchSee Calibration
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_calibration.ps1" -NoPause %*
set "launch_status=%errorlevel%"
if not "%launch_status%"=="0" pause
exit /b %launch_status%
