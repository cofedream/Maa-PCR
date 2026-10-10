@echo off
setlocal DisableDelayedExpansion
set "DOTNET_ROOT=%~dp0runtime\dotnet"
set "DOTNET_ROOT_X64=%~dp0runtime\dotnet"
set "PYTHONDONTWRITEBYTECODE=1"
cd /d "%~dp0"
"%~dp0MFAAvalonia.exe" %*
exit /b %errorlevel%
