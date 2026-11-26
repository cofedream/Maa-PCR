"""A synthetic target scope exercises both admission and the observed simulation stage."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil

import pytest

from pcr_maa import native
from pcr_maa.clan_battle_calibration import INDEX_PATH, load_calibration
from pcr_maa.clan_battle_native import NativeClanBattle, calibration_pipeline
from test_clan_battle_calibration import bundle, page_bundle_v2
from test_clan_battle_native import calibration, calibration_v2, own_only_profile, plan
from test_clan_opening import sampled_plan
from test_clan_simulation_navigation import NavigationHarness, page_context


PERIOD = '2026-11'
C1 = dict(period=PERIOD, phase='C', boss=1)


def add_c1_scope(profile):
    choices = profile['actions']['select_difficulty']['choices']
    choices['C'] = deepcopy(choices['B'])
    for phase in ('B', 'C'):
        choices[phase]['selected']['expected'] = f'^selected {phase}$'
    profile['simulation_targets'] = [dict(C1)]
    return profile


def test_bundled_admission_and_five_boss_navigation(page_bundle_v2, plan):
    root, profile, _, _, save = page_bundle_v2
    add_c1_scope(own_only_profile(profile))
    result = profile['pages']['result']
    result['fields']['damage'] = [80, 45, 332, 42]
    result['damage_line'] = dict(sample_sha256=profile['samples'][0]['sha256'], verify_roi=[88, 47, 324, 38])
    save()
    job = dict(plan, **C1)
    loaded = load_calibration(job, root=root)
    nodes = calibration_pipeline(loaded, job)
    assert 'CBStart' in nodes
    for boss in range(1, 6):
        name = loaded['boss_targets'][str(boss)]['name']
        assert re.fullmatch(nodes['CBNavBoss']['expected'], name)
        assert f'CBNavRound{boss}' in nodes and f'CBNavDefeated{boss}' in nodes
    for boss in range(2, 6):
        with pytest.raises(RuntimeError, match='^clan_battle_simulation_target_uncalibrated$'):
            load_calibration(dict(job, boss=boss), root=root)
    for changes, error in [
            ({'switches': [{'remaining_seconds': 80}]}, 'capability_missing:timed'),
            ({'support_mode': 'specified'}, 'support_mode_uncalibrated')]:
        with pytest.raises(RuntimeError, match=error):
            load_calibration(dict(job, **changes), root=root)
    for capability in ('formal', 'carryover'):
        with pytest.raises(RuntimeError, match='capability_missing:' + capability):
            load_calibration(job, root=root, capability=capability)


@pytest.mark.parametrize('changes', [
    {'boss': 2}, {'boss': 3}, {'boss': 4}, {'boss': 5}, {'phase': 'B'}, {'phase': 'D'}, {'phase': 'E'},
    {'period': '2026-12'}])
def test_prepare_stops_before_resource_or_device_use(calibration_v2, plan, tmp_path, changes):
    port = NativeClanBattle(None, tmp_path, calibration=add_c1_scope(calibration_v2))
    with pytest.raises(RuntimeError, match='^clan_battle_simulation_target_uncalibrated$'):
        port.prepare(dict(dict(plan, **C1), **changes))
    assert not port.started


@pytest.mark.parametrize('value', [
    None, {}, [], [None], [True], [{}], [dict(C1, slot=1)], [{'phase': 'C', 'boss': 1}],
    [dict(C1, boss=True)], [dict(C1, boss=1.0)], [dict(C1, boss=0)], [dict(C1, boss=6)],
    [dict(C1, phase='BC')], [dict(C1, phase='D')], [dict(C1, phase=1)], [dict(C1, period='26-11')],
    [dict(C1, period='2026-1')], [dict(C1, period='2026-11\n')], [dict(C1, period='2026-00')],
    [dict(C1, period='2026-13')], [C1, dict(C1)],
    [dict(C1, period=f'{2000+i:04d}-11') for i in range(101)],
])
def test_invalid_scope_fails_closed(calibration_v2, plan, value):
    add_c1_scope(calibration_v2)['simulation_targets'] = value
    with pytest.raises(ValueError, match='^clan_battle_simulation_targets_invalid$'):
        calibration_pipeline(calibration_v2, dict(plan, **C1))


def test_bc_selects_first_reviewed_stage_and_guards_observed_target(calibration_v2, plan):
    job = dict(plan, period=PERIOD, phase='BC', boss=1)
    add_c1_scope(calibration_v2)
    page, frame, nodes, texts, _, context = page_context(calibration_v2, job)
    assert nodes['CBDifficultySelected']['expected'] == '^selected C$'
    page.read(context, frame, 'boss_detail')
    # The fake device currently shows B. Passing through this page for navigation
    # is permitted, but it cannot establish a reviewed, executable target.
    with pytest.raises(RuntimeError, match='target_mismatch|target_uncalibrated'):
        page.read(context, frame, 'target')
    texts['CBField_target_phase'] = '第3阶段'
    assert page.read(context, frame, 'target')['phase'] == 'C'

    calibration_v2['simulation_targets'].append(dict(C1, phase='B'))
    nodes = calibration_pipeline(calibration_v2, job)
    assert nodes['CBDifficultySelected']['expected'] == '^selected B$'


def test_live_c5_can_reach_c1_and_switch_to_reviewed_stage(calibration_v2, plan, tmp_path, monkeypatch):
    job = dict(plan, period=PERIOD, phase='BC', boss=1)
    add_c1_scope(calibration_v2)
    harness = NavigationHarness(calibration_v2, job, tmp_path, monkeypatch, alive=(5,), simulation=True)
    # The menu confirmation in this fixture represents the concrete difficulty
    # control generated above, while the original job keeps its source label BC.
    harness.plan = dict(job, phase='C')
    target, _ = harness.port._open_simulation_target(job)
    assert target['boss'] == 1 and target['phase'] == 'C'
    assert harness.calls.count('CBNavStep') == 4
    assert harness.calls.count('CBAction_confirm_difficulty') == 1


@pytest.mark.parametrize('capability', ['formal', 'carryover'])
@pytest.mark.parametrize('targets', [[C1], None])
def test_formal_and_carryover_ignore_simulation_only_scope(calibration_v2, plan, capability, targets):
    other = dict(plan, period=PERIOD, phase='D', boss=5)
    before = calibration_pipeline(calibration_v2, other, capability=capability)
    add_c1_scope(calibration_v2)['simulation_targets'] = targets
    assert calibration_pipeline(calibration_v2, other, capability=capability) == before


def test_template_shaped_invalid_targets_do_not_invalidate_other_bundled_capabilities(bundle):
    root, profile, entry, index, save = bundle
    # Use the reviewed October install's full page, template, result and daily evidence.
    # Only the isolated install's index and profile will be changed.
    job = sampled_plan()
    installed_index = json.loads((native.ROOT / INDEX_PATH).read_bytes())
    installed_entry = next(row for row in installed_index['entries'] if row['content_version'] == job['content_version'])
    relative = Path(installed_entry['profile_path'])
    images = Path('assets/resource/image/clan-battle') / relative.parent.name
    for directory in (relative.parent, images):
        shutil.copytree(native.ROOT / directory, root / directory)
    index['version'] = installed_index['version']
    entry.update(installed_entry)
    profile.clear()
    profile.update(json.loads((root / relative).read_bytes()))
    save()
    assert load_calibration(job, root=root)['calibration_id'] == entry['calibration_id']
    malformed = {'recognition': 'TemplateMatch'}
    for targets in ([dict(C1, **malformed)], {'nested': malformed}):
        profile['simulation_targets'] = targets
        save()
        for capability in ('formal', 'carryover'):
            loaded = load_calibration(job, root=root, capability=capability)
            assert loaded['daily']['execution']['opening']['samples']
        with pytest.raises(ValueError, match='^clan_battle_simulation_targets_invalid$'):
            load_calibration(job, root=root)


def test_omitted_scope_preserves_existing_boss_and_bc_selection(calibration_v2, plan):
    job = dict(plan, period=PERIOD, phase='BC', boss=5)
    add_c1_scope(calibration_v2).pop('simulation_targets')
    nodes = calibration_pipeline(calibration_v2, job)
    assert 'CBStart' in nodes
    assert nodes['CBDifficultySelected']['expected'] == '^selected B$'


def test_legacy_v1_profile_cannot_declare_a_simulation_target_list(bundle, plan):
    root, profile, _, _, save = bundle
    assert load_calibration(plan, root=root)['version'] == 1
    profile['simulation_targets'] = [dict(C1)]
    save()
    with pytest.raises(ValueError, match='not_reviewed_device_profile|simulation_targets_invalid'):
        load_calibration(plan, root=root)
