@echo off
setlocal DisableDelayedExpansion
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
"%~dp0runtime\python\python.exe" -X utf8 -B "%~dp0scripts\player-maintenance.py" update --player "%~dp0." %*
set "PCR_MAINTENANCE_EXIT=%errorlevel%"
pause
exit /b %PCR_MAINTENANCE_EXIT%
