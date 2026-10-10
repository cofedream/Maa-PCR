"""Isolated synthetic player updates: no real install, device or executable."""
import hashlib
import json
import os
from pathlib import Path
import zipfile

import pytest
from pcr_maa import player_maintenance as maintenance


def record(name, data):
    return {'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def manifest(payload, revision=None, *, layout='portable-offline-validation'):
    value = {'layout': layout, 'distribution_ready': False,
        'personal_settings_or_ledgers_included': False,
        'files': [record(name, data) for name, data in sorted(payload.items())]}
    if revision is not None:
        value['update'] = {'schema_version': 1, 'product': maintenance.PRODUCT,
            'revision': revision, 'version': 'r' + str(revision) + '-pilot-candidate'}
    return value


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def archive(path, payload, metadata):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as output:
        for name, data in payload.items():
            output.writestr(name, data)
        output.writestr(maintenance.MANIFEST, encode(metadata))
    return path


@pytest.fixture
def update_case(tmp_path):
    player = tmp_path / '玩家 中文 space'
    player.mkdir()
    old = {'MFAAvalonia.exe': b'synthetic GUI never execute',
        'MFAAvalonia.Core.dll': b'old synthetic core',
        'pcr-repository.txt': b'.', 'runtime/python/python.exe': b'not executable',
        'assets/resource/pipeline/old.json': b'old program can retire'}
    private = {'config/instances/player.json': b'private settings',
        '.runtime/schedule-onekey-work/ledger/unknown.json': b'{"state":"in_flight"}',
        '.runtime/evidence/original.png': b'private evidence',
        'logs/old.log': b'private log', 'pcr-alpha-v1/journal/intent.json': b'unknown'}
    for name, data in {**old, **private}.items():
        path = player / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    old_bytes = encode(manifest(old, 7))
    (player / maintenance.MANIFEST).write_bytes(old_bytes)
    new = {**old, 'MFAAvalonia.Core.dll': b'new synthetic core',
        'src/pcr_maa/player_maintenance.py': b'# new offline tool',
        'scripts/player-maintenance.py': b'# new offline launcher',
        '资源/中文 空格.txt': '中文'.encode('utf-8')}
    del new['assets/resource/pipeline/old.json']
    packet = archive(tmp_path / '更新包 中文 space.zip', new, manifest(new, 8))
    return player, packet, old, new, private, old_bytes


def assert_private(case):
    for name, data in case[4].items():
        assert (case[0] / name).read_bytes() == data


def test_complete_unicode_update_preserves_settings_unknown_and_evidence(update_case):
    player, packet, old, new, _, _ = update_case
    calls = []
    result = maintenance.apply_update(player, packet, process_probe=lambda p: calls.append(p) or [])
    assert result['status'] == 'updated' and result['revision'] == 8
    assert result['gui_started'] is False and calls == [player, player, player]
    for name, data in new.items():
        assert (player / name).read_bytes() == data
    assert not (player / 'assets/resource/pipeline/old.json').exists()
    assert_private(update_case)
    backup = Path(result['backup_path'])
    assert (backup / 'MFAAvalonia.Core.dll').read_bytes() == old['MFAAvalonia.Core.dll']
    assert not list(backup.rglob('unknown.json'))
    assert not (player / '.runtime/player-maintenance/update-in-progress.json').exists()
    with pytest.raises(ValueError, match='update_not_newer'):
        maintenance.apply_update(player, packet, process_probe=lambda _: [])
    assert_private(update_case)


def test_mid_write_failure_restores_old_program_and_exact_manifest(update_case, monkeypatch):
    player, packet, old, new, _, old_bytes = update_case
    replace = maintenance._replace
    count = 0
    def fail_once(source, destination):
        nonlocal count
        count += 1
        if count == 3:
            raise OSError('synthetic interrupted replace')
        return replace(source, destination)
    monkeypatch.setattr(maintenance, '_replace', fail_once)
    with pytest.raises(RuntimeError, match='update_failed_rolled_back'):
        maintenance.apply_update(player, packet, process_probe=lambda _: [])
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    for name, data in old.items():
        assert (player / name).read_bytes() == data
    for name in set(new).difference(old):
        assert not (player / name).exists()
    assert_private(update_case)
    receipt = next((player / '.runtime/player-maintenance').glob('update-*/failure.json'))
    assert json.loads(receipt.read_text(encoding='utf-8'))['status'] == 'rolled_back'


@pytest.mark.parametrize('reopen', [False, True])
def test_exact_player_running_or_reopened_refuses_all_program_writes(update_case, reopen):
    player, packet, old, _, _, old_bytes = update_case
    calls = 0
    def probe(_):
        nonlocal calls
        calls += 1
        return [123] if not reopen or calls == 2 else []
    with pytest.raises(ValueError, match='player_or_agent_running'):
        maintenance.apply_update(player, packet, process_probe=probe)
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    for name, data in old.items():
        assert (player / name).read_bytes() == data
    assert_private(update_case)


@pytest.mark.parametrize('fault', ['hash', 'size', 'older', 'product', 'schema', 'binding', 'layout', 'extra'])
def test_bad_update_packet_cannot_write_program_or_private_state(update_case, fault):
    player, packet, old, new, _, old_bytes = update_case
    metadata = manifest(new, 8)
    payload = dict(new)
    if fault == 'hash': payload['MFAAvalonia.Core.dll'] = b'bad synthetic core'
    if fault == 'size': metadata['files'][0]['size'] += 1
    if fault == 'older': metadata['update']['revision'] = 6
    if fault == 'product': metadata['update']['product'] = 'other-product'
    if fault == 'schema': metadata['update']['schema_version'] = 2
    if fault == 'binding':
        payload['pcr-repository.txt'] = b'C:/other-root'
        metadata = manifest(payload, 8)
    if fault == 'layout': metadata['layout'] = 'bound-checkout'
    if fault == 'extra': payload['not-in-manifest.txt'] = b'unknown'
    packet.unlink()
    archive(packet, payload, metadata)
    with pytest.raises(ValueError):
        maintenance.apply_update(player, packet, process_probe=lambda _: [])
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    for name, data in old.items(): assert (player / name).read_bytes() == data
    assert_private(update_case)


@pytest.mark.parametrize('name', ['../outside.txt', r'C:\outside.txt', '.runtime/ledger/unknown.json',
    'CONFIG/private.json', 'assets/../private.json', 'assets/NUL.txt', 'assets/name.',
    'assets/runtime/ledger/unknown.json', 'assets/original/evidence/frame.png'])
def test_unsafe_or_private_payload_is_rejected(update_case, name):
    player, packet, _, new, _, old_bytes = update_case
    new[name] = b'must not install'
    packet.unlink()
    archive(packet, new, manifest(new, 8))
    with pytest.raises(ValueError):
        maintenance.apply_update(player, packet, process_probe=lambda _: [])
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    assert_private(update_case)


def test_unowned_file_not_in_private_root_is_preserved(update_case):
    player, packet, _, _, _, old_bytes = update_case
    existing = player / 'scripts/player-maintenance.py'
    existing.parent.mkdir()
    existing.write_bytes(b'user-owned file must not be overwritten')
    with pytest.raises(ValueError, match='unowned_file_conflict'):
        maintenance.apply_update(player, packet, process_probe=lambda _: [])
    assert existing.read_bytes() == b'user-owned file must not be overwritten'
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes


def test_legacy_manifest_can_upgrade_without_moving_bound_repository(update_case):
    player, packet, old, _, _, _ = update_case
    (player / maintenance.MANIFEST).write_bytes(encode(manifest(old)))
    assert maintenance.apply_update(player, packet, process_probe=lambda _: [])['revision'] == 8
    assert (player / 'pcr-repository.txt').read_bytes() == b'.'
    assert_private(update_case)


def test_previous_interrupted_update_blocks_new_update(update_case):
    player, packet, _, _, _, old_bytes = update_case
    marker = player / '.runtime/player-maintenance/update-in-progress.json'
    marker.parent.mkdir()
    marker.write_bytes(b'{"workspace":"existing-original"}')
    with pytest.raises(ValueError, match='previous_update_incomplete'):
        maintenance.apply_update(player, packet, process_probe=lambda _: [])
    assert marker.read_bytes() == b'{"workspace":"existing-original"}'
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    assert_private(update_case)


def test_running_bundled_python_refuses_native_runtime_change_before_program_writes(update_case, monkeypatch):
    player, packet, old, new, _, old_bytes = update_case
    monkeypatch.setattr(maintenance.sys, 'executable', str(player / 'runtime/python/python.exe'))
    new['runtime/python/python.exe'] = b'changed native runtime; do not execute'
    packet.unlink()
    archive(packet, new, manifest(new, 8))
    with pytest.raises(ValueError, match='python_runtime_requires_external_offline_update'):
        maintenance.apply_update(player, packet, process_probe=lambda _: [])
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    for name, data in old.items(): assert (player / name).read_bytes() == data
    assert not (player / '.runtime/player-maintenance').exists()
    assert_private(update_case)


def test_running_bundled_python_skips_identical_runtime_and_updates_program(update_case, monkeypatch):
    player, packet, _, _, _, _ = update_case
    monkeypatch.setattr(maintenance.sys, 'executable', str(player / 'runtime/python/python.exe'))
    assert maintenance.apply_update(player, packet, process_probe=lambda _: [])['status'] == 'updated'
    assert (player / 'runtime/python/python.exe').read_bytes() == b'not executable'
    assert_private(update_case)


def test_update_marker_is_flushed_and_synced_before_first_program_replace(update_case, monkeypatch):
    player, packet, _, _, _, _ = update_case
    synced = []
    fsync, replace = maintenance.os.fsync, maintenance._replace
    def check_sync(fd):
        marker = player / '.runtime/player-maintenance/update-in-progress.json'
        fsync(fd)
        if marker.exists():
            value = json.loads(marker.read_text(encoding='utf-8'))
            assert value['workspace'].startswith('update-')
            synced.append(value)
    def check_replace(source, destination):
        assert synced
        replace(source, destination)
    monkeypatch.setattr(maintenance.os, 'fsync', check_sync)
    monkeypatch.setattr(maintenance, '_replace', check_replace)
    assert maintenance.apply_update(player, packet, process_probe=lambda _: [])['status'] == 'updated'


def test_marker_sync_failure_cannot_write_any_program_file(update_case, monkeypatch):
    player, packet, old, _, _, old_bytes = update_case
    def fail_sync(_):
        raise OSError('synthetic sync failure')
    monkeypatch.setattr(maintenance.os, 'fsync', fail_sync)
    with pytest.raises(OSError, match='synthetic sync failure'):
        maintenance.apply_update(player, packet, process_probe=lambda _: [])
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    for name, data in old.items(): assert (player / name).read_bytes() == data
    assert_private(update_case)


@pytest.mark.skipif(os.name != 'nt', reason='Windows UNC path semantics')
def test_unc_archive_is_rejected_before_any_share_resolution(update_case, monkeypatch):
    resolve = maintenance.Path.resolve
    def no_share_resolution(path, *args, **kwargs):
        if str(path).startswith('\\\\'):
            raise AssertionError('UNC must be rejected before filesystem resolution')
        return resolve(path, *args, **kwargs)
    monkeypatch.setattr(maintenance.Path, 'resolve', no_share_resolution)
    with pytest.raises(ValueError, match='unsafe_root'):
        maintenance.apply_update(update_case[0], r'\\unreachable-server\share\packet.zip', process_probe=lambda _: [])
    assert (update_case[0] / maintenance.MANIFEST).read_bytes() == update_case[5]
    assert_private(update_case)
