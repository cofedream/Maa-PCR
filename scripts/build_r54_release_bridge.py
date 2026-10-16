"""Build an offline R54-test to accepted R52 bridge; do not install or publish."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa import player_maintenance as m
import build_pcr_player_patch as patcher

TOOLS = {'src/pcr_maa/__init__.py', 'src/pcr_maa/player_maintenance.py',
         'src/pcr_maa/player_update.py', 'convert-r54-to-r52.py'}


def build(base_manifest, target, output):
    target = m._root(target)
    base_manifest = Path(base_manifest).absolute()
    m._root(base_manifest.parent)
    old_bytes = m._path(base_manifest.parent, base_manifest.name).read_bytes()
    old = m._json(old_bytes)
    new_bytes = m._path(target, m.MANIFEST).read_bytes()
    new = m._json(new_bytes)
    pair = (m.R54_TEST_MANIFEST_SHA256, hashlib.sha256(new_bytes).hexdigest())
    m._r54_to_r52(pair, old, old_bytes, new, new_bytes)
    rows = m._rows(new)
    # Execute exactly the maintainer shipped with the accepted target.
    for name in TOOLS - {'convert-r54-to-r52.py'}:
        source = m._path(ROOT, name)
        m._require(name in rows and source.stat().st_size == rows[name]['size']
            and m._sha(source) == rows[name]['sha256'], 'bridge_target_tool_mismatch')
    output = Path(os.path.abspath(output))
    m._root(output.parent)
    m._require(not output.exists() and not output.is_symlink(), 'output_exists')
    m._require(not output.is_relative_to(target)
        and not output.is_relative_to(base_manifest.parent), 'output_inside_player')
    output.mkdir()
    patch = patcher.build_patch_from_manifest(base_manifest, target, output / 'update.zip', pair[0],
        r54_release_transition=pair)
    tools = {}
    for name in sorted(TOOLS):
        source = ROOT / ('scripts/' + name if name == 'convert-r54-to-r52.py' else name)
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        tools[name] = m._sha(destination)
        m._require(tools[name] == m._sha(source), 'bridge_tool_changed')
        if name != 'convert-r54-to-r52.py':
            m._require(tools[name] == rows[name]['sha256'], 'bridge_target_tool_mismatch')
    binding = dict(schema_version=1, product=m.PRODUCT, base_manifest_sha256=pair[0],
        target_manifest_sha256=pair[1], archive_sha256=patch['sha256'], tools=tools)
    m._save(output / 'bridge-manifest.json', binding, durable=True)
    powershell = """param([switch]$Recover)
$ErrorActionPreference='Stop'
$OutputEncoding=[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding=[System.Text.UTF8Encoding]::new($false)
$playerPath=Read-Host 'Maa-PCR folder (close GUI and Agent first)'
$pythonPath=Join-Path $playerPath 'runtime/python/python.exe'
if(-not(Test-Path -LiteralPath $pythonPath -PathType Leaf)){throw 'Invalid player folder'}
$converterPath=Join-Path $PSScriptRoot 'convert-r54-to-r52.py'
if($Recover){ & $pythonPath -X utf8 -B $converterPath --player $playerPath --recover }
else { & $pythonPath -X utf8 -B $converterPath --player $playerPath }
exit $LASTEXITCODE
"""
    (output / 'convert.ps1').write_text(powershell, encoding='ascii')
    for name, flag in (('convert.bat', ''), ('recover.bat', ' -Recover')):
        (output / name).write_text('@echo off\nsetlocal DisableDelayedExpansion\n'
            'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0convert.ps1"' + flag
            + '\nset "RESULT=%errorlevel%"\npause\nexit /b %RESULT%\n', encoding='ascii', newline='\r\n')
    shutil.copy2(ROOT / 'docs/R54_TO_R52.md', output / 'README.md')
    archive = output / 'Maa-PCR-R54-test-to-R52-formal-bridge.zip'
    files = sorted(path for path in output.rglob('*') if path.is_file())
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as packet:
        for path in files:
            packet.write(path, path.relative_to(output).as_posix())
    with zipfile.ZipFile(archive) as packet:
        m._require(packet.testzip() is None and len(packet.namelist()) == len(files), 'bridge_zip_invalid')
        for path in files:
            with packet.open(path.relative_to(output).as_posix()) as stream:
                m._require(hashlib.file_digest(stream, 'sha256').hexdigest() == m._sha(path),
                    'bridge_zip_hash_mismatch')
    return dict(status='offline_bridge_built', archive=str(archive), sha256=m._sha(archive),
        base_manifest_sha256=pair[0], target_manifest_sha256=pair[1], patch=patch,
        gui_started=False, published=False, private_state_included=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--base-manifest', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = build(args.base_manifest, args.target, args.output)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
