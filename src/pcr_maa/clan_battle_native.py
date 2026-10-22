"""Owner-only clan simulation port. Uncalibrated clan pages never provide input."""
import hashlib
import json
import math
from pathlib import Path
import re
import time

from maa.custom_recognition import CustomRecognition
from copy import deepcopy

from . import native
from .guide_trial_native import NativeGuideTrial
from .labyrinth.game_session import game_session_pipeline
from .labyrinth.profile import PROFILE_NODE, profile_player_id


PAGES = ('adventure', 'lobby', 'target', 'formation', 'support', 'battle', 'result', 'closed')
FIELDS = {
    'period': r'^20[0-9]{2}[-年](?:0[1-9]|1[0-2])月?$',
    'phase': r'^(?:B|C|BC|D|E)$',
    'round': r'^(?:第\s*)?[1-9][0-9]*(?:\s*周目)?$',
    'boss': r'^(?:BOSS\s*)?[1-5]$',
    'simulation': r'^模拟战$',
    'damage': r'^[0-9][0-9,，\s]*$',
    'remaining_seconds': r'^(?:1[:：](?:[0-2][0-9]|30)|0[:：][0-5][0-9])$',
}
ACTION_PAGES = dict(open_clan='adventure', open_boss='lobby', open_simulation='target',
                    open_support='formation', support_back='support', start='formation',
                    characters_top='formation', characters_next='formation',
                    support_top='support', support_next='support', return_to_target='result')
IDENTITY_FIELDS = ('period', 'phase', 'round', 'boss', 'simulation')
CALIBRATION_GAPS = (
    '公会战入口与关闭页面', '期次、阶段、周目和BOSS编号', '模拟战模式与正式战排除标记',
    '编队空位、星级、自有/支援标记及列表翻页', '支援编号、星级及可用状态',
    '模拟结果伤害页及返回模拟目标后继', '会战HUD与秒级读取/输入延迟',
)


def _roi(value):
    if (type(value) is not list or len(value) != 4 or any(type(n) is not int for n in value)
            or value[0] < 0 or value[1] < 0 or value[2] <= 0 or value[3] <= 0
            or value[0]+value[2] > 1280 or value[1]+value[3] > 720):
        raise ValueError('clan_battle_calibration_roi_invalid')
    return value


def _node(spec, *, expected=None):
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
        value.update(expected=text, threshold=.9)
    elif algorithm == 'TemplateMatch' and expected is None:
        name, digest = spec.get('template'), spec.get('sha256')
        if type(name) is not str or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('clan_battle_calibration_template_invalid')
        path = native.RESOURCE / 'image' / name
        if (type(digest) is not str or not re.fullmatch('[0-9a-f]{64}', digest)
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('clan_battle_calibration_template_changed')
        value.update(template=name, threshold=.9)
    else:
        raise ValueError('clan_battle_calibration_algorithm_invalid')
    return value


def calibration_pipeline(calibration, plan):
    """Explicit injection for saved-frame checks; no discovered file enables it."""
    if calibration is None:
        raise RuntimeError('clan_battle_calibration_missing:' + '、'.join(CALIBRATION_GAPS))
    if (type(calibration) is not dict or calibration.get('version') != 1
            or calibration.get('frame_size') != [1280, 720]
            or calibration.get('battle_hud') != 'guide-trial-1280x720'
            or calibration.get('server') != plan['server']
            or not calibration.get('calibration_id')
            or calibration.get('evidence_kind') not in ('offline_replay', 'device_saved_frame')):
        raise ValueError('clan_battle_calibration_invalid')
    samples = calibration.get('samples')
    if type(samples) is not list or not samples:
        raise ValueError('clan_battle_calibration_samples_missing')
    covered = set()
    for sample in samples:
        if type(sample) is not dict or sample.get('page') not in PAGES:
            raise ValueError('clan_battle_calibration_sample_invalid')
        path = Path(sample.get('path', ''))
        if (not path.is_absolute() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != sample.get('sha256')):
            raise ValueError('clan_battle_calibration_sample_changed')
        native.read_image(path)
        covered.add(sample['page'])
    if covered != set(PAGES):
        raise ValueError('clan_battle_calibration_pages_missing')
    pages = calibration.get('pages')
    if type(pages) is not dict or set(pages) != set(PAGES):
        raise ValueError('clan_battle_calibration_pages_missing')
    nodes = {}
    for page, spec in pages.items():
        if type(spec) is not dict or type(spec.get('labels')) is not list or len(spec['labels']) < 2:
            raise ValueError('clan_battle_calibration_page_guards_missing')
        for index, label in enumerate(spec['labels']):
            nodes[f'CBLabel_{page}_{index}'] = _node(label)
        fields = spec.get('fields', {})
        required = set(IDENTITY_FIELDS if page in ('target', 'formation', 'support', 'result')
                       else ('period', 'phase', 'round') if page == 'lobby'
                       else ('remaining_seconds',) if page == 'battle' else ())
        if page == 'result':
            required.add('damage')
        if type(fields) is not dict or set(fields) != required:
            raise ValueError('clan_battle_calibration_fields_missing:' + page)
        for field, roi in fields.items():
            nodes[f'CBField_{page}_{field}'] = _node(dict(roi=roi), expected=FIELDS[field])
        if page in ('target', 'formation', 'support'):
            nodes[f'CBFormal_{page}'] = _node(spec.get('formal'), expected=r'^(?:正式战|正式挑战|消耗挑战次数)$')
        nodes['CBPage_'+page] = dict(recognition='Custom', custom_recognition='ClanBattlePage',
            custom_recognition_param=dict(page=page), action='DoNothing', next=[], timeout=1000,
            pre_delay=0, post_delay=0)
    actions = calibration.get('actions')
    if type(actions) is not dict or set(actions) != set(ACTION_PAGES):
        raise ValueError('clan_battle_calibration_actions_missing')
    for name, page in ACTION_PAGES.items():
        spec = actions[name]
        if type(spec) is not dict or spec.get('page') != page:
            raise ValueError('clan_battle_calibration_action_page_invalid')
        if name in ('characters_top', 'characters_next', 'support_top', 'support_next'):
            begin, end = spec.get('begin'), spec.get('end')
            _roi(begin); _roi(end)
            action = dict(action='Swipe', begin=begin, end=end, duration=400)
        else:
            label = dict(spec.get('label', {}))
            if name == 'open_simulation':
                label['expected'] = '^模拟战$'
            elif name == 'open_boss':
                label['expected'] = rf'^BOSS\s*{plan["boss"]}$'
            elif name == 'start':
                label['expected'] = '^(?:开始模拟战|战斗开始)$'
            nodes['CBActionLabel_'+name] = _node(label)
            action = dict(action='Click', target=True)
        if name == 'start':
            # Only CBStart is actionable; its character callback rechecks all five slots.
            continue
        nodes['CBAction_'+name] = dict(recognition='Custom', custom_recognition='ClanBattlePage',
            custom_recognition_param=dict(page=page, action=name), next=[], timeout=1000,
            max_hit=1, pre_delay=0, post_delay=200, **action)
    for section in ('formation', 'support'):
        layout = calibration.get(section)
        if type(layout) is not dict or type(layout.get('slots')) is not list or not layout['slots']:
            raise ValueError('clan_battle_calibration_slots_missing')
        for roi in layout['slots']:
            _roi(roi)
        if section == 'formation':
            if (len(layout['slots']) != 5 or any(type(layout.get(key)) is not list or len(layout[key]) != 5
                    for key in ('empty', 'stars', 'owned', 'borrowed'))
                    or type(layout.get('candidates')) is not list or not layout['candidates']):
                raise ValueError('clan_battle_calibration_formation_invalid')
            for roi in layout['candidates']:
                _roi(roi)
            for index in range(5):
                for key, prefix in (('empty', 'CBEmpty'), ('owned', 'CBOwned'), ('borrowed', 'CBSupport')):
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
        nodes[name] = dict(recognition='Custom', custom_recognition='ClanBattleCharacters',
            custom_recognition_param=dict(mode=mode), action='Click' if click else 'DoNothing', target=True,
            next=['CBSettingsConfirmed'] if name in ('CBToggleAuto', 'CBToggleSet') else [],
            timeout=1000, pre_delay=0, post_delay=0)
        if click:
            nodes[name]['max_hit'] = 1
    nodes['CBSettingsConfirmed']['on_error'] = ['LabyDisconnect6002Observed']
    return nodes


def require_target_identity(plan, reading):
    phase = reading.get('phase')
    if (reading.get('period') != plan['period']
            or phase not in (('B', 'C', 'BC') if plan['phase'] == 'BC' else (plan['phase'],))
            or type(reading.get('round')) is not int or reading['round'] < plan['round_min']
            or plan.get('round_max') is not None and reading['round'] > plan['round_max']
            or type(reading.get('boss')) is not int or reading['boss'] != plan['boss']
            or reading.get('simulation') is not True):
        raise RuntimeError('clan_battle_target_mismatch_or_not_simulation')


class ClanBattlePage(CustomRecognition):
    def __init__(self, plan, calibration, support_unit_id=None):
        super().__init__()
        self.plan, self.calibration, self.support_unit_id = plan, calibration, support_unit_id

    def hit(self, context, image, name):
        seen = context.run_recognition(name, image)
        return seen is not None and seen.hit is True

    def word(self, context, image, name):
        seen = context.run_recognition(name, image)
        raw = getattr(seen, 'raw_detail', None) if seen is not None and seen.hit else None
        words = raw.get('filtered') if type(raw) is dict else None
        if (type(words) is not list or len(words) != 1 or type(words[0]) is not dict
                or raw.get('best') != words[0] or type(words[0].get('text')) is not str
                or type(words[0].get('score')) not in (int, float)
                or not math.isfinite(words[0]['score']) or not .9 <= words[0]['score'] <= 1):
            raise RuntimeError('clan_battle_page_field_unknown:' + name)
        node = context.get_node_data(name)
        box = words[0].get('box')
        roi = node['roi']
        if (type(box) is not list or len(box) != 4 or any(type(n) is not int for n in box)
                or box[2] <= 0 or box[3] <= 0 or box[0] < roi[0] or box[1] < roi[1]
                or box[0]+box[2] > roi[0]+roi[2] or box[1]+box[3] > roi[1]+roi[3]
                or re.fullmatch(node['expected'], words[0]['text']) is None):
            raise RuntimeError('clan_battle_page_field_unknown:' + name)
        return words[0]['text']

    def read(self, context, image, page):
        if (context.tasker.stopping or image is None or image.shape != (720, 1280, 3)
                or page not in PAGES):
            raise RuntimeError('clan_battle_page_unknown')
        if self.hit(context, image, 'LabyDisconnect6002Observed'):
            raise RuntimeError('clan_battle_disconnected_6002')
        spec = self.calibration['pages'][page]
        if not all(self.hit(context, image, f'CBLabel_{page}_{index}') for index in range(len(spec['labels']))):
            raise RuntimeError('clan_battle_page_unknown:' + page)
        if page in ('target', 'formation', 'support') and self.hit(context, image, 'CBFormal_'+page):
            raise RuntimeError('clan_battle_formal_battle_forbidden')
        values = {}
        for field in spec.get('fields', {}):
            text = self.word(context, image, f'CBField_{page}_{field}')
            if field == 'simulation':
                value = text == '模拟战'
            elif field == 'period':
                value = text.replace('年', '-').replace('月', '')
            elif field == 'phase':
                value = text
            elif field == 'remaining_seconds':
                minute, second = text.replace('：', ':').split(':')
                value = int(minute)*60+int(second)
            else:
                value = int(re.sub('[^0-9]', '', text))
            values[field] = value
        if page in ('target', 'formation', 'support', 'result'):
            require_target_identity(self.plan, values)
        if page == 'lobby':
            require_target_identity(self.plan, dict(values, boss=self.plan['boss'], simulation=True))
        return dict(values, page=page)

    def action(self, context, image, name):
        page = ACTION_PAGES[name]
        self.read(context, image, page)
        if name in ('characters_top', 'characters_next', 'support_top', 'support_next'):
            return [0, 0, 1, 1]
        node = 'CBActionLabel_'+name
        if not self.hit(context, image, node):
            raise RuntimeError('clan_battle_action_label_unknown:' + name)
        result = context.run_recognition(node, image)
        return _roi(list(result.box))

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
            detail = self.read(context, argv.image, params.get('page'))
            target = self.action(context, argv.image, params['action']) if 'action' in params else [0, 0, 1, 1]
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
        measured = json.loads(data.decode('utf-8'))
    except (OSError, ValueError, UnicodeError):
        return unknown
    if (type(measured) is not dict or measured.get('kind') != 'owner_native_clan_timing'
            or measured.get('calibration_id') != calibration['calibration_id']
            or measured.get('battle_hud') != calibration['battle_hud']
            or measured.get('observation_scope') != 'full_read_tick'
            or measured.get('action_scope') != 'toggle_dispatch_and_full_team_readback'):
        return unknown
    values = [measured.get(key) for key in ('observation_intervals_ms', 'action_latencies_ms')]
    if any(type(rows) is not list or len(rows) < 20 or any(type(n) is not int or n <= 0 for n in rows) for rows in values):
        return unknown
    return dict(validated=True, max_observation_interval_ms=max(values[0]),
                max_action_latency_ms=max(values[1]), source=str(path), source_sha256=binding['sha256'])


class NativeClanBattle(NativeGuideTrial):
    def __init__(self, tasker, directory, *, calibration=None):
        super().__init__(tasker, Path(directory))
        self.calibration = calibration
        self.timing_capability = dict(validated=False)
        self.started = False

    def configure(self, plan):
        from .clan_battle import load_snapshot, support_requirement
        from .clan_battle_characters import ClanBattleCharacters
        nodes = calibration_pipeline(self.calibration, plan)
        self.plan, self.snapshot = plan, load_snapshot(plan)
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
        # Keep the callbacks with the real Resource; registration rejection is terminal.
        self.timing_capability = timing_capability(self.calibration)

    def choose(self, names, *, timeout=1000):
        try:
            return super().choose(names, timeout=timeout)
        except RuntimeError as exc:
            if str(exc).startswith('guide_trial_disconnected_6002:'):
                raise RuntimeError('clan_battle_disconnected_6002:battle_start=' + self.battle_start) from exc
            raise

    def run(self, node, *, click=False):
        starting = click and node == 'CBStart'
        if starting:
            if self.started:
                raise RuntimeError('clan_battle_start_already_attempted')
            self.started, self.battle_start = True, 'unknown'
        try:
            result = super().run(node, click=click)
        except native.NativeInputNotSent as exc:
            if starting:
                self.battle_start = 'not_sent'
                exc.receipt['clan_battle_phase'] = 'simulation_start'
            raise
        if starting:
            self.battle_start = 'sent'
        self.last_receipt = result
        if any(row.get('name') == 'LabyDisconnect6002Observed' for row in result.get('nodes', [])):
            raise RuntimeError('clan_battle_disconnected_6002:battle_start=' + self.battle_start)
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
        hit = self.wait(['LabyClientGameHomeObserved', 'LabyClientAdventureObserved'], seconds=10)
        if hit == 'LabyClientGameHomeObserved':
            self.run('LabyClientHomeToAdventure', click=True)
            self.wait(['LabyClientAdventureObserved'], seconds=10)
        self.run('LabyClientOpenMainMenuOnce', click=True)
        self.wait(['LabyClientMainMenuObserved'], seconds=10)
        self.run('LabyClientOpenProfileOnce', click=True)
        self.wait([PROFILE_NODE], seconds=10)
        self.account_receipt = self.run(PROFILE_NODE)
        self.account = str(profile_player_id(self.account_receipt))
        if self.account != plan['account_id']:
            raise RuntimeError('clan_battle_account_mismatch')
        self.run('LabyClientProfileBackOnce', click=True)
        self.wait(['LabyClientMainMenuObserved'], seconds=10)
        self.run('LabyClientMenuToAdventureOnce', click=True)
        self.wait(['LabyClientAdventureObserved'], seconds=10)
        self.run('CBAction_open_clan', click=True)
        if self.wait(['CBPage_closed', 'CBPage_lobby'], seconds=15) == 'CBPage_closed':
            raise RuntimeError('clan_battle_closed')
        self.read_page('lobby')
        self.run('CBAction_open_boss', click=True)
        self.wait(['CBPage_target'], seconds=10)
        self.read_page('target')
        self.run('CBAction_open_simulation', click=True)
        self.wait(['CBPage_formation'], seconds=10)
        self.form_team()

    def read_team(self):
        return _detail(self.run('CBReadTeam'))

    def _list_hit(self, section, position):
        # Read-only recognition is dispatched through the same Owner, never a controller shortcut.
        return self.choose([f'CBList_{section}_{position}']) == f'CBList_{section}_{position}'

    def _reset_list(self, section):
        if not self._list_hit(section, 'top'):
            name = 'characters_top' if section == 'formation' else 'support_top'
            self.run('CBAction_'+name, click=True)
            if not self._list_hit(section, 'top'):
                raise RuntimeError('clan_battle_list_top_not_verified')

    def _next_list(self, section):
        if self._list_hit(section, 'bottom'):
            return False
        before = self.tasker.controller.cached_image.copy()
        name = 'characters_next' if section == 'formation' else 'support_next'
        self.run('CBAction_'+name, click=True)
        self.read_page(section)
        after = self.tasker.controller.cached_image
        rois = self.calibration[section]['candidates' if section == 'formation' else 'slots']
        def digest(frame):
            return [hashlib.sha256(frame[y:y+h, x:x+w].tobytes()).digest() for x,y,w,h in rois]
        if after is None or before.shape != after.shape or digest(before) == digest(after):
            raise RuntimeError('clan_battle_list_unchanged_no_retry')
        return True

    def form_team(self):
        from .clan_battle import select_support
        from .clan_battle_characters import box_inside_roi
        team = self.read_team()
        try:
            self.page.require_team(team)
            return
        except RuntimeError:
            pass
        for _ in range(5):
            if team['member_count'] == 0:
                break
            self.tasker.resource.override_pipeline({'CBEditTeam': dict(custom_recognition_param=dict(mode='clear', count=team['member_count']))})
            self.run('CBEditTeam', click=True)
            after = self.read_team()
            if after['member_count'] != team['member_count']-1:
                raise RuntimeError('clan_battle_clear_readback_unknown')
            team = after
        for uid in self.plan['unit_ids']:
            if uid == self.support_unit_id:
                continue
            self._reset_list('formation')
            found = False
            for _ in range(16):
                self.read_team()
                frame = self.tasker.controller.cached_image
                matches = [(row, roi) for roi in self.calibration['formation']['candidates']
                           if (row := self.characters.match(frame, uid, roi))]
                if len(matches) > 1:
                    raise RuntimeError('clan_battle_candidate_ambiguous')
                if matches:
                    if not box_inside_roi(matches[0][0]['box'], matches[0][1]):
                        raise RuntimeError('clan_battle_candidate_clipped')
                    self.tasker.resource.override_pipeline({'CBEditTeam': dict(custom_recognition_param=dict(mode='add', unit_id=uid))})
                    self.run('CBEditTeam', click=True)
                    after = self.read_team()
                    if after['member_count'] != team['member_count']+1 or uid not in after['unit_ids']:
                        raise RuntimeError('clan_battle_add_readback_unknown')
                    team, found = after, True
                    break
                if not self._next_list('formation'):
                    break
            if not found:
                raise RuntimeError('clan_battle_character_not_found:' + str(uid))
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
            if self.choose(['CBReadSettings']) != 'CBReadSettings':
                raise RuntimeError('clan_battle_controls_unknown')
            initial = self.last_receipt
        state = _detail(initial)
        self.last_controls = deepcopy(state)
        return state

    def _toggle(self, node, previous, desired, *, slot=None, remaining_seconds=None):
        before = getattr(self, 'last_controls', None)
        state = self.read_battle_settings()
        if remaining_seconds is not None and (type(remaining_seconds) is not int or not 0 <= remaining_seconds <= 89
                or state.get('same_frame') is not True or type(state.get('remaining_seconds')) is not int
                or state['remaining_seconds'] != remaining_seconds):
            raise RuntimeError('clan_battle_switch_window_missed')
        if before is not None and any(before.get(key) != state.get(key) for key in ('auto', 'sets', 'unit_ids')):
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
        if self.tasker.resource.override_pipeline({node: dict(custom_recognition_param=params),
                'CBSettingsConfirmed': dict(custom_recognition_param=dict(mode='battle_read', **expected))}) is False:
            raise RuntimeError('clan_battle_switch_pipeline_rejected')
        reading = _detail(self.run(node, click=True))
        if any(reading.get(name) != expected[name] for name in expected):
            raise RuntimeError('clan_battle_switch_readback_unknown')
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
        return dict(remaining_seconds=state['remaining_seconds'], state=state,
            same_frame=True, frame_pixel_sha256=state['frame_pixel_sha256'])

    def start_and_read(self, plan):
        from .clan_battle import execute_switches, require_timing
        ready = self.verify_team(plan)
        require_timing(self, plan)
        start = self.run('CBStart', click=True)
        hit = self.wait(['CBReadSettings', 'CBPage_result'], seconds=30)
        if hit != 'CBReadSettings':
            raise RuntimeError('clan_battle_settings_not_observed')
        initial = self.last_receipt
        settings = execute_switches(self, plan, initial=initial)
        self.wait(['CBPage_result'], seconds=180)
        result, result_receipt = self.read_page('result')
        require_target_identity(plan, result)
        return dict(ready, damage=result['damage'], result_verified=True, settings_verified=True,
            settings_evidence=settings, result_evidence=result_receipt,
            native_evidence=dict(start=start, ready=ready['native_evidence'], result=result_receipt))

    def return_to_target(self, plan):
        before, before_receipt = self.read_page('result')
        require_target_identity(plan, before)
        action = self.run('CBAction_return_to_target', click=True)
        self.wait(['CBPage_target'], seconds=15)
        target, target_receipt = self.read_page('target')
        require_target_identity(plan, target)
        if self.account != plan['account_id']:
            raise RuntimeError('clan_battle_account_mismatch')
        return dict(target, account_id=self.account, server=self.calibration['server'],
            return_verified=True, native_evidence=dict(before=before_receipt,
                action=action, target=target_receipt))
