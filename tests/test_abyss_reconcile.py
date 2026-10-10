"""Synthetic lifecycle tests; real original evidence is checked separately offline."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest
from pcr_maa import abyss, supported_daily as chain
from pcr_maa.abyss_reconcile import reconcile_closed_result, verify_result
from pcr_maa.preset_execution import Journal
from test_abyss import Port, reading, quotation, frames

OLD, NEW = '2026-10-03', '2026-10-04'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


@pytest.fixture
def case(tmp_path):
    device = dict(serial='127.0.0.1:16384', manager=dict(source='mumu_manager_local_v1',
        installation=str(tmp_path), index=0, created_timestamp=123,
        app_package='com.bilibili.priconne'))
    before, quote = reading(), quotation()
    ticket = dict(native_ok=True, identity_verified=True, scope=abyss.SCOPE,
        abyss_identity=before['abyss_identity'], background_counts=[3, 3, 3],
        stamina_cost=225, ticket_cost=9, stamina_owned=245, tickets_owned=100)
    anchors = {name: dict(hit=True, raw=dict(filtered=[dict(text=text, score=.99)]))
        for name, text in [('ExecResultTitle', '扫荡结果'),
                           ('ExecResultComplete', '扫荡完成！'), ('ExecResultButton', '确认')]}
    frame = tmp_path / 'result.png'; frame.write_bytes(b'synthetic-result-bytes')
    image = dict(kind='result', scope=abyss.SCOPE, abyss_identity=before['abyss_identity'],
        counts=[3, 3, 3], sha256=sha256(frame.read_bytes()).hexdigest(), path=str(frame), evidence=anchors)
    def receipt(name, proof, task, ok):
        return dict(tasker_inited=True, native_ok=ok, task_id=task, nodes=[dict(name=name,
            completed=True, action=dict(name=name, success=True, raw=dict(point=[700, 600])),
            recognition=dict(best=dict(detail=proof)))])
    start = datetime(2026, 10, 3, 12, tzinfo=timezone(timedelta(hours=8)))
    intent = dict(state='in_flight', scope=abyss.SCOPE, operation='abyss_outpost',
        intent_id='a' * 32, game_day=OLD, device=device, abyss_identity=before['abyss_identity'],
        quote=quote, final_confirmation=ticket, opening_receipt=dict(native_ok=True), at=start.timestamp())
    commit = receipt('AbyssTicketCommit', ticket, 147, True)
    closed = receipt('AbyssResultClose', image, 166, False)
    result = dict(scope=abyss.SCOPE, intent_id=intent['intent_id'], game_day=OLD, device=device,
        outcome='unknown_settlement', consumer_dispatched=True, consumption_sent=True,
        settlement_verified=False, home_verified=False, reading=before, quote=quote,
        confirmation=dict(reading=ticket, opening_receipt=intent['opening_receipt']),
        submission=dict(consumption_dispatched=True, abyss_return_verified=False,
            native_receipt=commit, result_evidence=closed, result_images=[image]))
    step = dict(name='abyss', status='stopped', result=result)
    step_path = tmp_path / 'step.json'; write(step_path, step)
    shop = tmp_path / 'shop.png'; shop.write_bytes(b'synthetic-shop-bytes')
    log = tmp_path / 'native.log'
    lines = []
    for i, (name, task) in enumerate([('AbyssTicketCommit', 147), ('AbyssResultClose', 166)]):
        for j, state in enumerate(['Starting', 'Succeeded']):
            event = dict(name=name, task_id=task, action_id=i + 1)
            if state == 'Succeeded':
                event['action_details'] = dict(action='Click', success=True, detail=dict(point=[700, 600]))
            at = (start + timedelta(seconds=i * 2 + j + 1)).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            lines.append(f'[{at}][INF][Pxabc][Utils/EventDispatcher.hpp][msg=Node.Action.{state}] [details={json.dumps(event)}]')
    log.write_text('\n'.join(lines), encoding='utf-8')
    proof = dict(offline_result_and_shop_verified=True, device_operations=0,
        native_log_utc_offset_seconds=28800, files={name: dict(path=str(path),
            sha256=sha256(path.read_bytes()).hexdigest()) for name, path in
            [('step', step_path), ('result_frame', frame), ('failed_shop_frame', shop), ('native_log', log)]})
    journal = Journal(tmp_path / 'ledger')
    with journal.locked(): journal.append(intent)
    return dict(journal=journal, intent=intent, step=step, proof=proof, device=device,
                prefix=journal.path.read_bytes())


def reconcile(case, apply=True):
    return reconcile_closed_result(case['journal'], case['proof'],
        expected_sha256=sha256(case['prefix']).hexdigest(), apply=apply)


def run(case, monkeypatch, *, day=NEW, zero=False, changed_device=False):
    monkeypatch.setattr(abyss, 'current_game_day', lambda *args: day)
    port = Port()
    if zero:
        def complete():
            port.calls.append('read')
            value = reading()
            for row in value['rows']: row['remaining'] = 0
            return value
        port.read = complete
    device = deepcopy(case['device'])
    if changed_device: device['manager']['created_timestamp'] += 1
    result = abyss.execute_abyss(port, case['journal'], game_day=day,
        context='gui-current-state', execute=True, device=device)
    return result, port


def test_default_dry_run_and_apply_preserve_unknown_counts_and_original_prefix(case):
    result = reconcile(case, apply=False)
    assert not result['applied'] and case['journal'].path.read_bytes() == case['prefix']
    assert not (case['journal'].directory / 'result-evidence').exists()
    result = reconcile(case)
    assert result['applied'] and result['consumption_verified']
    assert not result['settlement_verified'] and not result['home_verified']
    assert not result['post_counts_verified'] and not result['original_replay_permitted']
    events = case['journal'].events()
    assert events[0] == case['intent'] and events[1]['state'] == 'settled'
    assert 'remaining' not in events[1]
    assert case['journal'].path.read_bytes().startswith(case['prefix'])
    with pytest.raises(ValueError): reconcile(case)


def test_rotated_native_log_uses_durable_copy_in_new_day(case, monkeypatch):
    reconcile(case)
    row = case['proof']['files']['native_log']
    original = Path(row['path'])
    pinned = case['journal'].directory / 'result-evidence' / (row['sha256'] + '.log')
    assert pinned.read_bytes() == original.read_bytes()
    assert case['journal'].events()[-1]['evidence'] == case['proof']
    original.unlink()
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'unknown_settlement'
    assert port.calls == ['read', 'quote', 'confirm', 'submit']
    assert case['journal'].path.read_bytes().startswith(case['prefix'])


@pytest.mark.parametrize('zero,expected,calls', [
    (False, 'blocked_post_counts_unverified', ['read']),
    (True, 'skipped_goal_met', ['read', 'home'])])
def test_rotated_native_log_preserves_same_day_limits(case, monkeypatch, zero, expected, calls):
    reconcile(case)
    Path(case['proof']['files']['native_log']['path']).unlink()
    before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch, day=OLD, zero=zero)
    assert result['outcome'] == expected and port.calls == calls
    assert not result['consumption_sent'] and case['journal'].path.read_bytes() == before


@pytest.mark.parametrize('fault', ['missing', 'changed'])
def test_unavailable_durable_log_stops_before_navigation(case, monkeypatch, fault):
    reconcile(case)
    row = case['proof']['files']['native_log']
    Path(row['path']).unlink()
    pinned = case['journal'].directory / 'result-evidence' / (row['sha256'] + '.log')
    if fault == 'missing': pinned.unlink()
    else: pinned.write_bytes(pinned.read_bytes() + b'changed')
    before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'blocked_or_failed' and port.calls == []
    assert case['journal'].path.read_bytes() == before


@pytest.mark.parametrize('digest', ['../outside', 'g' * 64, 'a' * 63])
def test_native_log_digest_cannot_select_arbitrary_cache(case, digest):
    case['proof']['files']['native_log']['sha256'] = digest
    with pytest.raises(ValueError): reconcile(case)
    assert case['journal'].path.read_bytes() == case['prefix']
    assert not (case['journal'].directory / 'result-evidence').exists()


def test_existing_changed_log_copy_cannot_be_overwritten(case):
    row = case['proof']['files']['native_log']
    pinned = case['journal'].directory / 'result-evidence' / (row['sha256'] + '.log')
    pinned.parent.mkdir()
    pinned.write_bytes(b'changed')
    with pytest.raises(ValueError, match='evidence_cache_changed'): reconcile(case)
    assert pinned.read_bytes() == b'changed' and case['journal'].path.read_bytes() == case['prefix']


def test_another_journal_cannot_borrow_log_copy(case, monkeypatch, tmp_path):
    reconcile(case)
    Path(case['proof']['files']['native_log']['path']).unlink()
    other = Journal(tmp_path / 'other-ledger')
    other.directory.mkdir()
    before = case['journal'].path.read_bytes()
    other.path.write_bytes(before)
    result, port = run(dict(case, journal=other), monkeypatch)
    assert result['outcome'] == 'blocked_or_failed' and port.calls == []
    assert other.path.read_bytes() == before


def test_redirected_log_copy_directory_cannot_escape_journal(case, monkeypatch, tmp_path):
    from test_player_archive import directory_alias_in_tmp
    reconcile(case)
    row = case['proof']['files']['native_log']
    Path(row['path']).unlink()
    directory = case['journal'].directory / 'result-evidence'
    outside = tmp_path / 'elsewhere'
    outside.mkdir()
    (directory / (row['sha256'] + '.log')).rename(outside / (row['sha256'] + '.log'))
    directory.rmdir()
    directory_alias_in_tmp(directory, outside, tmp_path)
    before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'blocked_or_failed' and port.calls == []
    assert case['journal'].path.read_bytes() == before


def replace_rotated_log(case, content=None):
    row = case['proof']['files']['native_log']
    original = Path(row['path'])
    replacement = original.read_bytes() + b'\nindependent historical archive\n' if content is None else content
    terminal = case['journal'].events()[-1]
    digest = sha256(replacement).hexdigest()
    directory = case['journal'].directory / 'result-evidence'
    (directory / (digest + '.log')).write_bytes(replacement)
    manifest = dict(version=1, previous_root_sha256=terminal['previous_root_sha256'],
        terminal_sha256=sha256(json.dumps(terminal, ensure_ascii=False, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest(), original_native_log_sha256=row['sha256'],
        replacement_native_log_sha256=digest, replacement_bytes=len(replacement))
    manifest_path = directory / (row['sha256'] + '.recovery.json')
    write(manifest_path, manifest)
    original.unlink()
    (directory / (row['sha256'] + '.log')).unlink()
    return manifest_path, manifest


def test_bound_historical_log_replacement_preserves_terminal_and_new_day(case, monkeypatch):
    reconcile(case)
    before = case['journal'].path.read_bytes()
    replace_rotated_log(case)
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'unknown_settlement'
    assert port.calls == ['read', 'quote', 'confirm', 'submit']
    assert case['journal'].path.read_bytes().startswith(before)


@pytest.mark.parametrize('fault', ['terminal', 'prefix', 'old_hash', 'new_hash', 'unsafe_hash',
    'bytes', 'version', 'unexpected_field', 'missing_manifest', 'missing_replacement',
    'changed_replacement', 'original_changed', 'old_copy_changed', 'no_actions', 'wrong_action'])
def test_invalid_historical_log_replacement_blocks_before_navigation(case, monkeypatch, fault):
    reconcile(case)
    row = case['proof']['files']['native_log']
    original = Path(row['path'])
    content = None
    if fault == 'no_actions': content = b'no native actions'
    if fault == 'wrong_action': content = original.read_bytes().replace(b'"task_id": 147', b'"task_id": 999')
    path, manifest = replace_rotated_log(case, content)
    replacement = path.parent / (manifest['replacement_native_log_sha256'] + '.log')
    if fault in ('terminal', 'prefix', 'old_hash', 'new_hash'):
        field = dict(terminal='terminal_sha256', prefix='previous_root_sha256',
            old_hash='original_native_log_sha256', new_hash='replacement_native_log_sha256')[fault]
        manifest[field] = '0' * 64
    if fault == 'unsafe_hash': manifest['replacement_native_log_sha256'] = '../outside'
    if fault == 'bytes': manifest['replacement_bytes'] += 1
    if fault == 'version': manifest['version'] = True
    if fault == 'unexpected_field': manifest['path'] = str(replacement)
    write(path, manifest)
    if fault == 'missing_manifest': path.unlink()
    if fault == 'missing_replacement': replacement.unlink()
    if fault == 'changed_replacement': replacement.write_bytes(replacement.read_bytes() + b'changed')
    if fault == 'original_changed': original.write_bytes(b'changed')
    if fault == 'old_copy_changed': (path.parent / (row['sha256'] + '.log')).write_bytes(b'changed')
    before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'blocked_or_failed' and port.calls == []
    assert case['journal'].path.read_bytes() == before


@pytest.mark.parametrize('linked', ['original', 'old_copy', 'manifest', 'replacement'])
def test_symlink_evidence_cannot_be_hidden_by_recovery(case, monkeypatch, linked):
    reconcile(case)
    row = case['proof']['files']['native_log']
    path, manifest = replace_rotated_log(case)
    selected = dict(original=Path(row['path']), old_copy=path.with_name(row['sha256'] + '.log'),
        manifest=path, replacement=path.with_name(manifest['replacement_native_log_sha256'] + '.log'))[linked]
    original_is_symlink = Path.is_symlink
    # Exercise link rejection even when this Windows host cannot create file symlinks.
    monkeypatch.setattr(Path, 'is_symlink', lambda candidate: candidate == selected or original_is_symlink(candidate))
    before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'blocked_or_failed' and port.calls == []
    assert case['journal'].path.read_bytes() == before


@pytest.mark.parametrize('fault', ['missing_commit', 'missing_close', 'quote', 'frame_binding',
    'wrong_day', 'wrong_device', 'anchor', 'return_verified', 'other_completed_node'])
def test_incomplete_or_mismatched_result_cannot_resolve(case, fault):
    step = deepcopy(case['step']); result = step['result']; submitted = result['submission']
    if fault == 'missing_commit': submitted['native_receipt']['nodes'] = []
    if fault == 'missing_close': submitted['result_evidence']['nodes'][0]['action']['success'] = False
    if fault == 'quote': result['quote'] = dict(result['quote'], ticket_cost=10)
    if fault == 'frame_binding': submitted['result_evidence']['nodes'][0]['recognition']['best']['detail'] = {}
    if fault == 'wrong_day': result['game_day'] = NEW
    if fault == 'wrong_device': result['device']['manager']['created_timestamp'] += 1
    if fault == 'anchor': submitted['result_images'][0]['evidence']['ExecResultComplete']['raw']['filtered'][0]['score'] = .89
    if fault == 'return_verified': submitted['abyss_return_verified'] = True
    if fault == 'other_completed_node': submitted['result_evidence']['nodes'].append(dict(name='Other', completed=True))
    with pytest.raises(ValueError): verify_result(case['intent'], step)
    assert case['journal'].path.read_bytes() == case['prefix']


@pytest.mark.parametrize('name', ['step', 'result_frame', 'failed_shop_frame', 'native_log'])
def test_changed_pinned_evidence_blocks_without_any_input(case, monkeypatch, name):
    reconcile(case)
    path = Path(case['proof']['files'][name]['path']); path.write_bytes(path.read_bytes() + b'changed')
    before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'blocked_or_failed' and port.calls == []
    assert case['journal'].path.read_bytes() == before


def test_ledger_pin_mismatch_refuses_apply(case):
    with pytest.raises(ValueError, match='ledger_changed'):
        reconcile_closed_result(case['journal'], case['proof'], expected_sha256='0' * 64, apply=True)
    assert case['journal'].path.read_bytes() == case['prefix']


def test_native_log_with_no_actual_actions_refuses_apply(case):
    row = case['proof']['files']['native_log']; Path(row['path']).write_bytes(b'no native actions')
    row['sha256'] = sha256(Path(row['path']).read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='native_actions_missing'): reconcile(case)
    assert case['journal'].path.read_bytes() == case['prefix']


def test_truly_unknown_still_blocks_even_in_later_day(case, monkeypatch):
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'blocked_in_flight' and port.calls == []
    assert case['journal'].path.read_bytes() == case['prefix']


def test_same_day_cannot_reconsume_after_known_result(case, monkeypatch):
    reconcile(case); before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch, day=OLD)
    assert result['outcome'] == 'blocked_post_counts_unverified'
    assert port.calls == ['read'] and not result['consumption_sent']
    assert case['journal'].path.read_bytes() == before


def test_same_day_current_zero_can_skip_and_return_home(case, monkeypatch):
    reconcile(case); before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch, day=OLD, zero=True)
    assert result['outcome'] == 'skipped_goal_met' and result['home_verified']
    assert port.calls == ['read', 'home'] and case['journal'].path.read_bytes() == before


def test_new_day_uses_new_observation_and_new_intent_once(case, monkeypatch):
    reconcile(case)
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'unknown_settlement'
    assert port.calls == ['read', 'quote', 'confirm', 'submit']
    events = case['journal'].events()
    assert len(events) == 3 and events[-1]['game_day'] == NEW
    assert events[-1]['intent_id'] != case['intent']['intent_id']
    again, next_port = run(case, monkeypatch)
    assert again['outcome'] == 'blocked_in_flight' and next_port.calls == []


def test_changed_device_cannot_use_old_disposition(case, monkeypatch):
    reconcile(case)
    result, port = run(case, monkeypatch, changed_device=True)
    assert result['outcome'] == 'blocked_or_failed' and port.calls == []


def test_changed_original_prefix_cannot_use_disposition(case, monkeypatch):
    reconcile(case)
    events = case['journal'].events(); events[0]['at'] += 1
    case['journal'].path.write_text(''.join(json.dumps(x) + '\n' for x in events), encoding='utf-8')
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'blocked_or_failed' and 'history_changed' in result['error']
    assert port.calls == []


def test_recovered_abyss_does_continue_following_modules(case, monkeypatch, tmp_path):
    reconcile(case)
    monkeypatch.setattr(chain, 'current_game_day', lambda *args: NEW)
    monkeypatch.setattr(chain, 'CONTROL_ROOT', tmp_path / 'control')
    calls = []
    def step(tasker, name, *args, **kwargs):
        calls.append(name)
        if name == 'abyss': return run(case, monkeypatch, zero=True)[0]
        return dict(outcome='executed', consumption_sent=True, settlement_verified=True,
                    home_verified=True, goal_complete=True, daily_complete=False)
    monkeypatch.setattr(chain, '_run_step', step)
    cfg = dict(version=1, start='home', device=dict(input_display=2, screenshot_display='123'),
        count_context=None, presets=[dict(kind=k, slot=s, label=k, target_per_stage=n)
            for k, s, n in [('hard', 6, 3), ('deep', 4, 10), ('very_hard', 2, 3)]])
    result = chain.execute_supported(None, cfg, execute=True, game_day=NEW,
        context='user-reported-unreset', directory=tmp_path / ('run-' + '1' * 32),
        selected_modules=['abyss', 'event', 'hard'], rewards_final=False)
    assert calls == ['abyss', 'event', 'hard']
    assert result['supported_chain_complete'] and result['outcome'] == 'complete'
    assert not result['daily_complete']

def test_original_consumed_failure_then_native_shared_context_new_day(case, monkeypatch, tmp_path, frames):
    """Historical pixels plus synthetic zero counters; no live device qualification."""
    import os
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from test_abyss import borrowed_run, zero_home
    evidence_root = os.environ.get('PCR_ABYSS_RECOVERY_ROOT')
    if not evidence_root:
        pytest.skip('Private original evidence is optional; no device access')
    root = Path(evidence_root)
    pins = json.loads((root / 'ORIGINAL_PINS.json').read_text(encoding='utf-8'))
    original_events = [json.loads(line) for line in Path(pins['ledger_path']).read_text(encoding='utf-8').splitlines()]
    original = next(e for e in reversed(original_events) if e['state'] == 'in_flight')
    proof = json.loads((root / 'RECOVERY_PROOF.json').read_text(encoding='utf-8'))
    journal = Journal(tmp_path / 'cloned-original-ledger')
    with journal.locked(): journal.append(original)
    prefix = journal.path.read_bytes()
    reconcile_closed_result(journal, proof, expected_sha256=sha256(prefix).hexdigest(), apply=True)
    monkeypatch.setattr(abyss, 'current_game_day', lambda *args: NEW)
    image = native.read_image(Path(proof['files']['result_frame']['path']))
    controller = SavedImages([[frame] for frame in (frames['09-abyss-home'], frames['03-adventure'],
        frames['05-abyss-page'], frames['07-abyss-preview'], frames['13-abyss-current-execute'],
        image, zero_home(frames['05-abyss-page']), frames['09-abyss-home'])])
    tasker = native.bind(native.load_resource(), controller)
    result = borrowed_run(tasker, journal, original['device'], execute=True)
    (tmp_path / 'original-recovery-shared-context.json').write_text(json.dumps(result,
        ensure_ascii=False, indent=2), encoding='utf-8')
    assert result['outer_receipt']['native_ok'] and result['callback_errors'] == []
    assert result['outcome'] == 'executed' and result['settlement_verified'] and result['home_verified']
    assert len(controller.clicks) == 7 and not controller.swipes
    events = journal.events()
    assert [e['state'] for e in events] == ['in_flight', 'settled', 'in_flight', 'settled']
    assert events[1]['settlement_verified'] is False and events[1]['post_counts_verified'] is False
    assert events[2]['game_day'] == NEW and events[2]['intent_id'] != original['intent_id']
    assert journal.path.read_bytes().startswith(prefix)
    assert sha256(Path(pins['ledger_path']).read_bytes()).hexdigest() == pins['ledger_sha256']
