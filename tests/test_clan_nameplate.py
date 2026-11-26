"""Exact names and phase-specific levels on a simulated clan boss detail page."""
import re

import numpy as np
import pytest

from pcr_maa import clan_battle_native as module
from test_clan_battle_native import calibration, calibration_v2, plan, v2_context


@pytest.fixture
def plated(calibration_v2):
    profile = calibration_v2
    profile['pages']['boss_detail']['nameplate_levels'] = {'B': [230, 235, 240, 245, 250]}
    names = [re.escape(row['name']) for row in profile['boss_targets'].values()]
    for key in ('boss_detail', 'target'):
        spec = profile['pages'][key]
        spec['labels'].append(dict(recognition='OCR', roi=spec['fields']['boss'],
                                   expected='^(?:' + '|'.join(names) + ')$'))
    return profile


def bound_detail(profile, plan, plated=True):
    nodes, texts, guards, context = v2_context(profile, plan)
    if plated:
        for screen in ('boss_detail', 'target'):
            guards.add(f'CBLabel_{screen}_2')
            texts[f'CBField_{screen}_boss'] = '合成魔物3等级.240'
        texts['CBNavBoss'] = '合成魔物3等级.240'
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    page = module.ClanBattlePage(plan, profile)
    page.read(context, frame, 'lobby')
    page.advance('open_boss')
    return page, frame, context, texts, nodes


def test_nameplate_nodes_read_the_whole_line_and_keep_numeric_slot(plated, plan):
    page, frame, context, texts, nodes = bound_detail(plated, plan)
    for name in ('CBNavBoss', 'CBField_boss_detail_boss', 'CBField_target_boss',
                 'CBLabel_boss_detail_2', 'CBLabel_target_2'):
        assert nodes[name]['only_rec'] is True
        assert nodes[name]['roi'] == plated['pages']['boss_detail']['fields']['boss']
        assert re.fullmatch(nodes[name]['expected'], '合成魔物3等级.240')
        assert not re.fullmatch(nodes[name]['expected'], '合成魔物3等级.240人')

    texts['CBNavBoss'] = '合成魔物2等级.235'
    assert page.read_navigation(context, frame) == dict(page='boss_detail', period=plan['period'],
                                                       boss=2, phase='B', simulation=True)
    assert page.current_page == 'lobby'
    assert page.read(context, frame, 'boss_detail')['boss'] == plan['boss']
    result = page.read(context, frame, 'target')
    assert result['selected_difficulty'] == dict(phase='B', boss=3, simulation=True)


@pytest.mark.parametrize('route', ['navigation', 'boss_detail', 'target'])
@pytest.mark.parametrize('bad', ['wrong_name', 'wrong_level', 'wrong_phase', 'wrong_period',
                                 'trailing_text', 'missing', 'low_score'])
def test_nameplate_never_authorizes_uncertain_identity(plated, plan, route, bad):
    page, frame, context, texts, _ = bound_detail(plated, plan)
    if route == 'target':
        page.read(context, frame, 'boss_detail')
    field = 'CBNavBoss' if route == 'navigation' else f'CBField_{route}_boss'
    phase = 'CBField_target_phase' if route == 'target' else 'CBField_boss_detail_phase'
    if bad == 'wrong_name':
        texts[field] = '合成魔物4等级.240'
    elif bad == 'wrong_level':
        texts[field] = '合成魔物3等级.245'
    elif bad == 'wrong_phase':
        texts[phase] = '第3阶段'
    elif bad == 'wrong_period':
        page.bound['period'] = '2026-08'
    elif bad == 'trailing_text':
        texts[field] = '合成魔物3等级.240人'
    elif bad == 'missing':
        texts.pop(field)
    else:
        read = context.run_recognition

        def low_score(name, image):
            result = read(name, image)
            if name == field:
                result.raw_detail['best']['score'] = .899
            return result

        context.run_recognition = low_score
    with pytest.raises(RuntimeError):
        if route == 'navigation':
            page.read_navigation(context, frame)
        else:
            page.read(context, frame, route)


@pytest.mark.parametrize('levels', [{}, {'B': [230, 235, 240, 245]},
                                    {'B': [230, False, 240, 245, 250]},
                                    {'C': [1, 2, 3, 4, 10000]},
                                    {'unknown': [230, 235, 240, 245, 250]}])
def test_malformed_nameplate_mapping_is_rejected(plated, plan, levels):
    plated['pages']['boss_detail']['nameplate_levels'] = levels
    with pytest.raises(ValueError):
        module.calibration_pipeline(plated, plan)


def test_duplicate_canonical_name_is_rejected(plated, plan):
    plated['boss_targets']['1']['name'] = plated['boss_targets']['2']['name']
    with pytest.raises(ValueError):
        module.calibration_pipeline(plated, plan)


@pytest.mark.parametrize('page_name', ['boss_detail', 'target'])
def test_shared_level_cannot_substitute_for_planned_boss(plated, plan, page_name):
    plated['pages']['boss_detail']['nameplate_levels']['B'][3] = 240
    page, frame, context, texts, _ = bound_detail(plated, plan)
    texts['CBNavBoss'] = '合成魔物4-等级.240'
    assert page.read_navigation(context, frame)['boss'] == 4
    if page_name == 'target':
        page.read(context, frame, 'boss_detail')
    texts[f'CBField_{page_name}_boss'] = '合成魔物4-等级.240'
    with pytest.raises(RuntimeError):
        page.read(context, frame, page_name)


def test_level_of_same_slot_in_another_calibrated_phase_is_rejected(plated, plan):
    plated['pages']['boss_detail']['nameplate_levels']['C'] = [330, 335, 340, 345, 350]
    page, frame, context, texts, _ = bound_detail(plated, plan)
    texts['CBField_boss_detail_boss'] = '合成魔物3等级.340'
    texts['CBNavBoss'] = '合成魔物3等级.340'
    with pytest.raises(RuntimeError):
        page.read(context, frame, 'boss_detail')
    with pytest.raises(RuntimeError):
        page.read_navigation(context, frame)


def test_legacy_detail_keeps_plain_boss_names(calibration_v2, plan):
    page, frame, context, _, _ = bound_detail(calibration_v2, plan, plated=False)
    assert page.read_navigation(context, frame)['boss'] == 3
    assert page.read(context, frame, 'boss_detail')['boss'] == 3
    assert page.read(context, frame, 'target')['boss'] == 3


@pytest.mark.parametrize('page', ['boss_detail', 'target'])
@pytest.mark.parametrize('change', ['missing', 'wrong_roi', 'partial_names'])
def test_level_mapping_requires_a_matching_name_anchor(plated, plan, page, change):
    labels = plated['pages'][page]['labels']
    if change == 'missing':
        labels.pop()
    elif change == 'wrong_roi':
        labels[-1]['roi'] = [10, 10, 60, 20]
    else:
        labels[-1]['expected'] = '^合成魔物3$'
    with pytest.raises(ValueError):
        module.calibration_pipeline(plated, plan)
