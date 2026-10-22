"""Local manifest updates and small, explicit diagnostics; never execute game work."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from uuid import uuid4
import zipfile

MANIFEST = 'package-manifest.json'
PATCH = 'patch-manifest.json'
PRODUCT = 'maa-pcr-cn-windows-x64'
PRIVATE_ROOTS = {'.runtime', '.venv', '.git', 'config', 'logs', 'backup', 'debug',
    'temp', 'native-log', 'pcr-alpha-v1', '.pcr-alpha-v1', 'resource', '__pycache__'}
PRIVATE_PARTS = {'.runtime', '.venv', '.git', 'pcr-alpha-v1', '.pcr-alpha-v1',
    'ledger', 'ledgers', 'evidence', 'screenshot', 'screenshots'}
MODULES = ('schedule_onekey', 'event', 'abyss', 'deep', 'rewards', 'very_hard', 'hard', 'normal')
RUN_ID = re.compile(r'run-[0-9a-f]{32}')
MAX_JSON = 16 * 1024 * 1024
MAX_PAYLOAD = 4 * 1024 * 1024 * 1024
MAX_ARCHIVE = MAX_PAYLOAD + 64 * 1024 * 1024
R54_TEST_MANIFEST_SHA256 = '2b653ffdd0c1378497204ef4f1c3f3f48313a3ba39f298d30fad168153280578'


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _plain(path, *, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    _require(expected and not getattr(info, 'st_file_attributes', 0) & 0x400,
             'unsafe_file_path')
    return info


def _root(path):
    path = Path(os.path.abspath(path))
    _require(not str(path).startswith(('\\\\', '//')) and path.resolve() == path, 'unsafe_root')
    for node in (path, *path.parents):
        _plain(node, directory=True)
    return path


def _name(name):
    _require(type(name) is str and name and not any(char in name for char in '\\:*?<>|"'),
             'invalid_payload_path')
    parts = name.split('/')
    _require(not PurePosixPath(name).is_absolute() and all(
        part not in ('', '.', '..') and part.rstrip(' .') == part
        and not re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part)
        for part in parts), 'invalid_payload_path')
    _require(parts[0].casefold() not in PRIVATE_ROOTS
        and not any(part.casefold() in PRIVATE_PARTS for part in parts)
        and name.casefold() != MANIFEST and parts[-1].casefold() != PATCH,
             'private_payload_path')
    return name


def _path(root, name, *, optional=False):
    path = root / name
    for parent in reversed(path.parents):
        if parent == root or parent.is_relative_to(root):
            if parent.exists() or parent.is_symlink():
                _plain(parent, directory=True)
            else:
                _require(optional, 'payload_missing')
    if path.exists() or path.is_symlink():
        _plain(path)
    else:
        _require(optional, 'payload_missing')
    return path


def _json(data):
    _require(len(data) <= MAX_JSON, 'json_too_large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            _require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('invalid_json_number')
    value = json.loads(data, object_pairs_hook=unique, parse_constant=invalid)
    _require(type(value) is dict, 'invalid_json_object')
    return value


def _read_json(path):
    _require(_plain(path).st_size <= MAX_JSON, 'json_too_large')
    return _json(path.read_bytes())


def _rows(manifest):
    _require(manifest.get('personal_settings_or_ledgers_included') is False
        and manifest.get('layout') in ('portable-offline-validation', 'bound-checkout')
        and type(manifest.get('files')) is list and 0 < len(manifest['files']) <= 50000,
        'invalid_player_manifest')
    rows, folded = {}, set()
    for row in manifest['files']:
        _require(type(row) is dict, 'invalid_payload_record')
        name = _name(row.get('path'))
        _require(name.casefold() not in folded and type(row.get('size')) is int
            and 0 <= row['size'] <= MAX_PAYLOAD and type(row.get('sha256')) is str
            and re.fullmatch('[0-9a-f]{64}', row['sha256']) is not None,
            'invalid_payload_record')
        rows[name] = row
        folded.add(name.casefold())
    for name in rows:
        _require(not any('/'.join(name.split('/')[:i]).casefold() in folded
            for i in range(1, len(name.split('/')))), 'payload_path_collision')
    _require(sum(row['size'] for row in rows.values()) <= MAX_PAYLOAD,
             'payload_too_large')
    return rows


def _version(manifest, *, legacy=False):
    update = manifest.get('update')
    if update is None and legacy:
        return {'revision': 0, 'version': 'legacy-unversioned'}
    _require(type(update) is dict and set(update) == {'schema_version', 'product', 'revision', 'version'}
        and update['schema_version'] == 1 and type(update['schema_version']) is int
        and update['product'] == PRODUCT and type(update['revision']) is int
        and update['revision'] > 0 and type(update['version']) is str
        and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._+-]{0,95}', update['version']) is not None,
        'unsupported_update_version')
    return update


def _accepted_promotion(old_version, new):
    new_version = _version(new)
    old_candidate = re.fullmatch(
        rf'v(\d+)\.(\d+)\.(\d+)-r{old_version["revision"]}-candidate', old_version['version'])
    new_release = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', new_version['version'])
    checks = new.get('release_checks')
    return (old_candidate is not None and new_release is not None
        and tuple(map(int, new_release.groups())) >= tuple(map(int, old_candidate.groups()))
        and new.get('status') == 'player_test_release' and new.get('distribution_ready') is True
        and new.get('blockers') == [] and type(checks) is list and bool(checks)
        and all(type(check) is dict and set(check) == {'name', 'sha256'}
            and type(check['name']) is str and bool(check['name'])
            and type(check['sha256']) is str
            and re.fullmatch('[0-9a-f]{64}', check['sha256']) is not None for check in checks))


def _r54_to_r52(transition, old, old_bytes, new, new_bytes):
    if transition is None:
        return False
    _require(type(transition) is tuple and len(transition) == 2
        and all(type(value) is str and re.fullmatch('[0-9a-f]{64}', value)
                for value in transition), 'invalid_r54_release_transition')
    _require(transition[0] == R54_TEST_MANIFEST_SHA256
        and hashlib.sha256(old_bytes).hexdigest() == transition[0]
        and hashlib.sha256(new_bytes).hexdigest() == transition[1],
        'r54_release_manifest_mismatch')
    old_version, new_version = _version(old), _version(new)
    _require(old_version['revision'] == 54 and old_version['version'] == 'v0.1.7-r54-candidate'
        and new_version['revision'] == 52 and _accepted_promotion(old_version, new)
        and old.get('status') == 'release_staging' and old.get('distribution_ready') is False
        and old['layout'] == new['layout'], 'r54_release_transition_not_allowed')
    old_rows, new_rows = _rows(old), _rows(new)
    # The installed interpreter must remain usable through update and recovery.
    _require(all(old_rows.get(name) == new_rows.get(name)
        for name in set(old_rows) | set(new_rows) if name.casefold().startswith('runtime/python/')),
        'r54_release_python_runtime_changed')
    return True


def _same_revision_candidate(candidate_replacement, old, old_bytes, new, new_bytes):
    if candidate_replacement is None:
        return False
    _require(type(candidate_replacement) is tuple and len(candidate_replacement) == 2,
             'invalid_candidate_replacement')
    old_sha, new_sha = candidate_replacement
    _require(type(old_sha) is str and type(new_sha) is str
        and re.fullmatch('[0-9a-f]{64}', old_sha) is not None
        and re.fullmatch('[0-9a-f]{64}', new_sha) is not None
        and old_sha != new_sha, 'invalid_candidate_replacement')
    _require(old_sha == hashlib.sha256(old_bytes).hexdigest()
        and new_sha == hashlib.sha256(new_bytes).hexdigest(),
        'candidate_replacement_manifest_mismatch')
    old_version, new_version = _version(old), _version(new)
    candidate = (new.get('status') == 'release_staging'
        and new.get('distribution_ready') is False
        and old_version['version'] == new_version['version']
        and new_version['version'].endswith('-candidate'))
    _require(old.get('status') == 'release_staging' and old.get('distribution_ready') is False
        and old['layout'] == new['layout']
        and old_version['revision'] == new_version['revision']
        and (candidate or _accepted_promotion(old_version, new)),
        'candidate_replacement_not_allowed')
    return True


def _sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _patch(data, old, old_bytes, new, new_bytes):
    expected = {'schema_version': 1, 'product': PRODUCT, 'layout': old['layout'],
        'base_revision': _version(old)['revision'],
        'base_manifest_sha256': hashlib.sha256(old_bytes).hexdigest(),
        'target_revision': _version(new)['revision'],
        'target_manifest_sha256': hashlib.sha256(new_bytes).hexdigest()}
    _require(set(data) == set(expected) and all(
        type(data[key]) is type(value) and data[key] == value
        for key, value in expected.items()), 'patch_base_or_target_mismatch')
    return expected


def _verify(root, rows):
    for name, row in rows.items():
        path = _path(root, name)
        _require(path.stat().st_size == row['size'] and _sha(path) == row['sha256'],
                 'payload_hash_mismatch')


def _save(path, value, *, durable=False):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        if durable:
            stream.flush()
            os.fsync(stream.fileno())


def _processes(player):
    """Only inspect image paths; do not stop/start processes or expose command lines."""
    _require(os.name == 'nt', 'windows_process_inspection_required')
    code = r'''
$OutputEncoding=[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$items=@(Get-Process -Name MFAAvalonia,python,pythonw,dotnet -ErrorAction SilentlyContinue | ForEach-Object {
    $image=$null; $problem=$null
    try { $image=$_.Path; if ([string]::IsNullOrWhiteSpace($image)) { $problem='path_unavailable' } }
    catch { $problem='path_unavailable' }
    if ($problem) {
        try {
            $native=Get-CimInstance Win32_Process -Filter ('ProcessId = ' + $_.Id) -ErrorAction Stop
            if (-not [string]::IsNullOrWhiteSpace($native.ExecutablePath)) {
                $image=$native.ExecutablePath; $problem=$null
            }
        } catch { }
    }
    [pscustomobject]@{pid=$_.Id;image=$image;error=$problem}
})
ConvertTo-Json -InputObject $items -Compress
'''
    shell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-Command', code],
        capture_output=True, encoding='utf-8', timeout=30, creationflags=0x08000000)
    _require(result.returncode == 0, 'process_inspection_failed')
    values = json.loads(result.stdout)
    _require(type(values) is list and all(type(v) is dict and type(v.get('pid')) is int
        for v in values), 'process_inspection_incomplete')
    values = [value for value in values if value['pid'] != os.getpid()]
    _require(not any(v.get('error') or type(v.get('image')) is not str
        or not os.path.isabs(v['image']) for v in values), 'process_inspection_incomplete')
    images = {os.path.normcase(str(player / name)) for name in (
        'MFAAvalonia.exe', 'runtime/python/python.exe', 'runtime/python/pythonw.exe',
        'runtime/dotnet/dotnet.exe')}
    return [value['pid'] for value in values
        if os.path.normcase(os.path.abspath(value['image'])) in images]


def _replace(source, destination):
    os.replace(source, destination)


def _sync_file(path):
    with path.open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())


def _restore(source, destination):
    # Keep the verified backup usable if rollback itself is interrupted.
    temporary = source.parent / ('.restore-' + uuid4().hex)
    shutil.copy2(source, temporary)
    try:
        _sync_file(temporary)
        _replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_update(player, archive, *, process_probe=None, candidate_replacement=None,
        r54_release_transition=None):
    """Verify a full or base-bound delta ZIP; update owned files transactionally."""
    player = _root(player)
    archive = Path(archive).absolute()
    _root(archive.parent)
    _require(_plain(archive).st_size <= MAX_ARCHIVE, 'archive_too_large')
    probe = process_probe or _processes
    _require(not probe(player), 'player_or_agent_running')
    manifest_path = _path(player, MANIFEST)
    old_bytes = manifest_path.read_bytes()
    old = _json(old_bytes)
    old_rows = _rows(old)
    old_version = _version(old, legacy=True)
    _verify(player, old_rows)
    maintenance = player / '.runtime/player-maintenance'
    _path(player, '.runtime/player-maintenance/placeholder', optional=True)
    if maintenance.exists():
        _root(maintenance)
    marker = maintenance / 'update-in-progress.json'
    _require(not marker.exists() and not marker.is_symlink(), 'previous_update_incomplete')
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        _require(0 < len(infos) <= 50002, 'invalid_archive_inventory')
        names, folded = {}, set()
        for info in infos:
            name = info.filename
            _require(not info.is_dir(), 'unexpected_archive_directory')
            if name not in (MANIFEST, PATCH):
                _name(name)
            _require(name.casefold() not in folded and not info.flag_bits & 1
                and stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG),
                'invalid_archive_entry')
            names[name] = info
            folded.add(name.casefold())
        _require(MANIFEST in names and names[MANIFEST].file_size <= MAX_JSON,
                 'archive_manifest_missing')
        new_bytes = source.read(MANIFEST)
        new = _json(new_bytes)
        new_rows = _rows(new)
        new_version = _version(new)
        _require(candidate_replacement is None or r54_release_transition is None,
            'conflicting_candidate_transitions')
        transition = _r54_to_r52(r54_release_transition, old, old_bytes, new, new_bytes)
        candidate = _same_revision_candidate(candidate_replacement, old, old_bytes, new, new_bytes)
        _require(transition or (candidate and new_version['revision'] == old_version['revision'])
            or (not candidate and new_version['revision'] > old_version['revision']),
            'update_not_newer')
        _require(new['layout'] == old['layout'], 'update_layout_mismatch')
        changed = sorted(name for name in new_rows if old_rows.get(name) != new_rows[name])
        removed = sorted(set(old_rows).difference(new_rows))
        delta = PATCH in names
        if delta:
            _require(names[PATCH].file_size <= MAX_JSON, 'json_too_large')
            _patch(_json(source.read(PATCH)), old, old_bytes, new, new_bytes)
        stage_rows = {name: new_rows[name] for name in changed} if delta else new_rows
        # Windows cannot replace the interpreter/native modules that execute
        # this update. Identical runtimes are skipped; changed native Python
        # needs a separate installer running outside this player's runtime.
        if Path(sys.executable).resolve().is_relative_to(player / 'runtime/python'):
            active_runtime_changes = [name for name in set(old_rows) | set(new_rows)
                if name.casefold().startswith('runtime/python/')
                and Path(name).suffix.casefold() in ('.exe', '.dll', '.pyd')
                and old_rows.get(name) != new_rows.get(name)]
            _require(not active_runtime_changes, 'python_runtime_requires_external_offline_update')
        metadata = {MANIFEST, PATCH} if delta else {MANIFEST}
        _require(set(names) == set(stage_rows) | metadata, 'archive_inventory_mismatch')
        for name, row in new_rows.items():
            if name in stage_rows:
                _require(names[name].file_size == row['size'], 'archive_size_mismatch')
            _path(player, name, optional=True)
            _require(name in old_rows or not (player / name).exists(), 'unowned_file_conflict')
        _require('MFAAvalonia.exe' in new_rows and 'pcr-repository.txt' in new_rows
            and 'scripts/player-maintenance.py' in new_rows
            and 'src/pcr_maa/player_maintenance.py' in new_rows,
            'update_payload_incomplete')
        binding = (source.read('pcr-repository.txt') if 'pcr-repository.txt' in stage_rows
            else _path(player, 'pcr-repository.txt').read_bytes())
        _require(binding == _path(player, 'pcr-repository.txt').read_bytes(),
                 'repository_binding_changed')
        # Only our workspace is added to private runtime. Existing settings,
        # ledgers, logs and referenced evidence are never enumerated or written.
        maintenance.mkdir(parents=True, exist_ok=True)
        _root(maintenance)
        work = maintenance / ('update-' + uuid4().hex)
        staged = work / 'staged'
        backup = work / 'before'
        staged.mkdir(parents=True)
        backup.mkdir()
        for name, row in stage_rows.items():
            destination = staged / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open(name) as reader, destination.open('xb') as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
            _require(destination.stat().st_size == row['size'] and _sha(destination) == row['sha256'],
                     'archive_hash_mismatch')
        (staged / MANIFEST).write_bytes(new_bytes)
    for name in (*changed, *removed, MANIFEST):
        if name == MANIFEST or name in old_rows:
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_path(player, name), destination)
    _require(manifest_path.read_bytes() == old_bytes, 'installed_manifest_changed')
    _verify(player, old_rows)
    _verify(staged, stage_rows)
    _require(not probe(player), 'player_or_agent_running')
    (work / 'target-manifest.json').write_bytes(new_bytes)
    for name in (*changed, MANIFEST):
        _sync_file(staged / name)
    for name in (*changed, *removed, MANIFEST):
        if name == MANIFEST or name in old_rows:
            _sync_file(backup / name)
    _sync_file(work / 'target-manifest.json')
    marker_data = {'workspace': work.name, 'old_manifest_sha256': hashlib.sha256(old_bytes).hexdigest(),
                   'new_manifest_sha256': hashlib.sha256(new_bytes).hexdigest()}
    _save(marker, marker_data, durable=True)  # durable intent precedes every program write
    if probe(player):
        marker.unlink()
        raise ValueError('player_or_agent_running')
    written = []
    try:
        for name in (*changed, *removed, MANIFEST):
            destination = _path(player, name, optional=True)
            previous = hashlib.sha256(old_bytes).hexdigest() if name == MANIFEST else (
                old_rows[name]['sha256'] if name in old_rows else None)
            _require((_sha(destination) if destination.exists() else None) == previous,
                     'immediate_preimage_changed')
            if name in removed:
                destination.unlink()
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                _path(player, name, optional=True)
                _replace(staged / name, destination)
            written.append(name)
        _verify(player, new_rows)
        _require(manifest_path.read_bytes() == new_bytes, 'written_manifest_changed')
        for name in (*changed, MANIFEST):
            _sync_file(_path(player, name))
        result = {'status': 'updated', 'version': new_version['version'],
            'revision': new_version['revision'], 'manifest_sha256': hashlib.sha256(new_bytes).hexdigest(),
            'changed_payload_count': len(changed), 'removed_program_files': len(removed),
            'archive_kind': 'delta' if delta else 'full',
            'base_revision': old_version['revision'],
            'backup_path': str(backup), 'private_state_preserved': True, 'gui_started': False}
        _save(work / 'verification.json', result)
    except BaseException as error:
        conflicts = []
        for name in reversed(written):
            destination = _path(player, name, optional=True)
            expected = hashlib.sha256(new_bytes).hexdigest() if name == MANIFEST else (
                new_rows[name]['sha256'] if name in new_rows else None)
            if (_sha(destination) if destination.exists() else None) != expected:
                conflicts.append(name)
                continue
            if name == MANIFEST or name in old_rows:
                _restore(backup / name, destination)
            elif destination.exists():
                destination.unlink()
        if not conflicts:
            _verify(player, old_rows)
            _require(manifest_path.read_bytes() == old_bytes, 'rollback_manifest_mismatch')
            marker.unlink()
        _save(work / 'failure.json', {'status': 'rollback_incomplete' if conflicts else 'rolled_back',
            'error_type': type(error).__name__, 'written_program_files': written,
            'rollback_conflicts': conflicts, 'private_state_written': False})
        raise RuntimeError('update_rollback_incomplete' if conflicts else 'update_failed_rolled_back') from error
    marker.unlink()
    # Cleanup cannot undo a verified update; preserve changed or busy staging.
    directories = {staged}
    for name, row in {**stage_rows, MANIFEST: {
            'size': len(new_bytes), 'sha256': result['manifest_sha256']}}.items():
        path = staged / name
        directories.update(parent for parent in path.parents
            if parent == staged or parent.is_relative_to(staged))
        try:
            path = _path(staged, name, optional=True)
            if path.exists() and path.stat().st_size == row['size'] and _sha(path) == row['sha256']:
                path.unlink()
        except (OSError, ValueError):
            pass
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        try:
            _root(directory).rmdir()
        except (OSError, ValueError):
            pass
    return result


def _recovery_material(player):
    marker = _path(player, '.runtime/player-maintenance/update-in-progress.json', optional=True)
    if not marker.exists():
        return None
    intent = _read_json(marker)
    _require(set(intent) == {'workspace', 'old_manifest_sha256', 'new_manifest_sha256'}
        and type(intent.get('workspace')) is str
        and re.fullmatch('update-[0-9a-f]{32}', intent['workspace']) is not None
        and all(type(intent.get(key)) is str and re.fullmatch('[0-9a-f]{64}', intent[key])
            for key in ('old_manifest_sha256', 'new_manifest_sha256')), 'invalid_recovery_marker')
    work = _root(marker.parent / intent['workspace'])
    backup = _root(work / 'before')
    old_bytes = _path(backup, MANIFEST).read_bytes()
    new_bytes = _path(work, 'target-manifest.json').read_bytes()
    _require(hashlib.sha256(old_bytes).hexdigest() == intent['old_manifest_sha256']
        and hashlib.sha256(new_bytes).hexdigest() == intent['new_manifest_sha256'],
        'recovery_manifest_hash_mismatch')
    old, new = _json(old_bytes), _json(new_bytes)
    return marker, intent, work, backup, old_bytes, new_bytes, old, new


def recover_update(player, *, process_probe=None, candidate_replacement=None,
        r54_release_transition=None):
    """Roll back an interrupted update only when every owned byte has a known hash."""
    player = _root(player)
    probe = process_probe or _processes
    _require(not probe(player), 'player_or_agent_running')
    material = _recovery_material(player)
    if material is None:
        return {'status': 'no_recovery_needed', 'private_state_preserved': True, 'gui_started': False}
    marker, intent, work, backup, old_bytes, new_bytes, old, new = material
    old_rows, new_rows = _rows(old), _rows(new)
    old_version, new_version = _version(old, legacy=True), _version(new)
    _require(candidate_replacement is None or r54_release_transition is None,
        'conflicting_candidate_transitions')
    transition = _r54_to_r52(r54_release_transition, old, old_bytes, new, new_bytes)
    candidate = _same_revision_candidate(candidate_replacement, old, old_bytes, new, new_bytes)
    _require(old['layout'] == new['layout']
        and (transition or (candidate and new_version['revision'] == old_version['revision'])
        or (not candidate and new_version['revision'] > old_version['revision'])),
        'invalid_recovery_versions')
    changed = sorted(name for name in new_rows if old_rows.get(name) != new_rows[name])
    removed = sorted(set(old_rows).difference(new_rows))
    names = (*changed, *removed, MANIFEST)
    _verify(backup, {name: old_rows[name] for name in (*changed, *removed) if name in old_rows})
    _verify(player, {name: row for name, row in old_rows.items() if new_rows.get(name) == row})
    observed, conflicts = {}, []
    for name in names:
        destination = _path(player, name, optional=True)
        actual = _sha(destination) if destination.exists() else None
        before = intent['old_manifest_sha256'] if name == MANIFEST else (
            old_rows[name]['sha256'] if name in old_rows else None)
        after = intent['new_manifest_sha256'] if name == MANIFEST else (
            new_rows[name]['sha256'] if name in new_rows else None)
        if actual not in (before, after):
            conflicts.append(name)
        observed[name] = (actual, before)
    _require(not conflicts, 'recovery_unknown_conflict')
    _require(not probe(player), 'player_or_agent_running')
    for name in names:
        actual, before = observed[name]
        destination = _path(player, name, optional=True)
        _require((_sha(destination) if destination.exists() else None) == actual,
            'recovery_preimage_changed')
        if actual == before:
            continue
        if before is None:
            destination.unlink()
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _restore(_path(backup, name), destination)
    _verify(player, old_rows)
    _require(_path(player, MANIFEST).read_bytes() == old_bytes
        and all(not (player / name).exists() for name in set(new_rows).difference(old_rows)),
        'recovery_verification_failed')
    result = {'status': 'rolled_back', 'revision': old_version['revision'],
        'version': old_version['version'], 'private_state_preserved': True, 'gui_started': False}
    _save(work / ('recovery-' + uuid4().hex + '.json'), result, durable=True)
    marker.unlink()
    return result


def _error_code(value):
    if type(value) is not str:
        return None
    match = re.match(r'[a-z][a-z0-9_]{2,127}(?=[:\s]|$)', value)
    return match.group() if match else 'business_error_details_retained_locally'


def export_diagnostics(player, repository=None, *, run_id=None, output=None):
    """Export a whitelist summary; no recursive runtime read, ledger or images."""
    player = _root(player)
    if repository is None:
        binding = _path(player, 'pcr-repository.txt').read_text(encoding='utf-8').strip()
        _require(binding, 'empty_repository_binding')
        repository = Path(binding)
        if not repository.is_absolute():
            repository = player / repository
    repository = _root(repository)
    manifest_bytes = _path(player, MANIFEST).read_bytes()
    manifest = _json(manifest_bytes)
    _rows(manifest)
    version = _version(manifest, legacy=True)
    entry, report = {}, {}
    report_root = repository / '.runtime/supported-daily-work'
    if run_id is not None:
        _require(type(run_id) is str and RUN_ID.fullmatch(run_id) is not None, 'invalid_run_id')
        report = _read_json(_path(repository, '.runtime/supported-daily-work/' + run_id + '/report.json'))
        _require(report.get('run_id') == run_id, 'report_run_mismatch')
    else:
        entry_root = repository / '.runtime/alpha-agent'
        if entry_root.exists():
            _root(entry_root)
            candidates = [path for path in entry_root.glob('entry-*.json')
                if re.fullmatch(r'entry-[0-9a-f]{32}\.json', path.name)]
            if candidates:
                entry = _read_json(max(candidates, key=lambda path: _plain(path).st_mtime_ns))
                reference = entry.get('report_path')
                if type(reference) is str:
                    path = Path(reference).absolute()
                    if path.parent.parent == report_root and path.name == 'report.json' and RUN_ID.fullmatch(path.parent.name):
                        if path.is_file():
                            report = _read_json(_path(repository, path.relative_to(repository).as_posix()))
                            _require(report.get('run_id') == path.parent.name, 'report_run_mismatch')
        if not entry and report_root.exists():
            _root(report_root)
            candidates = [path for path in report_root.glob('run-*/report.json')
                if RUN_ID.fullmatch(path.parent.name)]
            if candidates:
                path = max(candidates, key=lambda item: _plain(item).st_mtime_ns)
                report = _read_json(_path(repository, path.relative_to(repository).as_posix()))
                _require(report.get('run_id') == path.parent.name, 'report_run_mismatch')
    combination = entry.get('combination', {})
    selected = report.get('selected_modules', combination.get('selected_modules', []))
    _require(type(selected) is list and all(name in MODULES for name in selected), 'invalid_report_selection')
    steps = []
    for step in report.get('steps', []):
        _require(type(step) is dict and step.get('name') in (*MODULES, 'rewards_final'), 'invalid_report_step')
        result = step.get('result') if type(step.get('result')) is dict else {}
        state = step.get('status')
        _require(type(state) is str and re.fullmatch('[a-z_]{1,64}', state), 'invalid_report_state')
        value = {'name': step['name'], 'status': state}
        for key in ('consumption_sent', 'settlement_verified', 'home_verified'):
            if type(result.get(key)) is bool:
                value[key] = result[key]
        error = _error_code(result.get('reason') or result.get('error') or step.get('error'))
        if error:
            value['error'] = error
        steps.append(value)
    outcome = report.get('outcome', entry.get('outcome', 'no_run'))
    _require(type(outcome) is str and re.fullmatch('[a-z_]{1,64}', outcome), 'invalid_report_outcome')
    actual_run = report.get('run_id')
    _require(actual_run is None or type(actual_run) is str and RUN_ID.fullmatch(actual_run), 'invalid_run_id')
    complete = (outcome == 'complete' and report.get('supported_chain_complete') is True
        and report.get('daily_complete') is False)
    document = {'diagnostic_version': 1, 'player_version': version['version'],
        'revision': version['revision'], 'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'selected_modules': selected, 'rewards_final': report.get('rewards_final', combination.get('rewards_final')) is True,
        'run_id': actual_run, 'outcome': outcome, 'supported_chain_complete': complete,
        'daily_complete': False, 'steps': steps,
        'summary': '本次所选支持项目已安全结束。' if complete else '本次未确认完整结束；未知消费不要直接重跑、删账或换日。',
        'error': _error_code(entry.get('error') or report.get('error')),
        'private_state_included': False, 'sent_to_anyone': False}
    if output is None:
        directory = repository / '.runtime/player-diagnostics'
        if directory.exists():
            _root(directory)
        else:
            _path(repository, '.runtime/player-diagnostics/placeholder', optional=True)
            directory.mkdir(parents=True)
            _root(directory)
        output = directory / ('diagnostic-' + uuid4().hex + '.json')
    else:
        output = Path(output).absolute()
        _root(output.parent)
    _require(not output.exists() and not output.is_symlink(), 'diagnostic_output_exists')
    _save(output, document)
    return {'status': 'diagnostics_exported', 'path': str(output), 'run_id': actual_run,
            'private_state_included': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest='command', required=True)
    update = commands.add_parser('update', allow_abbrev=False)
    update.add_argument('--player', type=Path, required=True)
    update.add_argument('--archive', type=Path)
    recover = commands.add_parser('recover', allow_abbrev=False)
    recover.add_argument('--player', type=Path, required=True)
    diagnostic = commands.add_parser('diagnostics', allow_abbrev=False)
    diagnostic.add_argument('--player', type=Path, required=True)
    diagnostic.add_argument('--repository', type=Path)
    diagnostic.add_argument('--run-id')
    diagnostic.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'update':
            archive = args.archive
            if archive is None:
                print('请先退出 Maa PCR，将可信本地补丁或完整更新 ZIP 拖入此窗口后回车：', file=sys.stderr)
                archive = Path(input().strip().strip('"'))
            from .player_update import launch_update
            result = launch_update(args.player, archive, parent_pid=os.getpid())
        elif args.command == 'recover':
            from .player_update import launch_update
            result = launch_update(args.player, recover=True, parent_pid=os.getpid())
        else:
            result = export_diagnostics(args.player, args.repository, run_id=args.run_id, output=args.output)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, EOFError) as error:
        code = str(error) if re.fullmatch('[a-z][a-z0-9_]{2,127}', str(error)) else type(error).__name__
        print(json.dumps({'status': 'failed', 'error': code}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
