from copy import deepcopy

import numpy as np
import pytest

from pcr_maa import clan_battle_daily_native as daily
from pcr_maa.clan_battle_calibration import load_calibration
from test_clan_battle_native import context_for
from test_clan_opening import sampled_plan


@pytest.fixture
def november():
    plan = sampled_plan()
    profile = load_calibration(plan, capability='formal')
    plan.update(period='2026-11', phase='D', content_version='202611131613', boss=1)
    profile['calibration_id'] = 'cn-bilibili-202611131613-clan-simulation'
    execution = profile['daily']['execution']
    sample = next(s for s in execution['samples'] if s['page'] == 'result')
    result = execution['pages']['result']
    result['fields']['damage'] = [80, 45, 332, 42]
    result['damage_line'] = dict(sample_sha256=sample['sha256'], verify_roi=[88, 47, 324, 38])
    return profile, plan


@pytest.mark.parametrize('first,second,error', [
    ('265113492×4.5', '265113492×4.5', None),
    ('265113492×4.5', '26511349×4.5', 'damage_mismatch'),
    ('265113492×4.5', '265113492×4.8', 'damage_mismatch'),
    ('265113492×4.5', '265113492', 'field_unknown'),
])
def test_full_formal_result_field_is_read_twice_on_same_frame(november, first, second, error):
    profile, plan = november
    nodes = daily.require_daily_execution(profile, plan)
    name = 'CBDaily_result_damage'
    assert nodes[name]['roi'] == [80, 45, 332, 42]
    assert nodes[name + '_verify']['roi'] == [88, 47, 324, 38]
    assert nodes[name + '_verify']['only_rec']
    hits = {'CBDaily_result_labels_0', 'CBDaily_result_labels_1'}
    context = context_for(nodes, {name: first, name + '_verify': second}, hits)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    page = daily.ClanBattleDailyPage(profile, plan)
    if error:
        with pytest.raises(RuntimeError, match=error):
            page.read(context, image, 'result')
    else:
        assert page.read(context, image, 'result')['damage'] == 265113492


@pytest.mark.parametrize('invalid', ['missing', 'other_page', 'simulation', 'bad_digest', 'same', 'outside', 'unknown_key'])
def test_reject_invalid_turned_over_formal_damage_evidence(november, invalid):
    profile, plan = november
    execution = profile['daily']['execution']
    result = execution['pages']['result']
    line = result['damage_line']
    if invalid == 'missing':
        del result['damage_line']
    elif invalid == 'other_page':
        line['sample_sha256'] = next(s['sha256'] for s in execution['samples'] if s['page'] == 'formation')
    elif invalid == 'simulation':
        execution_sample = next(s for s in execution['samples'] if s['page'] == 'result')
        execution_sample.update(deepcopy(next(s for s in profile['samples'] if s['page'] == 'result')))
        line['sample_sha256'] = execution_sample['sha256']
    elif invalid == 'bad_digest':
        line['sample_sha256'] = '0' * 64
    elif invalid == 'same':
        line['verify_roi'] = list(result['fields']['damage'])
    elif invalid == 'outside':
        line['verify_roi'][0] -= 20
    elif invalid == 'unknown_key':
        line['bypass'] = True
    with pytest.raises(ValueError, match='result_damage_uncalibrated'):
        daily.require_daily_execution(profile, plan)


def test_legacy_formal_damage_requires_matching_content_version():
    plan = sampled_plan()
    profile = load_calibration(plan, capability='formal')
    assert daily.require_daily_execution(profile, plan)['CBDaily_result_damage_verify']['only_rec']
    with pytest.raises(ValueError, match='result_damage_uncalibrated'):
        daily.require_daily_execution(profile, {**plan, 'content_version': '202611131613'})


def test_formal_nameplate_cannot_cross_stages(november):
    profile, plan = november
    profile['boss_targets']['1']['name'] = '厄勒克特拉夫人'
    profile['pages']['boss_detail']['nameplate_levels'] = {'C': [230,235,240,245,250], 'D': [355,360,365,370,375]}
    nodes = daily.daily_pipeline(profile, plan)
    hits = {'CBDaily_boss_detail_labels_' + str(i) for i in range(3)}
    texts = {'CBDaily_boss_detail_boss': '厄勒克特拉夫人 等级.355', 'CBDaily_boss_detail_phase': '第4阶段'}
    page = daily.ClanBattleDailyPage(profile, plan)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert page.read(context_for(nodes, texts, hits), image, 'boss_detail')['boss'] == 1
    texts['CBDaily_boss_detail_boss'] = '厄勒克特拉夫人 等级.230'
    with pytest.raises(RuntimeError, match='target_unknown'):
        page.read(context_for(nodes, texts, hits), image, 'boss_detail')
