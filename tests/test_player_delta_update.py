"""Synthetic delta updates only: never touch a real player, account or device."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest
from pcr_maa import player_maintenance as maintenance
from test_player_update import archive, encode, manifest, update_case


PATCH = 'patch-manifest.json'


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def delta_case(update_case):
    player, packet, original_old, _, original_private, _ = update_case
    old = {**original_old,
        'scripts/player-maintenance.py': b'# original synthetic launcher\n',
        'src/pcr_maa/player_maintenance.py': b'# original synthetic updater\n'}
    private = {**original_private,
        'config/instances/task-order.json': b'{"order":["abyss","schedule","rewards"]}\n',
        'config/instances/player.json': b'{"selected":["abyss"],"custom":true}\n'}
    for name, data in {**old, **private}.items():
        destination = player / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    old_bytes = encode(manifest(old, 22))
    (player / maintenance.MANIFEST).write_bytes(old_bytes)
    new = {**old,
        'MFAAvalonia.Core.dll': b'updated synthetic core',
        'scripts/player-maintenance.py': b'# updated synthetic launcher\n',
        'src/pcr_maa/player_maintenance.py': b'# updated synthetic updater\n',
        'assets/resource/pipeline/new.json': b'{"synthetic":true}\n'}
    del new['assets/resource/pipeline/old.json']
    case = {'player': player, 'packet': packet, 'old': old, 'new': new,
            'private': private, 'old_bytes': old_bytes}
    write_delta(case)
    return case


def write_delta(case, *, target=None, patch=None, payload=None, extra_entries=()):
    target = deepcopy(target if target is not None else manifest(case['new'], 23))
    target_bytes = encode(target)
    old_manifest = json.loads(case['old_bytes'])
    metadata = {'schema_version': 1, 'product': maintenance.PRODUCT,
        'layout': old_manifest['layout'],
        'base_revision': old_manifest['update']['revision'],
        'base_manifest_sha256': sha(case['old_bytes']),
        'target_revision': target['update']['revision'],
        'target_manifest_sha256': sha(target_bytes)}
    if patch is not None:
        metadata = deepcopy(patch)
    changes = {name: data for name, data in case['new'].items()
               if case['old'].get(name) != data}
    if payload is not None:
        changes = dict(payload)
    with zipfile.ZipFile(case['packet'], 'w', zipfile.ZIP_DEFLATED) as output:
        for name, data in changes.items():
            output.writestr(name, data)
        for name, data in extra_entries:
            output.writestr(name, data)
        output.writestr(maintenance.MANIFEST, target_bytes)
        output.writestr(PATCH, encode(metadata))
    return metadata, target_bytes, changes


def assert_private(case):
    for name, data in case['private'].items():
        assert (case['player'] / name).read_bytes() == data, name


def assert_original(case, *, manifest_bytes=None):
    player = case['player']
    assert (player / maintenance.MANIFEST).read_bytes() == (
        manifest_bytes if manifest_bytes is not None else case['old_bytes'])
    for name, data in case['old'].items():
        assert (player / name).read_bytes() == data, name
    for name in set(case['new']).difference(case['old']):
        assert not (player / name).exists(), name
    assert not (player / PATCH).exists()
    assert_private(case)


def assert_updated(case, result, target_bytes):
    assert result['status'] == 'updated' and result['revision'] == 23
    assert result['manifest_sha256'] == sha(target_bytes)
    assert result['private_state_preserved'] and result['gui_started'] is False
    assert (case['player'] / maintenance.MANIFEST).read_bytes() == target_bytes
    for name, data in case['new'].items():
        assert (case['player'] / name).read_bytes() == data, name
    for name in set(case['old']).difference(case['new']):
        assert not (case['player'] / name).exists(), name
    assert not (case['player'] / PATCH).exists()
    assert_private(case)


def test_delta_changed_added_removed_preserves_all_private_bytes(delta_case, monkeypatch, tmp_path):
    case = delta_case
    _, target_bytes, changes = write_delta(case)
    probes, replacements, staged_inventories = [], [], []
    original_replace = maintenance._replace

    def replace(source, destination):
        if not replacements:
            staged = next(parent for parent in Path(source).parents if parent.name == 'staged')
            staged_inventories.append({p.relative_to(staged).as_posix()
                                      for p in staged.rglob('*') if p.is_file()})
        replacements.append(Path(destination).relative_to(case['player']).as_posix())
        original_replace(source, destination)

    monkeypatch.setattr(maintenance, '_replace', replace)
    result = maintenance.apply_update(case['player'], case['packet'],
        process_probe=lambda player: probes.append(player) or [])
    (tmp_path / 'delta-success.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    assert_updated(case, result, target_bytes)
    assert probes == [case['player'], case['player'], case['player']]
    assert staged_inventories == [set(changes) | {maintenance.MANIFEST}]
    assert replacements == sorted(changes) + [maintenance.MANIFEST]
    assert result['changed_payload_count'] == len(changes)
    assert result['removed_program_files'] == 1
    backup = Path(result['backup_path'])
    expected_backup = set(changes).intersection(case['old']) | (
        set(case['old']).difference(case['new'])) | {maintenance.MANIFEST}
    assert {p.relative_to(backup).as_posix() for p in backup.rglob('*') if p.is_file()} == expected_backup
    assert (backup / maintenance.MANIFEST).read_bytes() == case['old_bytes']
    assert not (backup.parent / 'staged').exists()
    assert not (case['player'] / '.runtime/player-maintenance/update-in-progress.json').exists()


@pytest.mark.parametrize('fault', ['schema', 'product', 'layout', 'base_revision',
    'base_hash', 'target_revision', 'target_hash', 'extra_field', 'missing_field'])
def test_delta_metadata_is_exact_and_bound_to_both_manifests(delta_case, fault):
    patch, _, _ = write_delta(delta_case)
    if fault == 'schema': patch['schema_version'] = True
    if fault == 'product': patch['product'] = 'not-this-player'
    if fault == 'layout': patch['layout'] = 'bound-checkout'
    if fault == 'base_revision': patch['base_revision'] = 21
    if fault == 'base_hash': patch['base_manifest_sha256'] = '0' * 64
    if fault == 'target_revision': patch['target_revision'] = 24
    if fault == 'target_hash': patch['target_manifest_sha256'] = 'f' * 64
    if fault == 'extra_field': patch['removed'] = ['MFAAvalonia.exe']
    if fault == 'missing_field': del patch['target_manifest_sha256']
    write_delta(delta_case, patch=patch)
    with pytest.raises(ValueError):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert_original(delta_case)


def test_delta_same_revision_but_different_installed_manifest_bytes_is_not_the_base(delta_case):
    installed = json.loads(delta_case['old_bytes'])
    different = json.dumps(installed, separators=(',', ':')).encode('utf-8')
    assert different != delta_case['old_bytes']
    (delta_case['player'] / maintenance.MANIFEST).write_bytes(different)
    with pytest.raises(ValueError):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert_original(delta_case, manifest_bytes=different)


@pytest.mark.parametrize('revision', [22, 21])
def test_delta_same_revision_or_downgrade_is_rejected(delta_case, revision):
    write_delta(delta_case, target=manifest(delta_case['new'], revision))
    with pytest.raises(ValueError, match='update_not_newer'):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert_original(delta_case)


@pytest.mark.parametrize('fault', ['missing_changed', 'unchanged', 'extra', 'private',
    'unsafe', 'case_collision', 'hash', 'size', 'reserved_basename'])
def test_delta_inventory_contains_only_exact_changed_program_files(delta_case, fault):
    case = delta_case
    _, _, payload = write_delta(case)
    target = manifest(case['new'], 23)
    entries = []
    if fault == 'missing_changed': del payload['MFAAvalonia.Core.dll']
    if fault == 'unchanged': payload['MFAAvalonia.exe'] = case['new']['MFAAvalonia.exe']
    if fault == 'extra': payload['unexpected.txt'] = b'not owned'
    if fault == 'private': payload['.runtime/ledger/unknown.json'] = b'never install'
    if fault == 'unsafe': payload['../outside.txt'] = b'never install'
    if fault == 'case_collision': entries.append(('mfaavalonia.core.dll', b'case collision'))
    if fault == 'hash': payload['MFAAvalonia.Core.dll'] = b'corrupt core'
    if fault == 'size':
        next(row for row in target['files'] if row['path'] == 'MFAAvalonia.Core.dll')['size'] += 1
    if fault == 'reserved_basename':
        added = {**case['new'], 'assets/PATCH-MANIFEST.JSON': b'not program metadata'}
        target = manifest(added, 23)
        payload['assets/PATCH-MANIFEST.JSON'] = added['assets/PATCH-MANIFEST.JSON']
    write_delta(case, target=target, payload=payload, extra_entries=entries)
    with pytest.raises(ValueError):
        maintenance.apply_update(case['player'], case['packet'], process_probe=lambda _: [])
    assert_original(case)


def test_delta_added_file_cannot_overwrite_unowned_program_file(delta_case):
    destination = delta_case['player'] / 'assets/resource/pipeline/new.json'
    destination.write_bytes(b'user-owned synthetic content')
    with pytest.raises(ValueError, match='unowned_file_conflict'):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert destination.read_bytes() == b'user-owned synthetic content'
    assert (delta_case['player'] / maintenance.MANIFEST).read_bytes() == delta_case['old_bytes']
    assert_private(delta_case)


@pytest.mark.parametrize('reopened', [False, True])
def test_delta_running_before_validation_or_before_commit_cannot_write_program(delta_case, reopened):
    calls = []

    def probe(player):
        calls.append(player)
        return [123] if not reopened or len(calls) == 2 else []

    with pytest.raises(ValueError, match='player_or_agent_running'):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=probe)
    assert len(calls) == (2 if reopened else 1)
    assert_original(delta_case)
    assert not (delta_case['player'] / '.runtime/player-maintenance/update-in-progress.json').exists()


def test_delta_mid_write_failure_rolls_back_program_and_exact_old_manifest(delta_case, monkeypatch):
    original_replace = maintenance._replace
    calls = 0

    def fail_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('synthetic interrupted delta write')
        original_replace(source, destination)

    monkeypatch.setattr(maintenance, '_replace', fail_once)
    with pytest.raises(RuntimeError, match='update_failed_rolled_back'):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert_original(delta_case)
    work = delta_case['player'] / '.runtime/player-maintenance'
    failure = json.loads(next(work.glob('update-*/failure.json')).read_text(encoding='utf-8'))
    assert failure['status'] == 'rolled_back' and failure['rollback_conflicts'] == []
    assert not (work / 'update-in-progress.json').exists()


def test_delta_rollback_preserves_external_change_and_incomplete_marker(delta_case, monkeypatch):
    original_replace = maintenance._replace
    first = None

    def fail_with_conflict(source, destination):
        nonlocal first
        if first is None:
            original_replace(source, destination)
            first = Path(destination)
            return
        first.write_bytes(b'external synthetic change must survive rollback')
        raise OSError('synthetic concurrent write')

    monkeypatch.setattr(maintenance, '_replace', fail_with_conflict)
    with pytest.raises(RuntimeError, match='update_rollback_incomplete'):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert first.read_bytes() == b'external synthetic change must survive rollback'
    assert (delta_case['player'] / maintenance.MANIFEST).read_bytes() == delta_case['old_bytes']
    assert_private(delta_case)
    work = delta_case['player'] / '.runtime/player-maintenance'
    marker_bytes = (work / 'update-in-progress.json').read_bytes()
    failure = json.loads(next(work.glob('update-*/failure.json')).read_text(encoding='utf-8'))
    assert failure['status'] == 'rollback_incomplete'
    assert failure['rollback_conflicts'] == [first.relative_to(delta_case['player']).as_posix()]
    with pytest.raises(ValueError):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert (work / 'update-in-progress.json').read_bytes() == marker_bytes
    assert first.read_bytes() == b'external synthetic change must survive rollback'


def test_delta_previous_incomplete_marker_cannot_be_consumed(delta_case):
    marker = delta_case['player'] / '.runtime/player-maintenance/update-in-progress.json'
    marker.parent.mkdir()
    original = b'{"workspace":"keep-this-existing-marker"}\n'
    marker.write_bytes(original)
    with pytest.raises(ValueError, match='previous_update_incomplete'):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert marker.read_bytes() == original
    assert_original(delta_case)


def test_delta_running_bundled_python_cannot_replace_changed_native_runtime(delta_case, monkeypatch):
    monkeypatch.setattr(maintenance.sys, 'executable', str(delta_case['player'] / 'runtime/python/python.exe'))
    delta_case['new']['runtime/python/python.exe'] = b'changed synthetic native runtime'
    write_delta(delta_case)
    with pytest.raises(ValueError, match='python_runtime_requires_external_offline_update'):
        maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert_original(delta_case)
    assert not (delta_case['player'] / '.runtime/player-maintenance').exists()


def test_delta_running_bundled_python_can_update_its_python_maintainer(delta_case, monkeypatch):
    monkeypatch.setattr(maintenance.sys, 'executable', str(delta_case['player'] / 'runtime/python/python.exe'))
    _, target_bytes, _ = write_delta(delta_case)
    result = maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert_updated(delta_case, result, target_bytes)
    assert (delta_case['player'] / 'runtime/python/python.exe').read_bytes() == delta_case['old']['runtime/python/python.exe']
    assert (delta_case['player'] / 'src/pcr_maa/player_maintenance.py').read_bytes() != delta_case['old']['src/pcr_maa/player_maintenance.py']


def test_full_zip_remains_compatible_when_delta_support_is_enabled(delta_case):
    target = manifest(delta_case['new'], 23)
    archive(delta_case['packet'], delta_case['new'], target)
    result = maintenance.apply_update(delta_case['player'], delta_case['packet'], process_probe=lambda _: [])
    assert_updated(delta_case, result, encode(target))
