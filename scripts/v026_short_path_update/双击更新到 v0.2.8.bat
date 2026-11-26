@echo off
setlocal DisableDelayedExpansion
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0install.ps1" -PlayerExe "%~1" -TargetVersion v0.2.8
if not errorlevel 1 exit /b 0
if "%~1"=="" pause
exit /b 1
