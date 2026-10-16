"""Exact candidate-to-formal conversion; synthetic programs/private data only."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pytest
from pcr_maa import player_maintenance as m
from test_player_update import archive, assert_private, encode, promoted_packet, update_case
from test_player_maintenance_recovery import interrupted

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_r54_release_bridge as builder
import build_pcr_player_patch as patcher


@pytest.fixture
def transition_case(update_case, monkeypatch):
    player, packet, old, new, private, _ = update_case
    tools = {name: (ROOT / name).read_bytes() for name in builder.TOOLS
             if name != 'convert-r54-to-r52.py'}
    tools['scripts/player-maintenance.py'] = (ROOT/'scripts/player-maintenance.py').read_bytes()
    old, new = {**old, **tools}, {**new, **tools}
    private = {**private, 'config/credentials.dpapi': b'opaque synthetic encrypted bytes',
        '.runtime/labyrinth-work/ledger/batch-checkpoint.json': b'{"state":"unknown","attempts":1}',
        'config/task-order.json': b'["daily","labyrinth"]',
        '.runtime/daily/counters.json': b'{"used":3}'}
    for name, data in {**old, **private}.items():
        path = player / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    case = promoted_packet((player, packet, old, new, private, b''), old_revision=54,
        new_revision=52, old_version='v0.1.7-r54-candidate', new_version='v0.1.8')
    # Only synthetic fixtures substitute the frozen production source fingerprint.
    monkeypatch.setattr(m, 'R54_TEST_MANIFEST_SHA256', case[7][0])
    return case


def apply(case, **kwargs):
    return m.apply_update(case[0], case[1], process_probe=lambda _: [],
        r54_release_transition=case[7], **kwargs)


def original(case):
    assert (case[0] / m.MANIFEST).read_bytes() == case[5]
    for name, data in case[2].items():
        assert (case[0] / name).read_bytes() == data
    assert_private(case)


def test_exact_transition_preserves_all_private_bytes_and_repeat_refused(transition_case):
    case = transition_case
    with pytest.raises(ValueError, match='update_not_newer'):
        m.apply_update(case[0], case[1], process_probe=lambda _: [])
    original(case)
    result = apply(case)
    assert result['revision'] == 52 and result['version'] == 'v0.1.8'
    assert result['private_state_preserved'] and not result['gui_started']
    assert (case[0] / m.MANIFEST).read_bytes() == case[6]
    assert (Path(result['backup_path']) / m.MANIFEST).read_bytes() == case[5]
    assert_private(case)
    with pytest.raises(ValueError, match='r54_release_manifest_mismatch'):
        apply(case)
    assert_private(case)


@pytest.mark.parametrize('mutation', [
    {'update': {'revision': 51}}, {'update': {'revision': 53}},
    {'update': {'version': 'v0.1.6'}}, {'update': {'version': 'v0.1.8-candidate'}},
    {'status': 'release_staging'}, {'distribution_ready': False},
    {'blockers': ['pending']}, {'blockers': None}, {'release_checks': []},
    {'release_checks': [{'name': 'acceptance.json', 'sha256': 'bad'}]},
    {'layout': 'bound-checkout'},
])
def test_reject_unaccepted_or_wrong_target_without_writes(transition_case, mutation):
    case = transition_case
    metadata = json.loads(case[6])
    for key, value in mutation.items():
        if key == 'update': metadata[key].update(value)
        else: metadata[key] = value
    raw = encode(metadata)
    archive(case[1], case[3], metadata)
    pair = (case[7][0], hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match='r54_release_transition_not_allowed'):
        m.apply_update(case[0], case[1], process_probe=lambda _: [], r54_release_transition=pair)
    original(case)


@pytest.mark.parametrize('pair', [None, ('a'*64,'b'*64), ['a'*64,'b'*64], (), (True,'b'*64)])
def test_missing_or_inexact_binding_cannot_allow_downgrade(transition_case, pair):
    case = transition_case
    with pytest.raises(ValueError, match='update_not_newer|r54_release_manifest_mismatch|invalid_r54_release_transition'):
        m.apply_update(case[0], case[1], process_probe=lambda _: [], r54_release_transition=pair)
    original(case)


def test_other_r54_build_cannot_opt_in_with_self_selected_hashes(transition_case, monkeypatch):
    case = transition_case
    monkeypatch.setattr(m, 'R54_TEST_MANIFEST_SHA256', 'c'*64)
    with pytest.raises(ValueError, match='r54_release_manifest_mismatch'):
        apply(case)
    original(case)


def test_transition_flags_are_mutually_exclusive(transition_case):
    with pytest.raises(ValueError, match='conflicting_candidate_transitions'):
        apply(transition_case, candidate_replacement=transition_case[7])
    original(transition_case)


@pytest.mark.parametrize('damage', ['payload', 'private_path', 'binding', 'unowned', 'pending', 'old_program'])
def test_original_integrity_and_private_boundaries_still_hold(transition_case, damage):
    case = transition_case; metadata=json.loads(case[6]); payload=dict(case[3])
    expected='archive_size_mismatch|archive_hash_mismatch'
    if damage == 'payload': payload['MFAAvalonia.Core.dll']=b'bad payload'
    if damage == 'private_path':
        payload['config/credentials.dpapi']=b'must not replace'
        metadata['files'].append(dict(path='config/credentials.dpapi',size=16,sha256='a'*64))
        expected='private_payload_path'
    if damage == 'binding':
        payload['pcr-repository.txt']=b'other'
        row=next(r for r in metadata['files'] if r['path']=='pcr-repository.txt')
        row.update(size=5,sha256=hashlib.sha256(b'other').hexdigest()); expected='repository_binding_changed'
    if damage == 'unowned':
        name=next(name for name in payload if name not in case[2])
        p=case[0]/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'keep unowned bytes')
        expected='unowned_file_conflict'
    if damage == 'pending':
        p=case[0]/'.runtime/player-maintenance/update-in-progress.json'
        p.parent.mkdir(); p.write_bytes(b'keep existing pending transaction'); expected='previous_update_incomplete'
    if damage == 'old_program':
        (case[0]/'MFAAvalonia.Core.dll').write_bytes(b'unknown local edit'); expected='payload_hash_mismatch'
    raw=encode(metadata); archive(case[1],payload,metadata)
    before={p:p.read_bytes() for p in case[0].rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match=expected):
        m.apply_update(case[0],case[1],process_probe=lambda _:[],
            r54_release_transition=(case[7][0],hashlib.sha256(raw).hexdigest()))
    assert all(p.read_bytes()==value for p,value in before.items())
    assert_private(case)


@pytest.mark.parametrize('change', ['runtime/python/python.exe', 'runtime/python/new.dll',
    'Runtime/Python/new.py'])
def test_python_runtime_change_is_rejected(transition_case, change):
    case = transition_case
    payload = {**case[3], change: b'changed runtime'}
    metadata = json.loads(case[6]); rows = {row['path']: row for row in metadata['files']}
    rows[change] = dict(path=change, size=len(payload[change]), sha256=hashlib.sha256(payload[change]).hexdigest())
    metadata['files'] = list(rows.values()); raw = encode(metadata)
    archive(case[1], payload, metadata)
    with pytest.raises(ValueError, match='r54_release_python_runtime_changed'):
        m.apply_update(case[0], case[1], process_probe=lambda _: [],
            r54_release_transition=(case[7][0], hashlib.sha256(raw).hexdigest()))
    original(case)


@pytest.mark.parametrize('reopened', [False, True])
def test_process_guard_still_checks_before_writes(transition_case, reopened):
    case = transition_case; calls = []
    def probe(player):
        calls.append(player)
        return [123] if not reopened or len(calls) == 2 else []
    with pytest.raises(ValueError, match='player_or_agent_running'):
        m.apply_update(case[0], case[1], process_probe=probe, r54_release_transition=case[7])
    original(case)


def test_mid_write_failure_rolls_back_exact_old_program(transition_case, monkeypatch):
    case = transition_case; replace = m._replace; count = 0
    def fail_once(source, destination):
        nonlocal count
        count += 1
        if count == 3: raise OSError('synthetic failure')
        return replace(source, destination)
    monkeypatch.setattr(m, '_replace', fail_once)
    with pytest.raises(RuntimeError, match='update_failed_rolled_back'):
        apply(case)
    original(case)


@pytest.mark.parametrize('complete', [False, True])
def test_interrupted_transition_requires_exact_binding_and_recovers(transition_case, complete):
    case = transition_case
    _, _, marker = interrupted(case[0], case[2], case[3], case[5], case[6], complete=complete)
    with pytest.raises(ValueError, match='invalid_recovery_versions'):
        m.recover_update(case[0], process_probe=lambda _: [])
    with pytest.raises(ValueError, match='r54_release_manifest_mismatch'):
        m.recover_update(case[0], process_probe=lambda _: [], r54_release_transition=(case[7][0], 'c'*64))
    assert marker.exists()
    result = m.recover_update(case[0], process_probe=lambda _: [], r54_release_transition=case[7])
    assert result['status'] == 'rolled_back' and result['revision'] == 54
    assert not marker.exists()
    original(case)
    assert m.recover_update(case[0], process_probe=lambda _: [], r54_release_transition=case[7])['status'] == 'no_recovery_needed'


@pytest.mark.parametrize('damage', ['backup', 'unknown'])
def test_recovery_does_not_overwrite_corrupt_or_unknown_state(transition_case, damage):
    case = transition_case
    work, changes, marker = interrupted(case[0], case[2], case[3], case[5], case[6], complete=True)
    path = work/'before'/changes[0] if damage == 'backup' else case[0]/changes[0]
    path.write_bytes(b'unknown must be retained')
    before = {p: p.read_bytes() for p in case[0].rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='payload_hash_mismatch|recovery_unknown_conflict'):
        m.recover_update(case[0], process_probe=lambda _: [], r54_release_transition=case[7])
    assert marker.exists() and all(p.read_bytes() == value for p,value in before.items())
    assert_private(case)


def test_bridge_builder_emits_exact_delta_and_standalone_recovery(transition_case, tmp_path):
    case = transition_case; target = tmp_path/'target'; target.mkdir()
    for name,data in case[3].items():
        p=target/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(data)
    (target/m.MANIFEST).write_bytes(case[6])
    output=tmp_path/'bridge'; result=builder.build(case[0]/m.MANIFEST,target,output)
    assert result['published'] is False and result['private_state_included'] is False
    binding=json.loads((output/'bridge-manifest.json').read_text(encoding='utf-8'))
    assert (binding['base_manifest_sha256'],binding['target_manifest_sha256']) == case[7]
    assert binding['archive_sha256'] == m._sha(output/'update.zip')
    for name,digest in binding['tools'].items(): assert m._sha(output/name)==digest
    with zipfile.ZipFile(result['archive']) as packet:
        assert {'convert.bat','recover.bat','convert.ps1','convert-r54-to-r52.py','update.zip','bridge-manifest.json'} <= set(packet.namelist())
        assert not any('config/' in name or 'ledger' in name for name in packet.namelist())
    result=m.apply_update(case[0],output/'update.zip',process_probe=lambda _: [],r54_release_transition=case[7])
    assert result['revision']==52
    assert_private(case)


def test_ordinary_patch_builder_still_rejects_revision_decrease(transition_case, tmp_path):
    case=transition_case; target=tmp_path/'target'; target.mkdir()
    for name,data in case[3].items():
        p=target/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(data)
    (target/m.MANIFEST).write_bytes(case[6])
    with pytest.raises(ValueError,match='update_not_newer'):
        patcher.build_patch_from_manifest(case[0]/m.MANIFEST,target,tmp_path/'bad.zip',case[7][0])
    assert not (tmp_path/'bad.zip').exists()
