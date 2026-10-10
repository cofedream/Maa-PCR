"""First R38 upgrade: use verified installed Python externally, no system Python."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from uuid import uuid4
import zipfile

sys.dont_write_bytecode = True
PRODUCT = 'maa-pcr-cn-windows-x64'
REQUIRED = {'runtime/python/python.exe', 'scripts/player-maintenance.py',
    'src/pcr_maa/player_maintenance.py', 'src/pcr_maa/__init__.py'}
BOOTSTRAP = {'scripts/player-update.py', 'scripts/player-maintenance.py',
    'scripts/recover-player-update.ps1', 'src/pcr_maa/__init__.py',
    'src/pcr_maa/player_update.py', 'src/pcr_maa/player_maintenance.py'}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read_json(data):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    require(len(data) <= 16 * 1024 * 1024, 'json_too_large')
    value = json.loads(data, object_pairs_hook=pairs)
    require(type(value) is dict, 'invalid_json_object')
    return value


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def sync_file(path):
    with path.open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())


def plain(path, directory=False):
    value = path.lstat()
    require((stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
        and not getattr(value, 'st_file_attributes', 0) & 0x400, 'unsafe_bootstrap_path')


def root(path):
    path = Path(os.path.abspath(path))
    require(not str(path).startswith(('\\\\', '//')) and path.resolve() == path, 'unsafe_bootstrap_root')
    for node in (path, *path.parents):
        plain(node, directory=True)
    return path


def source_file(player, name):
    require(type(name) is str and not any(char in name for char in '\\:*?<>|"')
        and all(part not in ('', '.', '..') and part.rstrip(' .') == part
            for part in name.split('/')), 'unsafe_bootstrap_payload')
    path = player / name
    for parent in path.parents:
        if parent == player or parent.is_relative_to(player):
            plain(parent, directory=True)
    plain(path)
    return path


def prepare_bootstrap(player, archive, metadata, *, temp_root=None):
    player = root(player)
    require(not (player / '.runtime/player-maintenance/update-in-progress.json').exists(),
        'previous_update_incomplete')
    archive = Path(archive).absolute()
    root(archive.parent)
    plain(archive)
    require(type(metadata) is dict and set(metadata) == {'schema_version', 'product',
        'base_revision', 'base_manifest_sha256', 'target_revision', 'target_manifest_sha256', 'archive'}
        and type(metadata['schema_version']) is int and metadata['schema_version'] == 1
        and metadata['product'] == PRODUCT and type(metadata['base_revision']) is int
        and metadata['base_revision'] == 38 and type(metadata['target_revision']) is int
        and metadata['target_revision'] == 39
        and all(type(metadata[key]) is str and re.fullmatch('[0-9a-f]{64}', metadata[key])
            for key in ('base_manifest_sha256', 'target_manifest_sha256')), 'invalid_bootstrap_metadata')
    packet = metadata['archive']
    require(type(packet) is dict and set(packet) == {'name', 'size', 'sha256'}
        and packet.get('name') == archive.name and type(packet.get('size')) is int
        and 0 < packet['size'] <= 4 * 1024 * 1024 * 1024
        and type(packet.get('sha256')) is str and re.fullmatch('[0-9a-f]{64}', packet['sha256']),
        'invalid_bootstrap_archive')
    require(archive.stat().st_size == packet['size'] and sha(archive) == packet['sha256'],
        'bootstrap_archive_hash_mismatch')
    manifest_bytes = source_file(player, 'package-manifest.json').read_bytes()
    require(hashlib.sha256(manifest_bytes).hexdigest() == metadata['base_manifest_sha256'],
        'bootstrap_base_manifest_mismatch')
    manifest = read_json(manifest_bytes)
    require(manifest.get('update', {}).get('revision') == 38
        and manifest['update'].get('product') == PRODUCT and type(manifest.get('files')) is list,
        'bootstrap_not_r38')
    with zipfile.ZipFile(archive) as source:
        require(source.getinfo('package-manifest.json').file_size <= 16 * 1024 * 1024,
            'json_too_large')
        target_bytes = source.read('package-manifest.json')
        require(hashlib.sha256(target_bytes).hexdigest() == metadata['target_manifest_sha256']
            and read_json(target_bytes).get('update', {}).get('revision') == 39,
            'bootstrap_target_manifest_mismatch')
        target = read_json(target_bytes)
        target_rows = {row['path']: row for row in target['files']}
        require(BOOTSTRAP.issubset(target_rows), 'bootstrap_target_updater_missing')
    rows = {row['path']: row for row in manifest['files']
        if row['path'].startswith('runtime/python/') or row['path'] in REQUIRED}
    require(REQUIRED.issubset(rows), 'bootstrap_runtime_missing')
    for name, row in rows.items():
        path = source_file(player, name)
        require(path.stat().st_size == row['size'] and sha(path) == row['sha256'],
            'bootstrap_owned_hash_mismatch')
    temporary = root(temp_root or tempfile.gettempdir())
    require(not temporary.is_relative_to(player), 'bootstrap_requires_external_workspace')
    base = temporary / 'Maa-PCR-update'
    if base.exists():
        root(base)
    else:
        base.mkdir()
    work = base / ('update-' + uuid4().hex)
    work.mkdir()
    for name, row in rows.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file(player, name), path)
        require(path.stat().st_size == row['size'] and sha(path) == row['sha256'],
            'bootstrap_copy_hash_mismatch')
        sync_file(path)
    # Recover-capable R39 code is authenticated by the pinned target manifest,
    # while the external interpreter remains the verified installed R38 runtime.
    with zipfile.ZipFile(archive) as source:
        for name in BOOTSTRAP:
            row = target_rows[name]
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name in source.namelist():
                require(source.getinfo(name).file_size == row['size'], 'bootstrap_target_size_mismatch')
                with source.open(name) as reader, path.open('wb') as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
            else:
                require(name in rows and rows[name] == row, 'bootstrap_target_file_missing')
            require(path.stat().st_size == row['size'] and sha(path) == row['sha256'],
                'bootstrap_target_hash_mismatch')
            rows[name] = row
            sync_file(path)
    external_archive = work / 'update.zip'
    shutil.copy2(archive, external_archive)
    require(sha(external_archive) == packet['sha256'], 'bootstrap_archive_changed')
    sync_file(external_archive)
    maintenance = player / '.runtime/player-maintenance'
    if maintenance.exists():
        root(maintenance)
    else:
        runtime = player / '.runtime'
        if runtime.exists():
            root(runtime)
        else:
            runtime.mkdir()
        maintenance.mkdir()
    recovery_script = maintenance / 'recover-update.ps1'
    if recovery_script.exists():
        plain(recovery_script)
    shutil.copy2(work / 'scripts/recover-player-update.ps1', recovery_script)
    sync_file(recovery_script)
    launcher = maintenance / 'recover-update.bat'
    if launcher.exists():
        plain(launcher)
    launcher.write_text('@echo off\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0recover-update.ps1" '
        '-Player "%~dp0..\\.."\n'
        'pause\n', encoding='ascii', newline='\r\n')
    sync_file(launcher)
    receipt = {'schema_version': 1, 'player': str(player), 'workspace': str(work),
        'python': 'runtime/python/python.exe', 'script': 'scripts/player-update.py',
        'files': list(rows.values()), 'base_manifest_sha256': metadata['base_manifest_sha256'],
        'base_revision': 38}
    receipt_path = maintenance / 'external-updater.json'
    if receipt_path.exists():
        plain(receipt_path)
    receipt_temp = maintenance / ('external-updater-' + uuid4().hex + '.json')
    receipt_temp.write_text(json.dumps(receipt, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    sync_file(receipt_temp)
    os.replace(receipt_temp, receipt_path)
    return {'workspace': str(work), 'command': [str(work / 'runtime/python/python.exe'),
        '-X', 'utf8', '-B', str(work / 'scripts/player-update.py'), 'worker',
        '--player', str(player), '--archive', str(external_archive),
        '--archive-sha256', packet['sha256'], '--parent-pid', str(os.getpid())]}


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
        sys.stderr.reconfigure(encoding='utf-8', errors='strict')
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--player', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plain(args.metadata)
        result = prepare_bootstrap(args.player, args.archive, read_json(args.metadata.read_bytes()))
        log_path = Path(result['workspace']) / 'first-update.log'
        with log_path.open('xb') as log:
            process = subprocess.Popen(result['command'], cwd=result['workspace'], stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, close_fds=True, creationflags=0x08000000 if os.name == 'nt' else 0)
        print(json.dumps({'status': 'installer_started', 'worker_pid': process.pid,
            'log_path': str(log_path), 'gui_started': False}, ensure_ascii=False))
        return 0
    except Exception as error:
        code = str(error) if re.fullmatch('[a-z][a-z0-9_]{2,127}', str(error)) else type(error).__name__
        print(json.dumps({'status': 'failed', 'error': code}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
