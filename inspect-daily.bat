@echo off
setlocal
set "daily_pause=1"
set "daily_observe="
:parse_args
if "%~1"=="" goto args_ok
if "%~1"=="--observe" goto observe_arg
if not "%~1"=="--no-pause" goto bad_args
if "%daily_pause%"=="0" goto bad_args
set "daily_pause=0"
shift /1
goto parse_args
:observe_arg
if defined daily_observe goto bad_args
set "daily_observe=--observe"
shift /1
goto parse_args
:args_ok
set "daily_exit=1"
set "daily_codepage="
for /f "tokens=2 delims=:" %%C in ('chcp') do set "daily_codepage=%%C"
chcp 65001 >nul
pushd "%~dp0"
if errorlevel 1 goto finish
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Existing .venv is required. No installation will be attempted.
  popd
  goto finish
)
set "PYTHONPATH=%~dp0src"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
"%~dp0.venv\Scripts\python.exe" -m pcr_maa.cli inspect-daily --config "%~dp0.runtime\daily-config.json" %daily_observe%
set "daily_exit=%errorlevel%"
popd
:finish
if "%daily_pause%"=="1" (
  echo Press any key to close . . .
  pause >nul
)
if defined daily_codepage chcp %daily_codepage% >nul
endlocal & exit /b %daily_exit%
:bad_args
echo Usage: inspect-daily.bat [--no-pause] [--observe] 1>&2
endlocal & exit /b 2
