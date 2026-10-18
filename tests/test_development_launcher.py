"""Exercise the documented launcher with fake processes and no real app actions."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
param($Launcher, $Scenario, $NoBuild)
$ErrorActionPreference = 'Stop'
$global:reviewActions = @()
$target = Join-Path (Split-Path -Parent (Split-Path -Parent $Launcher)) 'gui/MFAAvalonia/bin/x64/Release/MFAAvalonia.exe'
function Get-Process {
    [CmdletBinding()] param($Name)
    switch ($Scenario) {
        'current' { [pscustomobject]@{ Id=41; Path=$target.ToUpperInvariant() } }
        'other' { [pscustomobject]@{ Id=42; Path=(Join-Path (Split-Path -Parent $Launcher) 'other/MFAAvalonia.exe') } }
        'unreadable' {
            $item = [pscustomobject]@{ Id=43 }
            $item | Add-Member -MemberType ScriptProperty -Name Path -Value { throw 'synthetic path access failure' }
            $item
        }
        'empty' { [pscustomobject]@{ Id=44; Path=$null } }
    }
}
function Stop-Process {
    [CmdletBinding()] param([Parameter(ValueFromPipeline=$true)]$InputObject,[switch]$Force)
    process { if ($null -ne $InputObject) { throw 'must never stop a process' } }
}
function Set-Content { [CmdletBinding()] param($Path,$Value,[switch]$NoNewline); $global:reviewActions += 'write' }
function Copy-Item { [CmdletBinding()] param($Path,$Destination,[switch]$Force); $global:reviewActions += 'copy' }
function Test-Path { param($Path); $true }
function New-Item { [CmdletBinding()] param($ItemType,$Path,$Target); $global:reviewActions += 'new' }
function dotnet { $global:LASTEXITCODE=0; $global:reviewActions += 'build' }
function Start-Process { param($FilePath); $global:reviewActions += 'start' }
$blocked = $false
$failure = ''
try {
    if ($NoBuild -eq 'yes') { & $Launcher -NoBuild } else { & $Launcher }
} catch { $blocked = $true; $failure = $_.Exception.Message }
Write-Output ('RESULT=' + (@{ blocked=$blocked; actions=@($global:reviewActions); failure=$failure } | ConvertTo-Json -Compress))
'''


@pytest.mark.parametrize('scenario,no_build,blocked', [
    ('current', False, True), ('current', True, True),
    ('other', False, False), ('other', True, False),
    ('none', False, False), ('unreadable', False, True), ('empty', True, True),
])
def test_documented_launcher_checks_process_scope_before_any_action(tmp_path, scenario, no_build, blocked):
    pwsh = shutil.which('pwsh')
    if pwsh is None:
        pytest.skip('PowerShell 7 is required for the Windows development launcher')
    document = (ROOT / 'docs/DEVELOPMENT_SETUP.md').read_text(encoding='utf-8')
    match = re.search(r"@'\n(.*?)\n'@ \| Set-Content -Encoding utf8 \.runtime\\run-gui.ps1", document, re.S)
    assert match, 'documented launcher script is missing'
    launcher = tmp_path / '.runtime/run-gui.ps1'
    launcher.parent.mkdir()
    launcher.write_text(match[1], encoding='utf-8')
    harness = tmp_path / 'harness.ps1'
    harness.write_text(HARNESS, encoding='utf-8')
    result = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-File', str(harness),
                             str(launcher), scenario, 'yes' if no_build else 'no'],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    outcome = json.loads(next(line[7:] for line in result.stdout.splitlines() if line.startswith('RESULT=')))
    assert outcome['blocked'] is blocked, outcome
    if blocked:
        assert outcome['actions'] == [], 'guard must precede writes, builds and starts'
    else:
        assert outcome['actions'] == ['write', 'copy', *([] if no_build else ['build']), 'start']
