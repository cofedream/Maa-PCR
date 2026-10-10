"""Stable Maa-PCR Release updates; stdout is one JSON result, never game input."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4
import zipfile

from . import player_maintenance as maintenance

REPOSITORY = 'shiwandehongbai/Maa-PCR'
RELEASE_URL = 'https://api.github.com/repos/' + REPOSITORY + '/releases/latest'
INDEX_NAME = 'update-index.json'
MAX_METADATA = 1024 * 1024
BOOTSTRAP = ('scripts/player-update.py', 'scripts/player-maintenance.py',
    'scripts/recover-player-update.ps1',
    'src/pcr_maa/__init__.py', 'src/pcr_maa/player_update.py',
    'src/pcr_maa/player_maintenance.py')


def _require(condition, error):
    maintenance._require(condition, error)


def _digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _installed(player):
    player = maintenance._root(player)
    data = maintenance._path(player, maintenance.MANIFEST).read_bytes()
    manifest = maintenance._json(data)
    maintenance._rows(manifest)
    maintenance._version(manifest)
    return player, data, manifest


def _asset_url(url, tag, name):
    _require(type(url) is str, 'invalid_release_asset_url')
    parsed = urlsplit(url)
    _require(parsed.scheme == 'https' and parsed.netloc == 'github.com'
        and not parsed.query and not parsed.fragment
        and unquote(parsed.path) == '/' + REPOSITORY + '/releases/download/' + tag + '/' + name,
        'invalid_release_asset_url')
    return url


def _release_assets(release):
    _require(type(release) is dict and release.get('draft') is False
        and release.get('prerelease') is False and type(release.get('tag_name')) is str
        and re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', release['tag_name'])
        and type(release.get('assets')) is list, 'invalid_stable_release')
    assets = {}
    for asset in release['assets']:
        _require(type(asset) is dict and type(asset.get('name')) is str
            and asset['name'] not in assets and type(asset.get('size')) is int
            and 0 < asset['size'] <= maintenance.MAX_PAYLOAD, 'invalid_release_assets')
        name = asset['name']
        maintenance._name(name)
        _require('/' not in name, 'invalid_release_asset_name')
        _asset_url(asset.get('browser_download_url'), release['tag_name'], name)
        assets[name] = asset
    return assets


def validate_index(index, release, old_manifest_bytes):
    """Bind every selected file to this stable Release and the exact installed base."""
    assets = _release_assets(release)
    _require(type(index) is dict and set(index) == {'schema_version', 'product', 'platform',
        'tag', 'version', 'revision', 'target_manifest_sha256', 'full', 'patches'}
        and type(index.get('schema_version')) is int and index['schema_version'] == 1
        and index.get('product') == maintenance.PRODUCT and index.get('platform') == 'win-x64'
        and index.get('tag') == release['tag_name'] and type(index.get('version')) is str
        and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._+-]{0,95}', index['version'])
        and type(index.get('revision')) is int and index['revision'] > 0
        and _digest(index.get('target_manifest_sha256'))
        and type(index.get('patches')) is list and len(index['patches']) <= 1000,
        'invalid_update_index')
    seen, bases = set(), set()

    def asset(record, *, delta=False):
        keys = {'name', 'size', 'sha256'} | ({'base_revision', 'base_manifest_sha256'} if delta else set())
        _require(type(record) is dict and set(record) == keys and type(record.get('name')) is str
            and record['name'].lower().endswith('.zip') and '/' not in record['name']
            and type(record.get('size')) is int and 0 < record['size'] <= maintenance.MAX_PAYLOAD
            and _digest(record.get('sha256')), 'invalid_update_asset')
        maintenance._name(record['name'])
        _require(record['name'] not in seen and record['name'] in assets
            and assets[record['name']]['size'] == record['size'], 'update_asset_missing_or_mismatched')
        seen.add(record['name'])
        if delta:
            _require(type(record.get('base_revision')) is int
                and 0 < record['base_revision'] < index['revision']
                and _digest(record.get('base_manifest_sha256')), 'invalid_patch_base')
            base = (record['base_revision'], record['base_manifest_sha256'])
            _require(base not in bases, 'duplicate_patch_base')
            bases.add(base)
        return {**record, 'url': assets[record['name']]['browser_download_url'],
            'kind': 'delta' if delta else 'full'}

    full = asset(index['full'])
    patches = [asset(row, delta=True) for row in index['patches']]
    old = maintenance._json(old_manifest_bytes)
    maintenance._rows(old)
    installed = maintenance._version(old)
    base_hash = hashlib.sha256(old_manifest_bytes).hexdigest()
    selected = next((row for row in patches if row['base_revision'] == installed['revision']
        and row['base_manifest_sha256'] == base_hash), full)
    return {'status': 'update_available' if index['revision'] > installed['revision'] else 'up_to_date',
        'current_revision': installed['revision'], 'current_version': installed['version'],
        'revision': index['revision'], 'version': index['version'], 'tag': index['tag'],
        'target_manifest_sha256': index['target_manifest_sha256'], 'asset': selected,
        'recovery_required': False}


def _open(url, opener=None):
    request = Request(url, headers={'Accept': 'application/vnd.github+json' if url == RELEASE_URL
        else 'application/octet-stream', 'User-Agent': 'Maa-PCR-player-updater/1'})
    response = (opener or urlopen)(request, timeout=30)
    final = urlsplit(response.geturl())
    allowed = {'api.github.com'} if url == RELEASE_URL else {
        'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}
    if final.scheme != 'https' or final.hostname not in allowed or final.username or final.password:
        response.close()
        raise ValueError('unsafe_update_redirect')
    return response


def _metadata(url, *, opener=None):
    with _open(url, opener) as response:
        data = response.read(MAX_METADATA + 1)
    _require(len(data) <= MAX_METADATA, 'update_metadata_too_large')
    return maintenance._json(data)


def check_update(player, *, opener=None):
    player, old_bytes, old = _installed(player)
    installed = maintenance._version(old)
    marker = maintenance._path(player, '.runtime/player-maintenance/update-in-progress.json', optional=True)
    if marker.exists():
        return {'status': 'recovery_required', 'current_revision': installed['revision'],
            'current_version': installed['version'], 'recovery_required': True}
    release = _metadata(RELEASE_URL, opener=opener)
    assets = _release_assets(release)
    _require(INDEX_NAME in assets and assets[INDEX_NAME]['size'] <= MAX_METADATA, 'update_index_missing')
    index = _metadata(assets[INDEX_NAME]['browser_download_url'], opener=opener)
    return validate_index(index, release, old_bytes)


def validate_archive(player, archive, *, expected=None):
    """Validate before handoff and again outside the installation before committing."""
    player, old_bytes, old = _installed(player)
    old_rows = maintenance._rows(old)
    maintenance._verify(player, old_rows)
    archive = Path(archive).absolute()
    maintenance._root(archive.parent)
    _require(maintenance._plain(archive).st_size <= maintenance.MAX_ARCHIVE, 'archive_too_large')
    with zipfile.ZipFile(archive) as source:
        names = {}
        folded = set()
        for info in source.infolist():
            name = info.filename
            if name not in (maintenance.MANIFEST, maintenance.PATCH):
                maintenance._name(name)
            _require(not info.is_dir() and not info.flag_bits & 1 and name.casefold() not in folded
                and maintenance.stat.S_IFMT(info.external_attr >> 16) in (0, maintenance.stat.S_IFREG),
                'invalid_archive_entry')
            names[name] = info
            folded.add(name.casefold())
        _require(0 < len(names) <= 50002 and maintenance.MANIFEST in names
            and names[maintenance.MANIFEST].file_size <= maintenance.MAX_JSON, 'archive_manifest_missing')
        new_bytes = source.read(maintenance.MANIFEST)
        new = maintenance._json(new_bytes)
        new_rows = maintenance._rows(new)
        version = maintenance._version(new)
        _require(version['revision'] > maintenance._version(old)['revision'], 'update_not_newer')
        _require(old['layout'] == new['layout'], 'update_layout_mismatch')
        if expected is not None:
            _require(version['revision'] == expected['revision'] and version['version'] == expected['version']
                and hashlib.sha256(new_bytes).hexdigest() == expected['target_manifest_sha256'],
                'download_target_mismatch')
        delta = maintenance.PATCH in names
        changed = {name: row for name, row in new_rows.items() if old_rows.get(name) != row}
        if delta:
            _require(names[maintenance.PATCH].file_size <= maintenance.MAX_JSON, 'json_too_large')
            maintenance._patch(maintenance._json(source.read(maintenance.PATCH)), old, old_bytes, new, new_bytes)
        rows = changed if delta else new_rows
        _require(set(names) == set(rows) | {maintenance.MANIFEST}
            | ({maintenance.PATCH} if delta else set()), 'archive_inventory_mismatch')
        if expected is not None:
            _require(('delta' if delta else 'full') == expected['asset']['kind'], 'download_kind_mismatch')
        for name, row in new_rows.items():
            maintenance._path(player, name, optional=True)
            _require(name in old_rows or not (player / name).exists(), 'unowned_file_conflict')
            if name in rows:
                _require(names[name].file_size == row['size'], 'archive_size_mismatch')
                digest = hashlib.sha256()
                with source.open(name) as reader:
                    while block := reader.read(1024 * 1024):
                        digest.update(block)
                _require(digest.hexdigest() == row['sha256'], 'archive_hash_mismatch')
        _require({'MFAAvalonia.exe', 'pcr-repository.txt', 'scripts/player-maintenance.py',
            'src/pcr_maa/player_maintenance.py'}.issubset(new_rows), 'update_payload_incomplete')
        binding = source.read('pcr-repository.txt') if 'pcr-repository.txt' in rows else (
            maintenance._path(player, 'pcr-repository.txt').read_bytes())
        _require(binding == maintenance._path(player, 'pcr-repository.txt').read_bytes(),
            'repository_binding_changed')
    return {'revision': version['revision'], 'version': version['version'],
        'archive_kind': 'delta' if delta else 'full',
        'manifest_sha256': hashlib.sha256(new_bytes).hexdigest()}


def download_update(player, *, opener=None):
    result = check_update(player, opener=opener)
    _require(result['status'] == 'update_available', 'no_update_available')
    player = maintenance._root(player)
    maintenance._path(player, '.runtime/player-maintenance/downloads/placeholder', optional=True)
    directory = player / '.runtime/player-maintenance/downloads' / ('download-' + uuid4().hex)
    directory.mkdir(parents=True)
    maintenance._root(directory)
    asset = result['asset']
    partial = directory / (asset['name'] + '.part')
    archive = directory / asset['name']
    count, digest = 0, hashlib.sha256()
    try:
        with _open(asset['url'], opener) as reader, partial.open('xb') as writer:
            while block := reader.read(1024 * 1024):
                count += len(block)
                _require(count <= asset['size'], 'download_size_mismatch')
                writer.write(block)
                digest.update(block)
                print(json.dumps({'phase': 'download', 'progress': int(count * 100 / asset['size']),
                    'bytes': count, 'total': asset['size']}), file=sys.stderr, flush=True)
        _require(count == asset['size'], 'download_size_mismatch')
        _require(digest.hexdigest() == asset['sha256'], 'download_hash_mismatch')
        validate_archive(player, partial, expected=result)
        os.replace(partial, archive)
    except BaseException:
        if partial.exists():
            partial.unlink()
        raise
    return {'status': 'downloaded', 'archive': str(archive), 'revision': result['revision'],
        'version': result['version'], 'tag': result['tag'], 'sha256': asset['sha256']}


def prepare_worker(player, archive=None, *, recover=False, parent_pid=None, temp_root=None):
    player = maintenance._root(player)
    sources = {}
    if not recover:
        _, manifest_bytes, manifest = _installed(player)
        rows = maintenance._rows(manifest)
        _require(archive is not None, 'update_archive_required')
        validate_archive(player, archive)
    else:
        material = maintenance._recovery_material(player)
        _require(material is not None, 'no_recovery_needed')
        _, _, _, before, manifest_bytes, _, old, _ = material
        manifest = old
        rows = maintenance._rows(old)
        # Assemble one coherent old runtime, not a mix of partially upgraded DLLs.
        for name in rows:
            saved = maintenance._path(before, name, optional=True)
            sources[name] = saved if saved.exists() else maintenance._path(player, name)
    # Copy only inventory-verified program bytes, never private account state.
    copied = {name: row for name, row in rows.items()
        if name.startswith('runtime/python/') or name in BOOTSTRAP}
    _require(set(BOOTSTRAP).issubset(copied) and 'runtime/python/python.exe' in copied,
        'updater_bootstrap_missing')
    for name, row in copied.items():
        source = sources.get(name) or maintenance._path(player, name)
        _require(source.stat().st_size == row['size'] and maintenance._sha(source) == row['sha256'],
            'payload_hash_mismatch')
    temporary = Path(temp_root).absolute() if temp_root else Path(tempfile.gettempdir())
    _require(not temporary.is_relative_to(player), 'updater_requires_external_workspace')
    if not temporary.exists():
        maintenance._root(temporary.parent)
        temporary.mkdir()
    temporary = maintenance._root(temporary)
    base = temporary / 'Maa-PCR-update'
    if base.exists():
        maintenance._root(base)
    else:
        base.mkdir()
    work = base / ('update-' + uuid4().hex)
    work.mkdir()
    for name in copied:
        destination = work / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sources.get(name) or maintenance._path(player, name), destination)
        maintenance._sync_file(destination)
    maintenance._verify(work, copied)
    packet, sha = None, None
    if archive is not None:
        packet = work / 'update.zip'
        _require(maintenance._plain(Path(archive).absolute()).st_size <= maintenance.MAX_ARCHIVE,
            'archive_too_large')
        shutil.copy2(Path(archive).absolute(), packet)
        maintenance._sync_file(packet)
        sha = maintenance._sha(packet)
        _require(sha == maintenance._sha(Path(archive).absolute()), 'update_archive_changed')
    receipt = {'schema_version': 1, 'player': str(player), 'workspace': str(work),
        'python': 'runtime/python/python.exe', 'script': 'scripts/player-update.py',
        'files': list(copied.values()), 'base_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'base_revision': maintenance._version(manifest)['revision']}
    maintenance._path(player, '.runtime/player-maintenance/placeholder', optional=True)
    receipt_root = player / '.runtime/player-maintenance'
    receipt_root.mkdir(parents=True, exist_ok=True)
    recovery_script = receipt_root / 'recover-update.ps1'
    shutil.copy2(work / 'scripts/recover-player-update.ps1', recovery_script)
    maintenance._sync_file(recovery_script)
    recovery_launcher = receipt_root / 'recover-update.bat'
    recovery_launcher.write_text('@echo off\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0recover-update.ps1" '
        '-Player "%~dp0..\\.."\n'
        'pause\n', encoding='ascii', newline='\r\n')
    maintenance._sync_file(recovery_launcher)
    temporary_receipt = receipt_root / ('external-updater-' + uuid4().hex + '.json')
    maintenance._save(temporary_receipt, receipt, durable=True)
    receipt_path = maintenance._path(player, '.runtime/player-maintenance/external-updater.json', optional=True)
    os.replace(temporary_receipt, receipt_path)
    return {'workspace': str(work), 'python': str(work / 'runtime/python/python.exe'),
        'script': str(work / 'scripts/player-update.py'), 'archive': str(packet) if packet else None,
        'archive_sha256': sha, 'recover': recover, 'parent_pid': parent_pid}


def validate_external_worker(player):
    """Verify the pre-update worker even if the installed interpreter cannot start."""
    player = maintenance._root(player)
    receipt = maintenance._read_json(maintenance._path(player,
        '.runtime/player-maintenance/external-updater.json'))
    _require(set(receipt) == {'schema_version', 'player', 'workspace', 'python', 'script', 'files',
        'base_manifest_sha256', 'base_revision'}
        and type(receipt['schema_version']) is int and receipt['schema_version'] == 1
        and receipt['player'] == str(player) and type(receipt['workspace']) is str
        and receipt['python'] == 'runtime/python/python.exe'
        and receipt['script'] == 'scripts/player-update.py'
        and _digest(receipt['base_manifest_sha256']) and type(receipt['base_revision']) is int
        and receipt['base_revision'] > 0, 'invalid_external_updater_receipt')
    material = maintenance._recovery_material(player)
    if material is not None:
        _, intent, _, _, _, _, old, _ = material
        _require(receipt['base_manifest_sha256'] == intent['old_manifest_sha256']
            and receipt['base_revision'] == maintenance._version(old)['revision'],
            'external_updater_base_mismatch')
    work = maintenance._root(receipt['workspace'])
    _require(not work.is_relative_to(player) and work.parent.name == 'Maa-PCR-update'
        and re.fullmatch('update-[0-9a-f]{32}', work.name), 'invalid_external_updater_workspace')
    rows = maintenance._rows({'layout': 'portable-offline-validation',
        'personal_settings_or_ledgers_included': False, 'files': receipt['files']})
    _require(set(BOOTSTRAP).issubset(rows) and 'runtime/python/python.exe' in rows
        and all(name.startswith('runtime/python/') or name in BOOTSTRAP for name in rows),
        'invalid_external_updater_inventory')
    maintenance._verify(work, rows)
    return {'workspace': str(work), 'python': str(work / receipt['python']),
        'script': str(work / receipt['script'])}


def launch_update(player, archive=None, *, recover=False, parent_pid=None, popen=None):
    _require(parent_pid is None or type(parent_pid) is int and parent_pid > 0, 'invalid_parent_pid')
    player = maintenance._root(player)
    if recover:
        _require(maintenance._path(player, '.runtime/player-maintenance/update-in-progress.json',
            optional=True).exists(), 'no_recovery_needed')
    external = maintenance._path(player, '.runtime/player-maintenance/external-updater.json', optional=True)
    bootstrap = (validate_external_worker(player) if recover and external.exists()
        else prepare_worker(player, archive, recover=recover, parent_pid=parent_pid))
    command = [bootstrap['python'], '-X', 'utf8', '-B', bootstrap['script'], 'worker',
        '--player', str(maintenance._root(player))]
    if recover:
        command.append('--recover')
    else:
        command.extend(['--archive', bootstrap['archive'], '--archive-sha256', bootstrap['archive_sha256']])
    if parent_pid is not None:
        command.extend(['--parent-pid', str(parent_pid)])
    log_path = Path(bootstrap['workspace']) / ('worker-' + uuid4().hex + '.log')
    with log_path.open('xb') as log:
        process = (popen or subprocess.Popen)(command, cwd=bootstrap['workspace'], stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, close_fds=True,
            creationflags=0x08000000 if os.name == 'nt' else 0)
    return {'status': 'installer_started', 'worker_pid': process.pid,
        'log_path': str(log_path), 'gui_started': False}


def _parent_running(pid):
    if pid is None:
        return False
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            _require(ctypes.get_last_error() == 87, 'parent_process_inspection_failed')
            return False
        try:
            code = ctypes.c_ulong()
            _require(kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)),
                'parent_process_inspection_failed')
            return code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@contextmanager
def _worker_lock(player):
    player = maintenance._root(player)
    path = maintenance._path(player, '.runtime/player-maintenance/worker.lock', optional=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b'1')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError('updater_worker_running') from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def run_worker(player, archive=None, *, recover=False, parent_pid=None, archive_sha256=None,
        process_probe=None, sleep=None, popen=None, wait_seconds=120):
    with _worker_lock(player):
        return _run_worker(player, archive, recover=recover, parent_pid=parent_pid,
            archive_sha256=archive_sha256, process_probe=process_probe, sleep=sleep,
            popen=popen, wait_seconds=wait_seconds)


def _run_worker(player, archive=None, *, recover=False, parent_pid=None, archive_sha256=None,
        process_probe=None, sleep=None, popen=None, wait_seconds=120):
    player = maintenance._root(player)
    probe, pause = process_probe or maintenance._processes, sleep or time.sleep
    _require(type(wait_seconds) is int and 0 <= wait_seconds <= 600, 'invalid_wait_seconds')
    deadline = time.monotonic() + wait_seconds
    while _parent_running(parent_pid) or probe(player):
        _require(time.monotonic() < deadline, 'player_exit_timeout')
        pause(0.5)
    if recover:
        result = maintenance.recover_update(player, process_probe=probe)
    else:
        _require(archive is not None and _digest(archive_sha256)
            and maintenance._sha(Path(archive)) == archive_sha256, 'update_archive_changed')
        validate_archive(player, archive)
        result = maintenance.apply_update(player, archive, process_probe=probe)
    _require(not probe(player), 'player_reopened_before_restart')
    (popen or subprocess.Popen)([str(player / 'MFAAvalonia.exe'), '--pcr-after-update'],
        cwd=str(player), stdin=subprocess.DEVNULL, close_fds=True,
        creationflags=0x08000000 if os.name == 'nt' else 0)
    return {**result, 'gui_started': True, 'game_tasks_started': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'download', 'launch', 'worker', 'recover'):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument('--player', type=Path, required=True)
        if name in ('launch', 'worker'):
            command.add_argument('--archive', type=Path)
            command.add_argument('--recover', action='store_true')
            command.add_argument('--parent-pid', type=int)
        if name == 'worker':
            command.add_argument('--archive-sha256')
            command.add_argument('--wait-seconds', type=int, default=120)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            result = check_update(args.player)
        elif args.command == 'download':
            result = download_update(args.player)
        elif args.command == 'recover':
            result = launch_update(args.player, recover=True, parent_pid=os.getpid())
        elif args.command == 'launch':
            result = launch_update(args.player, args.archive, recover=args.recover, parent_pid=args.parent_pid)
        else:
            result = run_worker(args.player, args.archive, recover=args.recover,
                parent_pid=args.parent_pid, archive_sha256=args.archive_sha256, wait_seconds=args.wait_seconds)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as error:
        code = str(error) if re.fullmatch('[a-z][a-z0-9_]{2,127}', str(error)) else type(error).__name__
        print(json.dumps({'status': 'failed', 'error': code}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
