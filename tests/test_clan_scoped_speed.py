import pytest

from pcr_maa.clan_battle_calibration import load_calibration
from pcr_maa.clan_battle_native import NativeClanBattle, require_battle_speed
from test_clan_opening import sampled_plan


def target(phase, **extra):
    return dict(period='2026-11', phase=phase, boss=1, **extra)


@pytest.fixture
def scoped():
    return {
        'version': 2,
        'auto_battle': {'speeds': [1, 2]},
        'phase_labels': {'第2阶段': 'B', '第3阶段': 'C', '第4阶段': 'D'},
        'actions': {'select_difficulty': {'choices': {
            phase: {'label': {}, 'selected': {}} for phase in ('B', 'C', 'D')}}},
        'simulation_targets': [target('C', speeds=[1]), target('D', speeds=[2])],
    }


@pytest.mark.parametrize(('phase', 'speed', 'accepted'), [
    ('C', 1, True), ('C', 2, False), ('D', 1, False), ('D', 2, True),
    ('C', 4, False), ('D', 4, False),
])
def test_speed_applies_to_selected_target(scoped, phase, speed, accepted):
    if accepted:
        require_battle_speed(scoped, target(phase), speed)
    else:
        with pytest.raises(RuntimeError, match='^clan_battle_auto_speed_uncalibrated$'):
            require_battle_speed(scoped, target(phase), speed)


def test_bc_uses_only_the_speed_allowed_by_the_selected_phase(scoped):
    job = target('BC')
    scoped['simulation_targets'] = [target('C', speeds=[2])]
    require_battle_speed(scoped, job, 2)
    scoped['simulation_targets'].append(target('B', speeds=[1]))
    require_battle_speed(scoped, job, 1)
    with pytest.raises(RuntimeError, match='^clan_battle_auto_speed_uncalibrated$'):
        require_battle_speed(scoped, job, 2)


@pytest.mark.parametrize('speeds', [[], [True], [1.0], [1, 1], [4], [0], '1', None])
def test_invalid_or_unavailable_target_speeds_are_rejected(scoped, speeds):
    scoped['simulation_targets'][0]['speeds'] = speeds
    with pytest.raises(ValueError, match='^clan_battle_simulation_targets_invalid$'):
        require_battle_speed(scoped, target('C'), 1)


def test_legacy_target_inherits_global_speeds_and_switch_rules(scoped):
    scoped['simulation_targets'][0].pop('speeds')
    require_battle_speed(scoped, target('C'), 2)
    with pytest.raises(RuntimeError, match='^clan_battle_auto_speed_uncalibrated$'):
        require_battle_speed(scoped, target('C', switches=[{'remaining_seconds': 60}]), 2)


def test_normal_simulation_prepare_rejects_speed_before_account_or_device_access(tmp_path):
    job = sampled_plan()
    profile = load_calibration(job)
    profile['simulation_targets'] = [dict(period=job['period'], phase=job['phase'], boss=job['boss'], speeds=[2])]
    port = NativeClanBattle(None, tmp_path, calibration=profile, battle_speed=1)
    with pytest.raises(RuntimeError, match='^clan_battle_auto_speed_uncalibrated$'):
        port.prepare(job)
    assert not port.started
