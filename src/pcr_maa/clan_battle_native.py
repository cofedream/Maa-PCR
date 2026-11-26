"""Owner-only clan simulation port. Uncalibrated clan pages never provide input."""
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import time
from uuid import uuid4

from maa.custom_recognition import CustomRecognition
from copy import deepcopy

from . import native
from .frame_recognition import same_frame_context
from .guide_trial_native import NativeGuideTrial
from .clan_battle_recovery import ClanDisconnected, observe_disconnect_after_failure, recover_disconnect
from .labyrinth.game_session import game_session_pipeline
from .labyrinth.profile import PROFILE_NODE, profile_player_id


PAGES = ('adventure', 'lobby', 'target', 'formation', 'support', 'battle', 'result', 'closed')
V2_PAGES = (*PAGES, 'boss_detail', 'difficulty')
FIELDS = {
    'period': r'^20[0-9]{2}[-年](?:0[1-9]|1[0-2])月?$',
    'phase': r'^(?:B|C|BC|D|E)$',
    'round': r'^(?:第\s*)?[1-9][0-9]*(?:\s*(?:周目|轮))?$',
    'boss': r'^(?:BOSS\s*)?[1-5]$',
    'simulation': r'^模拟战$',
    'damage': r'^[0-9][0-9,，\s]*$',
    'remaining_seconds': r'^(?:1[:：](?:[0-2][0-9]|30)|0[:：][0-5][0-9])$',
}
OPENING_CLOCK_PATTERN = r'^(?:1[:：]?(?:[0-2][0-9]|30)|0[:：]?[0-5][0-9])$'
RESULT_DAMAGE_LINE_PATTERN = r'^\s*([0-9]+(?:[,，][0-9]{3})*)\s*[×xX]\s*([0-9]+[.．][0-9]+)\s*$'
ACTION_PAGES = dict(open_clan='adventure', open_boss='lobby', open_simulation='target',
                    open_support='formation', support_back='support', start='formation',
                    characters_top='formation', characters_next='formation',
                    support_top='support', support_next='support', return_to_target='result')
V2_ACTION_PAGES = dict(ACTION_PAGES, open_simulation='boss_detail', open_formation='target',
                      back_to_lobby='boss_detail', back_to_adventure='lobby',
                      open_difficulty='boss_detail', select_difficulty='difficulty',
                      confirm_difficulty='difficulty', disable_live_state='target')
V2_DESTINATIONS = dict(open_clan='lobby', open_boss='boss_detail', open_simulation='target',
                       open_formation='formation', open_support='support', support_back='formation',
                       start='battle', return_to_target='lobby', open_difficulty='difficulty',
                       select_difficulty='difficulty', confirm_difficulty='boss_detail', disable_live_state='target')
IDENTITY_FIELDS = ('period', 'phase', 'round', 'boss', 'simulation')
CALIBRATION_GAPS = (
    '公会战入口与关闭页面', '期次、阶段、周目和BOSS编号', '模拟战模式与正式战排除标记',
    '编队空位、星级、自有/支援标记及列表翻页', '支援编号、星级及可用状态',
    '模拟结果伤害页及返回模拟目标后继', '会战HUD与秒级读取/输入延迟',
)


def result_damage_line(text, verify):
    matches = [re.fullmatch(RESULT_DAMAGE_LINE_PATTERN, item) if type(item) is str else None
        for item in (text, verify)]
    if any(match is None for match in matches):
        raise RuntimeError('clan_battle_result_damage_unknown')
    readings = [(int(match.group(1).replace(',', '').replace('，', '')),
        match.group(2).replace('．', '.')) for match in matches]
    if readings[0] != readings[1]:
        raise RuntimeError('clan_battle_result_damage_mismatch')
    return readings[0][0]


def result_damage_nodes(roi, name):
    if roi != [88, 47, 162, 38]:
        raise ValueError('clan_battle_result_damage_uncalibrated')
    # The old damage box clips long numbers; compare two full same-frame reads.
    x, y, w, h = roi
    return _damage_line_nodes(name, [x-8, y-2, w*2+8, h+4], [x, y, w*2, h])


def _damage_line_nodes(name, line, verification):
    nodes = {
        name: _node(dict(roi=line), expected=RESULT_DAMAGE_LINE_PATTERN),
        name + '_verify': _node(dict(roi=verification), expected=RESULT_DAMAGE_LINE_PATTERN),
    }
    nodes[name + '_verify']['only_rec'] = True
    return nodes


def _simulation_damage_nodes(calibration, result, line, content_version=None):
    name = 'CBField_result_damage'
    if 'damage_line' not in result:
        if (calibration['evidence_kind'] == 'device_saved_frame'
                and (content_version != '202610141448'
                     or calibration['calibration_id'] != 'cn-bilibili-202610141448-clan-simulation')):
            raise ValueError('clan_battle_result_damage_uncalibrated')
        return result_damage_nodes(line, name)

    layout = result['damage_line']
    if (type(layout) is not dict or set(layout) != {'sample_sha256', 'verify_roi'}
            or not any(s['page'] == 'result' and s['sha256'] == layout['sample_sha256']
                       for s in calibration['samples'])):
        raise ValueError('clan_battle_result_damage_uncalibrated')
    # Both reads target the same printed line on the reviewed result frame.
    x, y, w, h = _roi(line)
    vx, vy, vw, vh = _roi(layout['verify_roi'])
    if (layout['verify_roi'] == line or vx < x or vy < y
            or vx + vw > x + w or vy + vh > y + h):
        raise ValueError('clan_battle_result_damage_uncalibrated')
    return _damage_line_nodes(name, line, layout['verify_roi'])


def _roi(value):
    if (type(value) is not list or len(value) != 4 or any(type(n) is not int for n in value)
            or value[0] < 0 or value[1] < 0 or value[2] <= 0 or value[3] <= 0
            or value[0]+value[2] > 1280 or value[1]+value[3] > 720):
        raise ValueError('clan_battle_calibration_roi_invalid')
    return value


def _node(spec, *, expected=None, ocr_threshold=.9):
    if type(spec) is not dict:
        raise ValueError('clan_battle_calibration_node_invalid')
    algorithm = spec.get('recognition', 'OCR')
    value = dict(recognition=algorithm, roi=_roi(spec.get('roi')), action='DoNothing', next=[],
                 timeout=1000, pre_delay=0, post_delay=0)
    if algorithm == 'OCR':
        text = expected if expected is not None else spec.get('expected')
        if type(text) is not str or not text.startswith('^') or not text.endswith('$'):
            raise ValueError('clan_battle_calibration_label_invalid')
        re.compile(text)
        value.update(expected=text, threshold=ocr_threshold)
    elif algorithm == 'TemplateMatch' and expected is None:
        name, digest = spec.get('template'), spec.get('sha256')
        names, digests = (name, digest) if type(name) is list else ([name], [digest])
        if type(names) is not list or not 1 <= len(names) <= 16 or type(digests) is not list or len(names) != len(digests):
            raise ValueError('clan_battle_calibration_template_invalid')
        for filename, expected_digest in zip(names, digests):
            if type(filename) is not str or Path(filename).is_absolute() or '..' in Path(filename).parts:
                raise ValueError('clan_battle_calibration_template_invalid')
            path = native.RESOURCE / 'image' / filename
            if (type(expected_digest) is not str or not re.fullmatch('[0-9a-f]{64}', expected_digest)
                    or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_digest):
                raise ValueError('clan_battle_calibration_template_changed')
        value.update(template=name, threshold=.9)
    else:
        raise ValueError('clan_battle_calibration_algorithm_invalid')
    return value


def _difficulty_phase(calibration, plan):
    choices = calibration['actions']['select_difficulty'].get('choices')
    if (type(choices) is not dict or not choices or not set(choices) <= {'B', 'C', 'D', 'E'}
            or any(type(row) is not dict or set(row) != {'label', 'selected'} for row in choices.values())):
        raise ValueError('clan_battle_calibration_difficulty_invalid')
    allowed = ('B', 'C') if plan['phase'] == 'BC' else (plan['phase'],)
    if 'simulation_targets' in calibration:
        rows = calibration['simulation_targets']
        if calibration['version'] != 2 or type(rows) is not list or not 1 <= len(rows) <= 100:
            raise ValueError('clan_battle_simulation_targets_invalid')
        reviewed = set()
        for row in rows:
            if (type(row) is not dict or set(row) != {'period', 'phase', 'boss'}
                    or type(row['period']) is not str or not re.fullmatch(r'[0-9]{4}-(?:0[1-9]|1[0-2])', row['period'])
                    or type(row['phase']) is not str or row['phase'] not in choices
                    or type(row['boss']) is not int or not 1 <= row['boss'] <= 5):
                raise ValueError('clan_battle_simulation_targets_invalid')
            target = row['period'], row['phase'], row['boss']
            if target in reviewed:
                raise ValueError('clan_battle_simulation_targets_invalid')
            reviewed.add(target)
        for phase in allowed:
            if (plan.get('period'), phase, plan.get('boss')) in reviewed:
                return phase
        raise RuntimeError('clan_battle_simulation_target_uncalibrated')
    phase = next((phase for phase in allowed if phase in choices), None)
    if phase is None:
        raise RuntimeError('clan_battle_difficulty_unavailable')
    return phase


def _difficulty_choice(calibration, plan):
    phase = _difficulty_phase(calibration, plan)
    return calibration['actions']['select_difficulty']['choices'][phase]


def auto_battle_pipeline(calibration, plan):
    spec = calibration.get('auto_battle')
    if spec is None:
        return {}
    if (calibration.get('version') != 2 or type(spec) is not dict
            or set(spec) - {'inheritance', 'empty_tp', 'paused_boss_roi', 'paused_clock_alternate_roi'} != {'speeds', 'opening_guards', 'paused_guards', 'auto_on', 'auto_off', 'speed', 'set_badge', 'samples'}
            or type(spec['speeds']) is not list or not spec['speeds'] or 1 not in spec['speeds']
            or any(type(n) is not int or n not in (1, 2, 4) for n in spec['speeds'])
            or len(set(spec['speeds'])) != len(spec['speeds'])
            or type(spec['speed']) is not dict or set(spec['speed']) != {'1', '2', '4'}):
        raise ValueError('clan_battle_auto_calibration_invalid')
    if any(key not in calibration['pages']['battle'] for speed, key in ((2, 'speed_two'), (4, 'speed_four')) if speed in spec['speeds']):
        raise ValueError('clan_battle_auto_speed_calibration_missing')
    nodes = {}
    for group, count in (('opening_guards', 3), ('paused_guards', 4)):
        if type(spec[group]) is not list or len(spec[group]) not in ((3, 4) if group == 'paused_guards' else (count,)):
            raise ValueError('clan_battle_auto_calibration_invalid')
        for index, marker in enumerate(spec[group]):
            nodes[f'CBOpening_{group}_{index}'] = _node(marker)
    clock = spec['paused_guards'][2]
    nodes['CBOpeningClock'] = _node({'roi': _roi(clock['roi'])}, expected=OPENING_CLOCK_PATTERN)
    nodes['CBOpeningClockScaled'] = _node({'roi': [0, 0, clock['roi'][2]*2, clock['roi'][3]*2]}, expected=OPENING_CLOCK_PATTERN)
    if 'paused_clock_alternate_roi' in spec:
        nodes['CBOpeningClockAlternate'] = _node({'roi': spec['paused_clock_alternate_roi']}, expected=OPENING_CLOCK_PATTERN)
    nodes['CBOpeningBoss'] = _node(dict(roi=spec.get('paused_boss_roi', [805, 160, 135, 33])),
        expected='^' + re.escape(calibration['boss_targets'][str(plan['boss'])]['name']) + '$')
    for name in ('auto_on', 'auto_off'):
        nodes['CBOpening_'+name] = _node(spec[name])
    for speed, marker in spec['speed'].items():
        nodes['CBOpeningSpeed'+speed] = _node(marker, expected='^'+speed+'倍速$')
    for index in range(5):
        marker = deepcopy(spec['set_badge'])
        marker['roi'] = _roi(marker['roi']).copy()
        marker['roi'][0] += 116*index
        nodes['CBOpeningSet'+str(index)] = _node(marker)
    for name, mode, click in (('CBOpeningRead', 'opening_read', False), ('CBOpeningConfirmed', 'opening_read', False),
            ('CBOpeningAuto', 'opening_auto', True), ('CBOpeningSet', 'opening_set', True),
            ('CBOpeningSpeed', 'opening_speed', True), ('CBResumeOpening', 'opening_resume', True)):
        nodes[name] = dict(recognition='Custom', custom_recognition='ClanBattleCharacters',
            custom_recognition_param=dict(mode=mode), action='Click' if click else 'DoNothing', target=True,
            next=['CBOpeningConfirmed'] if name in ('CBOpeningAuto', 'CBOpeningSet', 'CBOpeningSpeed') else [],
            timeout=15000, pre_delay=0, post_delay=300 if click else 0,
            on_error=['LabyDisconnect6002Observed'])
        if click:
            nodes[name]['max_hit'] = 1
    nodes['CBOpeningObserve'] = dict(recognition='Custom', custom_recognition='ClanBattleCharacters',
        custom_recognition_param=dict(mode='opening_observe'), action='DoNothing', next=[],
        timeout=1000, pre_delay=0, post_delay=0)
    # Retain the existing Menu brightness check alongside the clock check.
    nodes['CBOpeningInputReady'] = dict(recognition='ColorMatch', roi=[1180, 22, 45, 24], method=4,
        lower=[220, 220, 220], upper=[255, 255, 255], count=400, action='DoNothing', next=[], pre_delay=0, post_delay=0)
    # Wait for the clock to start; this does not prove anything about UB timing.
    nodes['CBOpeningReadyClock'] = _node({'roi': calibration['pages']['battle']['fields']['remaining_seconds']},
        expected=r'^(?:1[:：][0-2][0-9]|0[:：](?:0[1-9]|[1-5][0-9]))$')
    opening_guards = [f'CBOpening_opening_guards_{i}' for i in range(3)]
    # On the usual HUD the positive running clock implies the general clock reading
    # on the very same pixels. Keep independent or more restrictive profile guards.
    if nodes[opening_guards[-1]] == dict(nodes['CBOpeningReadyClock'], expected=FIELDS['remaining_seconds']):
        opening_guards.pop()
    opening_guards.append('CBOpeningReadyClock')
    nodes['CBOpeningMatch'] = dict(recognition='Custom', custom_recognition='ClanBattleCharacters',
        custom_recognition_param=dict(mode='opening_match'), action='DoNothing', next=[],
        timeout=1000, pre_delay=0, post_delay=0)
    nodes['CBOpeningRetryGuard'] = dict(recognition='Custom', custom_recognition='ClanBattleCharacters',
        custom_recognition_param=dict(mode='pause_retry'), action='DoNothing', next=[],
        timeout=1000, pre_delay=0, post_delay=0)
    nodes['CBPauseOpening'] = dict(recognition='And',
        all_of=['CBOpeningObserve', 'CBOpeningInputReady', *opening_guards], action='Click', target=[1204, 31, 6, 6],
        max_hit=1, next=['CBOpeningRead', 'CBPauseRetry'], timeout=15000, rate_limit=1, pre_delay=0, post_delay=300,
        on_error=['LabyDisconnect6002Observed'])
    # Only reached after a successful first menu input and an unsuccessful paused read.
    nodes['CBPauseRetry'] = dict(recognition='And',
        all_of=['CBOpeningInputReady', *opening_guards, 'CBOpeningRetryGuard'], action='Click', target=[1204, 31, 6, 6],
        max_hit=1, next=['CBOpeningRead'], timeout=15000, rate_limit=1, pre_delay=0, post_delay=300,
        on_error=['LabyDisconnect6002Observed'])
    if 'inheritance' in spec:
        from .clan_battle_inheritance import inheritance_pipeline
        nodes.update(inheritance_pipeline(spec['inheritance']))
        guards = [f'CBLabel_formation_{i}' for i in range(len(calibration['pages']['formation']['labels']))]
        nodes['CBInheritanceFormationObserved'] = dict(recognition='And', all_of=guards,
            action='DoNothing', next=[], timeout=1000, pre_delay=0, post_delay=0)
        nodes['CBInheritanceFormationCancelLabel'] = _node(dict(roi=[855, 560, 190, 75], expected='^取消$'))
        nodes['CBInheritanceFormationCancel'] = dict(recognition='And',
            all_of=['CBInheritanceFormationCancelLabel', *guards], action='Click', target=True,
            next=[], max_hit=1, timeout=3000, pre_delay=0, post_delay=500)
    return nodes


def require_battle_speed(calibration, plan, battle_speed):
    if type(battle_speed) is not int or battle_speed not in (1, 2, 4):
        raise ValueError('clan_battle_speed_invalid')
    if battle_speed != 1 and (plan.get('switches') or battle_speed not in calibration.get('auto_battle', {}).get('speeds', [])):
        raise RuntimeError('clan_battle_auto_speed_uncalibrated')



class _SimulationNavigationRestart(RuntimeError):
    pass


class _SimulationPopup(RuntimeError):
    def __init__(self, reading):
        super().__init__('clan_battle_navigation_popup')
        self.reading = reading


class _SimulationReward(_SimulationPopup):
    pass


class _SimulationDifficultyNotice(_SimulationPopup):
    pass


def _restart_navigation_budget(budget, popup):
    now = time.monotonic()
    owner_deadline = native.RUN_DEADLINE.get()
    if now >= owner_deadline or (not isinstance(popup, _SimulationReward) and now >= budget['deadline']):
        raise RuntimeError('clan_battle_simulation_navigation_timeout')
    if isinstance(popup, _SimulationReward):
        # Only a confirmed new reward starts a fresh navigation segment.
        budget['deadline'] = min(now + 90, owner_deadline)


# Real 2026-10-26 difficulty-increase popup, replayed with the shared OCR model.
_DIFFICULTY_NOTICE_LABELS = (
    ([458, 175, 194, 40], r'^首领魔物\s*等级$'),
    ([638, 175, 120, 40], r'^[（(]难度[）)]$'),
    ([747, 175, 75, 40], r'^提升$'),
    ([480, 308, 310, 37], r'^魔物的等级\s*[（(]难度[）)]\s*提升了[。.]$'),
    ([560, 372, 160, 35], r'^进行模拟战[。.]$'),
)
_DIFFICULTY_NOTICE_CONTENT_ROI = [330, 225, 610, 215]


def _navigation_popup_key(reading):
    page = reading.get('page', 'navigation_reward')
    if page not in ('navigation_reward', 'difficulty_notice'):
        raise RuntimeError('clan_battle_simulation_popup_unknown')
    signature = reading['content_sha256']
    return ('difficulty_notice:' + signature) if page == 'difficulty_notice' else signature


def _boss_name_pattern(calibration, slots, *, phase=None, with_level=True):
    levels = calibration['pages']['boss_detail'].get('nameplate_levels') if with_level else None
    alternatives = []
    for slot in slots:
        name = re.escape(calibration['boss_targets'][str(slot)]['name'])
        if levels is not None:
            allowed = [row[slot-1] for stage, row in levels.items() if phase is None or stage == phase]
            if not allowed:
                continue
            name += r'\s*-?\s*等级\s*[.．]?\s*(?:' + '|'.join(map(str, sorted(set(allowed)))) + ')'
        alternatives.append(name)
    return '^(?:'+'|'.join(alternatives)+')$' if alternatives else r'^(?!)$'


def _boss_name_node(calibration, roi, slots):
    node = _node(dict(roi=roi), expected=_boss_name_pattern(calibration, slots))
    if 'nameplate_levels' in calibration['pages']['boss_detail']:
        node['only_rec'] = True
    return node


def simulation_navigation_pipeline(calibration):
    slots = calibration['pages']['lobby'].get('boss_slots')
    arrows = calibration['pages']['boss_detail'].get('navigation')
    reward = calibration['pages']['lobby'].get('navigation_reward')
    if slots is None and arrows is None and reward is None:
        return {}
    if (type(slots) is not dict or set(slots) != set('12345')
            or type(arrows) is not dict or set(arrows) != {'previous', 'next'}):
        raise ValueError('clan_battle_simulation_navigation_invalid')
    nodes = {}
    for boss, spec in slots.items():
        if type(spec) is not dict or set(spec) != {'round', 'defeated'}:
            raise ValueError('clan_battle_simulation_navigation_invalid')
        nodes['CBNavRound'+boss] = _node(dict(roi=spec['round']), expected=r'^第\s*[1-9][0-9]*\s*轮$')
        nodes['CBNavDefeated'+boss] = _node(spec['defeated'])
    for direction, marker in arrows.items():
        nodes['CBNavArrow_'+direction] = _node(marker)
    nodes['CBNavBoss'] = _boss_name_node(calibration, calibration['pages']['boss_detail']['fields']['boss'], range(1, 6))
    nodes['CBNavSimulation'] = _node(dict(roi=calibration['pages']['target']['fields']['simulation']),
        expected=r'^设定为模拟战[。.]?$')
    modes = dict(CBNavDetail='read', CBNavOpenBoss='open_boss',
                 CBNavSelectSimulation='select_simulation', CBNavStep='next')
    if reward is not None:
        if (type(reward) is not dict or set(reward) != {'labels', 'confirm', 'content_roi'}
                or type(reward['labels']) is not list or len(reward['labels']) < 2):
            raise ValueError('clan_battle_simulation_reward_invalid')
        _roi(reward['content_roi'])
        for index, marker in enumerate(reward['labels']):
            nodes['CBNavRewardLabel'+str(index)] = _node(marker)
        nodes['CBNavRewardConfirmLabel'] = _node(reward['confirm'])
        modes.update(CBNavRewardObserved='reward_read', CBNavRewardConfirm='reward_confirm')
    for index, (roi, expected) in enumerate(_DIFFICULTY_NOTICE_LABELS):
        nodes['CBNavDifficultyNoticeLabel'+str(index)] = _node(dict(roi=roi), expected=expected)
    nodes['CBNavDifficultyNoticeCloseLabel'] = _node(dict(roi=[595, 475, 90, 40]), expected=r'^关闭$')
    modes.update(CBNavDifficultyNoticeObserved='difficulty_notice_read',
                 CBNavDifficultyNoticeClose='difficulty_notice_close')
    for name, mode in modes.items():
        click = mode not in ('read', 'reward_read', 'difficulty_notice_read')
        nodes[name] = dict(recognition='Custom', custom_recognition='ClanBattlePage',
            custom_recognition_param=dict(navigation=mode), action='Click' if click else 'DoNothing',
            target=True, next=[], timeout=1000, pre_delay=0, post_delay=200 if click else 0)
        if click:
            nodes[name]['max_hit'] = 1
    return nodes


def calibration_pipeline(calibration, plan, *, capability='simulation'):
    """Build shared nodes for the internally selected battle capability."""
    if capability not in ('simulation', 'formal', 'carryover'):
        raise ValueError('clan_battle_pipeline_capability_invalid')
    if calibration is None:
        raise RuntimeError('clan_battle_calibration_missing:' + '、'.join(CALIBRATION_GAPS))
    if (type(calibration) is not dict or type(calibration.get('version')) is not int
            or calibration['version'] not in (1, 2)
            or calibration.get('frame_size') != [1280, 720]
            or calibration.get('battle_hud') != 'guide-trial-1280x720'
            or calibration.get('server') != plan['server']
            or not calibration.get('calibration_id')
            or calibration.get('evidence_kind') not in ('offline_replay', 'device_saved_frame')):
        raise ValueError('clan_battle_calibration_invalid')
    modern = calibration['version'] == 2
    simulation = not modern or capability == 'simulation'
    if not modern and 'simulation_targets' in calibration:
        raise ValueError('clan_battle_simulation_targets_invalid')
    support_scope = calibration.get('support_modes')
    if 'support_modes' in calibration and (not modern or support_scope != ['none']):
        raise ValueError('clan_battle_calibration_support_scope_invalid')
    own_only = support_scope == ['none']
    if own_only and plan.get('support_mode') != 'none':
        from .clan_battle import load_snapshot, support_requirement
        if plan.get('support_mode') != 'qsjs' or support_requirement(plan, load_snapshot(plan)) is not None:
            raise RuntimeError('clan_battle_support_mode_uncalibrated')
    page_names = V2_PAGES if modern else PAGES
    action_pages = V2_ACTION_PAGES if modern else ACTION_PAGES
    if own_only:
        action_pages = {name: page for name, page in action_pages.items()
                        if name not in ('open_support', 'support_back', 'support_top', 'support_next')}
    if modern:
        targets, phases = calibration.get('boss_targets'), calibration.get('phase_labels')
        if (type(targets) is not dict or set(targets) != set('12345')
                or any(type(row) is not dict or set(row) != {'target', 'name'}
                    or type(row['name']) is not str or not 0 < len(row['name']) <= 100 for row in targets.values())):
            raise ValueError('clan_battle_calibration_boss_targets_invalid')
        for row in targets.values():
            _roi(row['target'])
        if len({tuple(row['target']) for row in targets.values()}) != 5 or len({row['name'] for row in targets.values()}) != 5:
            raise ValueError('clan_battle_calibration_boss_targets_invalid')
        if (type(phases) is not dict or not phases or any(type(key) is not str or not key
                or value not in ('B', 'C', 'D', 'E') for key, value in phases.items())):
            raise ValueError('clan_battle_calibration_phase_labels_invalid')
    samples = calibration.get('samples')
    if type(samples) is not list or not samples:
        raise ValueError('clan_battle_calibration_samples_missing')
    covered = set()
    for sample in samples:
        if type(sample) is not dict or sample.get('page') not in page_names:
            raise ValueError('clan_battle_calibration_sample_invalid')
        path = Path(sample.get('path', ''))
        if (not path.is_absolute() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != sample.get('sha256')):
            raise ValueError('clan_battle_calibration_sample_changed')
        native.read_image(path)
        covered.add(sample['page'])
    required_samples = set(page_names) - ({'closed'} if modern else set())
    if not required_samples <= covered:
        raise ValueError('clan_battle_calibration_pages_missing')
    pages = calibration.get('pages')
    if (type(pages) is not dict or not set(page_names) - ({'closed'} if modern else set()) <= set(pages)
            or not set(pages) <= set(page_names)):
        raise ValueError('clan_battle_calibration_pages_missing')
    detail_spec = pages.get('boss_detail')
    levels = detail_spec.get('nameplate_levels') if type(detail_spec) is dict else None
    if type(detail_spec) is dict and 'nameplate_levels' in detail_spec:
        if (not modern or type(levels) is not dict or not levels or not set(levels) <= set(phases.values())
                or any(type(row) is not list or len(row) != 5
                       or any(type(level) is not int or not 1 <= level <= 9999 for level in row)
                       for row in levels.values())
                or len({row['name'] for row in targets.values()}) != 5):
            raise ValueError('clan_battle_nameplate_levels_invalid')
    nodes = {}
    if modern and 'closed' not in pages:
        # An unavailable closed-event sample must not be replaced by invented positive guards.
        nodes['CBPage_closed'] = dict(recognition='Custom', custom_recognition='ClanBattlePage',
            custom_recognition_param=dict(page='closed'), action='DoNothing', next=[], timeout=1000,
            pre_delay=0, post_delay=0)
    for page, spec in pages.items():
        if type(spec) is not dict or type(spec.get('labels')) is not list or len(spec['labels']) < 2:
            raise ValueError('clan_battle_calibration_page_guards_missing')
        for index, label in enumerate(spec['labels']):
            nodes[f'CBLabel_{page}_{index}'] = _node(label, ocr_threshold=.8 if page == 'adventure' else .9)
        fields = spec.get('fields', {})
        if type(fields) is not dict:
            raise ValueError('clan_battle_calibration_fields_missing:' + page)
        nameplate = levels is not None and page in ('boss_detail', 'target')
        if nameplate:
            # Replace only the declared full-name label, leaving every independent
            # detail, mode and period guard in place.
            labels = [i for i, label in enumerate(spec['labels'])
                      if label.get('roi') == fields.get('boss')
                      and label.get('expected') == _boss_name_pattern(calibration, range(1, 6), with_level=False)]
            if len(labels) != 1:
                raise ValueError('clan_battle_nameplate_guard_missing')
            nodes[f'CBLabel_{page}_{labels[0]}'] = _boss_name_node(calibration, fields['boss'], range(1, 6))
        required = set(IDENTITY_FIELDS if page in ('target', 'formation', 'support', 'result')
                       else ('period', 'phase', 'round') if page == 'lobby'
                       else ('remaining_seconds',) if page == 'battle' else ())
        inherited = spec.get('inherit', [])
        if modern:
            required = set(('period', 'phase', 'boss', 'simulation') if page in ('target', 'formation', 'support', 'result')
                           else ('period', 'phase', 'boss') if page == 'boss_detail'
                           else ('period', 'boss') if page == 'difficulty'
                           else ('period',) if page == 'lobby' else ('remaining_seconds',) if page == 'battle' else ())
            if (type(inherited) is not list or any(type(key) is not str for key in inherited)
                    or len(set(inherited)) != len(inherited)
                    or any(key not in required for key in inherited) or set(inherited) & set(fields)):
                raise ValueError('clan_battle_calibration_inheritance_invalid:' + page)
            visible = {'period'} if page == 'lobby' else {'phase', 'boss'} if page in ('boss_detail', 'target') else set()
            if page in ('target', 'formation', 'support', 'result'):
                visible.add('simulation')
            if not visible <= set(fields):
                raise ValueError('clan_battle_calibration_fields_missing:' + page)
        if page == 'result':
            required.add('damage')
        if type(fields) is not dict or set(fields) | set(inherited) != required:
            raise ValueError('clan_battle_calibration_fields_missing:' + page)
        for field, roi in fields.items():
            if nameplate and field == 'boss':
                nodes[f'CBField_{page}_boss'] = _boss_name_node(calibration, roi, [plan['boss']])
                continue
            expected = FIELDS.get(field)
            if modern:
                if field == 'boss':
                    expected = '^' + re.escape(targets[str(plan['boss'])]['name']) + '$'
                elif field == 'phase':
                    expected = '^(?:' + '|'.join(re.escape(key) for key in phases)
                    expected += r'|第[1-5]阶段)$' if page == 'boss_detail' else ')$'
                elif field == 'simulation':
                    expected = (r'^模拟战的战斗结果不会显示在团队战成绩中[。.]?$' if page == 'result'
                                else r'^设定为模拟战[。.]?$')
                elif field == 'period':
                    expected = r'^(?:20[0-9]{2}[-年](?:0[1-9]|1[0-2])月?|[0-1]?[0-9]/[0-3]?[0-9]\s*[:：]?\s*[0-2][0-9]:[0-5][0-9]\s*[~～〜至—－-]\s*[0-1]?[0-9]/[0-3]?[0-9]\s*[:：]?\s*[0-2][0-9]:[0-5][0-9])$'
            if modern and simulation and page == 'result' and field == 'damage':
                nodes.update(_simulation_damage_nodes(calibration, spec, roi, plan.get('content_version')))
                continue
            nodes[f'CBField_{page}_{field}'] = _node(dict(roi=roi), expected=expected)
            if modern and page == 'lobby' and field == 'period':
                # Read the whole date row; detector splits can duplicate boundary digits.
                nodes[f'CBField_{page}_{field}']['only_rec'] = True
        if modern and page == 'battle':
            nodes['CBOneSpeed'] = _node(spec.get('speed_one'))
            for speed, key in ((2, 'speed_two'), (4, 'speed_four')):
                if key in spec:
                    nodes['CBBattleSpeed'+str(speed)] = _node(spec[key])
        if modern and page == 'target':
            states = spec.get('live_state')
            if type(states) is not dict or set(states) != {'on', 'off', 'absent'}:
                raise ValueError('clan_battle_calibration_simulation_settings_missing')
            for state, marker in states.items():
                nodes['CBLiveState_'+state] = _node(marker)
        if page in ('formation', 'support'):
            nodes[f'CBFormal_{page}'] = _node(spec.get('formal'), expected=r'^(?:实战|设定为实战[。.]?|正式战|正式挑战|消耗挑战次数)$')
        nodes['CBPage_'+page] = dict(recognition='Custom', custom_recognition='ClanBattlePage',
            custom_recognition_param=dict(page=page), action='DoNothing', next=[], timeout=1000,
            pre_delay=0, post_delay=0)
        if modern and page in ('lobby', 'boss_detail'):
            nodes['CBObserve_'+page] = dict(nodes['CBPage_'+page],
                custom_recognition_param=dict(page=page, observe_only=True))
    if modern and not simulation:
        # Daily execution supplies formal page/navigation guards; keep validation above.
        nodes = {}
    elif modern:
        nodes.update(simulation_navigation_pipeline(calibration))
    actions = calibration.get('actions')
    if type(actions) is not dict or set(actions) != set(action_pages):
        raise ValueError('clan_battle_calibration_actions_missing')
    if modern and simulation:
        selected = _difficulty_choice(calibration, plan)
        nodes['CBDifficultySelected'] = _node(selected['selected'])
    for name, page in action_pages.items():
        if not simulation and name not in ('characters_top', 'characters_next', 'support_top', 'support_next'):
            continue
        spec = actions[name]
        post_delay = 200
        if type(spec) is not dict or spec.get('page') != page:
            raise ValueError('clan_battle_calibration_action_page_invalid')
        if name in ('characters_top', 'characters_next', 'support_top', 'support_next'):
            begin, end = spec.get('begin'), spec.get('end')
            _roi(begin); _roi(end)
            action = dict(action='Swipe', begin=begin, end=end, duration=400)
            if name == 'characters_next':
                timing = dict(duration=spec.get('duration', 400), end_hold=spec.get('end_hold', 0),
                    post_delay=spec.get('post_delay', 200))
                if any(type(value) is not int or not (1 if key == 'duration' else 0) <= value <= 5000
                        for key, value in timing.items()):
                    raise ValueError('clan_battle_calibration_scroll_timing_invalid')
                action.update(duration=timing['duration'], end_hold=timing['end_hold'])
                post_delay = timing['post_delay']
        elif modern and name in ('open_boss', 'back_to_lobby', 'back_to_adventure', 'disable_live_state'):
            _roi(targets[str(plan['boss'])]['target'] if name == 'open_boss' else spec.get('target'))
            action = dict(action='Click', target=True)
        else:
            label = dict(selected['label'] if modern and name == 'select_difficulty' else spec.get('label', {}))
            if name == 'open_simulation':
                label['expected'] = '^模拟战$'
            elif name == 'open_boss':
                label['expected'] = rf'^BOSS\s*{plan["boss"]}$'
            elif name == 'start':
                label['expected'] = '^(?:模拟战开始|开始模拟战|战斗开始)$'
            elif name == 'open_formation':
                label['expected'] = '^挑战$'
            elif name == 'open_difficulty':
                label['expected'] = '^难度变更$'
            elif name == 'confirm_difficulty':
                label['expected'] = '^确认$'
            nodes['CBActionLabel_'+name] = _node(label)
            action = dict(action='Click', target=True)
        if name == 'start':
            # Only CBStart is actionable; its character callback rechecks all five slots.
            continue
        params = dict(page=page, action=name)
        if modern and name in ('back_to_lobby', 'back_to_adventure'):
            params['observe_only'] = True
        nodes['CBAction_'+name] = dict(recognition='Custom', custom_recognition='ClanBattlePage',
            custom_recognition_param=params, next=[], timeout=1000,
            max_hit=1, pre_delay=0, post_delay=post_delay, **action)
    if modern and simulation:
        nodes['CBDifficultyChosen'] = dict(nodes['CBPage_difficulty'],
            custom_recognition_param=dict(page='difficulty', require_selected_difficulty=True))
        nodes['CBFullSimulationReady'] = dict(nodes['CBPage_target'],
            custom_recognition_param=dict(page='target', require_full_simulation=True))
    for section in ('formation', 'support'):
        if own_only and section == 'support':
            continue
        layout = calibration.get(section)
        if type(layout) is not dict or type(layout.get('slots')) is not list or not layout['slots']:
            raise ValueError('clan_battle_calibration_slots_missing')
        for roi in layout['slots']:
            _roi(roi)
        if section == 'formation':
            if ('star_source' in layout and (not modern or not own_only
                    or layout['star_source'] != 'account_snapshot')):
                raise ValueError('clan_battle_calibration_star_source_invalid')
            markers = ('empty', 'stars', 'borrowed') if modern else ('empty', 'stars', 'owned', 'borrowed')
            if (len(layout['slots']) != 5 or any(type(layout.get(key)) is not list or len(layout[key]) != 5
                    for key in markers)
                    or type(layout.get('candidates')) is not list or not layout['candidates']):
                raise ValueError('clan_battle_calibration_formation_invalid')
            for roi in layout['candidates']:
                _roi(roi)
            for index in range(5):
                for key, prefix in (('empty', 'CBEmpty'), ('owned', 'CBOwned'), ('borrowed', 'CBSupport')):
                    if modern and key == 'owned':
                        continue
                    nodes[prefix+str(index)] = _node(layout[key][index])
                for star in range(1, 7):
                    nodes[f'CBStar{index}_{star}'] = _node(dict(roi=layout['stars'][index]), expected=rf'^{star}$')
        else:
            for key in ('stars', 'ids', 'available', 'unavailable'):
                if type(layout.get(key)) is not list or len(layout[key]) != len(layout['slots']):
                    raise ValueError('clan_battle_calibration_support_invalid')
            for index in range(len(layout['slots'])):
                nodes[f'CBSupportId{index}'] = _node(dict(roi=layout['ids'][index]), expected=r'^[0-9]{1,19}$')
                nodes[f'CBSupportAvailable{index}'] = _node(layout['available'][index])
                nodes[f'CBSupportUnavailable{index}'] = _node(layout['unavailable'][index])
                for star in range(1, 7):
                    nodes[f'CBSupportStar{index}_{star}'] = _node(dict(roi=layout['stars'][index]), expected=rf'^{star}$')
        for position in ('top', 'bottom'):
            nodes[f'CBList_{section}_{position}'] = _node(layout.get(position))
    for name, mode, click in (('CBReadTeam', 'read', False), ('CBEditTeam', 'read', True),
                              ('CBReadSupport', 'support_read', False), ('CBAddSupport', 'support_add', True),
                              ('CBStart', 'start', True), ('CBReadSettings', 'battle_read', False),
                              ('CBSettingsConfirmed', 'battle_read', False),
                              ('CBToggleAuto', 'battle_auto', True), ('CBToggleSet', 'battle_set', True)):
        if own_only and name in ('CBReadSupport', 'CBAddSupport'):
            continue
        nodes[name] = dict(recognition='Custom', custom_recognition='ClanBattleCharacters',
            custom_recognition_param=dict(mode=mode), action='Click' if click else 'DoNothing', target=True,
            next=['CBSettingsConfirmed'] if name in ('CBToggleAuto', 'CBToggleSet') else [],
            timeout=1000, pre_delay=0, post_delay=0)
        if click:
            nodes[name]['max_hit'] = 1
    nodes['CBSettingsConfirmed']['on_error'] = ['LabyDisconnect6002Observed']
    if simulation:
        nodes.update(auto_battle_pipeline(calibration, plan))
        if calibration.get('auto_battle') and not plan['switches']:
            # Read inherited settings only after the first guarded pause; the full HUD match is too slow here.
            nodes['CBStart'].update(next=['CBPauseOpening'], timeout=30000, rate_limit=1)
    return nodes


def require_target_identity(plan, reading):
    phase = reading.get('phase')
    difficulty = reading.get('selected_difficulty')
    virtual_round = (reading.get('round') is None and reading.get('round_source') == 'selected_difficulty'
                     and difficulty == dict(phase=phase, boss=reading.get('boss'), simulation=True))
    observed_round = (type(reading.get('round')) is int and reading['round'] >= plan['round_min']
                      and (plan.get('round_max') is None or reading['round'] <= plan['round_max']))
    if (reading.get('period') != plan['period']
            or phase not in (('B', 'C', 'BC') if plan['phase'] == 'BC' else (plan['phase'],))
            or not (virtual_round or observed_round)
            or type(reading.get('boss')) is not int or reading['boss'] != plan['boss']
            or reading.get('simulation') is not True):
        raise RuntimeError('clan_battle_target_mismatch_or_not_simulation')


def _current_period(text):
    if '/' not in text:
        return text.replace('年', '-').replace('月', '')
    match = re.fullmatch(r'([0-1]?[0-9])/([0-3]?[0-9])\s*[:：]?\s*([0-2][0-9]):([0-5][0-9])\s*[~～〜至—－-]\s*'
                         r'([0-1]?[0-9])/([0-3]?[0-9])\s*[:：]?\s*([0-2][0-9]):([0-5][0-9])', text)
    if match is None:
        raise RuntimeError('clan_battle_period_unknown')
    now = datetime.now(timezone(timedelta(hours=8)))
    month, day, hour, minute, end_month, end_day, end_hour, end_minute = map(int, match.groups())
    try:
        start = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
        end = now.replace(month=end_month, day=end_day, hour=end_hour, minute=end_minute, second=59, microsecond=999999)
    except ValueError as exc:
        raise RuntimeError('clan_battle_period_unknown') from exc
    if not start <= now <= end:
        raise RuntimeError('clan_battle_period_not_current')
    return start.strftime('%Y-%m')


class ClanBattlePage(CustomRecognition):
    def __init__(self, plan, calibration, support_unit_id=None):
        super().__init__()
        if 'simulation_targets' in calibration:
            # Every inherited identity, including a resumed formation, must use the reviewed stage.
            plan = dict(plan, phase=_difficulty_phase(calibration, plan))
        self.plan, self.calibration, self.support_unit_id = plan, calibration, support_unit_id
        self.modern = calibration.get('version') == 2
        self.reset_context()

    def reset_context(self):
        self.bound, self.current_page, self.expected_page = {}, None, None

    def advance(self, action):
        if self.modern:
            self.expected_page = V2_DESTINATIONS.get(action, self.current_page)

    def hit(self, context, image, name):
        seen = context.run_recognition(name, image)
        return seen is not None and seen.hit is True

    def word(self, context, image, name, *, evidence=None):
        seen = context.run_recognition(name, image)
        if evidence is not None:
            evidence.update(node=name, hit=seen is not None and seen.hit is True,
                raw=deepcopy(getattr(seen, 'raw_detail', None)))
        raw = getattr(seen, 'raw_detail', None) if seen is not None and seen.hit else None
        words = raw.get('filtered') if type(raw) is dict else None
        if (type(words) is not list or len(words) != 1 or type(words[0]) is not dict
                or raw.get('best') != words[0] or type(words[0].get('text')) is not str
                or type(words[0].get('score')) not in (int, float)
                or not math.isfinite(words[0]['score']) or not .9 <= words[0]['score'] <= 1):
            raise RuntimeError('clan_battle_page_field_unknown:' + name)
        node = context.get_node_data(name)
        if type(node) is not dict:
            raise RuntimeError('clan_battle_page_field_unknown:' + name)
        if type(node.get('recognition')) is dict:
            recognition = node['recognition']
            node = recognition.get('param') if recognition.get('type') == 'OCR' else None
            if type(node) is not dict:
                raise RuntimeError('clan_battle_page_field_unknown:' + name)
        expected = node.get('expected')
        if type(expected) is list:
            expected = expected[0] if len(expected) == 1 else None
        if type(expected) is not str:
            raise RuntimeError('clan_battle_page_field_unknown:' + name)
        box = words[0].get('box')
        roi = _roi(node.get('roi'))
        if (type(box) is not list or len(box) != 4 or any(type(n) is not int for n in box)
                or box[2] <= 0 or box[3] <= 0 or box[0] < roi[0] or box[1] < roi[1]
                or box[0]+box[2] > roi[0]+roi[2] or box[1]+box[3] > roi[1]+roi[3]
                or re.fullmatch(expected, words[0]['text']) is None):
            raise RuntimeError('clan_battle_page_field_unknown:' + name)
        return words[0]['text']

    def read(self, context, image, page, *, observe_only=False):
        if (context.tasker.stopping or image is None or image.shape != (720, 1280, 3)
                or page not in (V2_PAGES if self.modern else PAGES)):
            raise RuntimeError('clan_battle_page_unknown')
        if self.hit(context, image, 'LabyDisconnect6002Observed'):
            raise RuntimeError('clan_battle_disconnected_6002')
        spec = self.calibration['pages'].get(page)
        if spec is None:
            raise RuntimeError('clan_battle_page_uncalibrated:' + page)
        if not all(self.hit(context, image, f'CBLabel_{page}_{index}') for index in range(len(spec['labels']))):
            raise RuntimeError('clan_battle_page_unknown:' + page)
        if observe_only:
            if not self.modern or page not in ('lobby', 'boss_detail'):
                raise RuntimeError('clan_battle_observation_scope_invalid')
            return dict(page=page)
        # A boss detail offers both battle modes; only a selected formal mode is forbidden.
        if page in ('formation', 'support') and self.hit(context, image, 'CBFormal_'+page):
            raise RuntimeError('clan_battle_formal_battle_forbidden')
        values = {}
        if self.modern:
            observed_transition = (self.current_page, page) in (('boss_detail', 'target'), ('battle', 'result'))
            if page not in ('adventure', 'lobby', 'closed') and page not in (self.current_page, self.expected_page) and not observed_transition:
                raise RuntimeError('clan_battle_page_transition_unverified:' + page)
            for field in spec.get('inherit', []):
                if field not in self.bound:
                    raise RuntimeError('clan_battle_page_identity_missing:' + field)
                values[field] = self.bound[field]
        for field in spec.get('fields', {}):
            text = self.word(context, image, f'CBField_{page}_{field}')
            if field == 'simulation':
                value = (text.startswith('模拟战的战斗结果不会显示在团队战成绩中') if page == 'result'
                         else text.startswith('设定为模拟战')) if self.modern else text == '模拟战'
            elif field == 'damage' and self.modern:
                verify = self.word(context, image, 'CBField_result_damage_verify')
                value = result_damage_line(text, verify)
            elif field == 'period':
                value = _current_period(text) if self.modern else text.replace('年', '-').replace('月', '')
            elif field == 'phase':
                value = self.calibration['phase_labels'].get(text) if self.modern else text
            elif field == 'boss' and self.modern and page in ('boss_detail', 'target') and 'nameplate_levels' in self.calibration['pages']['boss_detail']:
                # Phase and boss may appear in either order in a profile.
                value = text
            elif field == 'boss' and self.modern:
                if text != self.calibration['boss_targets'][str(self.plan['boss'])]['name']:
                    raise RuntimeError('clan_battle_boss_unknown')
                value = self.plan['boss']
            elif field == 'remaining_seconds':
                minute, second = text.replace('：', ':').split(':')
                value = int(minute)*60+int(second)
            else:
                value = int(re.sub('[^0-9]', '', text))
            values[field] = value
        if (self.modern and page in ('boss_detail','target')
                and 'nameplate_levels' in self.calibration['pages']['boss_detail']):
            if self._nameplate_slot(values['boss'], values.get('phase')) != self.plan['boss']:
                raise RuntimeError('clan_battle_boss_unknown')
            values['boss'] = self.plan['boss']
        if self.modern:
            if page == 'battle':
                speeds = [speed for speed, key in ((1, 'speed_one'), (2, 'speed_two'), (4, 'speed_four'))
                    if key in spec and self.hit(context, image, 'CBOneSpeed' if speed == 1 else 'CBBattleSpeed'+str(speed))]
                values['battle_speed'] = speeds[0] if len(speeds) == 1 else None
            if page == 'target':
                states = [state for state in ('on', 'off', 'absent') if self.hit(context, image, 'CBLiveState_'+state)]
                if len(states) != 1:
                    raise RuntimeError('clan_battle_simulation_settings_unknown')
                values['live_state'] = states[0]
                values.update(round=None, round_source='selected_difficulty', selected_difficulty=dict(
                    phase=values['phase'], boss=values['boss'], simulation=values['simulation']))
            elif page in ('formation', 'support', 'result'):
                for field in ('round', 'round_source', 'selected_difficulty'):
                    if field not in self.bound:
                        raise RuntimeError('clan_battle_page_identity_missing:' + field)
                    values[field] = deepcopy(self.bound[field])
            if page in ('lobby', 'boss_detail') and values['period'] != self.plan['period']:
                raise RuntimeError('clan_battle_target_mismatch_or_not_simulation')
        if page in ('target', 'formation', 'support', 'result'):
            require_target_identity(self.plan, values)
        if page == 'lobby' and not self.modern:
            require_target_identity(self.plan, dict(values, boss=self.plan['boss'], simulation=True))
        if self.modern and page == 'lobby' and 'boss_slots' in spec:
            values['bosses'] = self.read_boss_slots(context, image)
        if self.modern:
            self.bound.update(deepcopy(values))
            self.current_page, self.expected_page = page, None
        return dict(values, page=page)

    def action(self, context, image, name, *, observe_only=False):
        if observe_only and name not in ('back_to_lobby', 'back_to_adventure'):
            raise RuntimeError('clan_battle_observation_scope_invalid')
        page = (V2_ACTION_PAGES if self.modern else ACTION_PAGES)[name]
        reading = self.read(context, image, page, observe_only=observe_only)
        if self.modern and name == 'open_formation' and reading.get('live_state') not in ('off', 'absent'):
            raise RuntimeError('clan_battle_simulation_settings_not_ready')
        if self.modern and name == 'disable_live_state' and reading.get('live_state') != 'on':
            raise RuntimeError('clan_battle_simulation_settings_changed')
        if self.modern and name == 'confirm_difficulty' and not self.hit(context, image, 'CBDifficultySelected'):
            raise RuntimeError('clan_battle_difficulty_selection_unverified')
        if self.modern and name == 'open_boss':
            rows = reading.get('bosses')
            if type(rows) is not list or not any(row['boss'] == self.plan['boss'] and row['available'] for row in rows):
                raise RuntimeError('clan_battle_simulation_boss_unavailable')
            return _roi(self.calibration['boss_targets'][str(self.plan['boss'])]['target'])
        if self.modern and name in ('back_to_lobby', 'back_to_adventure', 'disable_live_state'):
            return _roi(self.calibration['actions'][name]['target'])
        if name in ('characters_top', 'characters_next', 'support_top', 'support_next'):
            return [0, 0, 1, 1]
        node = 'CBActionLabel_'+name
        if not self.hit(context, image, node):
            raise RuntimeError('clan_battle_action_label_unknown:' + name)
        if self.modern and name == 'select_difficulty':
            x, y, width, height = _roi(_difficulty_choice(self.calibration, self.plan)['selected']['roi'])
            return [x+width//2, y+height//2, 1, 1]
        result = context.run_recognition(node, image)
        return _roi(list(result.box))

    def read_boss_slot(self, context, image, boss):
        if 'boss_slots' not in self.calibration['pages']['lobby']:
            raise RuntimeError('clan_battle_simulation_navigation_uncalibrated')
        if type(boss) is not int or not 1 <= boss <= 5:
            raise RuntimeError('clan_battle_simulation_boss_changed')
        dead = self.hit(context, image, 'CBNavDefeated'+str(boss))
        alive = self.hit(context, image, 'CBNavRound'+str(boss))
        if dead == alive:
            raise RuntimeError('clan_battle_simulation_boss_state_unknown')
        round_ = None if dead else int(re.sub(r'\D', '', self.word(context, image, 'CBNavRound'+str(boss))))
        return dict(boss=boss, round=round_, available=alive)

    def read_boss_slots(self, context, image):
        return [self.read_boss_slot(context, image, boss) for boss in range(1, 6)]

    def _nameplate_slot(self, text, phase):
        if phase is None:
            return None
        matches = [slot for slot in range(1, 6)
                   if re.fullmatch(_boss_name_pattern(self.calibration, [slot], phase=phase), text)]
        return matches[0] if len(matches) == 1 else None

    def read_navigation(self, context, image):
        if 'navigation' not in self.calibration['pages']['boss_detail']:
            raise RuntimeError('clan_battle_simulation_navigation_uncalibrated')
        self.read(context, image, 'boss_detail', observe_only=True)
        if (self.bound.get('period') != self.plan['period']
                or self.current_page not in ('lobby', 'boss_detail', 'target')
                or self.current_page == 'lobby' and self.expected_page != 'boss_detail'):
            raise RuntimeError('clan_battle_simulation_navigation_unbound')
        name = self.word(context, image, 'CBNavBoss')
        phase = self.calibration['phase_labels'].get(self.word(context, image, 'CBField_boss_detail_phase'))
        boss = self._nameplate_slot(name, phase)
        if boss is None:
            raise RuntimeError('clan_battle_simulation_navigation_unknown')
        simulation = self.hit(context, image, 'CBNavSimulation')
        if simulation:
            self.word(context, image, 'CBNavSimulation')
        return dict(page='boss_detail', period=self.bound['period'], boss=boss,
                    phase=phase, simulation=simulation)

    def navigation_action(self, context, image, params):
        mode = params['navigation']
        if mode in ('difficulty_notice_read', 'difficulty_notice_close'):
            if (not self.modern or 'boss_slots' not in self.calibration['pages']['lobby']
                    or context.tasker.stopping or image is None or image.shape != (720, 1280, 3)
                    or self.hit(context, image, 'LabyDisconnect6002Observed')):
                raise RuntimeError('clan_battle_simulation_difficulty_notice_unknown')
            for index in range(len(_DIFFICULTY_NOTICE_LABELS)):
                self.word(context, image, 'CBNavDifficultyNoticeLabel'+str(index))
            close = 'CBNavDifficultyNoticeCloseLabel'
            self.word(context, image, close)
            x, y, w, h = _DIFFICULTY_NOTICE_CONTENT_ROI
            reading = dict(page='difficulty_notice', content_sha256=hashlib.sha256(image[y:y+h, x:x+w].tobytes()).hexdigest())
            if mode == 'difficulty_notice_read':
                return reading, [0, 0, 1, 1]
            if params.get('expected_sha256') != reading['content_sha256']:
                raise RuntimeError('clan_battle_simulation_difficulty_notice_changed')
            return reading, _roi(list(context.run_recognition(close, image).box))
        if mode in ('reward_read', 'reward_confirm'):
            spec = self.calibration['pages']['lobby'].get('navigation_reward')
            if (context.tasker.stopping or image is None or image.shape != (720, 1280, 3)
                    or not spec or self.hit(context, image, 'LabyDisconnect6002Observed')
                    or not all(self.hit(context, image, 'CBNavRewardLabel'+str(i)) for i in range(len(spec['labels'])))
                    or not self.hit(context, image, 'CBNavRewardConfirmLabel')):
                raise RuntimeError('clan_battle_simulation_reward_unknown')
            x, y, w, h = _roi(spec['content_roi'])
            reading = dict(page='navigation_reward', content_sha256=hashlib.sha256(image[y:y+h, x:x+w].tobytes()).hexdigest())
            if mode == 'reward_read':
                return reading, [0, 0, 1, 1]
            if params.get('expected_sha256') != reading['content_sha256']:
                raise RuntimeError('clan_battle_simulation_reward_changed')
            return reading, _roi(list(context.run_recognition('CBNavRewardConfirmLabel', image).box))
        if mode == 'open_boss':
            expected = params.get('expected')
            if (type(expected) is not dict or set(expected) != {'boss', 'round'}
                    or type(expected['boss']) is not int or not 1 <= expected['boss'] <= 5
                    or type(expected['round']) is not int or expected['round'] < 1):
                raise RuntimeError('clan_battle_simulation_boss_changed')
            rows = self.bound.get('bosses')
            if (self.current_page != 'lobby' or self.bound.get('period') != self.plan['period']
                    or type(rows) is not list or not any(row.get('available') is True
                        and all(row.get(key) == expected[key] for key in expected) for row in rows)):
                raise RuntimeError('clan_battle_simulation_navigation_unbound')
            self.read(context, image, 'lobby', observe_only=True)
            period = _current_period(self.word(context, image, 'CBField_lobby_period'))
            if period != self.plan['period']:
                raise RuntimeError('clan_battle_target_mismatch_or_not_simulation')
            entry = self.read_boss_slot(context, image, expected['boss'])
            if not entry['available'] or any(entry[key] != expected[key] for key in expected):
                raise RuntimeError('clan_battle_simulation_boss_changed')
            # Only this entry is fresh; keep the earlier complete lobby snapshot separate.
            reading = dict(page='lobby', period=period, selected_entry=entry)
            x, y, w, h = _roi(self.calibration['boss_targets'][str(expected['boss'])]['target'])
            return reading, [x + w // 2, y + h // 2, 1, 1]
        reading = self.read_navigation(context, image)
        if mode == 'read':
            return reading, [0, 0, 1, 1]
        expected = params.get('expected')
        if (type(expected) is not dict or set(expected) != {'period', 'boss', 'phase', 'simulation'}
                or any(reading[key] != value for key, value in expected.items())):
            raise RuntimeError('clan_battle_simulation_navigation_changed')
        if mode == 'select_simulation':
            if reading['simulation']:
                raise RuntimeError('clan_battle_simulation_already_selected')
            node = 'CBActionLabel_open_simulation'
        elif mode in ('previous', 'next') and reading['simulation'] is True:
            node = 'CBNavArrow_'+mode
        else:
            raise RuntimeError('clan_battle_simulation_navigation_forbidden')
        if not self.hit(context, image, node):
            raise RuntimeError('clan_battle_simulation_navigation_action_unknown')
        return reading, _roi(list(context.run_recognition(node, image).box))

    def require_team(self, reading):
        ids, stars = reading.get('unit_ids'), reading.get('stars')
        if (type(ids) is not list or len(ids) != 5 or len(set(ids)) != 5 or set(ids) != set(self.plan['unit_ids'])
                or any(type(uid) is not int for uid in ids)
                or type(stars) is not list or len(stars) != 5
                or any(type(star) is not int for star in stars)
                or dict(zip(ids, stars)) != dict(zip(self.plan['unit_ids'], self.plan['stars']))
                or reading.get('member_count') != 5 or reading.get('support_unit_id') != self.support_unit_id):
            raise RuntimeError('clan_battle_team_or_support_mismatch')
        require_target_identity(self.plan, reading)

    def analyze(self, context, argv):
        try:
            params = json.loads(argv.custom_recognition_param)
            context = same_frame_context(context, argv.image)
            if 'navigation' in params:
                detail, target = self.navigation_action(context, argv.image, params)
                return None if context.tasker.stopping else self.AnalyzeResult(box=target, detail=detail)
            observed = params.get('observe_only', False)
            detail = self.read(context, argv.image, params.get('page'), observe_only=observed)
            if params.get('require_selected_difficulty') and not self.hit(context, argv.image, 'CBDifficultySelected'):
                return None
            if params.get('require_full_simulation') and detail.get('live_state') not in ('off', 'absent'):
                return None
            target = self.action(context, argv.image, params['action'], observe_only=observed) if 'action' in params else [0, 0, 1, 1]
            if context.tasker.stopping:
                return None
            return self.AnalyzeResult(box=target, detail=detail)
        except Exception:
            return None


def _detail(receipt):
    try:
        detail = receipt['nodes'][-1]['recognition']['best']['detail']
        detail = json.loads(detail) if type(detail) is str else detail
        if type(detail) is not dict:
            raise ValueError()
        return detail
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError('clan_battle_native_detail_unknown') from None


def _result_detail(receipt):
    try:
        node = receipt['nodes'][-1]
        if receipt['native_ok'] is not True or node['name'] != 'CBPage_result' or node['completed'] is not True:
            raise ValueError()
        result = _detail(receipt)
        if result.get('page') != 'result' or type(result.get('damage')) is not int or not 0 <= result['damage'] <= 10**15:
            raise ValueError()
        return result
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError('clan_battle_result_unknown') from None


def timing_capability(calibration):
    """Derive timing bounds from a bound measurement file, never a validated flag."""
    binding = calibration.get('timing_measurements')
    unknown = dict(validated=False)
    if type(binding) is not dict or calibration.get('evidence_kind') != 'device_saved_frame':
        return unknown
    path = Path(binding.get('path', ''))
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 1024*1024:
        return unknown
    try:
        data = path.read_bytes()
        if len(data) > 1024*1024 or hashlib.sha256(data).hexdigest() != binding.get('sha256'):
            return unknown
        from .clan_battle_calibration import _json
        measured = _json(data)
    except (OSError, ValueError, UnicodeError):
        return unknown
    if (type(measured) is not dict or measured.get('kind') != 'owner_native_clan_timing'
            or measured.get('calibration_id') != calibration['calibration_id']
            or measured.get('battle_hud') != calibration['battle_hud']
            or measured.get('observation_scope') != 'full_read_tick'
            or measured.get('action_scope') != 'toggle_dispatch_and_full_team_readback'):
        return unknown
    if type(measured.get('battle_speed')) is not int or measured['battle_speed'] != 1:
        return unknown
    values = [measured.get(key) for key in ('observation_intervals_ms', 'action_latencies_ms', 'game_second_ms')]
    if any(type(rows) is not list or len(rows) < 20 or any(type(n) is not int or n <= 0 for n in rows) for rows in values):
        return unknown
    return dict(validated=True, max_observation_interval_ms=max(values[0]),
                max_action_latency_ms=max(values[1]), battle_speed=1, min_game_second_ms=min(values[2]),
                source=str(path), source_sha256=binding['sha256'])


class NativeClanBattle(NativeGuideTrial):
    def __init__(self, tasker, directory, *, calibration=None, battle_speed=1):
        super().__init__(tasker, Path(directory))
        self.calibration = calibration
        self.timing_capability = dict(validated=False)
        self.started = False
        self.inheritance = None
        self.inheritance_managers = []
        self._account_binding = None
        self._opening_control_binding, self._opening_control_policy = None, None
        self.battle_speed, self.opening_paused = battle_speed, False

    def configure(self, plan):
        from .clan_battle import load_snapshot, support_requirement
        from .clan_battle_characters import ClanBattleCharacters
        nodes = calibration_pipeline(self.calibration, plan)
        require_battle_speed(self.calibration, plan, self.battle_speed)
        self.plan, self.snapshot = plan, load_snapshot(plan)
        if self.calibration['formation'].get('star_source') == 'account_snapshot':
            from .clan_battle import account_battle_stars
            account_battle_stars(plan)
        self.support_unit_id = support_requirement(plan, self.snapshot)
        self.page = ClanBattlePage(plan, self.calibration, self.support_unit_id)
        self.characters = ClanBattleCharacters(plan, self.calibration, self.page)
        self.directory.mkdir(parents=True, exist_ok=True)
        if (not self.tasker.resource.override_pipeline(game_session_pipeline(int(plan['account_id'])))
                or not self.tasker.resource.override_pipeline(nodes)):
            raise RuntimeError('clan_battle_pipeline_rejected')
        resource = getattr(self.tasker, '_resource_holder', None) or self.tasker.resource
        if not resource.register_custom_recognition('ClanBattlePage', self.page):
            raise RuntimeError('clan_battle_callback_registration_failed:ClanBattlePage')
        if not resource.register_custom_recognition('ClanBattleCharacters', self.characters):
            raise RuntimeError('clan_battle_callback_registration_failed:ClanBattleCharacters')
        inheritance = self.calibration.get('auto_battle', {}).get('inheritance')
        if inheritance is not None:
            from .clan_battle_inheritance import InheritancePage
            self.inheritance_page = InheritancePage(inheritance, self.page)
            if not resource.register_custom_recognition('ClanBattleInheritance', self.inheritance_page):
                raise RuntimeError('clan_battle_callback_registration_failed:ClanBattleInheritance')
        # Keep the callbacks with the real Resource; registration rejection is terminal.
        self.timing_capability = timing_capability(self.calibration)

    def choose(self, names, *, timeout=1000):
        try:
            return super().choose(names, timeout=timeout)
        except ClanDisconnected:
            raise
        except RuntimeError as exc:
            self.invalidate_account_binding()
            if str(exc).startswith('guide_trial_disconnected_6002:'):
                raise RuntimeError('clan_battle_disconnected_6002:battle_start=' + self.battle_start) from exc
            raise

    def wait(self, names, *, seconds=30):
        budget = getattr(self, '_simulation_navigation_budget', None)
        if budget is None:
            return super().wait(names, seconds=seconds)
        allowed = {PROFILE_NODE, 'LabyClientGameHomeObserved', 'LabyClientAdventureObserved',
            'LabyClientMainMenuObserved', 'CBObserve_boss_detail', 'CBObserve_lobby',
            'CBInheritanceSettingsObserved', 'CBInheritanceFormationObserved',
            'CBPage_closed', 'CBPage_lobby', 'CBPage_boss_detail', 'CBPage_target',
            'CBPage_formation', 'CBPage_difficulty', 'CBDifficultyChosen', 'CBFullSimulationReady', 'CBNavDetail'}
        if (self.started or self.battle_start != 'not_sent' or getattr(self, 'pending', None) is not None
                or not names or not set(names) <= allowed):
            raise RuntimeError('clan_battle_simulation_navigation_scope_invalid')
        try:
            return self._wait_simulation_page(names, deadline=budget['deadline'],
                acknowledged=budget['acknowledged'], seconds=seconds)
        except _SimulationPopup as exc:
            self._confirm_navigation_popup(exc.reading, **budget)
            raise _SimulationNavigationRestart() from exc

    def _start_reset_hits(self):
        # A borrowed Owner shares hit counts across knives; renew only this opening's menu nodes.
        if self.calibration and self.calibration.get('auto_battle') and not self.plan['switches']:
            return ('CBPauseOpening', 'CBPauseRetry')
        return ()

    def _annotate_unsent_start(self, receipt):
        receipt['clan_battle_phase'] = 'simulation_start'

    def _dispatch_clan_node(self, node, *, click, starting):
        if starting:
            self.started, self.battle_start = True, 'unknown'
        capture = getattr(self, '_opening_capture', None)
        if capture is not None:
            capture.mark('dispatch', node, self.battle_start)
        try:
            reset_hits = self._start_reset_hits() if starting else ()
            if reset_hits:
                result = super().run(node, click=click, reset_hits=reset_hits)
            else:
                result = super().run(node, click=click)
        except Exception as exc:
            self.invalidate_account_binding()
            if isinstance(exc, ClanDisconnected):
                raise
            if starting and isinstance(exc, native.NativeInputNotSent):
                self.battle_start = 'not_sent'
                self._annotate_unsent_start(exc.receipt)
                raise
            disconnected = observe_disconnect_after_failure(self, exc, input_attempted=click)
            if disconnected is not None:
                if click:
                    disconnected.replay_forbidden = True
                raise disconnected from exc
            raise
        if starting:
            self.battle_start = 'sent'
            if capture is not None:
                self._opening_anchor_start_receipt = deepcopy(result)
                self._opening_anchor_start_receipt_path = self.directory / f'{self.sequence:03d}-CBStart.json'
        self.last_receipt = result
        if capture is not None:
            capture.mark('returned', node, self.battle_start)
        if any(row.get('name') == 'LabyDisconnect6002Observed' for row in result.get('nodes', [])):
            self.invalidate_account_binding()
            raise ClanDisconnected(result, self.battle_start)
        if any(row.get('name') == 'LabyClientTitleObserved' for row in result.get('nodes', [])):
            self.invalidate_account_binding()
        return result

    def run(self, node, *, click=False):
        starting = click and node == 'CBStart'
        if starting and self.started:
            raise RuntimeError('clan_battle_start_already_attempted')
        result = self._dispatch_clan_node(node, click=click, starting=starting)
        if click and getattr(getattr(self, 'page', None), 'modern', False):
            action = node.removeprefix('CBAction_') if node.startswith('CBAction_') else (
                'start' if node == 'CBStart' else 'support_back' if node == 'CBAddSupport' else None)
            if node == 'CBNavOpenBoss':
                self.page.advance('open_boss')
            elif action in V2_DESTINATIONS:
                self.page.advance(action)
        return result

    def read_page(self, page):
        receipt = self.run('CBPage_'+page)
        return _detail(receipt), receipt

    def prepare(self, plan):
        if self.calibration is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Exact complete closure text is read-only; unknown/open pages stay blocked.
            if self.choose(['CBClosedReadOnly']) == 'CBClosedReadOnly':
                raise RuntimeError('clan_battle_closed')
        self.configure(plan)
        self._simulation_prepare_budget = self._new_simulation_navigation_budget()
        try:
            try:
                self._prepare_from_current_page(plan)
            except ClanDisconnected as exc:
                recovery = recover_disconnect(self, plan, exc)
                if not recovery['can_continue']:
                    raise
                self._prepare_from_current_page(plan)
        finally:
            self._simulation_prepare_budget = None

    def _new_simulation_navigation_budget(self):
        lobby = self.calibration.get('pages', {}).get('lobby', {})
        if (self.calibration.get('version') == 2
                and ('boss_slots' in lobby or 'navigation_reward' in lobby)):
            return dict(deadline=time.monotonic()+90, acknowledged=set(), rewards=[])
        return None

    def _prepare_from_current_page(self, plan):
        budget = getattr(self, '_simulation_prepare_budget', None)
        if budget is None:
            budget = self._new_simulation_navigation_budget()
        if budget is not None and (self.started or self.battle_start != 'not_sent'
                or getattr(self, 'pending', None) is not None):
            raise RuntimeError('clan_battle_simulation_navigation_scope_invalid')
        self._simulation_navigation_budget = budget
        try:
            while True:
                try:
                    self._prepare_navigation(plan)
                    break
                except _SimulationNavigationRestart as restart:
                    # A confirmed popup invalidates the route; observe the actual page again.
                    if budget is None:
                        raise RuntimeError('clan_battle_simulation_navigation_timeout') from None
                    _restart_navigation_budget(budget, restart.__cause__)
        finally:
            self._simulation_navigation_budget = None
        self.form_team()

    def _prepare_navigation(self, plan):
        self._main_menu()
        self._verify_account(plan)
        if self.calibration.get('auto_battle', {}).get('inheritance'):
            from .clan_battle_inheritance import BattleInheritance
            from .preset_execution import Journal
            if not self.inheritance_managers:
                self.inheritance = BattleInheritance.for_port(self)
                self.inheritance_managers = [self.inheritance,
                    BattleInheritance(self, Journal(native.ROOT / '.runtime/clan-battle-daily-work/inheritance'))]
            pending = []
            for manager in self.inheritance_managers:
                with manager.journal.locked():
                    if manager._pending() is not None:
                        pending.append(manager)
            if pending:
                self.open_inheritance()
                for manager in pending:
                    manager.recover()
                self.close_inheritance()
        self.run('LabyClientMenuToAdventureOnce', click=True)
        self.wait(['LabyClientAdventureObserved'], seconds=10)
        self.run('CBAction_open_clan', click=True)
        if self.calibration.get('version') != 2 and self.wait(['CBPage_closed', 'CBPage_lobby'], seconds=15) == 'CBPage_closed':
            raise RuntimeError('clan_battle_closed')
        if self.calibration.get('version') == 2:
            self._open_simulation_target(plan)
            self.run('CBAction_open_formation', click=True)
        else:
            self.read_page('lobby')
            self.run('CBAction_open_boss', click=True)
            self.wait(['CBPage_target'], seconds=10)
            self.read_page('target')
            self.run('CBAction_open_simulation', click=True)
        self.wait(['CBPage_formation'], seconds=10)

    def _main_menu(self):
        modern = self.calibration.get('version') == 2
        initial = ['LabyClientGameHomeObserved', 'LabyClientAdventureObserved', 'LabyClientMainMenuObserved']
        if modern:
            initial = [PROFILE_NODE, 'CBObserve_boss_detail', 'CBObserve_lobby', *initial]
        if self.calibration.get('auto_battle', {}).get('inheritance'):
            initial = ['CBInheritanceSettingsObserved', 'CBInheritanceFormationObserved', *initial]
        hit = self.wait(initial, seconds=10)
        if hit == PROFILE_NODE:
            self.run('LabyClientProfileBackOnce', click=True)
            self.wait(['LabyClientMainMenuObserved'], seconds=10)
            hit = 'LabyClientMainMenuObserved'
        if hit == 'CBInheritanceSettingsObserved':
            self.close_inheritance()
            hit = 'LabyClientMainMenuObserved'
        if hit == 'CBInheritanceFormationObserved':
            self.run('CBInheritanceFormationCancel', click=True)
            self.wait(['CBObserve_boss_detail'], seconds=10)
            hit = 'CBObserve_boss_detail'
        if hit == 'CBObserve_boss_detail':
            self.run('CBAction_back_to_lobby', click=True)
            self.wait(['CBObserve_lobby'], seconds=10)
            hit = 'CBObserve_lobby'
        if hit == 'CBObserve_lobby':
            self.run('CBAction_back_to_adventure', click=True)
            self.wait(['LabyClientAdventureObserved'], seconds=10)
            self.page.reset_context()
        if hit == 'LabyClientGameHomeObserved':
            self.run('LabyClientHomeToAdventure', click=True)
            self.wait(['LabyClientAdventureObserved'], seconds=10)
        if hit != 'LabyClientMainMenuObserved':
            self.run('LabyClientOpenMainMenuOnce', click=True)
            self.wait(['LabyClientMainMenuObserved'], seconds=10)

    def invalidate_account_binding(self):
        characters = getattr(self, 'characters', None)
        if characters is not None and callable(getattr(characters, 'invalidate_opening', None)):
            characters.invalidate_opening('owner_binding_invalidated')
        self._account_binding = None
        self._opening_control_binding, self._opening_control_policy = None, None
        self.account, self.account_receipt = None, None

    def _account_source(self):
        source = getattr(self.tasker, 'observation_source', None)
        # Only the live Owner can certify a reusable connection, not an SDK getter view.
        return source() if callable(source) else None

    def account_binding_valid(self, plan):
        try:
            source = self._account_source()
        except Exception:
            self.invalidate_account_binding()
            raise
        if (source is not None and self.account == plan['account_id']
                and self._account_binding == (self.account, source)):
            return True
        self.invalidate_account_binding()
        return False

    def _verify_account(self, plan):
        if self.account_binding_valid(plan):
            return
        source = self._account_source()
        try:
            self.run('LabyClientOpenProfileOnce', click=True)
            self.wait([PROFILE_NODE], seconds=10)
            receipt = self.run(PROFILE_NODE)
            account = str(profile_player_id(receipt))
            if account != plan['account_id']:
                raise RuntimeError('clan_battle_account_mismatch')
            self.run('LabyClientProfileBackOnce', click=True)
            self.wait(['LabyClientMainMenuObserved'], seconds=10)
            if self._account_source() != source:
                raise RuntimeError('clan_battle_account_binding_changed')
        except Exception:
            self.invalidate_account_binding()
            raise
        self.account, self.account_receipt = account, receipt
        self._account_binding = (account, source) if source is not None else None
        # Optional settings evidence is unknown until explicitly read on its current page.
        self._opening_control_binding, self._opening_control_policy = None, None

    def _observe_opening_control_policy(self):
        """Read only the current settings frame; never navigate for optional evidence."""
        self._opening_control_binding, self._opening_control_policy = None, None
        if not getattr(getattr(self, 'inheritance_page', None), 'ub_auto_clear', False):
            return None
        plan = dict(account_id=self.account)
        if not self.account_binding_valid(plan):
            return None
        binding = self._account_binding
        hit = self.choose(['CBSetResetRead'])
        receipt = deepcopy(self.last_receipt)
        reading = _detail(receipt) if hit == 'CBSetResetRead' else {}
        known = (receipt.get('native_ok') is True and type(reading.get('auto_release_on_ub')) is bool
            and reading.get('same_frame') is True and type(reading.get('frame_pixel_sha256')) is str
            and re.fullmatch('[0-9a-f]{64}', reading['frame_pixel_sha256']) is not None)
        if not self.account_binding_valid(plan) or self._account_binding != binding:
            return None
        if known:
            policy = dict(reading, account_id=self.account, native_evidence=receipt,
                source='owner_account_bound_settings_readback')
            self._opening_control_binding, self._opening_control_policy = binding, policy
            return deepcopy(policy)
        return None

    def freeze_opening_control_policy(self, plan):
        """Reuse optional bound evidence without opening or reading any settings page."""
        if not self.account_binding_valid(plan) or self._opening_control_binding != self._account_binding:
            return None
        policy = self._opening_control_policy
        if (type(policy) is not dict or policy.get('account_id') != self.account
                or type(policy.get('auto_release_on_ub')) is not bool or policy.get('same_frame') is not True
                or type(policy.get('frame_pixel_sha256')) is not str
                or re.fullmatch('[0-9a-f]{64}', policy['frame_pixel_sha256']) is None
                or type(policy.get('native_evidence')) is not dict or not policy['native_evidence']):
            return None
        return deepcopy(policy)

    def open_inheritance(self):
        self.run('CBInheritanceOpen', click=True)
        self.wait(['CBInheritanceSettingsObserved'], seconds=10)
        self.run('CBInheritanceBattle', click=True)
        if self.choose(['CBInheritanceRead', 'CBInheritanceAllExpanded']) == 'GTUnknown':
            self.run('CBInheritanceExpand', click=True)
        for index in range(7):
            if self.choose(['CBInheritanceRead']) == 'CBInheritanceRead':
                return
            if index < 6:
                self.run('CBInheritanceDown', click=True)
        raise RuntimeError('clan_battle_inheritance_unknown')

    def close_inheritance(self):
        self.run('CBInheritanceClose', click=True)
        self.wait(['LabyClientMainMenuObserved'], seconds=10)

    def read_inheritance(self):
        result = _detail(self.run('CBInheritanceRead'))
        if (type(result.get('inherit')) is not bool or result.get('same_frame') is not True
                or type(result.get('frame_pixel_sha256')) is not str
                or re.fullmatch('[0-9a-f]{64}', result['frame_pixel_sha256']) is None):
            raise RuntimeError('clan_battle_inheritance_unknown')
        return dict(result, native_evidence=deepcopy(self.last_receipt))

    def set_inheritance(self, previous, desired):
        state = self.read_inheritance()
        if type(previous) is not bool or type(desired) is not bool or state['inherit'] is not previous or previous is desired:
            raise RuntimeError('clan_battle_inheritance_changed')
        self.run('CBInheritanceEnable' if desired else 'CBInheritanceDisable', click=True)
        state = self.read_inheritance()
        if state['inherit'] is not desired:
            raise RuntimeError('clan_battle_inheritance_readback_unknown')
        return state

    def finish_inheritance(self):
        # Cleanup never adds inputs after a stop or an unresolved legacy readback.
        managers = self.inheritance_managers or ([self.inheritance] if self.inheritance is not None else [])
        evidence = [deepcopy(manager.evidence) for manager in managers if manager.evidence is not None]
        if len(evidence) > 1:
            return dict(restored=all(row.get('restored') is True for row in evidence), recoveries=evidence)
        return evidence[0] if evidence else None

    def _navigation_input(self, node, mode, **params):
        if not self.tasker.resource.override_pipeline({node: dict(custom_recognition_param=dict(navigation=mode, **params))}):
            raise RuntimeError('clan_battle_simulation_navigation_pipeline_rejected')
        return self.run(node, click=True)

    def _confirm_navigation_popup(self, reading, *, deadline, acknowledged, rewards):
        prebattle = self.started is False and self.battle_start == 'not_sent'
        settled = (self.battle_start == 'sent' and self.started is True
            and getattr(self, '_simulation_result_return_verified', False) is True)
        if not (prebattle or settled) or getattr(self, 'pending', None) is not None:
            raise RuntimeError('clan_battle_simulation_navigation_scope_invalid')
        signature, key = reading['content_sha256'], _navigation_popup_key(reading)
        if len(rewards) >= 5 or time.monotonic() >= deadline or key in acknowledged:
            raise RuntimeError('clan_battle_simulation_reward_limit')
        node, mode = (('CBNavDifficultyNoticeClose', 'difficulty_notice_close')
            if reading.get('page') == 'difficulty_notice' else ('CBNavRewardConfirm', 'reward_confirm'))
        action = self._navigation_input(node, mode, expected_sha256=signature)
        acknowledged.add(key)
        rewards.append(dict(observed=reading, action=action))
        self.page.reset_context()

    def _wait_simulation_page(self, names, *, deadline, acknowledged=None, seconds=10):
        end = min(deadline, time.monotonic()+seconds)
        lobby = self.calibration['pages']['lobby']
        popups = {}
        if 'navigation_reward' in lobby:
            popups['CBNavRewardObserved'] = _SimulationReward
        if self.calibration.get('version') == 2 and 'boss_slots' in lobby:
            popups['CBNavDifficultyNoticeObserved'] = _SimulationDifficultyNotice
        previous, stabilizing_popup = None, False
        while time.monotonic() < end:
            hit = self.choose([*popups, *names])
            observed_at = time.monotonic()
            if observed_at >= deadline:
                break
            if hit in popups:
                reading = _detail(self.last_receipt)
                signature = reading.get('content_sha256')
                key = _navigation_popup_key(reading) if signature else None
                unacknowledged = key and key not in (acknowledged or set())
                if unacknowledged and not stabilizing_popup:
                    # A late first known popup gets one bounded second-read window.
                    end = min(deadline, max(end, observed_at+seconds))
                    stabilizing_popup = True
                if unacknowledged and key == previous:
                    raise popups[hit](reading)
                previous = key
            elif hit in names:
                return hit
            else:
                previous = None
            self.checkpoint()
            time.sleep(.1)
        raise RuntimeError('clan_battle_simulation_navigation_timeout')

    def _navigation_detail(self, wait, *, expected=None, previous=None):
        end = time.monotonic()+10
        while time.monotonic() < end:
            wait(['CBNavDetail'])
            reading = _detail(self.last_receipt)
            if expected is None or reading == expected:
                return reading
            if reading != previous:
                raise RuntimeError('clan_battle_simulation_navigation_changed')
            self.checkpoint()
            time.sleep(.1)
        raise RuntimeError('clan_battle_simulation_navigation_timeout')

    def _open_simulation_target(self, plan):
        """Reach an independent simulation through verified live slots and detail arrows."""
        shared = getattr(self, '_simulation_navigation_budget', None)
        budget = shared if shared is not None else dict(deadline=time.monotonic()+90, acknowledged=set(), rewards=[])
        deadline, acknowledged, rewards = (budget[key] for key in ('deadline', 'acknowledged', 'rewards'))
        while True:
            try:
                wait = lambda names, seconds=10: self._wait_simulation_page(
                    names, deadline=deadline, acknowledged=acknowledged, seconds=seconds)
                if wait(['CBPage_closed', 'CBPage_lobby']) == 'CBPage_closed':
                    raise RuntimeError('clan_battle_closed')
                lobby, lobby_receipt = self.read_page('lobby')
                rows = lobby.get('bosses')
                if type(rows) is not list:
                    raise RuntimeError('clan_battle_simulation_navigation_uncalibrated')
                live = [row for row in rows if row['available']]
                if not live:
                    raise RuntimeError('clan_battle_simulation_boss_unavailable')
                entry = min(live, key=lambda row: (abs(row['boss']-plan['boss']), row['boss']))
                action = self._navigation_input('CBNavOpenBoss', 'open_boss',
                    expected={key: entry[key] for key in ('boss', 'round')})
                detail = self._navigation_detail(wait)
                if detail['boss'] != entry['boss']:
                    raise RuntimeError('clan_battle_simulation_navigation_changed')
                path = [dict(detail)]
                expected = lambda row: {key: row[key] for key in ('period', 'boss', 'phase', 'simulation')}
                if not detail['simulation']:
                    self._navigation_input('CBNavSelectSimulation', 'select_simulation', expected=expected(detail))
                    detail = self._navigation_detail(wait, expected=dict(detail, simulation=True), previous=detail)
                for _ in range(4):
                    if detail['boss'] == plan['boss']:
                        break
                    direction = 'next' if detail['boss'] < plan['boss'] else 'previous'
                    next_boss = detail['boss'] + (1 if direction == 'next' else -1)
                    self._navigation_input('CBNavStep', direction, expected=expected(detail))
                    detail = self._navigation_detail(wait, expected=dict(detail, boss=next_boss), previous=detail)
                    path.append(dict(detail))
                if detail['boss'] != plan['boss'] or detail['simulation'] is not True:
                    raise RuntimeError('clan_battle_simulation_navigation_changed')
                detail, _ = self.read_page('boss_detail')
                if 'simulation_targets' in self.calibration:
                    phases = (_difficulty_phase(self.calibration, plan),)
                else:
                    phases = ('B', 'C') if plan['phase'] == 'BC' else (plan['phase'],)
                if detail['phase'] not in phases:
                    self.run('CBAction_open_difficulty', click=True)
                    wait(['CBPage_difficulty'])
                    self.run('CBAction_select_difficulty', click=True)
                    wait(['CBDifficultyChosen'])
                    self.run('CBAction_confirm_difficulty', click=True)
                    wait(['CBPage_boss_detail'])
                    detail, _ = self.read_page('boss_detail')
                    if detail['phase'] not in phases:
                        raise RuntimeError('clan_battle_difficulty_selection_unverified')
                if wait(['CBPage_target', 'CBPage_boss_detail']) != 'CBPage_target':
                    self.run('CBAction_open_simulation', click=True)
                    wait(['CBPage_target'])
                target, receipt = self.read_page('target')
                if target['live_state'] == 'on':
                    self.run('CBAction_disable_live_state', click=True)
                    wait(['CBFullSimulationReady'])
                    target, receipt = self.read_page('target')
                if target['live_state'] not in ('off', 'absent'):
                    raise RuntimeError('clan_battle_simulation_settings_not_ready')
                return target, dict(lobby=lobby_receipt, select=action, navigation=path, rewards=rewards, target=receipt)
            except _SimulationPopup as exc:
                self._confirm_navigation_popup(exc.reading, **budget)
                if shared is not None:
                    raise _SimulationNavigationRestart() from exc
                _restart_navigation_budget(budget, exc)
                deadline = budget['deadline']

    def read_team(self, *, require_stars=True):
        deadline = time.monotonic() + 15
        while True:
            reading = _detail(self.run('CBReadTeam'))
            if (not require_stars or not self.calibration or self.calibration.get('version') != 2
                    or sum(type(star) is int for star in reading.get('stars', [])) == reading.get('member_count')):
                return reading
            # Cards alternate star digits and equipment badges; observation must not clear a valid team.
            if time.monotonic() >= deadline:
                raise RuntimeError('clan_battle_stars_unreadable')
            self.checkpoint()
            time.sleep(.05)

    def _list_hit(self, section, position):
        # Read-only recognition is dispatched through the same Owner, never a controller shortcut.
        return self.choose([f'CBList_{section}_{position}']) == f'CBList_{section}_{position}'

    def _read_team_after_edit(self, previous_count, *, added_unit_id=None):
        adding = added_unit_id is not None
        expected_count = previous_count + (1 if adding else -1)
        error = 'clan_battle_add_readback_unknown' if adding else 'clan_battle_clear_readback_unknown'
        # The card click effect can temporarily hide an empty slot or a newly added portrait.
        # Observe it settling without resending the edit or accepting any other count change.
        deadline = time.monotonic() + 5
        first_read = True
        while True:
            reading = self.read_team(require_stars=False)
            count = reading['member_count']
            if count == expected_count:
                if not adding or added_unit_id in reading['unit_ids']:
                    return reading
                if sum(type(uid) is int for uid in reading['unit_ids']) == count:
                    raise RuntimeError(error)
            first_unclear_read = first_read and (count == previous_count or (adding and count == expected_count))
            if count not in (previous_count, expected_count) or (time.monotonic() >= deadline and not first_unclear_read):
                raise RuntimeError(error)
            first_read = False
            self.checkpoint()
            time.sleep(.05)

    def _reset_list(self, section, *, find_on_return=None):
        if section == 'formation' and self.calibration.get('version') == 2:
            from .labyrinth.character_recognition import scrollbar
            for _ in range(16):
                if self._list_hit(section, 'top'):
                    return
                self.read_page(section)
                before = scrollbar(self.tasker.controller.cached_image, 'battle', include_geometry=True)['absolute_top']
                if before is None:
                    raise RuntimeError('clan_battle_list_position_unknown')
                if find_on_return is not None:
                    uid, last_seen_top = find_on_return
                    if self._formation_candidate(self.tasker.controller.cached_image, uid, hint_only=True):
                        return True
                    if before <= last_seen_top:
                        find_on_return = None
                self.run('CBAction_characters_top', click=True)
                self.read_page(section)
                after = scrollbar(self.tasker.controller.cached_image, 'battle', include_geometry=True)['absolute_top']
                if self._list_hit(section, 'top'):
                    return
                # Compare actual thumb pixels; a changing thumb length can also change its normalized ratio.
                if after is None:
                    raise RuntimeError('clan_battle_list_position_unknown')
                if after >= before:
                    raise RuntimeError('clan_battle_list_unchanged_no_retry')
            raise RuntimeError('clan_battle_list_top_not_verified')
        if not self._list_hit(section, 'top'):
            name = 'characters_top' if section == 'formation' else 'support_top'
            self.run('CBAction_'+name, click=True)
            if not self._list_hit(section, 'top'):
                raise RuntimeError('clan_battle_list_top_not_verified')

    def _next_list(self, section):
        if self._list_hit(section, 'bottom'):
            return False
        modern_formation = section == 'formation' and self.calibration.get('version') == 2
        if modern_formation:
            from .labyrinth.character_recognition import scrollbar
            self.read_page(section)
            before_position = scrollbar(self.tasker.controller.cached_image, 'battle', include_geometry=True)['absolute_top']
            if before_position is None:
                raise RuntimeError('clan_battle_list_position_unknown')
        before = self.tasker.controller.cached_image.copy()
        name = 'characters_next' if section == 'formation' else 'support_next'
        self.run('CBAction_'+name, click=True)
        self.read_page(section)
        after = self.tasker.controller.cached_image
        if modern_formation:
            after_position = scrollbar(after, 'battle', include_geometry=True)['absolute_top']
            if after_position is None:
                raise RuntimeError('clan_battle_list_position_unknown')
            if after_position <= before_position:
                raise RuntimeError('clan_battle_list_unchanged_no_retry')
            return True
        rois = self.calibration[section]['candidates' if section == 'formation' else 'slots']
        def digest(frame):
            return [hashlib.sha256(frame[y:y+h, x:x+w].tobytes()).digest() for x,y,w,h in rois]
        if after is None or before.shape != after.shape or digest(before) == digest(after):
            raise RuntimeError('clan_battle_list_unchanged_no_retry')
        return True

    def _formation_candidate(self, frame, uid, *, hint_only=False):
        from .clan_battle_characters import box_inside_roi
        matches = [(row, roi) for roi in self.calibration['formation']['candidates']
                   if (row := self.characters.match(frame, uid, roi))]
        if len(matches) > 1 and not hint_only:
            raise RuntimeError('clan_battle_candidate_ambiguous')
        if len(matches) != 1:
            return False
        candidate, roi = matches[0]
        box = candidate['box']
        modern = self.calibration.get('version') == 2
        if box_inside_roi(box, roi) and (not modern or box[1]+box[3] < roi[1]+roi[3]):
            return True
        visible = [box[0], box[1], box[2], roi[1]+roi[3]-box[1]]
        if hint_only or (modern and candidate.get('unit_id') == uid
                and box[1]+box[3] >= roi[1]+roi[3] and box_inside_roi(visible, roi)):
            return False
        raise RuntimeError('clan_battle_candidate_clipped')

    def _find_formation_unit(self, uid, later, hints, *, check_current):
        from .labyrinth.character_recognition import scrollbar
        near = hints.pop(uid, None)
        if near is not None:
            if self._reset_list('formation', find_on_return=(uid, near)):
                return True
        else:
            if check_current:
                self.read_team(require_stars=False)
                if self._formation_candidate(self.tasker.controller.cached_image, uid, hint_only=True):
                    return True
            self._reset_list('formation')
        max_swipes = 48 if self.calibration.get('version') == 2 else 16
        for page_index in range(max_swipes + 1):
            self.read_team(require_stars=False)
            frame = self.tasker.controller.cached_image
            if self._formation_candidate(frame, uid):
                return True
            if later:
                top = scrollbar(frame, 'battle', include_geometry=True)['absolute_top']
                if top is not None:
                    for other in later:
                        if other not in hints and self._formation_candidate(frame, other, hint_only=True):
                            hints[other] = top
            if page_index == max_swipes or not self._next_list('formation'):
                return False

    def form_team(self):
        from .clan_battle import select_support
        team = self.read_team(require_stars=False)
        if (team['member_count'] == 5 and set(team['unit_ids']) == set(self.plan['unit_ids'])
                and sum(type(star) is int for star in team.get('stars', [])) != 5):
            # A matching party with transient badges must be observed, never cleared for missing stars.
            team = self.read_team()
        try:
            self.page.require_team(team)
            return
        except RuntimeError:
            pass
        ids = team.get('unit_ids')
        selected = [uid for uid in ids if type(uid) is int] if type(ids) is list else []
        count = team.get('member_count')
        reuse = (self.support_unit_id is None
            and team.get('support_unit_id') is None and type(count) is int and 0 < count < 5
            and type(ids) is list and len(ids) == 5 and len(selected) == len(set(selected)) == count
            and set(selected) <= set(self.plan['unit_ids']))
        if not reuse:
            for _ in range(5):
                if team['member_count'] == 0:
                    break
                self.tasker.resource.override_pipeline({'CBEditTeam': dict(custom_recognition_param=dict(mode='clear', count=team['member_count']))})
                self.run('CBEditTeam', click=True)
                team = self._read_team_after_edit(team['member_count'])
        planned = self.plan['unit_ids']
        use_hints = self.calibration.get('version') == 2 and self.support_unit_id is None
        hints = {}
        for index, uid in enumerate(planned):
            if uid == self.support_unit_id or uid in team['unit_ids']:
                continue
            later = [other for other in planned[index+1:] if other not in team['unit_ids']] if use_hints else ()
            if not self._find_formation_unit(uid, later, hints, check_current=use_hints and index > 0):
                raise RuntimeError('clan_battle_character_not_found:' + str(uid))
            self.tasker.resource.override_pipeline({'CBEditTeam': dict(custom_recognition_param=dict(mode='add', unit_id=uid))})
            self.run('CBEditTeam', click=True)
            team = self._read_team_after_edit(team['member_count'], added_unit_id=uid)
        if self.support_unit_id is not None:
            self.run('CBAction_open_support', click=True)
            self.wait(['CBPage_support'], seconds=10)
            self._reset_list('support')
            selected = None
            for _ in range(16):
                reading = _detail(self.run('CBReadSupport'))
                try:
                    selected = select_support(self.plan, self.snapshot, reading['candidates'])
                except RuntimeError as exc:
                    if str(exc) != 'clan_battle_matching_support_unavailable':
                        raise
                    selected = None
                if selected is not None:
                    self.tasker.resource.override_pipeline({'CBAddSupport': dict(custom_recognition_param=dict(
                        mode='support_add', support_id=selected['support_id'], unit_id=selected['unit_id'], stars=selected['stars']))})
                    self.run('CBAddSupport', click=True)
                    self.wait(['CBPage_formation'], seconds=10)
                    break
                if not self._next_list('support'):
                    break
            if selected is None:
                raise RuntimeError('clan_battle_matching_support_unavailable')
        self.page.require_team(self.read_team())

    def verify_team(self, plan):
        reading = self.read_team()
        self.page.require_team(reading)
        if self.account != plan['account_id']:
            raise RuntimeError('clan_battle_account_mismatch')
        return dict(reading, account_id=self.account, server=self.calibration['server'],
            native_evidence=dict(account=self.account_receipt, formation=reading,
                calibration_id=self.calibration['calibration_id'], evidence_kind=self.calibration['evidence_kind']))

    def read_battle_settings(self, initial=None):
        if initial is None:
            node = 'CBOpeningRead' if self.opening_paused else 'CBReadSettings'
            hit = self.wait([node], seconds=5) if self.opening_paused else self.choose([node])
            if hit != node:
                raise RuntimeError('clan_battle_controls_unknown')
            initial = self.last_receipt
        state = _detail(initial)
        state = self._opening_capture_before_change(initial, state)
        self.last_controls = deepcopy(state)
        return state

    def _opening_capture_before_change(self, initial, state):
        """Finish a qualified capture before a needed change, then re-read the pause."""
        if (getattr(self, 'formal_opening', False) and getattr(self, 'action', {}).get('kind') == 'carryover'
                and state.get('opening_action_kind') == 'carryover'):
            return state
        timed = getattr(self, '_opening_capture_timed_observer', None)
        capture = getattr(self, '_opening_capture', None)
        plan = getattr(self, 'plan', {})
        units, sets = state.get('unit_ids'), state.get('sets')
        desired = plan.get('set_by_unit')
        from .clan_battle_opening_observation import OpeningCaptureMaterials
        if (not getattr(self, 'opening_paused', False) or state.get('paused') is not True or capture is None or timed is None
                or not isinstance(capture, OpeningCaptureMaterials)
                or not timed.capture_qualified(getattr(self, 'battle_speed', None)) or plan.get('switches')
                or type(units) is not list or len(units) != 5 or type(sets) is not list or len(sets) != 5
                or type(desired) is not dict or any(str(uid) not in desired for uid in units)):
            return state
        affected = set(units) if state.get('auto') != plan.get('auto') else set()
        affected.update(uid for uid, actual in zip(units, sets) if actual != desired[str(uid)])
        if state.get('battle_speed') != getattr(self, 'battle_speed', None):
            affected.update(units)
        if not affected:
            return state
        from .clan_battle import require_first_ub, require_frozen_opening
        try:
            require_first_ub(state, affected)
        except RuntimeError as exc:
            if str(exc) != 'clan_battle_first_ub_unverified':
                return state
        else:
            return state  # A separate causal proof already permits the affected roles.
        if (initial != getattr(self, '_opening_anchor_start_receipt', None)
                or self.battle_start != 'sent' or state.get('opening_start_token') != timed.start_token
                or getattr(getattr(capture, 'directory', None), 'name', None) != timed.start_token
                or timed.owner_source != self._opening_capture_expected[0]
                or any(type(uid) is not int for uid in units) or len(set(units)) != 5
                or type(plan.get('unit_ids')) is not list or set(units) != set(plan['unit_ids'])
                or set(units) != set(timed.units)
                or self._opening_capture_source() != self._opening_capture_expected):
            raise RuntimeError('clan_battle_opening_capture_binding_changed')
        result = self.finish_opening_capture()
        if (not result or result.get('source_bound') is not True
                or self._opening_capture_source() != self._opening_capture_expected):
            raise RuntimeError('clan_battle_opening_capture_binding_changed')
        if self.choose(['CBOpeningRead']) != 'CBOpeningRead':
            raise RuntimeError('clan_battle_controls_unknown')
        fresh = _detail(self.last_receipt)
        require_frozen_opening(fresh, state)
        if any(fresh.get(key) != state.get(key) for key in ('unit_ids', 'sets', 'auto', 'battle_speed')):
            raise RuntimeError('clan_battle_switch_state_changed')
        return fresh

    def _toggle(self, node, previous, desired, *, slot=None, remaining_seconds=None):
        before = getattr(self, 'last_controls', None)
        state = self.read_battle_settings()
        if remaining_seconds is not None and (type(remaining_seconds) is not int or not 0 <= remaining_seconds <= 89
                or state.get('same_frame') is not True or type(state.get('remaining_seconds')) is not int
                or state['remaining_seconds'] != remaining_seconds
                or self.calibration and self.calibration.get('version') == 2
                and (type(state.get('battle_speed')) is not int or state['battle_speed'] != 1)):
            raise RuntimeError('clan_battle_switch_window_missed')
        if before is not None and any(before.get(key) != state.get(key) for key in ('auto', 'sets', 'unit_ids')):
            raise RuntimeError('clan_battle_switch_state_changed')
        if self.opening_paused and before is not None:
            from .clan_battle import require_frozen_opening
            require_frozen_opening(state, before, carryover=bool(getattr(self, 'formal_opening', False)
                and getattr(self, 'action', {}).get('kind') == 'carryover'))
            if before.get('battle_speed') != state.get('battle_speed'):
                raise RuntimeError('clan_battle_switch_state_changed')
        key = 'auto' if slot is None else 'sets'
        value = state['auto'] if slot is None else state['sets'][slot]
        if type(previous) is not bool or type(desired) is not bool or value is not previous or previous is desired:
            raise RuntimeError('clan_battle_switch_previous_unknown')
        expected = dict(auto=state['auto'], sets=list(state['sets']), unit_ids=list(state['unit_ids']))
        if slot is None:
            expected['auto'] = desired
        else:
            expected[key][slot] = desired
        params = dict(mode='battle_auto' if slot is None else 'battle_set', previous=previous,
                      desired=desired, auto=state['auto'], sets=state['sets'], unit_ids=state['unit_ids'])
        if remaining_seconds is not None:
            params['expected_remaining_seconds'] = remaining_seconds
        if slot is not None:
            params['slot'] = slot
        confirmed = 'CBSettingsConfirmed'
        if self.opening_paused:
            if remaining_seconds is not None:
                raise RuntimeError('clan_battle_switch_window_missed')
            node = 'CBOpeningAuto' if slot is None else 'CBOpeningSet'
            params.update(mode='opening_auto' if slot is None else 'opening_set', battle_speed=state['battle_speed'])
            expected.update(battle_speed=state['battle_speed'])
            if not (getattr(self, 'formal_opening', False) and getattr(self, 'action', {}).get('kind') == 'carryover'):
                params['remaining_seconds'] = expected['remaining_seconds'] = state['remaining_seconds']
            confirmed = 'CBOpeningConfirmed'
        if self.tasker.resource.override_pipeline({node: dict(custom_recognition_param=params),
                confirmed: dict(custom_recognition_param=dict(mode='opening_read' if self.opening_paused else 'battle_read', **expected))}) is False:
            raise RuntimeError('clan_battle_switch_pipeline_rejected')
        reading = _detail(self.run(node, click=True))
        if any(reading.get(name) != expected[name] for name in expected):
            raise RuntimeError('clan_battle_switch_readback_unknown')
        if (remaining_seconds is not None and self.calibration and self.calibration.get('version') == 2
                and (type(reading.get('battle_speed')) is not int or reading['battle_speed'] != 1)):
            raise RuntimeError('clan_battle_speed_unknown')
        self.last_controls = deepcopy(reading)
        return reading

    def toggle_auto(self, previous, desired, *, remaining_seconds=None):
        return self._toggle('CBToggleAuto', previous, desired, remaining_seconds=remaining_seconds)

    def toggle_set(self, slot, previous, desired, *, remaining_seconds=None):
        if type(slot) is not int or not 0 <= slot < 5:
            raise RuntimeError('clan_battle_switch_slot_invalid')
        return self._toggle('CBToggleSet', previous, desired, slot=slot, remaining_seconds=remaining_seconds)

    def read_tick(self):
        self.checkpoint()
        hit = self.choose(['CBPage_result', 'CBReadSettings'])
        if hit == 'CBPage_result':
            receipt = self.last_receipt
            result = _detail(receipt)
            return dict(result=dict(result, result_verified=True, result_evidence=receipt))
        if hit != 'CBReadSettings':
            raise RuntimeError('clan_battle_tick_unknown')
        state = self.read_battle_settings(self.last_receipt)
        if (type(state.get('remaining_seconds')) is not int or not 0 <= state['remaining_seconds'] <= 90
                or state.get('same_frame') is not True or type(state.get('frame_pixel_sha256')) is not str
                or re.fullmatch('[0-9a-f]{64}', state['frame_pixel_sha256']) is None):
            raise RuntimeError('clan_battle_tick_same_frame_unknown')
        if getattr(self, 'plan', {}).get('switches') and (type(state.get('battle_speed')) is not int or state['battle_speed'] != 1):
            raise RuntimeError('clan_battle_speed_unknown')
        return dict(remaining_seconds=state['remaining_seconds'], state=state,
            same_frame=True, frame_pixel_sha256=state['frame_pixel_sha256'])

    def _opening_capture_source(self):
        self.checkpoint()
        if not self.account_binding_valid(self.plan):
            raise RuntimeError('clan_battle_opening_capture_source_changed')
        context = getattr(self.tasker, '_context', None)
        controller = context.tasker.controller if context is not None else self.tasker.controller
        info = controller.info
        if type(info) is not dict or info.get('type') != 'adb':
            raise RuntimeError('clan_battle_opening_capture_adb_required')
        return self._account_binding, info.get('adb_path'), info.get('adb_serial')

    def capture_opening_frame(self, display_id, *, timeout=10):
        """Collect a fresh-command interval; this alone cannot prove no UB history."""
        from .clan_battle_opening_observation import capture_adb_png, decode_capture
        expected = self._opening_capture_source()
        data, packet = capture_adb_png(expected[1], expected[2], display_id, timeout=timeout)
        if self._opening_capture_source() != expected:
            raise RuntimeError('clan_battle_opening_capture_source_changed')
        return decode_capture(data, packet)

    def _start_opening_capture(self, token):
        import os
        if os.environ.get('PCR_CLAN_OPENING_CAPTURE_ONLY') != '1':
            return
        from .clan_battle_opening_observation import OpeningCaptureMaterials, OpeningMp4Materials
        capture_format = os.environ.get('PCR_CLAN_OPENING_CAPTURE_FORMAT') or 'png'
        capture_type = {'png': OpeningCaptureMaterials, 'mp4': OpeningMp4Materials}.get(capture_format)
        if capture_type is None:
            raise RuntimeError('clan_battle_opening_capture_format_invalid')
        directory = Path(os.environ['PCR_CLAN_OPENING_CAPTURE_DIR']).resolve()
        display_id = os.environ['PCR_CLAN_OPENING_CAPTURE_DISPLAY_ID']
        if not directory.is_relative_to((native.ROOT/'.runtime').resolve()) or re.fullmatch('[0-9]+', display_id) is None:
            raise RuntimeError('clan_battle_opening_capture_binding_invalid')
        expected = self._opening_capture_source()
        self._opening_capture_expected = expected
        characters = getattr(self, 'characters', None)
        self._opening_capture_observer = getattr(characters, 'opening_observer', None)
        self._opening_capture_timed_observer = getattr(characters, 'opening_timed_observer', None)
        self._opening_anchor_start_receipt, self._opening_anchor_start_receipt_path = None, None
        self._opening_capture = capture_type(expected[1], expected[2], display_id, directory/token).start()
        if self._opening_capture_source() != expected:
            self.finish_opening_capture()
            raise RuntimeError('clan_battle_opening_capture_source_changed')

    def finish_opening_capture(self):
        capture = getattr(self, '_opening_capture', None)
        if capture is None:
            return None
        capture.stop_and_join()
        try:
            source_valid = self._opening_capture_source() == self._opening_capture_expected
        except Exception:
            source_valid = False
        result = capture.finish(source_valid=source_valid, battle_start=self.battle_start)
        self.opening_capture_evidence = dict(path=str(capture.directory/'capture.json'),
            status=result['status'], source_bound=result['source_bound'], first_ub_verified=False)
        if result.get('format') == 'mp4':
            self.opening_capture_evidence.update(format='mp4', coverage='unknown', first_frame_verified=False,
                media_sha256=(result.get('media') or {}).get('sha256'))
        observer = getattr(self, '_opening_capture_observer', None)
        if observer is not None:
            anchors = observer.write_anchors(capture.directory, start_token=capture.directory.name,
                owner_source=self._opening_capture_expected[0], source_valid=source_valid,
                start_receipt=getattr(self, '_opening_anchor_start_receipt', None),
                start_receipt_path=getattr(self, '_opening_anchor_start_receipt_path', None))
            anchor_evidence = dict(path=str(capture.directory/'anchors.json'), status=anchors['status'],
                sha256=hashlib.sha256((capture.directory/'anchors.json').read_bytes()).hexdigest(),
                first_frame_verified=False, first_ub_verified=False, coverage='unknown')
            result['anchors'] = anchor_evidence
            self.opening_capture_evidence['anchors'] = anchor_evidence
        timed = getattr(self, '_opening_capture_timed_observer', None)
        paused = getattr(observer, 'anchor_refs', {}).get('paused') if observer is not None else None
        if timed is not None and paused is not None:
            receipt = getattr(self, '_opening_anchor_start_receipt', None)
            try:
                state = _detail(receipt)
                same_team = (type(state.get('unit_ids')) is list and len(state['unit_ids']) == 5
                    and len(set(state['unit_ids'])) == 5 and set(state['unit_ids']) == set(timed.units)
                    and set(timed.units) == set(self.plan['unit_ids']))
                bound = (source_valid and result.get('status') == 'captured'
                    and result.get('anchors', {}).get('status') == 'captured'
                    and timed.owner_source == self._opening_capture_expected[0]
                    and timed.start_token == capture.directory.name
                    and state.get('opening_start_token') == timed.start_token and same_team)
            except (KeyError, TypeError, RuntimeError):
                state, bound = {}, False
            if not bound:
                timed.invalidate('owner_capture_binding_unverified')
            from .clan_battle_opening_observation import bind_screencap_materials
            material = bind_screencap_materials(capture.directory, result,
                display_id=getattr(capture, 'display_id', None), owner_bound=bound)
            result['timed_capture_material'] = material
            self.opening_capture_evidence['timed_capture_material'] = material
            # No production classifier or capture-clock qualification is available yet.
            # Never turn synthetic events or a file timestamp into a live no-UB proof.
            timed.invalidate('capture_timeline_or_marker_unverified')
            observation = timed.seal(capture.directory.name, paused['frame_pixel_sha256'],
                owner_source=self._opening_capture_expected[0], battle_speed=state.get('battle_speed'))
            result['timed_observation'] = observation
            self.opening_capture_evidence['timed_observation'] = observation
        from .supported_daily import _atomic_json
        _atomic_json(self.directory/'opening-capture.json', self.opening_capture_evidence)
        self._opening_capture = None
        self._opening_capture_observer = None
        self._opening_capture_timed_observer = None
        return result

    def arm_opening_start(self, token, *, start_seconds=None, action_kind=None):
        policy = self.freeze_opening_control_policy(self.plan)
        binding = getattr(self, '_account_binding', None)
        self.characters.begin_opening(token, control_policy=policy, owner_source=binding,
            start_seconds=start_seconds, action_kind=action_kind,
            owner_valid=lambda: self.account_binding_valid(self.plan) and self._account_binding == binding)
        opening_match = dict(mode='opening_match', opening_start_token=token,
            battle_speed=self.battle_speed, start_seconds=start_seconds if start_seconds is not None else getattr(self, 'start_seconds', 90))
        if not self.tasker.resource.override_pipeline({
                'CBStart': dict(custom_recognition_param=dict(mode='start', opening_start_token=token)),
                'CBOpeningMatch': dict(custom_recognition_param=opening_match)}):
            raise RuntimeError('clan_battle_switch_pipeline_rejected')
        try:
            self._start_opening_capture(token)
        except Exception as exc:
            if not self.started and self.battle_start == 'not_sent':
                receipt = dict(input_execution='not_sent', pre_input_validation_failed=True,
                    clan_battle_phase='simulation_start', reason='opening_capture_before_cbstart')
                pending, action = getattr(self, 'pending', None), getattr(self, 'action', None)
                if type(pending) is dict and type(action) is dict and action.get('kind') in ('regular', 'carryover'):
                    receipt.update(clan_battle_phase=action['kind']+'_start', daily_intent_id=pending['intent_id'])
                raise native.NativeInputNotSent(receipt, str(exc)) from exc
            raise

    def start_and_read(self, plan):
        try:
            return self._start_and_read_bound(plan)
        except ClanDisconnected as exc:
            self.finish_opening_capture()
            recover_disconnect(self, plan, exc)
            raise
        finally:
            self.finish_opening_capture()

    def _start_and_read_bound(self, plan):
        from .clan_battle import execute_switches, require_timing, require_first_ub, require_frozen_opening
        ready = self.verify_team(plan)
        require_timing(self, plan)
        if self.calibration and self.calibration.get('auto_battle') and not plan['switches']:
            self.arm_opening_start(uuid4().hex, start_seconds=90, action_kind='simulation')
        start = self.run('CBStart', click=True)
        if self.calibration and self.calibration.get('auto_battle') and not plan['switches']:
            initial = start
            opening = _detail(start)
            self.opening_paused = opening.get('paused') is True
            if not self.opening_paused and opening.get('opening_direct') is not True:
                raise RuntimeError('clan_battle_opening_not_verified')
        else:
            hit = self.wait(['CBReadSettings', 'CBPage_result'], seconds=30)
            if hit != 'CBReadSettings':
                raise RuntimeError('clan_battle_settings_not_observed')
            initial = self.last_receipt
        settings = execute_switches(self, plan, initial=initial)
        if self.opening_paused:
            state = deepcopy(settings['observation'])
            require_frozen_opening(state, settings['opening_evidence'])
            if any(state.get(key) != settings['opening_evidence'].get(key) for key in ('auto', 'sets', 'unit_ids', 'battle_speed')):
                raise RuntimeError('clan_battle_switch_state_changed')
            for _ in range(2):
                if state['battle_speed'] == self.battle_speed:
                    break
                require_first_ub(state, plan['unit_ids'])
                expected = {key: deepcopy(state[key]) for key in ('auto', 'sets', 'unit_ids', 'battle_speed', 'remaining_seconds')}
                desired = {1: 2, 2: 4, 4: 1}[state['battle_speed']]
                if not self.tasker.resource.override_pipeline({
                        'CBOpeningSpeed': dict(custom_recognition_param=dict(mode='opening_speed', **expected)),
                        'CBOpeningConfirmed': dict(custom_recognition_param=dict(mode='opening_read', **dict(expected, battle_speed=desired)))}):
                    raise RuntimeError('clan_battle_switch_pipeline_rejected')
                before = deepcopy(state)
                state = self.read_battle_settings(self.run('CBOpeningSpeed', click=True))
                require_frozen_opening(state, before)
                if any(state.get(key) != value for key, value in dict(expected, battle_speed=desired).items()):
                    raise RuntimeError('clan_battle_speed_readback_unknown')
                settings['actions'].append(dict(control='speed', previous=expected['battle_speed'], target=desired))
            if state['battle_speed'] != self.battle_speed:
                raise RuntimeError('clan_battle_speed_readback_unknown')
            settings['opening_evidence'] = deepcopy(state)
            expected = {key: deepcopy(state[key]) for key in ('auto', 'sets', 'unit_ids', 'battle_speed', 'remaining_seconds')}
            if not self.tasker.resource.override_pipeline({'CBResumeOpening': dict(custom_recognition_param=dict(mode='opening_resume', **expected)),
                    'CBSettingsConfirmed': dict(custom_recognition_param=dict(mode='battle_read', **{key: value for key, value in expected.items() if key != 'remaining_seconds'}))}):
                raise RuntimeError('clan_battle_switch_pipeline_rejected')
            self.run('CBResumeOpening', click=True)
            self.opening_paused = False
            self.wait(['CBSettingsConfirmed'], seconds=15)
            settings['observation'] = self.read_battle_settings(self.last_receipt)
            settings['battle_speed'] = self.battle_speed
        elif (self.calibration and self.calibration.get('auto_battle') and not plan['switches']
                and opening.get('opening_direct') is True):
            settings['battle_speed'] = self.battle_speed
        self.wait(['CBPage_result'], seconds=360)
        result_receipt = self.last_receipt
        result = _result_detail(result_receipt)
        require_target_identity(plan, result)
        self._simulation_result_receipt = result_receipt
        return dict(ready, damage=result['damage'], result_verified=True, settings_verified=True,
            settings_evidence=settings, result_evidence=result_receipt,
            native_evidence=dict(start=start, ready=ready['native_evidence'], result=result_receipt))

    def return_to_target(self, plan):
        before_receipt = getattr(self, 'last_receipt', None)
        if before_receipt is not getattr(self, '_simulation_result_receipt', None):
            raise RuntimeError('clan_battle_result_unknown')
        before = _result_detail(before_receipt)
        require_target_identity(plan, before)
        action = self.run('CBAction_return_to_target', click=True)
        if self.calibration.get('version') == 2:
            self._simulation_result_return_verified = True
            try:
                target, target_receipt = self._open_simulation_target(plan)
            finally:
                self._simulation_result_return_verified = False
        else:
            self.wait(['CBPage_target'], seconds=15)
            target, target_receipt = self.read_page('target')
        require_target_identity(plan, target)
        if self.account != plan['account_id']:
            raise RuntimeError('clan_battle_account_mismatch')
        return dict(target, account_id=self.account, server=self.calibration['server'],
            return_verified=True, native_evidence=dict(before=before_receipt,
                action=action, target=target_receipt))
