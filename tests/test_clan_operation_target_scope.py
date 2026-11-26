"""Operation-specific target lists in a single isolated, hash-checked installation."""
import json
from pathlib import Path
import shutil

import pytest

from pcr_maa import native
from pcr_maa.clan_battle_calibration import INDEX_PATH, load_calibration
from pcr_maa.clan_battle_native import require_battle_speed
from test_clan_battle_calibration import bundle
from test_clan_battle_native import calibration, plan
from test_clan_opening import sampled_plan


def target(phase, boss):
    return dict(period='2026-11', phase=phase, boss=boss)


@pytest.fixture
def scoped_install(bundle):
    # Reuse the distributable October layout and real sample digests; the target lists
    # below are synthetic. This checks the normal loader without using a player account.
    root, profile, entry, index, save = bundle
    installed_index = json.loads((native.ROOT / INDEX_PATH).read_bytes())
    installed = next(row for row in installed_index['entries'] if row['content_version'] == sampled_plan()['content_version'])
    path = Path(installed['profile_path'])
    for directory in (path.parent, Path('assets/resource/image/clan-battle') / path.parent.name):
        shutil.copytree(native.ROOT / directory, root / directory)
    index['version'] = 2
    entry.update(installed)
    entry['capabilities'] = ['simulation', 'formal']
    profile.clear()
    profile.update(json.loads((root / path).read_bytes()))
    profile['simulation_targets'] = [dict(target('C', 1), speeds=[1])] + [
        dict(target('D', boss), speeds=[2]) for boss in (1, 3, 5)]
    profile['formal_targets'] = [target('D', boss) for boss in (1, 3, 5)]
    save()
    return root, profile, entry, save


def test_combined_current_targets_and_speed(scoped_install):
    root, *_ = scoped_install
    for phase, boss, speed in [('C', 1, 1), ('D', 1, 2), ('D', 3, 2), ('D', 5, 2)]:
        job = dict(sampled_plan(), **target(phase, boss))
        simulation = load_calibration(job, root=root)
        require_battle_speed(simulation, job, speed)
        with pytest.raises(RuntimeError, match='clan_battle_auto_speed_uncalibrated'):
            require_battle_speed(simulation, job, 3 - speed)
        if phase == 'D':
            assert load_calibration(job, root=root, capability='formal')['daily']['execution']
        else:
            with pytest.raises(RuntimeError, match='clan_battle_formal_target_uncalibrated'):
                load_calibration(job, root=root, capability='formal')
        with pytest.raises(RuntimeError, match='capability_missing:carryover'):
            load_calibration(job, root=root, capability='carryover')
    for boss in (2, 4):
        job = dict(sampled_plan(), **target('D', boss))
        for capability in ('simulation', 'formal'):
            with pytest.raises(RuntimeError, match=f'clan_battle_{capability}_target_uncalibrated'):
                load_calibration(job, root=root, capability=capability)


def test_formal_and_carryover_have_independent_lists(scoped_install):
    root, profile, entry, save = scoped_install
    entry['capabilities'].append('carryover')
    profile['carryover_targets'] = [target('D', 2)]
    save()
    for capability, boss in [('formal', 3), ('carryover', 2)]:
        job = dict(sampled_plan(), **target('D', boss))
        assert load_calibration(job, root=root, capability=capability)['daily']['execution']
        other = 'carryover' if capability == 'formal' else 'formal'
        with pytest.raises(RuntimeError, match=f'clan_battle_{other}_target_uncalibrated'):
            load_calibration(job, root=root, capability=other)
        with pytest.raises(RuntimeError, match=f'clan_battle_{capability}_target_uncalibrated'):
            load_calibration(dict(job, period='2026-12'), root=root, capability=capability)
    assert load_calibration(dict(sampled_plan(), **target('C', 1)), root=root)['auto_battle']


def test_malformed_target_data_is_local_to_one_operation(scoped_install):
    root, profile, entry, save = scoped_install
    entry['capabilities'].append('carryover')
    job = dict(sampled_plan(), **target('D', 1))
    for capability, other in [('formal', 'carryover'), ('carryover', 'formal')]:
        profile[other + '_targets'] = [target('D', 1)]
        for invalid in (None, [], [dict(target('D', 1), speeds=[2])],
                        [target('D', True)], [target('E', 1)], [target('BC', 1)],
                        [dict(target('D', 1), recognition='TemplateMatch')], [target('D', 1)] * 2):
            profile[capability + '_targets'] = invalid
            save()
            with pytest.raises(ValueError, match=f'clan_battle_{capability}_targets_invalid'):
                load_calibration(job, root=root, capability=capability)
            assert load_calibration(job, root=root, capability=other)['daily']['execution']
            assert load_calibration(job, root=root, capability='simulation')['auto_battle']
