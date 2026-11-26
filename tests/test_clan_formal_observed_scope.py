from copy import deepcopy
from types import SimpleNamespace

import pytest

from pcr_maa import clan_battle_calibration
from pcr_maa.clan_battle_daily_native import NativeClanDailyAdapter
from pcr_maa.clan_battle_native import _require_target_scope
from pcr_maa.game_day import current_game_day


def scoped_owner():
    port = object.__new__(NativeClanDailyAdapter)
    port.pending = None
    port.calibration = {
        'version': 2,
        'phase_labels': {'第2阶段': 'B', '第3阶段': 'C'},
        'formal_targets': [{'period': '2026-11', 'phase': 'C', 'boss': 3}],
        'carryover_targets': [{'period': '2026-11', 'phase': 'B', 'boss': 3}],
        'daily': {'execution': {'opening': {'start_seconds': [90]}}},
    }
    job = dict(period='2026-11', phase='BC', boss=3, round_min=1, round_max=22)
    return port, job


@pytest.mark.parametrize('kind,phase,accepted', [
    ('regular', 'B', False), ('regular', 'C', True),
    ('carryover', 'C', False), ('carryover', 'B', True),
])
def test_bc_draft_is_checked_against_observed_target_before_selection(kind, phase, accepted):
    port, job = scoped_owner()
    original = deepcopy(job)
    capability = 'formal' if kind == 'regular' else 'carryover'
    assert _require_target_scope(port.calibration, job, capability) in ('B', 'C')
    action = dict(kind=kind, phase=phase, boss=job['boss'], round=10, instance_id='observed',
                  start_seconds=90 if kind == 'regular' else 49)
    port.last_bosses = {job['boss']: dict(action, available=True)}
    port.enter_lobby = lambda: None
    port.configure_slot = lambda job, capability: None
    moves = []
    port.prepare_inheritance = lambda: moves.append('inheritance')
    port.page = SimpleNamespace(bound=None)

    def stop_at_target(node, **kwargs):
        moves.append(node)
        raise StopIteration

    port.run = stop_at_target
    if accepted:
        with pytest.raises(StopIteration):
            port.prepare(action, job, {})
        assert moves == ['inheritance', 'CBAction_open_boss']
    else:
        with pytest.raises(RuntimeError, match='^clan_battle_' + capability + '_target_uncalibrated$'):
            port.prepare(action, job, {})
        assert not moves
    assert job == original and port.pending is None


def test_prepare_preserves_original_job_phase_and_boss():
    port, job = scoped_owner()
    for altered in (dict(job, phase='B'), dict(job, boss=2)):
        with pytest.raises(RuntimeError, match='^clan_battle_daily_target_changed$'):
            port._require_observed_target(port.calibration, altered, dict(phase='C', boss=3), 'formal')


@pytest.mark.parametrize('phase,blocked', [('B', {}), ('C', {2: 'carryover_capability_not_calibrated'})])
def test_live_carryover_scope_can_hold_bc_without_altering_plan(monkeypatch, phase, blocked):
    port, job = scoped_owner()
    port.last_bosses = {3: dict(boss=3, phase=phase, round=10, available=True)}
    monkeypatch.setattr(clan_battle_calibration, 'load_calibration', lambda job, capability: port.calibration)
    assert port.blocked_carryovers({'slot_plans': {'1': job}}, {2: {'source_slot': 1}}) == blocked
    assert job['phase'] == 'BC'


@pytest.mark.parametrize('kind,phase,capability', [('regular', 'B', 'formal'), ('carryover', 'C', 'carryover')])
def test_last_start_gate_rejects_out_of_scope_phase_without_sending_input(monkeypatch, kind, phase, capability):
    from pcr_maa import clan_battle_daily_native
    port, job = scoped_owner()
    port.action = dict(kind=kind, boss=3, phase=phase)
    intent = dict(action=deepcopy(port.action), game_day=current_game_day(port.now()))
    monkeypatch.setattr(clan_battle_calibration, 'load_calibration', lambda job, capability: port.calibration)
    monkeypatch.setattr(clan_battle_daily_native, 'require_daily_execution', lambda *args, **kwargs: {})
    port.run = lambda *args, **kwargs: pytest.fail('unexpected game input')
    with pytest.raises(RuntimeError, match='^clan_battle_' + capability + '_target_uncalibrated$'):
        port._execute_once(intent, job)
    assert port.pending is None
