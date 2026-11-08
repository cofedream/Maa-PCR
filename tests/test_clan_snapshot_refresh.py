"""Fresh account reads may renew a fixed daily plan, never replay its attacks."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from test_clan_battle_batch import batch, daily_request, ledgers, Port, rebind, run
from test_clan_consumption_consent import ConsentPort


def snapshot(request, observed_at, name, *, mutate=None, replace=False):
    source = Path(request['slot_plans']['1']['snapshot']['path'])
    value = json.loads(source.read_text(encoding='utf-8'))
    value['observed_at'] = observed_at.isoformat()
    if mutate:
        mutate(value)
    if replace:
        history = source.parent / '.snapshot-history'
        history.mkdir(exist_ok=True)
        (history / (hashlib.sha256(source.read_bytes()).hexdigest() + '.json')).write_bytes(source.read_bytes())
    target = source if replace else source.with_name(name + '.json')
    target.write_text(json.dumps(value), encoding='utf-8')
    for job in request['slot_plans'].values():
        job['snapshot'] = dict(path=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
    rebind(request)
    return target


def clock(monkeypatch):
    value = [datetime(2026, 11, 7, 2, tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return value[0].astimezone(tz) if tz else value[0].replace(tzinfo=None)
    monkeypatch.setattr(batch.simulation, 'datetime', Clock)
    return value


def first_attack(tmp_path, request, now):
    port = Port(request, now=now)
    port.available = {1: True, 2: False, 3: False}
    result, _ = run(tmp_path, request, port, now=now)
    assert result['outcome'] == 'no_available_target' and result['regular_settled'] == 1
    return port


def test_verified_attack_settles_when_snapshot_expires_during_battle(tmp_path, daily_request, monkeypatch):
    current = clock(monkeypatch)
    snapshot(daily_request, current[0] - timedelta(days=1, seconds=-1), 'almost-expired')
    port = Port(daily_request, now=current[0])
    port.available = {1: True, 2: False, 3: False}
    def expires(result, owner):
        current[0] += timedelta(seconds=2)
    port.result_change = expires
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['regular_settled'] == 1, result
    assert [row['state'] for row in batch.Journal(ledgers(tmp_path)[0].parent).events()] == ['in_flight', 'settled']
    assert len(port.executions) == 1


def test_refreshed_snapshot_resumes_fixed_plan_and_preserves_old_receipts(tmp_path, daily_request, monkeypatch):
    current = clock(monkeypatch)
    snapshot(daily_request, current[0] - timedelta(hours=23, minutes=59), 'original')
    port = first_attack(tmp_path, daily_request, current[0])
    path = ledgers(tmp_path)[0]
    previous = path.read_bytes()
    frozen = (path.parent / 'batch.json').read_bytes()
    current[0] += timedelta(minutes=2)
    with pytest.raises(ValueError, match='snapshot_expired'):
        run(tmp_path, daily_request, port, now=current[0])
    snapshot(daily_request, current[0], 'refreshed', replace=True)
    port.clock = current[0]
    port.available = {1: True, 2: True, 3: False}
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['regular_settled'] == 2 and result['outcome'] == 'no_available_target', result
    second_prefix = path.read_bytes()
    current[0] += timedelta(minutes=1)
    snapshot(daily_request, current[0], 'refreshed-again', replace=True)
    port.clock = current[0]
    port.available[3] = True
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['outcome'] == 'complete' and result['regular_settled'] == 3, result
    assert [row['action']['slot'] for row in port.executions] == [1, 2, 3]
    assert path.read_bytes().startswith(second_prefix) and second_prefix.startswith(previous)
    assert (path.parent / 'batch.json').read_bytes() == frozen
    settled = path.read_bytes()
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['outcome'] == 'complete' and len(port.executions) == 3
    assert path.read_bytes() == settled


def test_refresh_authorizes_old_carryover_with_current_request_once(tmp_path, daily_request, monkeypatch):
    current = clock(monkeypatch)
    snapshot(daily_request, current[0] - timedelta(hours=23, minutes=59), 'original')
    old_request = deepcopy(daily_request)
    port = ConsentPort(daily_request)
    port.clock = current[0]
    port.instances = {slot: (f'original-{slot}', current[0] - timedelta(seconds=30)) for slot in (1, 2, 3)}
    port.issue, port.consent_fault = {1}, 'deny_carry'
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['outcome'] == 'remind_carryover' and result['regular_settled'] == 1
    path = ledgers(tmp_path)[0]
    prefix = path.read_bytes()
    old_grant = batch.Journal(path.parent).events()[1]['result']['grant']
    assert old_grant['daily_plan_hash'] == old_request['confirmed_for']
    assert old_grant['slot_confirmed_for'] == old_request['slot_plans']['1']['confirmed_for']
    current[0] += timedelta(minutes=2)
    snapshot(daily_request, current[0], 'refreshed', replace=True)
    port.clock, port.consent_fault = current[0], None
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['outcome'] == 'complete' and result['carryover_settled'] == 1, result
    assert [(row['action']['kind'], row['action']['slot']) for row in port.executions] == [
        ('regular', 1), ('carryover', 1), ('regular', 2), ('regular', 3)]
    carry = port.executions[1]
    approval = carry['ready']['evidence']['consumption_authorization']
    assert carry['daily_plan_hash'] == approval['daily_plan_hash'] == daily_request['confirmed_for']
    assert approval['preview']['actions'][0]['source_intent_id'] == old_grant['source_intent_id']
    assert approval['preview']['actions'][0]['using_unit'] == old_grant['using_unit']
    assert path.read_bytes().startswith(prefix)
    settled = path.read_bytes()
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['outcome'] == 'complete' and len(port.executions) == 4
    assert path.read_bytes() == settled


@pytest.mark.parametrize('fault', ['missing', 'changed'])
def test_refreshed_history_requires_original_snapshot_bytes(tmp_path, daily_request, monkeypatch, fault):
    current = clock(monkeypatch)
    original = snapshot(daily_request, current[0], 'original')
    first_attack(tmp_path, daily_request, current[0])
    digest = daily_request['slot_plans']['1']['snapshot']['sha256']
    current[0] += timedelta(minutes=1)
    snapshot(daily_request, current[0], 'refreshed', replace=True)
    archive = original.parent / '.snapshot-history' / (digest + '.json')
    if fault == 'missing':
        archive.unlink()
    else:
        archive.write_text('{}', encoding='utf-8')
    before = ledgers(tmp_path)[0].read_bytes()
    port = Port(daily_request, now=current[0])
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['error'] in ('clan_battle_source_file_missing', 'clan_battle_source_changed')
    assert not port.observations and not port.executions
    assert ledgers(tmp_path)[0].read_bytes() == before


def test_new_consumption_cannot_use_archived_snapshot(tmp_path, daily_request, monkeypatch):
    current = clock(monkeypatch)
    snapshot(daily_request, current[0], 'original')
    old_request = deepcopy(daily_request)
    current[0] += timedelta(minutes=1)
    snapshot(daily_request, current[0], 'refreshed', replace=True)
    with pytest.raises(ValueError, match='clan_battle_source_changed'):
        run(tmp_path, old_request, now=current[0])
    assert not ledgers(tmp_path)


@pytest.mark.parametrize('fault', ['missing', 'changed'])
def test_intermediate_request_version_must_remain_intact(tmp_path, daily_request, monkeypatch, fault):
    current = clock(monkeypatch)
    snapshot(daily_request, current[0], 'original')
    port = first_attack(tmp_path, daily_request, current[0])
    current[0] += timedelta(minutes=1)
    snapshot(daily_request, current[0], 'second', replace=True)
    port.clock, port.available[2] = current[0], True
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['regular_settled'] == 2
    path = ledgers(tmp_path)[0]
    sidecar = path.parent / ('batch-' + daily_request['confirmed_for'] + '.json')
    if fault == 'missing':
        sidecar.unlink()
    else:
        value = json.loads(sidecar.read_text(encoding='utf-8'))
        value['request']['slot_plans']['2']['auto'] = False
        sidecar.write_text(json.dumps(value), encoding='utf-8')
    before = path.read_bytes()
    current[0] += timedelta(minutes=1)
    snapshot(daily_request, current[0], 'third', replace=True)
    again = Port(daily_request, now=current[0])
    result, _ = run(tmp_path, daily_request, again, now=current[0])
    assert result.get('error')
    assert not again.observations and not again.executions
    assert path.read_bytes() == before


@pytest.mark.parametrize('field', ['team_label', 'auto', 'stars'])
def test_snapshot_refresh_cannot_change_reviewed_plan(tmp_path, daily_request, monkeypatch, field):
    current = clock(monkeypatch)
    snapshot(daily_request, current[0], 'original')
    first_attack(tmp_path, daily_request, current[0])
    before = ledgers(tmp_path)[0].read_bytes()
    snapshot(daily_request, current[0], 'refreshed')
    daily_request['slot_plans']['2'][field] = {'team_label': 'different', 'auto': False, 'stars': [4] * 5}[field]
    rebind(daily_request)
    port = Port(daily_request, now=current[0])
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['error'] == 'daily_consumed_batch_plan_immutable'
    assert not port.observations and not port.executions
    assert ledgers(tmp_path)[0].read_bytes() == before


def test_snapshot_refresh_cannot_change_resolved_support(tmp_path, daily_request, monkeypatch):
    current = clock(monkeypatch)
    daily_request['slot_plans']['1']['support_mode'] = 'qsjs'
    snapshot(daily_request, current[0], 'original', mutate=lambda value:
        value.update(units=[row for row in value['units'] if row['unit_id'] != 105]))
    first_attack(tmp_path, daily_request, current[0])
    snapshot(daily_request, current[0], 'refreshed', mutate=lambda value:
        value['units'].append(dict(unit_id=105, battle_rarity=5)))
    port = Port(daily_request, now=current[0])
    result, _ = run(tmp_path, daily_request, port, now=current[0])
    assert result['error'] == 'daily_consumed_batch_plan_immutable'
    assert not port.observations and not port.executions


def test_refreshed_snapshot_identity_and_previous_unknown_still_block(tmp_path, daily_request, monkeypatch):
    current = clock(monkeypatch)
    snapshot(daily_request, current[0], 'original')
    port = Port(daily_request, now=current[0])
    port.fail = 'synthetic_unknown'
    run(tmp_path, daily_request, port, now=current[0])
    before = ledgers(tmp_path)[0].read_bytes()
    snapshot(daily_request, current[0], 'refreshed')
    again = Port(daily_request, now=current[0])
    result, _ = run(tmp_path, daily_request, again, now=current[0])
    assert result['error'] == 'daily_previous_result_unknown', result
    assert not again.observations and not again.executions
    assert ledgers(tmp_path)[0].read_bytes() == before
    snapshot(daily_request, current[0], 'wrong-account', mutate=lambda value:
        value.update(account_id='987654321'))
    with pytest.raises(ValueError, match='snapshot_identity_mismatch'):
        run(tmp_path, daily_request, again, now=current[0])
    assert ledgers(tmp_path)[0].read_bytes() == before
