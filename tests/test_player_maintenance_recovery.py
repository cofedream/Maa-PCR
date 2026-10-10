"""Interrupted updates are synthetic; all owned and private bytes stay in tmp paths."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcr_maa import player_maintenance as maintenance
from pcr_maa import player_update as updater
from test_player_update import assert_private, encode, manifest, update_case
from test_player_online_update import online_case


def interrupted(player, old, new, old_bytes, new_bytes, *, complete=False):
    work = player / '.runtime/player-maintenance' / ('update-' + 'a' * 32)
    before = work / 'before'
    before.mkdir(parents=True)
    changes = sorted(name for name in new if old.get(name) != new[name])
    removed = sorted(set(old).difference(new))
    for name in (*changes, *removed):
        if name in old:
            path = before / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(old[name])
    (before / maintenance.MANIFEST).write_bytes(old_bytes)
    (work / 'target-manifest.json').write_bytes(new_bytes)
    marker = work.parent / 'update-in-progress.json'
    marker.write_bytes(encode({'workspace': work.name,
        'old_manifest_sha256': hashlib.sha256(old_bytes).hexdigest(),
        'new_manifest_sha256': hashlib.sha256(new_bytes).hexdigest()}))
    for name in changes if complete else changes[:1]:
        path = player / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new[name])
    if complete:
        for name in removed:
            (player / name).unlink()
        (player / maintenance.MANIFEST).write_bytes(new_bytes)
    return work, changes, marker


@pytest.mark.parametrize('complete', [False, True])
def test_partial_or_committed_marker_recovers_exact_old_program(update_case, complete):
    player, _, old, new, _, old_bytes = update_case
    work, _, marker = interrupted(player, old, new, old_bytes, encode(manifest(new, 8)), complete=complete)
    result = maintenance.recover_update(player, process_probe=lambda _: [])
    assert result['status'] == 'rolled_back' and result['revision'] == 7
    assert result['gui_started'] is False and not marker.exists()
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    for name, data in old.items():
        assert (player / name).read_bytes() == data
    for name in set(new).difference(old):
        assert not (player / name).exists()
    assert_private(update_case)
    assert (work / 'before' / maintenance.MANIFEST).read_bytes() == old_bytes


def test_unknown_conflict_preserves_all_owned_bytes_and_marker(update_case):
    player, _, old, new, _, old_bytes = update_case
    _, changes, marker = interrupted(player, old, new, old_bytes, encode(manifest(new, 8)), complete=True)
    (player / changes[-1]).write_bytes(b'unknown external write must survive')
    known_before = {name: (player / name).read_bytes() for name in new}
    marker_bytes = marker.read_bytes()
    with pytest.raises(ValueError, match='recovery_unknown_conflict'):
        maintenance.recover_update(player, process_probe=lambda _: [])
    assert marker.read_bytes() == marker_bytes
    for name, data in known_before.items():
        assert (player / name).read_bytes() == data
    assert_private(update_case)


def test_tampered_backup_or_target_cannot_be_restored(update_case):
    player, _, old, new, _, old_bytes = update_case
    work, changes, marker = interrupted(player, old, new, old_bytes, encode(manifest(new, 8)))
    (work / 'before' / changes[0]).write_bytes(b'not the old payload')
    with pytest.raises(ValueError, match='payload_hash_mismatch'):
        maintenance.recover_update(player, process_probe=lambda _: [])
    assert (player / changes[0]).read_bytes() == new[changes[0]] and marker.exists()
    assert_private(update_case)


def test_recovery_refuses_active_owner_before_program_writes(update_case):
    player, _, old, new, _, old_bytes = update_case
    _, changes, marker = interrupted(player, old, new, old_bytes, encode(manifest(new, 8)))
    with pytest.raises(ValueError, match='player_or_agent_running'):
        maintenance.recover_update(player, process_probe=lambda _: [123])
    assert (player / changes[0]).read_bytes() == new[changes[0]] and marker.exists()


def test_interruption_during_recovery_can_resume_without_consuming_backups(update_case, monkeypatch):
    player, _, old, new, _, old_bytes = update_case
    _, _, marker = interrupted(player, old, new, old_bytes, encode(manifest(new, 8)), complete=True)
    original = maintenance._restore
    calls = 0

    def interrupted_restore(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError('synthetic process interruption during restore')
        original(source, destination)

    monkeypatch.setattr(maintenance, '_restore', interrupted_restore)
    with pytest.raises(OSError):
        maintenance.recover_update(player, process_probe=lambda _: [])
    assert marker.exists()
    monkeypatch.setattr(maintenance, '_restore', original)
    assert maintenance.recover_update(player, process_probe=lambda _: [])['status'] == 'rolled_back'
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    assert_private(update_case)


def test_legacy_marker_without_target_remains_fail_closed(update_case):
    player = update_case[0]
    marker = player / '.runtime/player-maintenance/update-in-progress.json'
    marker.parent.mkdir()
    marker.write_bytes(b'{"workspace":"keep-original-legacy-marker"}')
    with pytest.raises(ValueError, match='invalid_recovery_marker'):
        maintenance.recover_update(player, process_probe=lambda _: [])
    assert marker.read_bytes() == b'{"workspace":"keep-original-legacy-marker"}'
    assert_private(update_case)


def test_recovery_bootstrap_uses_coherent_verified_old_runtime(online_case):
    case = online_case
    interrupted(case['player'], case['old'], case['new'], case['old_bytes'],
        case['target_bytes'], complete=True)
    worker = updater.prepare_worker(case['player'], recover=True,
        temp_root=case['tmp_path'] / 'external recovery')
    assert Path(worker['python']).read_bytes() == case['old']['runtime/python/python.exe']
    assert not Path(worker['workspace']).is_relative_to(case['player'])
    assert not list(Path(worker['workspace']).rglob('local-secret.txt'))
    assert not list(Path(worker['workspace']).rglob('unknown.json'))


def test_recovery_worker_restarts_only_gui_after_old_manifest_verifies(online_case):
    case = online_case
    interrupted(case['player'], case['old'], case['new'], case['old_bytes'], case['target_bytes'], complete=True)
    launches = []
    result = updater.run_worker(case['player'], recover=True, process_probe=lambda _: [],
        popen=lambda args, **kwargs: launches.append(args) or SimpleNamespace(pid=123))
    assert result['status'] == 'rolled_back' and result['revision'] == 39
    assert result['gui_started'] is True and result['game_tasks_started'] is False
    assert launches == [[str(case['player'] / 'MFAAvalonia.exe'), '--pcr-after-update']]
    assert (case['player'] / maintenance.MANIFEST).read_bytes() == case['old_bytes']


def test_owner_starting_after_intent_is_detected_before_any_program_write(update_case, monkeypatch):
    player, packet, old, _, _, old_bytes = update_case
    calls = 0

    def probe(_):
        nonlocal calls
        calls += 1
        if calls == 3:
            assert (player / '.runtime/player-maintenance/update-in-progress.json').is_file()
            return [123]
        return []

    monkeypatch.setattr(maintenance, '_replace', lambda *_: pytest.fail('program write before owner gate'))
    with pytest.raises(ValueError, match='player_or_agent_running'):
        maintenance.apply_update(player, packet, process_probe=probe)
    assert calls == 3
    assert not (player / '.runtime/player-maintenance/update-in-progress.json').exists()
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    for name, data in old.items():
        assert (player / name).read_bytes() == data
    assert_private(update_case)


def test_backups_target_and_changed_staging_are_synced_before_intent(update_case, monkeypatch):
    player, packet, old, new, _, _ = update_case
    synced = []
    sync_file, save = maintenance._sync_file, maintenance._save

    def sync(path):
        sync_file(path)
        synced.append(path)

    def save_intent(path, value, **kwargs):
        if path.name == 'update-in-progress.json':
            changed = {name for name in new if old.get(name) != new[name]}
            before_names = {name for name in changed if name in old} | set(old).difference(new) | {maintenance.MANIFEST}
            work = path.parent / value['workspace']
            assert {work / 'staged' / name for name in changed | {maintenance.MANIFEST}}.issubset(synced)
            assert {work / 'before' / name for name in before_names}.issubset(synced)
            assert work / 'target-manifest.json' in synced
            assert kwargs.get('durable') is True
        return save(path, value, **kwargs)

    monkeypatch.setattr(maintenance, '_sync_file', sync)
    monkeypatch.setattr(maintenance, '_save', save_intent)
    assert maintenance.apply_update(player, packet, process_probe=lambda _: [])['status'] == 'updated'


@pytest.mark.parametrize('entry', ['apply', 'validate', 'prepare'])
def test_archive_trailing_junk_above_limit_is_refused_before_copy(update_case, monkeypatch, entry):
    player, packet, _, _, _, old_bytes = update_case
    maximum = packet.stat().st_size
    with packet.open('ab') as stream:
        stream.write(b'trailing junk')
    monkeypatch.setattr(maintenance, 'MAX_ARCHIVE', maximum)
    with pytest.raises(ValueError, match='archive_too_large'):
        if entry == 'apply':
            maintenance.apply_update(player, packet, process_probe=lambda _: [])
        elif entry == 'validate':
            updater.validate_archive(player, packet)
        else:
            updater.prepare_worker(player, packet)
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    assert_private(update_case)


def test_recovery_bootstrap_does_not_depend_on_current_manifest(online_case):
    case = online_case
    interrupted(case['player'], case['old'], case['new'], case['old_bytes'], case['target_bytes'], complete=True)
    (case['player'] / maintenance.MANIFEST).write_bytes(b'unknown damaged manifest')
    worker = updater.prepare_worker(case['player'], recover=True,
        temp_root=case['tmp_path'] / 'external recovery')
    assert Path(worker['python']).read_bytes() == case['old']['runtime/python/python.exe']
    # Unknown manifest damage is not silently overwritten by recovery.
    with pytest.raises(ValueError, match='recovery_unknown_conflict'):
        maintenance.recover_update(case['player'], process_probe=lambda _: [])


def test_persisted_external_receipt_recovery_never_loads_damaged_installed_runtime(online_case, monkeypatch):
    case = online_case
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    archive.write_bytes(case['blobs'][case['index']['full']['name']])
    worker = updater.prepare_worker(case['player'], archive, temp_root=case['tmp_path'])
    interrupted(case['player'], case['old'], case['new'], case['old_bytes'], case['target_bytes'], complete=True)
    (case['player'] / 'runtime/python/python.exe').write_bytes(b'damaged after abrupt update')
    monkeypatch.setattr(updater, 'prepare_worker', lambda *_args, **_kwargs: pytest.fail('must reuse verified external runtime'))
    launches = []
    result = updater.launch_update(case['player'], recover=True, parent_pid=123,
        popen=lambda args, **kwargs: launches.append(args) or SimpleNamespace(pid=456))
    assert result['status'] == 'installer_started'
    assert launches[0][0] == worker['python'] and '--recover' in launches[0]


def test_tampered_external_receipt_runtime_is_not_executed(online_case):
    case = online_case
    archive = case['tmp_path'] / 'synthetic-offline.zip'
    archive.write_bytes(case['blobs'][case['index']['full']['name']])
    worker = updater.prepare_worker(case['player'], archive, temp_root=case['tmp_path'])
    Path(worker['python']).write_bytes(b'unknown external binary')
    with pytest.raises(ValueError, match='payload_hash_mismatch'):
        updater.validate_external_worker(case['player'])


def test_second_update_or_recovery_worker_cannot_race_first_owner(online_case):
    case = online_case
    with updater._worker_lock(case['player']):
        with pytest.raises(ValueError, match='updater_worker_running'):
            updater.run_worker(case['player'], recover=True, process_probe=lambda _: [], wait_seconds=0)
    with updater._worker_lock(case['player']):
        pass
