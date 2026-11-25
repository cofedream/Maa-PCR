@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PCR_REPAIR_BUNDLE=%~dp0"
if "%~1"=="" goto choose
if not "%~3"=="" exit /b 2
if "%~2"=="--check" goto check
if not "%~2"=="" exit /b 2
"%~dp0python\python.exe" -I -B -X utf8 "%~dp0scripts\repair_hotfix_baseline.py" --player "%~1"
exit /b %errorlevel%
:check
"%~dp0python\python.exe" -I -B -X utf8 "%~dp0scripts\repair_hotfix_baseline.py" --player "%~1" --check
exit /b %errorlevel%
:choose
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -STA -Command "Add-Type -AssemblyName System.Windows.Forms; $dialog = New-Object System.Windows.Forms.FolderBrowserDialog; $dialog.Description = '选择原 Maa-PCR 安装目录（含 MFAAvalonia.exe）'; $dialog.ShowNewFolderButton = $false; if ($dialog.ShowDialog() -ne 'OK') { exit 2 }; & (Join-Path $env:PCR_REPAIR_BUNDLE 'python/python.exe') -I -B -X utf8 (Join-Path $env:PCR_REPAIR_BUNDLE 'scripts/repair_hotfix_baseline.py') --player $dialog.SelectedPath; exit $LASTEXITCODE"
set "PCR_REPAIR_EXIT=%errorlevel%"
pause
exit /b %PCR_REPAIR_EXIT%
