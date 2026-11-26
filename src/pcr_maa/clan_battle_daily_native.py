"""Read real formal-battle counters; missing consumption pages never authorize input."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from maa.custom_recognition import CustomRecognition

from .clan_battle_native import (ClanBattlePage, NativeClanBattle, OPENING_CLOCK_PATTERN, _boss_name_node,
    _boss_name_pattern, _current_period, _detail, _node, _result_detail, _roi, result_damage_line,
    result_damage_nodes, reviewed_damage_line_nodes)
from .clan_battle_characters import ClanBattleCharacters, box_inside_roi
from .guide_trial_native import NativeGuideTrial
from .frame_recognition import same_frame_context


PAGE_FIELDS = {
    'lobby': {'remaining_count', 'carry_count', 'cp'},
    'target': {'remaining_count', 'cp', 'health', 'boss', 'phase'},
    'carry_empty': set(),
}
FULL_CP_PATTERN = r'[已己]达今[天无]的上限'
PATTERNS = {
    'remaining_count': r'^[0-3]$',
    'carry_count': r'^[0-3]$',
    'cp': rf'^(?:[0-9]{{1,3}}\s*/\s*900|{FULL_CP_PATTERN})$',
    'health': r'^[0-9][0-9,，]*\s*/\s*[1-9][0-9,，]*$',
    'phase': r'^第[1-9]阶段$',
    'period': r'^[0-1]?[0-9]/[0-3]?[0-9]\s*[:：]?\s*[0-2][0-9]:[0-5][0-9]\s*[~～〜至—－-]\s*[0-1]?[0-9]/[0-3]?[0-9]\s*[:：]?\s*[0-2][0-9]:[0-5][0-9]$',
    'game_day': r'^20[0-9]{2}[-/][0-1][0-9][-/][0-3][0-9]$',
    'damage': r'^[0-9][0-9,，]*$',
    'remaining_seconds': r'^(?:1[:：](?:[0-2][0-9]|30)|0[:：][0-5][0-9])$',
}
EXECUTION_FIELDS = {
    'adventure': set(), 'boss_detail': {'boss', 'phase'}, 'formation': set(), 'support': set(),
    'battle': {'remaining_seconds'}, 'result': {'damage'}, 'used_units': {'game_day'}, 'carry': set(),
}
ACTION_PAGES = dict(open_clan='adventure', open_boss='lobby', open_formal='boss_detail',
    open_formation='target', cancel_formation='formation', back_to_lobby='boss_detail',
    back_to_adventure='lobby', open_support='formation', support_back='support',
    characters_top='formation', characters_next='formation', support_top='support', support_next='support',
    start='formation', open_used='lobby', close_used='used_units', open_carry='lobby', close_carry='carry',
    close_empty_carry='carry_empty', open_carry_for_target='target', select_carry='carry', return_to_lobby='result')
DESTINATIONS = dict(open_clan='lobby', open_boss='boss_detail', open_formal='target', open_formation='formation',
    cancel_formation='boss_detail', back_to_lobby='lobby', back_to_adventure='adventure',
    open_support='support', support_back='formation', start='battle', open_used='used_units', close_used='lobby',
    open_carry='carry', open_carry_for_target='carry', close_carry='lobby', close_empty_carry='lobby',
    select_carry='formation', return_to_lobby='lobby', enable_carry_team='carry_selection', close_carry_selection='boss_detail')



def formal_opening_pipeline(calibration, plan, *, require_scope=True, carryover=False):
    """Formal controls have their own guards, clocks and 1/2-speed cycle."""
    from .clan_battle_inheritance import inheritance_pipeline
    spec = calibration.get('daily', {}).get('execution', {}).get('opening')
    keys = {'battle_speed', 'opening_guards', 'paused_guards', 'clock', 'auto_on', 'auto_off',
        'speed', 'speed_two', 'set_badge', 'targets', 'inheritance', 'samples'}
    if type(spec) is not dict or set(spec) - {'start_seconds', 'empty_tp'} != keys:
        raise ValueError('clan_battle_daily_opening_calibration_missing')
    if type(spec['battle_speed']) is not int or spec['battle_speed'] != 2:
        raise ValueError('clan_battle_daily_speed_invalid')
    seconds = spec.get('start_seconds', [90])
    if (type(seconds) is not list or not seconds or any(type(n) is not int or not 1 <= n <= 90 for n in seconds)
            or len(set(seconds)) != len(seconds) or 90 not in seconds):
        raise ValueError('clan_battle_daily_start_time_calibration_invalid')
    if require_scope and plan.get('support_mode') != 'none':
        from .clan_battle import load_snapshot, support_requirement
        if plan.get('support_mode') != 'qsjs' or support_requirement(plan, load_snapshot(plan)) is not None:
            raise RuntimeError('clan_battle_support_mode_uncalibrated')
    if require_scope and plan.get('switches') != []:
        raise RuntimeError('clan_battle_daily_opening_axis_unsupported')
    nodes = {}
    for group in ('opening_guards', 'paused_guards'):
        if type(spec[group]) is not list or len(spec[group]) != 3:
            raise ValueError('clan_battle_daily_opening_calibration_missing')
        for index, marker in enumerate(spec[group]):
            nodes[f'CBFormalOpening_{group}_{index}'] = _node(marker)
    _roi(spec['clock'])
    nodes['CBFormalOpeningClock'] = _node({'roi': spec['clock']}, expected=OPENING_CLOCK_PATTERN)
    nodes['CBFormalOpeningClockScaled'] = _node({'roi': [0, 0, spec['clock'][2]*2, spec['clock'][3]*2]}, expected=OPENING_CLOCK_PATTERN)
    nodes['CBDailyOpeningStartClock'] = _node({'roi': calibration['daily']['execution']['pages']['battle']['fields']['remaining_seconds']},
        expected=PATTERNS['remaining_seconds'])
    if type(spec['speed']) is not dict or set(spec['speed']) != {'1', '2'}:
        raise ValueError('clan_battle_daily_speed_invalid')
    for speed, marker in spec['speed'].items():
        nodes['CBFormalOpeningSpeed' + speed] = _node(marker, expected='^' + speed + '倍速$')
    for name in ('auto_on', 'auto_off'):
        nodes['CBFormalOpening_' + name] = _node(spec[name])
    nodes['CBDailyTwoSpeed'] = _node(spec['speed_two'])
    for index in range(5):
        for key, name in (('set_badge', 'Set'),):
            marker = deepcopy(spec[key])
            marker['roi'] = _roi(marker['roi']).copy()
            marker['roi'][0] += 116 * index
            nodes[f'CBFormalOpening{name}{index}'] = _node(marker)
    targets = spec['targets']
    if (type(targets) is not dict or set(targets) != {'pause', 'auto', 'speed', 'resume', 'sets'}
            or type(targets['sets']) is not list or len(targets['sets']) != 5):
        raise ValueError('clan_battle_daily_opening_targets_invalid')
    for target in [*(targets[key] for key in ('pause', 'auto', 'speed', 'resume')), *targets['sets']]:
        _roi(target)
    samples = spec['samples']
    if (type(samples) is not list or len(samples) != 3
            or {row.get('page') for row in samples if type(row) is dict} != {'battle', 'paused', 'battle_two'}):
        raise ValueError('clan_battle_daily_opening_samples_missing')
    for sample in samples:
        if type(sample) is not dict or set(sample) != {'page', 'path', 'sha256'}:
            raise ValueError('clan_battle_daily_opening_sample_invalid')
        path = Path(sample['path'])
        if (not path.is_absolute() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']):
            raise ValueError('clan_battle_daily_opening_sample_changed')
    nodes.update(inheritance_pipeline(spec['inheritance']))
    inheritance_samples = spec['inheritance']['samples']
    if type(inheritance_samples) is not list or not inheritance_samples:
        raise ValueError('clan_battle_inheritance_samples_missing')
    for sample in inheritance_samples:
        if type(sample) is not dict or set(sample) != {'page', 'path', 'sha256'}:
            raise ValueError('clan_battle_inheritance_sample_invalid')
        path = Path(sample['path'])
        if (not path.is_absolute() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']):
            raise ValueError('clan_battle_inheritance_sample_changed')
    # Character callbacks are already held by the real Owner resource and route to the current slot.
    for name, mode, click in (('CBOpeningRead', 'opening_read', False),
            ('CBOpeningConfirmed', 'opening_read', False), ('CBOpeningAuto', 'opening_auto', True),
            ('CBOpeningSet', 'opening_set', True), ('CBOpeningSpeed', 'opening_speed', True),
            ('CBResumeOpening', 'opening_resume', True), ('CBOpeningMatch', 'opening_match', False),
            ('CBDailyOpeningBinding', 'formal_pause', False),
            ('CBDailyOpeningRetryBinding', 'formal_pause_retry', False)):
        nodes[name] = dict(recognition='Custom', custom_recognition='ClanBattleCharacters',
            custom_recognition_param=dict(mode=mode), action='Click' if click else 'DoNothing', target=True,
            next=['CBOpeningConfirmed'] if name in ('CBOpeningAuto', 'CBOpeningSet', 'CBOpeningSpeed') else [],
            timeout=15000, pre_delay=0, post_delay=300 if click else 0,
            on_error=['LabyDisconnect6002Observed'])
        if click:
            nodes[name]['max_hit'] = 1
    nodes['CBDailyPauseOpening'] = dict(recognition='And',
        all_of=[*[f'CBFormalOpening_opening_guards_{i}' for i in range(2 if carryover else 3)],
            *([] if carryover else ['CBDailyOpeningStartClock']), 'CBDailyOpeningBinding'], action='Click', target=targets['pause'],
        max_hit=1, next=['CBOpeningRead', 'CBDailyPauseRetry'], timeout=15000, rate_limit=1, pre_delay=0, post_delay=300,
        on_error=['LabyDisconnect6002Observed'])
    pause = targets['pause']
    nodes['CBDailyOpeningInputReady'] = dict(recognition='ColorMatch',
        roi=[min(1235, max(0, pause[0]-24)), min(696, max(0, pause[1]-9)), 45, 24],
        method=4, lower=[220, 220, 220],
        upper=[255, 255, 255], count=400, action='DoNothing', next=[], pre_delay=0, post_delay=0)
    nodes['CBDailyPauseRetry'] = dict(recognition='And',
        all_of=['CBDailyOpeningInputReady', *[f'CBFormalOpening_opening_guards_{i}' for i in range(2 if carryover else 3)],
            *([] if carryover else ['CBDailyOpeningStartClock']), 'CBDailyOpeningRetryBinding'], action='Click', target=pause,
        max_hit=1, next=['CBOpeningRead'], timeout=15000, rate_limit=1, pre_delay=0, post_delay=300,
        on_error=['LabyDisconnect6002Observed'])
    return nodes


def validate_daily_calibration(calibration):
    daily = calibration.get('daily')
    if (calibration.get('version') != 2 or type(daily) is not dict
            or set(daily) != ({'version', 'cp_semantics', 'pages', 'samples', 'execution'}
                if daily.get('version') == 2 else {'version', 'cp_semantics', 'pages', 'samples'})
            or type(daily['version']) is not int or daily['version'] not in (1, 2)
            or daily['cp_semantics'] != 'daily_earned'
            or type(daily['pages']) is not dict or set(daily['pages']) != set(PAGE_FIELDS)):
        raise ValueError('clan_battle_daily_calibration_invalid')
    covered = set()
    if type(daily['samples']) is not list or not daily['samples']:
        raise ValueError('clan_battle_daily_samples_missing')
    for sample in daily['samples']:
        if (type(sample) is not dict or set(sample) != {'page', 'path', 'sha256'}
                or sample['page'] not in PAGE_FIELDS or type(sample['path']) is not str):
            raise ValueError('clan_battle_daily_sample_invalid')
        path = Path(sample['path'])
        if (not path.is_absolute() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']):
            raise ValueError('clan_battle_daily_sample_changed')
        covered.add(sample['page'])
    if covered != set(PAGE_FIELDS):
        raise ValueError('clan_battle_daily_samples_missing')
    for page, spec in daily['pages'].items():
        fields = PAGE_FIELDS[page] | ({'period', 'phase'} if page == 'lobby' and daily['version'] == 2 else set())
        if (type(spec) is not dict or set(spec) != {'labels', 'rejects', 'fields'}
                or type(spec['labels']) is not list or len(spec['labels']) < 2
                or type(spec['rejects']) is not list
                or type(spec['fields']) is not dict or set(spec['fields']) != fields):
            raise ValueError('clan_battle_daily_page_invalid:' + page)
    return daily


def _formal_damage_nodes(calibration, plan, spec):
    name = 'CBDaily_result_damage'
    roi = spec['fields']['damage']
    if 'damage_line' in spec:
        layout = spec['damage_line']
        if isinstance(layout, dict) and any(s['page'] == 'result' and s['sha256'] == layout.get('sample_sha256')
                                            for s in calibration['samples']):
            raise ValueError('clan_battle_result_damage_uncalibrated')
        return reviewed_damage_line_nodes(name, roi, layout, calibration['daily']['execution']['samples'])
    production = calibration.get('evidence_kind') == 'device_saved_frame'
    if (calibration.get('calibration_id') == 'cn-bilibili-202610141448-clan-simulation'
            and calibration.get('frame_size') == [1280, 720]
            and (not production or plan.get('content_version') == '202610141448')):
        return result_damage_nodes(roi, name)
    if production:
        raise ValueError('clan_battle_result_damage_uncalibrated')
    return {name: _node({'roi': roi}, expected=PATTERNS['damage'])}


def daily_pipeline(calibration, plan):
    daily = validate_daily_calibration(calibration)
    nodes = {}
    pages = dict(daily['pages'])
    if daily['version'] == 2:
        pages.update(daily['execution']['pages'])
    for page, spec in pages.items():
        for kind in ('labels', 'rejects'):
            for index, label in enumerate(spec[kind]):
                nodes[f'CBDaily_{page}_{kind}_{index}'] = _node(label,
                    ocr_threshold=.8 if page == 'adventure' and kind == 'labels' else .9)
        for field, roi in spec['fields'].items():
            expected = PATTERNS.get(field)
            if field == 'boss' and 'nameplate_levels' in calibration.get('pages', {}).get('boss_detail', {}):
                nodes[f'CBDaily_{page}_{field}'] = _boss_name_node(calibration, roi, [plan['boss']])
                continue
            if field == 'boss':
                expected = '^' + re.escape(calibration['boss_targets'][str(plan['boss'])]['name']) + '$'
            if page == 'result' and field == 'damage':
                nodes.update(_formal_damage_nodes(calibration, plan, spec))
            else:
                nodes[f'CBDaily_{page}_{field}'] = _node({'roi': roi}, expected=expected)
                if field == 'health':
                    # The detector can split the HP ratio into incomplete number fragments.
                    nodes[f'CBDaily_{page}_{field}']['only_rec'] = True
        nodes['CBDailyPage_' + page] = dict(recognition='Custom', custom_recognition='ClanBattleDailyPage',
            custom_recognition_param=dict(page=page), action='DoNothing', next=[], timeout=1000,
            pre_delay=0, post_delay=0)
    return nodes


class ClanBattleDailyPage(CustomRecognition):
    hit = ClanBattlePage.hit
    word = ClanBattlePage.word

    def __init__(self, calibration, plan):
        super().__init__()
        self.calibration, self.plan = calibration, plan

    def read(self, context, image, page, *, labels_only=False):
        pages = dict(self.calibration['daily']['pages'])
        pages.update(self.calibration['daily'].get('execution', {}).get('pages', {}))
        if (context.tasker.stopping or image is None or image.shape != (720, 1280, 3)
                or page not in pages):
            raise RuntimeError('clan_battle_daily_page_unknown')
        if self.hit(context, image, 'LabyDisconnect6002Observed'):
            raise RuntimeError('clan_battle_disconnected_6002')
        spec = pages[page]
        if (any(self.hit(context, image, f'CBDaily_{page}_rejects_{i}') for i in range(len(spec['rejects'])))
                or not all(self.hit(context, image, f'CBDaily_{page}_labels_{i}') for i in range(len(spec['labels'])))):
            raise RuntimeError('clan_battle_daily_page_unknown:' + page)
        values = {'page': page}
        fields = () if labels_only else spec['fields']
        boss_text = None
        for field in fields:
            text = self.word(context, image, f'CBDaily_{page}_{field}')
            if field == 'boss':
                boss_text = text
                values[field] = self.plan['boss']
            elif field == 'phase':
                values['phase_label'] = text
                values[field] = self.calibration['phase_labels'].get(text)
            elif field == 'health':
                current, maximum = [int(re.sub('[,，\\s]', '', value)) for value in text.split('/')]
                if not 0 <= current <= maximum:
                    raise RuntimeError('clan_battle_daily_health_invalid')
                values.update(health=current, health_max=maximum, alive=current > 0)
            elif field == 'cp':
                values['cp'] = 900 if re.fullmatch(FULL_CP_PATTERN, text) else int(text.split('/')[0].strip())
                if not 0 <= values['cp'] <= 900:
                    raise RuntimeError('clan_battle_daily_cp_invalid')
                values['cp_semantics'] = 'daily_earned'
            elif field == 'period':
                values[field] = _current_period(text)
            elif field == 'game_day':
                values[field] = text.replace('/', '-')
            elif field == 'remaining_seconds':
                minute, second = map(int, text.replace('：', ':').split(':'))
                values[field] = minute * 60 + second
            elif field == 'damage':
                if ('damage_line' in spec or self.calibration.get('evidence_kind') == 'device_saved_frame'
                        or self.calibration.get('calibration_id') == 'cn-bilibili-202610141448-clan-simulation'
                        and self.calibration.get('frame_size') == [1280, 720]):
                    values[field] = result_damage_line(text, self.word(context, image, 'CBDaily_result_damage_verify'))
                else:
                    values[field] = int(re.sub('[,，]', '', text))
            else:
                values[field] = int(text)
        if boss_text is not None and 'nameplate_levels' in self.calibration.get('pages', {}).get('boss_detail', {}):
            phase = values.get('phase')
            if phase is None or not re.fullmatch(_boss_name_pattern(self.calibration, [self.plan['boss']], phase=phase), boss_text):
                raise RuntimeError('clan_battle_daily_target_unknown')
        if page == 'carry_empty':
            values['carry_stock_indices'] = []
        if page == 'target':
            values['formal'] = True
        values['evidence'] = dict(calibration_id=self.calibration['calibration_id'], page=page,
            frame_pixel_sha256=hashlib.sha256(image.tobytes()).hexdigest())
        return values

    def analyze(self, context, argv):
        try:
            params = json.loads(argv.custom_recognition_param)
            if type(params) is not dict or set(params) != {'page'}:
                return None
            context = same_frame_context(context, argv.image)
            values = self.read(context, argv.image, params['page'])
            return self.AnalyzeResult(box=[0, 0, 1, 1], detail=values)
        except Exception:
            return None


class NativeClanDailyPages(NativeGuideTrial):
    """Owner-scoped read-only probe; this class has no battle-start operation."""
    def __init__(self, tasker, directory, *, calibration, plan):
        super().__init__(tasker, Path(directory))
        self.calibration = deepcopy(calibration)
        self.plan = deepcopy(plan)
        nodes = daily_pipeline(self.calibration, self.plan)
        self.page = ClanBattleDailyPage(self.calibration, self.plan)
        self.directory.mkdir(parents=True, exist_ok=True)
        if not tasker.resource.override_pipeline(nodes):
            raise RuntimeError('clan_battle_daily_pipeline_rejected')
        owner = getattr(tasker, '_resource_holder', None) or tasker.resource
        if not owner.register_custom_recognition('ClanBattleDailyPage', self.page):
            raise RuntimeError('clan_battle_callback_registration_failed:ClanBattleDailyPage')

    def read_page(self, page):
        if page not in PAGE_FIELDS:
            raise ValueError('clan_battle_daily_page_invalid')
        receipt = self.run('CBDailyPage_' + page)
        return _detail(receipt), receipt


def require_daily_execution(calibration, plan, *, require_scope=True, capability='formal', require_carry_pages=False):
    """A read-only daily profile never qualifies as an executable formal profile."""
    if capability not in ('simulation', 'formal', 'carryover'):
        raise ValueError('clan_battle_pipeline_capability_invalid')
    if require_scope and capability in ('formal', 'carryover'):
        from .clan_battle_native import _require_target_scope
        _require_target_scope(calibration, plan, capability)
    daily = validate_daily_calibration(calibration)
    execution = daily.get('execution')
    personal = type(execution) is dict and 'used_units' in execution
    keys = {'pages', 'actions', 'carry_slots', 'boss_slots', 'formation_owner', 'formation_mode', 'speed_one', 'samples', 'opening'} | ({'used_units'} if personal else set())
    if type(execution) is dict:
        keys |= set(execution) & {'start_confirmation', 'carry_start_confirmation', 'result_reward', 'carry_selection'}
    page_fields = {page: fields for page, fields in EXECUTION_FIELDS.items() if personal or page != 'used_units'}
    action_pages = {name: page for name, page in ACTION_PAGES.items() if personal or name not in ('open_used', 'close_used')}
    selection = type(execution) is dict and 'carry_selection' in execution
    if selection:
        page_fields['carry_selection'] = set()
        action_pages.update(select_carry='carry_selection', enable_carry_team='carry_selection', close_carry_selection='carry_selection')
    if daily['version'] != 2 or type(execution) is not dict or set(execution) != keys:
        raise ValueError('clan_battle_daily_execution_calibration_missing')
    if type(execution['pages']) is not dict:
        raise ValueError('clan_battle_daily_execution_pages_missing')
    support = 'support' in execution['pages']
    carry = 'carry' in execution['pages']
    if (not carry and (capability == 'carryover' or require_carry_pages or selection)
            or (not support or not carry) and calibration.get('support_modes') != ['none']):
        raise ValueError('clan_battle_daily_execution_scope_missing')
    if not support:
        page_fields.pop('support')
        for name in ('open_support', 'support_back', 'support_top', 'support_next'):
            action_pages.pop(name)
        if execution['formation_owner'] is not None:
            raise ValueError('clan_battle_daily_execution_scope_invalid')
    if not carry:
        page_fields.pop('carry')
        for name in ('close_carry', 'open_carry_for_target', 'select_carry'):
            action_pages.pop(name)
        if execution['carry_slots'] != []:
            raise ValueError('clan_battle_daily_carry_slots_missing')
    if type(execution['pages']) is not dict or set(execution['pages']) != set(page_fields):
        raise ValueError('clan_battle_daily_execution_pages_missing')
    for page, fields in page_fields.items():
        spec = execution['pages'][page]
        allowed_options = {'damage_line'} if page == 'result' else set()
        if (type(spec) is not dict or set(spec) - allowed_options != {'labels', 'rejects', 'fields'}
                or type(spec['labels']) is not list or len(spec['labels']) < 2
                or type(spec['rejects']) is not list or not spec['rejects']
                or type(spec['fields']) is not dict or set(spec['fields']) != fields):
            raise ValueError('clan_battle_daily_execution_page_invalid:' + page)
    samples = execution['samples']
    if type(samples) is not list or {row.get('page') for row in samples if type(row) is dict} != set(page_fields):
        raise ValueError('clan_battle_daily_execution_samples_missing')
    for sample in samples:
        if type(sample) is not dict or set(sample) != {'page', 'path', 'sha256'}:
            raise ValueError('clan_battle_daily_execution_sample_invalid')
        path = Path(sample['path'])
        if not path.is_absolute() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']:
            raise ValueError('clan_battle_daily_execution_sample_changed')
    nodes = daily_pipeline(calibration, plan)
    for name, count in (('start_confirmation', 3), ('carry_start_confirmation', 3), ('result_reward', 2)):
        if name not in execution:
            continue
        dialog = execution[name]
        if (type(dialog) is not dict or set(dialog) != {'labels', 'target', 'samples'}
                or type(dialog['labels']) is not list or len(dialog['labels']) != count
                or type(dialog['samples']) is not list or len(dialog['samples']) != 1):
            raise ValueError('clan_battle_daily_dialog_calibration_invalid:' + name)
        _roi(dialog['target'])
        for index, label in enumerate(dialog['labels']):
            nodes[f'CBDailyDialog_{name}_{index}'] = _node(label)
        sample = dialog['samples'][0]
        if (type(sample) is not dict or set(sample) != {'page', 'path', 'sha256'}
                or sample['page'] != name or type(sample['path']) is not str):
            raise ValueError('clan_battle_daily_dialog_sample_invalid:' + name)
        path = Path(sample['path'])
        if (not path.is_absolute() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != sample['sha256']):
            raise ValueError('clan_battle_daily_dialog_sample_changed:' + name)
        start_dialog = name in ('start_confirmation', 'carry_start_confirmation')
        modes = [('CBDailyStartConfirmation', 'formal_start_confirmation', True)] if start_dialog else [
            ('CBDailyResultRewardObserved', 'result_reward_observed', False), ('CBDailyResultRewardConfirm', 'result_reward_confirm', True)]
        for node, mode, click in modes:
            nodes[node] = dict(recognition='Custom', custom_recognition='ClanBattleCharacters',
                custom_recognition_param=dict(mode=mode), action='Click' if click else 'DoNothing', target=True,
                next=['CBDailyPauseOpening'] if start_dialog else [],
                timeout=15000 if start_dialog else 1000, rate_limit=1, pre_delay=0, post_delay=0,
                on_error=['LabyDisconnect6002Observed'])
            if click:
                nodes[node]['max_hit'] = 1
    actions = execution['actions']
    if type(actions) is not dict or set(actions) != set(action_pages):
        raise ValueError('clan_battle_daily_execution_actions_missing')
    for name, page in action_pages.items():
        spec = actions[name]
        scrolling = name in ('characters_top', 'characters_next', 'support_top', 'support_next')
        keys = {'page', 'label', 'target'} | ({'begin', 'end'} if scrolling else set())
        if (type(spec) is not dict or set(spec) != keys or spec['page'] != page):
            raise ValueError('clan_battle_daily_action_invalid:' + name)
        nodes['CBDailyActionLabel_' + name] = _node(spec['label'])
        _roi(spec['target'])
        if name == 'start':
            continue
        nodes['CBAction_' + name] = dict(recognition='Custom', custom_recognition='ClanBattleDailyPage',
            custom_recognition_param=dict(page=page, action=name), action='Click', target=True,
            next=[], max_hit=1, timeout=1000, pre_delay=0, post_delay=0)
        if scrolling:
            begin, end = _roi(spec['begin']), _roi(spec['end'])
            if name in ('characters_top', 'characters_next'):
                from .clan_battle_native import _scroll_action
                shared = _scroll_action(name, calibration['actions'].get(name))
                if begin != shared['begin'] or end != shared['end']:
                    raise ValueError('clan_battle_daily_formation_scroll_mismatch:' + name)
                # Reuse the reviewed gesture; keep the formal page/identity callback.
                nodes['CBAction_' + name].update(deepcopy(shared))
            else:
                nodes['CBAction_' + name].update(action='Swipe', begin=begin, end=end, duration=300)
    for page in {*PAGE_FIELDS, *page_fields}:
        nodes['CBPage_' + page] = dict(nodes['CBDailyPage_' + page])
    if type(execution['boss_slots']) is not dict or set(execution['boss_slots']) != set('12345'):
        raise ValueError('clan_battle_daily_boss_slots_missing')
    for boss, spec in execution['boss_slots'].items():
        if type(spec) is not dict or set(spec) != {'round', 'defeated'}:
            raise ValueError('clan_battle_daily_boss_slot_invalid')
        nodes['CBDailyBossRound' + boss] = _node({'roi': spec['round']}, expected=r'^第\s*[1-9][0-9]*\s*轮$')
        nodes['CBDailyBossDefeated' + boss] = _node(spec['defeated'])
    def members(layout, prefix, size):
        if (type(layout) is not dict or set(layout) != {'slots', 'empty', 'borrowed'}
                or any(type(layout[key]) is not list or len(layout[key]) != size for key in layout)):
            raise ValueError('clan_battle_daily_unit_layout_invalid:' + prefix)
        for index in range(size):
            _roi(layout['slots'][index])
            nodes[f'{prefix}Empty{index}'] = _node(layout['empty'][index])
            nodes[f'{prefix}Borrowed{index}'] = _node(layout['borrowed'][index])
    if personal:
        members(execution['used_units'], 'CBDailyUsed', 15)
    stocks = execution['carry_slots']
    if type(stocks) is not list or (carry and not 1 <= len(stocks) <= 3) or (not carry and stocks):
        raise ValueError('clan_battle_daily_carry_slots_missing')
    def stock_nodes(rows, prefix):
        if type(rows) is not list or not 1 <= len(rows) <= 3:
            raise ValueError('clan_battle_daily_carry_slots_missing')
        for index, spec in enumerate(rows):
            compact = type(spec) is dict and set(spec) == {'stock_index', 'time', 'members', 'target'}
            name = prefix + str(index)
            if compact:
                layout = spec['members']
                if (calibration.get('support_modes') != ['none'] or type(layout) is not dict
                        or set(layout) != {'slots'} or type(layout['slots']) is not list or len(layout['slots']) != 5):
                    raise ValueError('clan_battle_daily_carry_slot_invalid')
                for roi in layout['slots']: _roi(roi)
            else:
                if type(spec) is not dict or set(spec) != {'empty', 'occupied', 'stock_index', 'time', 'members', 'support_owner', 'target'}:
                    raise ValueError('clan_battle_daily_carry_slot_invalid')
                nodes[name + 'Empty'] = _node(spec['empty'])
                nodes[name + 'Occupied'] = _node(spec['occupied'])
                nodes[name + 'Owner'] = _node({'roi': spec['support_owner']}, expected=r'^[1-9][0-9]{0,19}$')
                members(spec['members'], name, 5)
            nodes[name + 'Index'] = _node({'roi': spec['stock_index']}, expected=r'^[0-9]{1,6}$')
            nodes[name + 'Time'] = _node({'roi': spec['time']}, expected=r'^(?:[1-9]|[1-8][0-9]|90|0[:：][0-5][0-9]|1[:：](?:[0-2][0-9]|30))$')
            _roi(spec['target'])
    if stocks:
        stock_nodes(stocks, 'CBDailyCarry')
    if selection:
        spec = execution['carry_selection']
        if type(spec) is not dict or set(spec) != {'slots', 'reuse_on', 'reuse_off'}:
            raise ValueError('clan_battle_daily_carry_selection_invalid')
        stock_nodes(spec['slots'], 'CBDailyCarrySelection')
        nodes['CBDailyCarryReuseOn'] = _node(spec['reuse_on'])
        nodes['CBDailyCarryReuseOff'] = _node(spec['reuse_off'])
    if support:
        nodes['CBDailyFormationOwner'] = _node({'roi': execution['formation_owner']}, expected=r'^[1-9][0-9]{0,19}$')
    mode = execution['formation_mode']
    if type(mode) is not dict or set(mode) != {'regular', 'carryover', 'stock_index', 'time'}:
        raise ValueError('clan_battle_daily_formation_mode_missing')
    nodes['CBDailyFormationRegular'] = _node(mode['regular'])
    if mode['carryover'] is None:
        if (support or calibration.get('support_modes') != ['none']
                or (carry or capability == 'carryover') and not selection
                or mode['stock_index'] is not None or mode['time'] is not None):
            raise ValueError('clan_battle_daily_execution_scope_invalid')
    else:
        nodes['CBDailyFormationCarryover'] = _node(mode['carryover'])
    if carry and mode['carryover'] is not None:
        nodes['CBDailyFormationStock'] = _node({'roi': mode['stock_index']}, expected=r'^[0-9]{1,6}$')
        nodes['CBDailyFormationTime'] = _node({'roi': mode['time']}, expected=r'^(?:[1-9]|[1-8][0-9]|90)$')
    elif mode['stock_index'] is not None or mode['time'] is not None:
        raise ValueError('clan_battle_daily_execution_scope_invalid')
    nodes['CBDailyOneSpeed'] = _node(execution['speed_one'])
    nodes.update(formal_opening_pipeline(calibration, plan, require_scope=require_scope, carryover=capability == 'carryover'))
    return nodes


class ClanBattleDailyExecutionPage(ClanBattleDailyPage):
    def __init__(self, calibration, plan, port):
        super().__init__(calibration, plan)
        self.port = port
        self.current = None
        self.expected = None
        self.bound = None

    def members(self, context, image, layout, prefix, *, complete=False):
        units, support = [], None
        for index, roi in enumerate(layout['slots']):
            empty = self.hit(context, image, f'{prefix}Empty{index}')
            hits = [uid for uid in self.port.all_units
                if (match := self.port.catalogue.match(image, uid, roi)) and box_inside_roi(match['box'], roi)]
            borrowed = self.hit(context, image, f'{prefix}Borrowed{index}')
            if empty:
                if hits or borrowed or complete:
                    raise RuntimeError('clan_battle_daily_units_unknown')
                continue
            if len(hits) != 1:
                raise RuntimeError('clan_battle_daily_units_unknown')
            if borrowed:
                if complete and support is not None:
                    raise RuntimeError('clan_battle_daily_support_ambiguous')
                support = hits[0]
                if complete:
                    units.append(hits[0])
            else:
                units.append(hits[0])
        if len(units) != len(set(units)) or complete and len(units) != 5:
            raise RuntimeError('clan_battle_daily_units_unknown')
        return units, support

    def read(self, context, image, page):
        if not self.bound and page in ('formation', 'boss_detail'):
            return dict(super().read(context, image, page, labels_only=True), navigation_only=True)
        value = super().read(context, image, page)
        execution = self.calibration['daily']['execution']
        if page == 'lobby':
            if value['period'] != self.plan['period'] or value['phase'] is None:
                raise RuntimeError('clan_battle_daily_period_or_phase_unknown')
            bosses = []
            for boss in range(1, 6):
                dead = self.hit(context, image, 'CBDailyBossDefeated' + str(boss))
                round_ = None if dead else int(re.sub(r'\D', '', self.word(context, image, 'CBDailyBossRound' + str(boss))))
                bosses.append(dict(boss=boss, round=round_, phase=value['phase'], available=not dead))
            value['bosses'] = bosses
        elif page == 'used_units':
            value['used_unit'], _ = self.members(context, image, execution['used_units'], 'CBDailyUsed')
        elif page in ('carry', 'carry_selection'):
            value['stocks'] = []
            selection = page == 'carry_selection'
            rows = execution['carry_selection']['slots'] if selection else execution['carry_slots']
            for index, spec in enumerate(rows):
                prefix = ('CBDailyCarrySelection' if selection else 'CBDailyCarry') + str(index)
                if set(spec['members']) == {'slots'}:
                    units, support = [], None
                    for roi in spec['members']['slots']:
                        hits = [uid for uid in self.port.all_units if self.port.catalogue.match_carry(image, uid, roi)]
                        if len(hits) != 1:
                            raise RuntimeError('clan_battle_daily_units_unknown')
                        units.append(hits[0])
                    if len(set(units)) != 5:
                        raise RuntimeError('clan_battle_daily_units_unknown')
                else:
                    empty, occupied = (self.hit(context, image, prefix + key) for key in ('Empty', 'Occupied'))
                    if empty == occupied:
                        raise RuntimeError('clan_battle_daily_carry_slot_unknown')
                    if empty:
                        continue
                    units, support = self.members(context, image, spec['members'], prefix, complete=True)
                text = self.word(context, image, prefix + 'Time').replace('：', ':')
                seconds = int(text) if ':' not in text else sum(int(part)*factor for part, factor in zip(text.split(':'), (60,1)))
                if not 1 <= seconds <= 90:
                    raise RuntimeError('clan_battle_daily_carry_slot_unknown')
                value['stocks'].append(dict(stock_index=int(self.word(context, image, prefix + 'Index')),
                    time=seconds, using_unit=units, support_unit=support,
                    support_owner_viewer_id=self.word(context, image, prefix + 'Owner') if support else None,
                    target=spec['target']))
            if selection:
                on, off = (self.hit(context, image, 'CBDailyCarryReuse'+name) for name in ('On','Off'))
                if on == off:
                    raise RuntimeError('clan_battle_daily_carry_reuse_unknown')
                value['reuse_team'] = on
            if not value['stocks'] or len({row['stock_index'] for row in value['stocks']}) != len(value['stocks']):
                raise RuntimeError('clan_battle_daily_carry_inventory_unknown')
        elif page in ('target', 'formation', 'support', 'battle', 'result'):
            finished = (page == 'result' and self.current == 'battle' and self.port.pending is not None
                and self.port.started and self.port.battle_start == 'sent')
            if not self.bound or not (page in (self.current, self.expected) or finished):
                raise RuntimeError('clan_battle_daily_transition_unverified')
            if page == 'target' and (value['boss'] != self.bound['boss'] or value['phase'] != self.bound['phase'] or not value['alive']):
                raise RuntimeError('clan_battle_daily_target_changed')
            value.update(self.bound)
            if page == 'formation':
                regular = self.hit(context, image, 'CBDailyFormationRegular')
                if execution['formation_mode']['carryover'] is None:
                    if self.port.action['kind'] == 'carryover' and 'carry_selection' in execution:
                        self.port.require_selected_carry(self.bound)
                        carryover, regular = True, False
                    else:
                        self.port.require_regular_empty_inventory(self.bound)
                        carryover = False
                else:
                    carryover = self.hit(context, image, 'CBDailyFormationCarryover')
                if regular == carryover or ('regular' if regular else 'carryover') != self.port.action['kind']:
                    raise RuntimeError('clan_battle_daily_consumption_mode_unknown')
                value['battle_kind'] = self.port.action['kind']
                if carryover and execution['formation_mode']['stock_index'] is not None:
                    stock = int(self.word(context, image, 'CBDailyFormationStock'))
                    seconds = int(self.word(context, image, 'CBDailyFormationTime'))
                    if stock != self.port.action['stock_index'] or seconds != self.port.action['start_seconds']:
                        raise RuntimeError('clan_battle_daily_selected_carry_changed')
                value['support_owner_viewer_id'] = None
            if page == 'battle':
                speeds = [speed for speed, node in ((1, 'CBDailyOneSpeed'), (2, 'CBDailyTwoSpeed'))
                    if self.hit(context, image, node)]
                value['battle_speed'] = speeds[0] if len(speeds) == 1 else None
        self.current, self.expected = page, None
        return value

    def require_team(self, reading):
        plan, bound = self.plan, self.bound
        if (not bound or any(reading.get(key) != value for key, value in bound.items())
                or reading.get('battle_kind') != self.port.action['kind']
                or reading.get('member_count') != 5 or set(reading.get('unit_ids', [])) != set(plan['unit_ids'])
                or dict(zip(reading.get('unit_ids', []), reading.get('stars', []))) != dict(zip(plan['unit_ids'], plan['stars']))
                or reading.get('support_unit_id') != self.port.support_unit_id
                or self.port.support_unit_id is not None and (not reading.get('support_owner_viewer_id')
                    or reading['support_owner_viewer_id'] == plan['account_id'])):
            raise RuntimeError('clan_battle_daily_team_or_mode_unknown')

    def action(self, context, image, name):
        spec = self.calibration['daily']['execution']['actions'][name]
        value = self.read(context, image, spec['page'])
        if value.get('navigation_only') and name not in ('cancel_formation', 'back_to_lobby'):
            raise RuntimeError('clan_battle_daily_transition_unverified')
        if not self.hit(context, image, 'CBDailyActionLabel_' + name):
            raise RuntimeError('clan_battle_daily_action_unknown:' + name)
        if name == 'open_boss':
            from .clan_battle_batch import _hash
            action = self.port.action
            actual = next(row for row in value['bosses'] if row['boss'] == self.plan['boss'])
            if (not actual['available'] or any(actual[key] != action[key] for key in ('boss', 'phase', 'round'))
                    or _hash(dict(period=value['period'], boss=actual['boss'], phase=actual['phase'], round=actual['round'])) != action['instance_id']):
                raise RuntimeError('clan_battle_daily_target_changed_before_selection')
            x, y, w, h = _roi(self.calibration['boss_targets'][str(self.plan['boss'])]['target'])
            return [x + w // 2, y + h // 2, 1, 1]
        if name == 'enable_carry_team':
            if value.get('reuse_team') is not False:
                raise RuntimeError('clan_battle_daily_carry_reuse_changed')
        if name == 'select_carry':
            rows = [row for row in value['stocks'] if row['stock_index'] == self.port.action['stock_index']]
            if len(rows) != 1 or rows[0]['time'] != self.port.action['start_seconds']:
                raise RuntimeError('clan_battle_daily_carry_changed')
            if 'carry_selection' in self.calibration['daily']['execution']:
                source = next((row for row in self.port.stocks if row['stock_index'] == rows[0]['stock_index']), None)
                fields = ('stock_index','time','using_unit','support_unit','support_owner_viewer_id')
                if (value.get('reuse_team') is not True or source is None
                        or any(rows[0][key] != source[key] for key in fields)
                        or set(rows[0]['using_unit']) != set(self.plan['unit_ids'])
                        or not self.port.account_binding_valid(self.plan)):
                    raise RuntimeError('clan_battle_daily_carry_changed')
                from .game_day import current_game_day
                self.port._selected_carry = (deepcopy(self.port.action), dict(deepcopy(rows[0]), evidence=deepcopy(value['evidence'])),
                    self.port._account_binding, self.port._account_source(), current_game_day(self.port.now()))
            return rows[0]['target']
        if name == 'start':
            from .clan_battle_batch import _timestamp
            from .game_day import current_game_day
            pending = self.port.pending
            now = self.port.now()
            observed_at = pending.get('observed_at') if pending is not None else None
            if pending is not None and getattr(self.port, '_regular_prepared', None) is not None:
                observed_at = self.port._regular_ready_at(pending['before'], pending['ready'])
            if (pending is None or self.port.action != pending['action']
                    or current_game_day(now) != pending['game_day']
                    or not 0 <= (now - _timestamp(observed_at)).total_seconds() <= 60):
                raise RuntimeError('clan_battle_daily_final_start_binding_changed')
            if self.calibration['daily']['execution']['formation_mode']['carryover'] is None:
                if self.port.action['kind'] == 'carryover' and 'carry_selection' in self.calibration['daily']['execution']:
                    self.port.require_selected_carry(self.bound)
                else:
                    self.port.require_regular_empty_inventory(self.bound)
            team = self.port.characters.formation(context, image)
            self.require_team(team)
            ready = pending['ready']
            if (team['unit_ids'] != ready['unit_ids'] or team['stars'] != ready['stars']
                    or team['support_unit_id'] != ready['support_unit']
                    or team['support_owner_viewer_id'] != ready['support_owner_viewer_id']):
                raise RuntimeError('clan_battle_daily_final_team_changed')
            self.expected = 'battle'
        return spec['target']

    def analyze(self, context, argv):
        try:
            params = json.loads(argv.custom_recognition_param)
            if set(params) - {'page', 'action'}:
                return None
            context = same_frame_context(context, argv.image)
            value = self.read(context, argv.image, params['page'])
            target = self.action(context, argv.image, params['action']) if 'action' in params else [0, 0, 1, 1]
            if context.tasker.stopping:
                return None
            return self.AnalyzeResult(box=target, detail=value)
        except Exception:
            return None


class _DailyTeamReader(ClanBattleCharacters):
    def formation(self, context, image):
        reading = super().formation(context, image)
        reading['support_owner_viewer_id'] = (self.page.word(context, image, 'CBDailyFormationOwner')
            if reading['support_unit_id'] is not None else None)
        return reading


    def opening_control(self, context, argv, params):
        port = self.page.port
        pending = port.opening_binding()
        spec = self.calibration['daily']['execution']['opening']
        if (self.page.hit(context, argv.image, 'LabyDisconnect6002Observed')
                or not all(self.page.hit(context, argv.image, f'CBFormalOpening_paused_guards_{i}') for i in range(3))):
            return None
        carryover = pending['action'].get('kind') == 'carryover'
        actual, clock_evidence, frozen = None, None, getattr(port, 'opening_clock', None)
        if not carryover:
            actual, clock_evidence = self.read_opening_clock(context, argv.image, 'CBFormalOpeningClock', spec['clock'])
            if not 1 <= actual <= pending['action']['start_seconds'] or frozen is not None and frozen != (pending['intent_id'], actual):
                return None
        elif (not getattr(self, 'opening_started', False) or self.opening_action_kind != 'carryover'
                or self.opening_token != pending['intent_id']):
            return None
        state = self.read_paused_controls(context, argv.image, prefix='CBFormalOpening',
            remaining_seconds=actual, speeds=(1, 2))
        if state is None:
            return None
        state.update(formal=True, intent_id=pending['intent_id'], clock_evidence=clock_evidence)
        mode = params['mode']
        state = self.bind_paused_opening(state, mode, image=argv.image)
        if state is None:
            return None
        if not carryover and frozen is None:
            port.opening_clock = (pending['intent_id'], actual)
        if mode != 'opening_read' and not all(key in params for key in ('unit_ids', 'sets', 'auto', 'battle_speed')):
            return None
        if ('battle_speed' in params and type(params['battle_speed']) is not int
                or 'remaining_seconds' in params and type(params['remaining_seconds']) is not int):
            return None
        if any(key in params and state[key] != params[key] for key in ('unit_ids', 'sets', 'auto', 'battle_speed', 'remaining_seconds')):
            return None
        if not carryover and mode in ('opening_set', 'opening_auto', 'opening_speed'):
            from .clan_battle import require_first_ub
            slot = params.get('slot')
            require_first_ub(state, [state['unit_ids'][slot]] if mode == 'opening_set' and type(slot) is int and 0 <= slot < 5 else state['unit_ids'])
        target = [0, 0, 1, 1]
        if mode in ('opening_set', 'opening_auto'):
            slot = params.get('slot')
            if mode == 'opening_set' and (type(slot) is not int or not 0 <= slot < 5):
                return None
            previous = state['auto'] if mode == 'opening_auto' else state['sets'][slot]
            if (type(params.get('previous')) is not bool or type(params.get('desired')) is not bool
                    or params['previous'] is params['desired'] or previous is not params['previous']):
                return None
            target = spec['targets']['auto'] if mode == 'opening_auto' else spec['targets']['sets'][slot]
        elif mode == 'opening_speed':
            if state['battle_speed'] != 1:
                return None
            target = spec['targets']['speed']
        elif mode == 'opening_resume':
            if state['battle_speed'] != 2:
                return None
            target = spec['targets']['resume']
        elif mode != 'opening_read':
            return None
        if context.tasker.stopping:
            return None
        self.page.current, self.page.expected = 'battle', None
        return self.AnalyzeResult(box=target, detail=state)

    def analyze(self, context, argv):
        try:
            params = json.loads(argv.custom_recognition_param)
            context = same_frame_context(context, argv.image)
            if params in ({'mode': 'formal_start_confirmation'}, {'mode': 'result_reward_observed'}, {'mode': 'result_reward_confirm'}):
                if context.tasker.stopping or argv.image is None or argv.image.shape != (720, 1280, 3):
                    return None
                mode = params['mode']
                start_confirmation = mode == 'formal_start_confirmation'
                port = self.page.port
                pending = port.opening_binding(pausing=start_confirmation)
                name, count = (('carry_start_confirmation' if pending['action']['kind'] == 'carryover' else 'start_confirmation'), 3) if start_confirmation else ('result_reward', 2)
                spec = self.calibration['daily']['execution'].get(name)
                if spec is None or not all(self.page.hit(context, argv.image, f'CBDailyDialog_{name}_{i}') for i in range(count)):
                    return None
                if (pending['action']['kind'] not in ('regular', 'carryover') or self.page.hit(context, argv.image, 'LabyDisconnect6002Observed')
                        or not start_confirmation and (port.battle_start != 'sent' or self.page.current != 'battle')):
                    return None
                authorization = pending['ready']['evidence'].get('consumption_authorization')
                if (type(authorization) is not dict or authorization.get('reserved_intent_id') != pending['intent_id']
                        or type(authorization.get('preview')) is not dict or authorization['preview'].get('kind') != pending['action']['kind']):
                    return None
                fingerprint = port.consumption_binding()
                if (authorization.get('authorization_generation') != fingerprint.get('authorization_generation')
                        or any(authorization['preview'].get(key) != fingerprint.get(key)
                            for key in ('source_sha256', 'calibration_sha256', 'owner_core_sha256'))):
                    return None
                attempted = '_formal_start_confirmation_token' if start_confirmation else '_result_reward_confirmation_token'
                if mode != 'result_reward_observed':
                    if getattr(port, attempted, None) == pending['intent_id']:
                        return None
                    setattr(port, attempted, pending['intent_id'])
                return self.AnalyzeResult(box=spec['target'] if mode != 'result_reward_observed' else [0, 0, 1, 1],
                    detail=dict(page=name, intent_id=pending['intent_id']))
            if params.get('mode') == 'opening_match':
                if context.tasker.stopping or argv.image is None or argv.image.shape != (720, 1280, 3):
                    return None
                pending = self.page.port.opening_binding(pausing=True)
                if (params.get('opening_start_token') != pending['intent_id']
                        or params.get('start_seconds') != pending['action']['start_seconds']
                        or type(params.get('battle_speed')) is not int or params['battle_speed'] != 2):
                    return None
                return super().analyze(context, argv)
            if params in ({'mode': 'formal_pause'}, {'mode': 'formal_pause_retry'}):
                if context.tasker.stopping or argv.image is None or argv.image.shape != (720, 1280, 3):
                    return None
                pending = self.page.port.opening_binding(pausing=True)
                retry = params['mode'] == 'formal_pause_retry'
                if retry and (not getattr(self, 'opening_started', False)
                        or self.page.hit(context, argv.image, 'LabyDisconnect6002Observed')
                        or all(self.page.hit(context, argv.image, f'CBFormalOpening_paused_guards_{i}') for i in range(3))):
                    return None
                anchor = None if retry else self.record_opening_frame(argv.image, 'battle')
                if pending['action']['kind'] != 'carryover':
                    clock = self.page.word(context, argv.image, 'CBDailyOpeningStartClock')
                    if re.fullmatch(PATTERNS['remaining_seconds'], clock) is None:
                        return None
                    minute, second = map(int, clock.replace('：', ':').split(':'))
                    if not 0 < minute * 60 + second < pending['action']['start_seconds']:
                        return None
                if context.tasker.stopping:
                    return None
                detail = {'intent_id': pending['intent_id']}
                if not retry:
                    detail['opening_anchor'] = anchor
                return self.AnalyzeResult(box=[0, 0, 1, 1], detail=detail)
            return super().analyze(context, argv)
        except Exception:
            return None


class _DailyCharacters(CustomRecognition):
    """One registered callback stays owned while the reviewed slot changes."""
    def __init__(self, port):
        super().__init__()
        self.port = port

    def analyze(self, context, argv):
        return self.port.characters.analyze(context, argv)


class _DailyInheritance(CustomRecognition):
    def __init__(self, port):
        super().__init__()
        self.port = port

    def analyze(self, context, argv):
        return self.port.inheritance_page.analyze(context, argv)


class NativeClanDailyAdapter(NativeClanBattle):
    def __init__(self, tasker, directory, *, request, calibrations, consumption_consent=None, owner_core_sha256=None):
        from .clan_battle_characters import ClanBattleCharacters
        super().__init__(tasker, directory)
        self.request, self.calibrations = deepcopy(request), deepcopy(calibrations)
        self.consumption_consent, self.owner_core_sha256 = consumption_consent, owner_core_sha256
        self._consumption_files, self._consumption_owner, self._consumption_account = None, None, None
        self.invalidate_consumption_authorizations()
        self.action, self.pending, self.last_bosses, self.stocks, self.boss_history = None, None, {}, [], {}
        self.all_units = sorted({uid for job in request['slot_plans'].values() for uid in job['unit_ids']})
        self.page, self.characters = None, None
        self.history_binding, self.regular_history = None, None
        self.configure_slot(request['slot_plans']['1'])
        catalogue_plan = dict(self.plan, unit_ids=self.all_units)
        self.catalogue = ClanBattleCharacters(catalogue_plan, self.calibration, self.page)
        self.callback = _DailyCharacters(self)
        self.inheritance_callback = _DailyInheritance(self)
        self.inheritance_recovered = False
        owner = getattr(tasker, '_resource_holder', None) or tasker.resource
        if not owner.register_custom_recognition('ClanBattleDailyPage', self.page):
            raise RuntimeError('clan_battle_callback_registration_failed:ClanBattleDailyPage')
        if not owner.register_custom_recognition('ClanBattleCharacters', self.callback):
            raise RuntimeError('clan_battle_callback_registration_failed:ClanBattleCharacters')
        if not owner.register_custom_recognition('ClanBattleInheritance', self.inheritance_callback):
            raise RuntimeError('clan_battle_callback_registration_failed:ClanBattleInheritance')

    def invalidate_consumption_authorizations(self):
        from uuid import uuid4
        self._consumption_generation = uuid4().hex
        self._consumption_owner, self._consumption_account = None, None

    def consumption_binding(self):
        from .clan_battle_batch import consumption_file_binding
        if (not callable(self.consumption_consent) or type(self.owner_core_sha256) is not str
                or re.fullmatch('[0-9a-f]{64}', self.owner_core_sha256) is None):
            raise RuntimeError('trusted_clan_consumption_consent_required')
        self.checkpoint()
        if not self.account_binding_valid(self.plan):
            raise RuntimeError('clan_battle_account_binding_changed')
        owner = self.tasker.observation_source()
        files = consumption_file_binding(self.request)
        if self._consumption_files is not None and files != self._consumption_files:
            raise RuntimeError('daily_consumption_source_or_calibration_changed')
        self._consumption_files = files
        if self._consumption_owner != owner or self._consumption_account is not self._account_binding:
            self.invalidate_consumption_authorizations()
            self._consumption_owner, self._consumption_account = owner, self._account_binding
        return dict(files, owner_core_sha256=self.owner_core_sha256, authorization_generation=self._consumption_generation)

    def request_consumption_consent(self, daily_plan_hash, preview):
        from .alpha_owner import validate_clan_preview
        if daily_plan_hash != self.request['confirmed_for']:
            raise RuntimeError('daily_consumption_plan_changed')
        validate_clan_preview(preview)
        before = self.consumption_binding()
        if any(preview.get(key) != before[key] for key in ('source_sha256', 'calibration_sha256', 'owner_core_sha256')):
            raise RuntimeError('daily_consumption_source_or_calibration_changed')
        approval = self.consumption_consent(daily_plan_hash, deepcopy(preview))
        if self.consumption_binding() != before:
            raise RuntimeError('daily_consumption_authorization_binding_changed')
        return approval

    @staticmethod
    def now():
        return datetime.now(timezone.utc)

    def configure_slot(self, plan, *, capability='formal'):
        from .clan_battle import load_snapshot, support_requirement
        from .clan_battle_calibration import load_calibration
        from .clan_battle_characters import ClanBattleCharacters
        from .clan_battle_native import calibration_pipeline, timing_capability
        from .labyrinth.game_session import game_session_pipeline
        calibration = (load_calibration(plan, capability='carryover') if capability == 'carryover'
            else self.calibrations[plan['confirmed_for']])
        calibration = deepcopy(calibration)
        # Formal opening is independent of the simulation guards and speed cycle.
        calibration.pop('auto_battle', None)
        nodes = calibration_pipeline(calibration, plan, capability=capability)
        nodes.update(require_daily_execution(calibration, plan, capability=capability))
        # Pause before the full HUD match; inherited 4x magnifies its delay.
        nodes['CBStart'].update(next=(['CBDailyStartConfirmation', 'CBDailyPauseOpening']
            if any(key in calibration['daily']['execution'] for key in ('start_confirmation', 'carry_start_confirmation')) else ['CBDailyPauseOpening']), timeout=30000, rate_limit=1)
        self.plan, self.calibration, self.snapshot = plan, calibration, load_snapshot(plan)
        self.support_unit_id = support_requirement(plan, self.snapshot)
        if self.page is None:
            self.page = ClanBattleDailyExecutionPage(calibration, plan, self)
        else:
            self.page.calibration, self.page.plan = calibration, plan
        self.characters = _DailyTeamReader(plan, calibration, self.page)
        from .clan_battle_inheritance import InheritancePage
        self.inheritance_page = InheritancePage(calibration['daily']['execution']['opening']['inheritance'], self.page)
        self.battle_speed, self.opening_paused, self.last_controls = 2, False, None
        self.formal_opening = True
        self.opening_clock = None
        self.timing_capability = timing_capability(calibration)
        self.directory.mkdir(parents=True, exist_ok=True)
        if (not self.tasker.resource.override_pipeline(game_session_pipeline(int(plan['account_id'])))
                or not self.tasker.resource.override_pipeline(nodes)):
            raise RuntimeError('clan_battle_daily_pipeline_rejected')

    def _start_reset_hits(self):
        execution = self.calibration['daily']['execution']
        nodes = ['CBDailyPauseOpening', 'CBDailyPauseRetry']
        if 'start_confirmation' in execution or 'carry_start_confirmation' in execution:
            nodes.append('CBDailyStartConfirmation')
        if 'result_reward' in execution:
            nodes.append('CBDailyResultRewardConfirm')
        return tuple(nodes)

    def _annotate_unsent_start(self, receipt):
        receipt.update(clan_battle_phase=self.action['kind'] + '_start', daily_intent_id=self.pending['intent_id'])

    def run(self, node, *, click=False):
        starting = click and node == 'CBStart'
        if starting and (self.started or self.pending is None):
            raise RuntimeError('clan_battle_daily_start_not_reserved')
        receipt = self._dispatch_clan_node(node, click=click, starting=starting)
        if click:
            action = node.removeprefix('CBAction_') if node.startswith('CBAction_') else (
                'start' if node == 'CBStart' else 'support_back' if node == 'CBAddSupport' else None)
            if action in DESTINATIONS:
                self.page.expected = ('carry_selection' if action == 'open_carry_for_target'
                    and 'carry_selection' in self.calibration['daily']['execution'] else DESTINATIONS[action])
        return receipt

    def opening_binding(self, *, pausing=False):
        from .clan_battle_batch import _timestamp
        from .game_day import current_game_day
        pending, now = self.pending, self.now()
        observed_at = pending.get('observed_at') if pending is not None else None
        if pending is not None and pausing and getattr(self, '_regular_prepared', None) is not None:
            observed_at = self._regular_ready_at(pending['before'], pending['ready'])
        opening = self.calibration.get('daily', {}).get('execution', {}).get('opening', {})
        if (type(opening.get('battle_speed')) is not int or opening['battle_speed'] != 2
                or self.action['kind'] != 'carryover' and self.action['start_seconds'] not in opening.get('start_seconds', [90])):
            raise RuntimeError('clan_battle_daily_start_time_uncalibrated')
        if (pending is None or self.action != pending['action'] or not self.started
                or self.battle_start not in ('unknown', 'sent')
                or pending['slot_confirmed_for'] != self.plan['confirmed_for']
                or current_game_day(now) != pending['game_day']
                or (now - _timestamp(observed_at)).total_seconds() < 0
                or pausing and self.action['kind'] != 'carryover' and (now - _timestamp(observed_at)).total_seconds() > 60
                or not self.page.bound or self.page.bound.get('formal') is not True
                or any(self.page.bound.get(key) != self.action[key] for key in ('boss', 'phase', 'round', 'instance_id'))
                or not (self.page.current == 'battle' or self.page.current == 'formation' and self.page.expected == 'battle')):
            raise RuntimeError('clan_battle_daily_opening_binding_changed')
        return pending

    def configure_opening(self, job, initial):
        from .clan_battle import execute_switches, require_first_ub, require_frozen_opening
        carryover = self.action['kind'] == 'carryover'
        settings = execute_switches(self, job, initial=initial)
        state = deepcopy(settings['observation'])
        if not self.opening_paused and state.get('opening_direct') is True:
            settings['battle_speed'] = 2
            return settings
        require_frozen_opening(state, settings['opening_evidence'], carryover=carryover)
        if any(state.get(key) != settings['opening_evidence'].get(key) for key in ('auto', 'sets', 'unit_ids', 'battle_speed')):
            raise RuntimeError('clan_battle_switch_state_changed')
        expected = {key: deepcopy(state[key]) for key in ('auto', 'sets', 'unit_ids', 'battle_speed')}
        if not carryover:
            expected['remaining_seconds'] = state['remaining_seconds']
        if state['battle_speed'] == 1:
            if not carryover:
                require_first_ub(state, job['unit_ids'])
            before = deepcopy(state)
            if not self.tasker.resource.override_pipeline({
                    'CBOpeningSpeed': dict(custom_recognition_param=dict(mode='opening_speed', **expected)),
                    'CBOpeningConfirmed': dict(custom_recognition_param=dict(mode='opening_read', **dict(expected, battle_speed=2)))}):
                raise RuntimeError('clan_battle_switch_pipeline_rejected')
            state = self.read_battle_settings(self.run('CBOpeningSpeed', click=True))
            require_frozen_opening(state, before, carryover=carryover)
            if any(state.get(key) != value for key, value in dict(expected, battle_speed=2).items()):
                raise RuntimeError('clan_battle_speed_readback_unknown')
            settings['actions'].append(dict(control='speed', previous=1, target=2))
        if type(state.get('battle_speed')) is not int or state['battle_speed'] != 2:
            raise RuntimeError('clan_battle_daily_speed_invalid')
        settings['opening_evidence'] = deepcopy(state)
        expected = {key: deepcopy(state[key]) for key in ('auto', 'sets', 'unit_ids', 'battle_speed')}
        if not carryover:
            expected['remaining_seconds'] = state['remaining_seconds']
        if not self.tasker.resource.override_pipeline({
                'CBResumeOpening': dict(custom_recognition_param=dict(mode='opening_resume', **expected)),
                'CBSettingsConfirmed': dict(custom_recognition_param=dict(mode='battle_read', **{key: value for key, value in expected.items() if key != 'remaining_seconds'}))}):
            raise RuntimeError('clan_battle_switch_pipeline_rejected')
        self.run('CBResumeOpening', click=True)
        self.opening_paused = False
        self.wait(['CBSettingsConfirmed'], seconds=15)
        settings['observation'] = self.read_battle_settings(self.last_receipt)
        settings['battle_speed'] = 2
        return settings

    def inheritance_menu(self):
        self.enter_lobby()
        self.run('CBAction_back_to_adventure', click=True)
        self.wait(['CBPage_adventure'], seconds=10)
        self.run('LabyClientOpenMainMenuOnce', click=True)
        self.wait(['LabyClientMainMenuObserved'], seconds=10)
        self._verify_account(self.plan)
        self.open_inheritance()

    def inheritance_lobby(self):
        self.close_inheritance()
        self.run('LabyClientMenuToAdventureOnce', click=True)
        self.wait(['CBPage_adventure'], seconds=10)
        self.run('CBAction_open_clan', click=True)
        self.wait(['CBPage_lobby'], seconds=10)

    def inheritance_manager(self):
        if self.inheritance is None:
            from .clan_battle_inheritance import BattleInheritance
            from .preset_execution import Journal
            from .native import ROOT
            self.inheritance = BattleInheritance(self, Journal(ROOT / '.runtime/clan-battle-daily-work/inheritance'))
        return self.inheritance

    def recover_inheritance(self):
        if self.inheritance_recovered:
            return
        from .clan_battle_inheritance import BattleInheritance
        managers = (self.inheritance_manager(), BattleInheritance.for_port(self))
        pending = []
        # Check both former global-setting journals before any result can consume a battle.
        for manager in managers:
            with manager.journal.locked():
                if manager._pending() is not None:
                    pending.append(manager)
        if pending:
            self.inheritance_menu()
            for manager in pending:
                manager.recover()
            self.inheritance_lobby()
        self.inheritance_recovered = True

    def prepare_inheritance(self):
        self.recover_inheritance()

    def after_settlement(self, intent, result):
        self.checkpoint()
        if self.pending is not None:
            raise RuntimeError('clan_battle_daily_previous_input_unresolved')
        self.started, self.battle_start = False, 'not_sent'

    def enter_lobby(self):
        names = ['CBPage_lobby', 'CBPage_boss_detail', 'CBPage_formation']
        selection = 'carry_selection' in self.calibration['daily']['execution']
        if selection:
            names.append('CBPage_carry_selection')
        if 'support' in self.calibration['daily']['execution']['pages']:
            names.append('CBPage_support')
        names.extend(['CBPage_adventure', 'LabyClientGameHomeObserved', 'LabyClientMainMenuObserved', 'CBInheritanceSettingsObserved'])
        hit = self.wait(names, seconds=10)
        if hit == 'CBInheritanceSettingsObserved':
            self.close_inheritance()
            hit = 'LabyClientMainMenuObserved'
        if hit == 'CBPage_support':
            self.run('CBAction_support_back', click=True)
            self.wait(['CBPage_formation'], seconds=10)
            hit = 'CBPage_formation'
        if hit == 'CBPage_formation':
            self.run('CBAction_cancel_formation', click=True)
            hit = self.wait(['CBPage_carry_selection' if selection and self.action is not None
                and self.action['kind'] == 'carryover' else 'CBPage_boss_detail'], seconds=10)
        if hit == 'CBPage_carry_selection':
            self.run('CBAction_close_carry_selection', click=True)
            self.wait(['CBPage_boss_detail'], seconds=10)
            hit = 'CBPage_boss_detail'
        if hit == 'CBPage_boss_detail':
            self.run('CBAction_back_to_lobby', click=True)
            self.wait(['CBPage_lobby'], seconds=10)
            hit = 'CBPage_lobby'
        if hit == 'LabyClientGameHomeObserved':
            self.run('LabyClientHomeToAdventure', click=True)
            self.wait(['CBPage_adventure'], seconds=10)
            hit = 'CBPage_adventure'
        if not self.account_binding_valid(self.plan):
            if hit == 'CBPage_lobby':
                self.run('CBAction_back_to_adventure', click=True)
                self.wait(['CBPage_adventure'], seconds=10)
            if hit != 'LabyClientMainMenuObserved':
                self.run('LabyClientOpenMainMenuOnce', click=True)
                self.wait(['LabyClientMainMenuObserved'], seconds=10)
            self._verify_account(self.plan)
            hit = 'LabyClientMainMenuObserved'
        if hit == 'LabyClientMainMenuObserved':
            self.run('LabyClientMenuToAdventureOnce', click=True)
            self.wait(['CBPage_adventure'], seconds=10)
            hit = 'CBPage_adventure'
        if hit == 'CBPage_adventure':
            self.run('CBAction_open_clan', click=True)
            self.wait(['CBPage_lobby'], seconds=10)

    def inventory(self):
        carry = bool(self.calibration['daily']['execution']['carry_slots'])
        if not carry:
            lobby, _ = self.read_page('lobby')
            if type(lobby.get('carry_count')) is not int or lobby['carry_count'] != 0:
                raise RuntimeError('clan_battle_daily_carry_inventory_unknown')
        self.run('CBAction_open_carry', click=True)
        pages = ['CBPage_carry_empty', 'CBPage_carry'] if carry else ['CBPage_carry_empty']
        hit = self.wait(pages, seconds=10)
        if hit not in pages:
            raise RuntimeError('clan_battle_daily_carry_inventory_unknown')
        reading, receipt = self.read_page('carry_empty' if hit == 'CBPage_carry_empty' else 'carry')
        if not carry and reading.get('carry_stock_indices') != []:
            raise RuntimeError('clan_battle_daily_carry_inventory_unknown')
        self.stock_observed_at = self.now().isoformat()
        stocks = [] if hit == 'CBPage_carry_empty' else reading['stocks']
        self.run('CBAction_close_empty_carry' if hit == 'CBPage_carry_empty' else 'CBAction_close_carry', click=True)
        self.wait(['CBPage_lobby'], seconds=10)
        return stocks, receipt, self.last_receipt

    def require_selected_carry(self, bound):
        from .game_day import current_game_day
        proof = getattr(self, '_selected_carry', None)
        if proof is None:
            raise RuntimeError('clan_battle_daily_selected_carry_unverified')
        action, stock, account, source, game_day = proof
        if (action != self.action or action['kind'] != 'carryover'
                or stock['stock_index'] != action['stock_index'] or stock['time'] != action['start_seconds']
                or set(stock['using_unit']) != set(self.plan['unit_ids']) or stock['support_unit'] != self.support_unit_id
                or bound.get('formal') is not True
                or any(bound.get(key) != action[key] for key in ('boss','phase','round','instance_id'))
                or not self.account_binding_valid(self.plan) or self._account_binding is not account
                or self._account_source() != source or current_game_day(self.now()) != game_day):
            raise RuntimeError('clan_battle_daily_selected_carry_binding_changed')
        return stock

    def require_regular_empty_inventory(self, bound):
        from .clan_battle_batch import _timestamp
        from .game_day import current_game_day
        proof = getattr(self, '_regular_empty_observation', None)
        if proof is None:
            raise RuntimeError('clan_battle_daily_empty_inventory_unverified')
        before, source, account = proof
        action, pending, now = self.action, self.pending, self.now()
        age = (now - _timestamp(before['observed_at'])).total_seconds()
        target = next((row for row in before['bosses'] if row['boss'] == action['boss']), None)
        if (action['kind'] != 'regular' or action['stock_index'] is not None or action['start_seconds'] != 90
                or self.support_unit_id is not None or bound.get('formal') is not True
                or any(bound.get(key) != action[key] for key in ('boss', 'phase', 'round', 'instance_id'))
                or not target or target.get('available') is not True
                or any(target.get(key) != action[key] for key in ('boss', 'phase', 'round', 'instance_id'))
                or any(before.get(key) != value for key, value in self.history_binding.items())
                or current_game_day(now) != before['game_day']
                # The stock observation keeps its real time while this Owner only edits a party.
                or age < 0
                or not self.account_binding_valid(self.plan) or self._account_binding is not account
                or self._account_source() != source
                or pending is not None and (pending.get('before') != before or pending.get('action') != action
                    or pending.get('observed_at') != before['observed_at'])):
            raise RuntimeError('clan_battle_daily_empty_inventory_binding_changed')
        if pending is not None and not 0 <= (now - _timestamp(
                self._regular_ready_at(pending['before'], pending['ready']))).total_seconds() <= 60:
            raise RuntimeError('clan_battle_daily_empty_inventory_binding_changed')

    def _regular_ready_at(self, before, ready):
        from .clan_battle_batch import READY_KEYS, _timestamp
        from .game_day import current_game_day
        prepared = getattr(self, '_regular_prepared', None)
        if prepared is None:
            return before['observed_at']
        original_before, verified = prepared
        if (before != original_before
                or any(ready.get(key) != verified.get(key) for key in READY_KEYS - {'evidence'})
                or any(ready.get('evidence', {}).get(key) != value for key, value in verified['evidence'].items())
                or ready.get('kind') != 'regular'
                or ready.get('support_unit') is not None or before['carry_stock_indices'] != []
                or current_game_day(self.now()) != before['game_day']
                or not 0 <= (self.now() - _timestamp(ready['evidence']['prepared_at'])).total_seconds() <= 60):
            raise RuntimeError('clan_battle_daily_prepared_binding_changed')
        return ready['evidence']['prepared_at']

    def prepared_regular_observation(self, before, ready):
        if getattr(self, '_regular_prepared', None) is None:
            return False
        self.checkpoint()
        if self.pending is not None or self.started or self.battle_start != 'not_sent':
            raise RuntimeError('clan_battle_daily_previous_input_unresolved')
        self.require_regular_empty_inventory(self.page.bound)
        if self._regular_empty_observation[0] != before:
            raise RuntimeError('clan_battle_daily_empty_inventory_binding_changed')
        self._regular_ready_at(before, ready)
        return True

    def bind_history(self, binding, regular, *, external=None):
        # Freeze program results and separately confirmed external use at the native boundary.
        self.history_binding, self.regular_history = deepcopy(binding), deepcopy(regular)
        self.external_regular_history = deepcopy(external or {})

    def observe_external_baseline(self, binding):
        # Only a live Owner may ask about the one initial, unexplained ordinary use.
        if (not callable(getattr(self, 'consumption_consent', None)) or type(getattr(self, 'owner_core_sha256', None)) is not str
                or re.fullmatch('[0-9a-f]{64}', self.owner_core_sha256) is None
                or self.regular_history or getattr(self, 'external_regular_history', {}) or self.pending is not None):
            return self.observe(binding)
        self._external_baseline_observing = True
        try:
            observation = self.observe(binding)
            if observation['remaining_count'] == 2 and observation['cp'] == 900:
                proof = self._regular_empty_observation
                if proof is None or proof[0] != observation:
                    raise RuntimeError('clan_battle_daily_empty_inventory_unverified')
                self.consumption_binding()
            return observation
        finally:
            self._external_baseline_observing = False

    def observe(self, binding, *, result_intent=None):
        from .clan_battle_recovery import ClanDisconnected, recover_disconnect
        try:
            return self._observe_once(binding, result_intent=result_intent)
        except ClanDisconnected as error:
            recovered = recover_disconnect(self, self.plan, error, pending=self.pending is not None)
            if recovered['can_continue']:
                return self._observe_once(binding, result_intent=result_intent)
            raise

    def _observe_once(self, binding, *, result_intent=None):
        self._regular_prepared = None
        from .clan_battle_batch import BINDING_KEYS, _hash, _now
        from .game_day import current_game_day
        try:
            if self.history_binding != binding or self.regular_history is None:
                raise RuntimeError('clan_battle_daily_history_binding_missing')
            regular = dict(self.regular_history)
            if result_intent is not None:
                if (self.pending != result_intent or not self.started or self.battle_start != 'sent'
                        or any(result_intent.get(key) != binding[key] for key in BINDING_KEYS)):
                    raise RuntimeError('clan_battle_daily_result_history_binding_changed')
                if result_intent['action']['kind'] == 'regular':
                    slot = result_intent['action']['slot']
                    if slot in regular:
                        raise RuntimeError('clan_battle_daily_regular_slot_duplicate')
                    # This candidate becomes history only after result/count validation and Journal settlement.
                    regular[slot] = dict(intent=result_intent)
            elif self.pending is not None:
                raise RuntimeError('clan_battle_daily_previous_input_unresolved')
            started = _now(self.now())
            if current_game_day(started) != binding['game_day']:
                raise RuntimeError('clan_battle_daily_game_day_changed')
            self.enter_lobby()
        finally:
            # Keep proof for non-consuming return, then discard it before the refresh.
            self._regular_empty_observation = None
            self._selected_carry = None
        self.recover_inheritance()
        started = _now(self.now())
        if current_game_day(started) != binding['game_day']:
            raise RuntimeError('clan_battle_daily_game_day_changed')
        execution = self.calibration['daily']['execution']
        regular_only = execution['formation_mode']['carryover'] is None and 'carry_selection' not in execution
        inventory_source = self._account_source()
        self.stocks, stock_receipt, lobby_receipt = self.inventory()
        lobby = _detail(lobby_receipt)
        ended = _now(self.now())
        if (current_game_day(ended) != binding['game_day']
                or not 0 <= (ended - started).total_seconds() <= 60):
            raise RuntimeError('clan_battle_daily_observation_window_invalid')
        external = getattr(self, 'external_regular_history', {})
        baseline_gap = (getattr(self, '_external_baseline_observing', False) is True and not regular and not external
            and lobby['cp'] == 900 and lobby['remaining_count'] == 2 and lobby['carry_count'] == 0 and self.stocks == [])
        if ((lobby['remaining_count'] != lobby['cp'] // 300 - len(regular) - len(external) and not baseline_gap)
                or lobby['carry_count'] != len(self.stocks)):
            raise RuntimeError('clan_battle_daily_count_or_inventory_changed')
        used = sorted({uid for row in regular.values() for uid in row['intent']['ready']['unit_ids']
            if uid != row['intent']['ready']['support_unit']} | {uid for row in external.values() for uid in row['unit_ids']})
        stamp, bosses = ended.isoformat(), []
        for value in lobby['bosses']:
            row = dict(value, instance_id=None, appeared_at=None, evidence=dict(lobby=lobby_receipt))
            previous = self.boss_history.get(row['boss'])
            if row['available']:
                row['instance_id'] = _hash(dict(period=lobby['period'], boss=row['boss'], phase=row['phase'], round=row['round']))
                if previous and previous['instance_id'] == row['instance_id']:
                    row['appeared_at'] = previous['appeared_at']
                elif previous and previous['round'] is not None and row['round'] > previous['round']:
                    row['appeared_at'] = stamp
                    row['evidence']['previous'] = previous['evidence']
                self.boss_history[row['boss']] = deepcopy(row)
            bosses.append(row)
        self.last_bosses = {row['boss']: deepcopy(row) for row in bosses}
        history = dict(source='journal_and_live_counter',
            settled_regular_intents=[row['intent']['intent_id'] for row in self.regular_history.values()],
            external_regular_observed_slots=sorted(external), external_regular_unconfirmed=baseline_gap,
            pending_result_intent=result_intent['intent_id'] if result_intent is not None else None)
        observation = dict(binding, observed_at=stamp, remaining_count=lobby['remaining_count'], cp=lobby['cp'],
            cp_semantics='daily_earned', cp_evidence=lobby_receipt, used_unit=used,
            carry_stock_indices=[row['stock_index'] for row in self.stocks], bosses=bosses,
            reset_verified=True, reset_evidence=dict(consumption_history=history, inventory=stock_receipt,
                account=self.account_receipt, observation_window=dict(started_at=started.isoformat(), ended_at=stamp)))
        if regular_only or (type(lobby.get('carry_count')) is int and lobby['carry_count'] == 0
                and self.stocks == [] and stock_receipt.get('native_ok') is True):
            empty = _detail(stock_receipt)
            node = stock_receipt['nodes'][-1]
            frame = empty.get('evidence', {}).get('frame_pixel_sha256')
            if (type(lobby.get('carry_count')) is not int or lobby['carry_count'] != 0 or self.stocks != []
                    or stock_receipt.get('native_ok') is not True or node.get('name') != 'CBPage_carry_empty'
                    or node.get('completed') is not True or empty.get('page') != 'carry_empty'
                    or empty.get('carry_stock_indices') != [] or type(frame) is not str
                    or re.fullmatch('[0-9a-f]{64}', frame) is None
                    or inventory_source is None or not self.account_binding_valid(self.plan)
                    or self._account_source() != inventory_source):
                raise RuntimeError('clan_battle_daily_empty_inventory_unverified')
            self._regular_empty_observation = (deepcopy(observation), inventory_source, self._account_binding)
        return observation

    @staticmethod
    def _require_observed_target(calibration, job, target, capability):
        if capability + '_targets' not in calibration:
            return
        from .clan_battle_native import _require_target_scope
        phase = target['phase']
        job_phases = ('B', 'C') if job['phase'] == 'BC' else (job['phase'],)
        if phase not in job_phases or target['boss'] != job['boss']:
            raise RuntimeError('clan_battle_daily_target_changed')
        _require_target_scope(calibration, dict(job, phase=phase), capability)

    def blocked_carryovers(self, request, grants):
        from .clan_battle_batch import _target
        from .clan_battle_calibration import load_calibration
        blocked = {}
        for stock, grant in grants.items():
            job = request['slot_plans'][str(grant['source_slot'])]
            try:
                calibration = load_calibration(job, capability='carryover')
                if 'carryover_targets' in calibration:
                    observed = self.last_bosses.get(job['boss'])
                    if observed and _target(job, [observed]) is not None:
                        self._require_observed_target(calibration, job, observed, 'carryover')
            except RuntimeError as exc:
                if str(exc) not in ('clan_battle_calibration_capability_missing:carryover',
                                    'clan_battle_carryover_target_uncalibrated'):
                    raise
                blocked[stock] = 'carryover_capability_not_calibrated'
                continue
        return blocked

    def prepare(self, action, job, binding):
        from .clan_battle_recovery import ClanDisconnected, recover_disconnect
        try:
            return self._prepare_once(action, job, binding)
        except ClanDisconnected as error:
            recovered = recover_disconnect(self, job, error, pending=self.pending is not None)
            if recovered['can_continue']:
                self.observe(binding)
                return self._prepare_once(action, job, binding)
            raise

    def _prepare_once(self, action, job, binding):
        self._regular_prepared = None
        if self.pending is not None:
            raise RuntimeError('clan_battle_daily_previous_input_unresolved')
        self.enter_lobby()
        self._selected_carry = None
        capability = 'carryover' if action['kind'] == 'carryover' else 'formal'
        self.configure_slot(job, capability=capability)
        self.action, self.start_seconds = deepcopy(action), action['start_seconds']
        if (action['kind'] != 'carryover' and (self.start_seconds <= 1 or self.start_seconds not in
                self.calibration['daily']['execution']['opening'].get('start_seconds', [90]))):
            raise RuntimeError('clan_battle_daily_start_time_uncalibrated')
        actual = self.last_bosses.get(action['boss'])
        if not actual or any(actual[key] != action[key] for key in ('boss', 'round', 'phase', 'instance_id')):
            raise RuntimeError('clan_battle_daily_target_changed')
        self._require_observed_target(self.calibration, job, actual, capability)
        self.prepare_inheritance()
        self.page.bound = dict(period=job['period'], formal=True,
            **{key: action[key] for key in ('boss', 'round', 'phase', 'instance_id')})
        self.run('CBAction_open_boss', click=True)
        self.wait(['CBPage_boss_detail'], seconds=10)
        self.run('CBAction_open_formal', click=True)
        self.wait(['CBPage_target'], seconds=10)
        retained_before, target_receipt = None, None
        if (action['kind'] == 'regular' and self.support_unit_id is None
                and getattr(self, '_regular_empty_observation', None) is not None):
            self.require_regular_empty_inventory(self.page.bound)
            target, target_receipt = self.read_page('target')
            before = self._regular_empty_observation[0]
            if (self.stocks != [] or target.get('cp') != before['cp']
                    or target.get('remaining_count') != before['remaining_count']
                    or target.get('boss') != action['boss'] or target.get('phase') != action['phase']):
                raise RuntimeError('clan_battle_daily_target_counters_changed')
            retained_before = deepcopy(before)
        if action['kind'] == 'regular':
            self.run('CBAction_open_formation', click=True)
            self.wait(['CBPage_formation'], seconds=10)
            self.form_team()
        else:
            self.run('CBAction_open_carry_for_target', click=True)
            selection = 'carry_selection' in self.calibration['daily']['execution']
            self.wait(['CBPage_carry_selection' if selection else 'CBPage_carry'], seconds=10)
            if selection and _detail(self.last_receipt).get('reuse_team') is not True:
                self.run('CBAction_enable_carry_team', click=True)
                self.wait(['CBPage_carry_selection'], seconds=10)
                if _detail(self.last_receipt).get('reuse_team') is not True:
                    raise RuntimeError('clan_battle_daily_carry_reuse_unknown')
            self.run('CBAction_select_carry', click=True)
            self.wait(['CBPage_formation'], seconds=10)
        team = self.read_team()
        self.page.require_team(team)
        ready = dict(binding, **action, slot_confirmed_for=job['confirmed_for'], verified=True,
            unit_ids=team['unit_ids'], stars=team['stars'], support_unit=team['support_unit_id'],
            support_owner_viewer_id=team.get('support_owner_viewer_id'),
            evidence=dict(account=self.account_receipt, formation=team, mode='formal', calibration_id=self.calibration['calibration_id']))
        if action['kind'] == 'carryover' and 'carry_selection' in self.calibration['daily']['execution']:
            ready['evidence']['carry_selection'] = deepcopy(self.require_selected_carry(self.page.bound))
        if retained_before is not None:
            ready['evidence'].update(prepared_at=self.now().isoformat(), target=target_receipt)
            self._regular_prepared = (retained_before, deepcopy(ready))
        return ready

    def execute(self, intent, job):
        from .clan_battle_recovery import ClanDisconnected, recover_disconnect
        try:
            return self._execute_once(intent, job)
        except ClanDisconnected as error:
            if not getattr(self, '_clan_disconnect_recovery_used', False):
                recover_disconnect(self, job, error, pending=True)
            raise
        finally:
            self.finish_opening_capture()

    def _execute_once(self, intent, job):
        from .clan_battle import require_timing
        from .clan_battle_batch import BINDING_KEYS
        from .game_day import current_game_day
        from .supported_daily import _atomic_json
        if (self.pending is not None or self.action != intent['action']
                or current_game_day(self.now()) != intent['game_day']):
            raise RuntimeError('clan_battle_daily_start_binding_changed')
        if self.action['kind'] == 'carryover':
            from .clan_battle_calibration import load_calibration
            calibration = load_calibration(job, capability='carryover')
            require_daily_execution(calibration, job, capability='carryover')
            self._require_observed_target(calibration, job, self.action, 'carryover')
        else:
            self._require_observed_target(self.calibration, job, self.action, 'formal')
        require_timing(self, job)
        team = self.read_team()
        self.page.require_team(team)
        if (team['unit_ids'] != intent['ready']['unit_ids'] or team['stars'] != intent['ready']['stars']
                or team['support_unit_id'] != intent['ready']['support_unit']
                or team.get('support_owner_viewer_id') != intent['ready']['support_owner_viewer_id']):
            raise RuntimeError('clan_battle_daily_team_changed_before_start')
        self.pending, self.started = deepcopy(intent), False
        self.arm_opening_start(intent['intent_id'], start_seconds=self.action['start_seconds'], action_kind=self.action['kind'])
        start = self.run('CBStart', click=True)
        opening = _detail(start)
        self.opening_paused = opening.get('paused') is True
        if not self.opening_paused and opening.get('opening_direct') is not True:
            raise RuntimeError('clan_battle_opening_not_verified')
        settings = self.configure_opening(job, start)
        result_pages = ['CBPage_result']
        if 'result_reward' in self.calibration.get('daily', {}).get('execution', {}):
            result_pages.append('CBDailyResultRewardObserved')
        if self.wait(result_pages, seconds=360) == 'CBDailyResultRewardObserved':
            self.run('CBDailyResultRewardConfirm', click=True)
            self.wait(['CBPage_result'], seconds=20)
        result_receipt = self.last_receipt
        result = _result_detail(result_receipt)
        if (result.get('period') != job['period'] or result.get('formal') is not True
                or any(result.get(key) != intent['action'][key] for key in ('boss', 'phase', 'round', 'instance_id'))):
            raise RuntimeError('clan_battle_daily_result_binding_mismatch')
        # Preserve known damage before any return/navigation, even if consumption remains unresolved.
        _atomic_json(self.directory / ('result-' + intent['intent_id'] + '.json'), dict(intent_id=intent['intent_id'],
            damage=result['damage'], result_evidence=result_receipt, settings_evidence=settings, start=start))
        self.run('CBAction_return_to_lobby', click=True)
        self.wait(['CBPage_lobby'], seconds=15)
        binding = {key: intent[key] for key in BINDING_KEYS}
        after = self.observe(binding, result_intent=intent)
        regular = intent['action']['kind'] == 'regular'
        earned = after['cp'] // 300 - intent['before']['cp'] // 300
        if (after['cp'] < intent['before']['cp']
                or intent['before']['remaining_count'] + earned - after['remaining_count'] != int(regular)):
            raise RuntimeError('clan_battle_daily_consumption_unknown')
        before_stock, after_stock = set(intent['before']['carry_stock_indices']), set(after['carry_stock_indices'])
        added, removed = after_stock - before_stock, before_stock - after_stock
        if (regular and (removed or len(added) > 1)
                or not regular and (added or removed != {intent['action']['stock_index']})):
            raise RuntimeError('clan_battle_daily_carry_consumption_unknown')
        grant = None
        if added:
            stock = next(row for row in self.stocks if row['stock_index'] in added)
            ready = intent['ready']
            if (stock['using_unit'] != ready['unit_ids'] or stock['support_unit'] != ready['support_unit']
                    or stock['support_owner_viewer_id'] != ready['support_owner_viewer_id']):
                raise RuntimeError('clan_battle_daily_grant_team_unknown')
            grant = dict(binding, slot_confirmed_for=ready['slot_confirmed_for'], source_slot=ready['slot'],
                source_intent_id=intent['intent_id'], stock_index=stock['stock_index'], time=stock['time'],
                using_unit=stock['using_unit'], support_unit=stock['support_unit'],
                support_owner_viewer_id=stock['support_owner_viewer_id'], source_instance_id=ready['instance_id'],
                issued_at=self.stock_observed_at, evidence=dict(inventory=after['reset_evidence']['inventory'],
                    issuance_bound='new_stock_readback_after_reserved_battle'))
        self.pending = None
        return dict(ready=deepcopy(intent['ready']), result_verified=True, consumption_verified=True,
            damage=result['damage'], settings_verified=True, settings_evidence=settings,
            result_evidence=result_receipt, grant=grant, after=after, return_verified=True)
