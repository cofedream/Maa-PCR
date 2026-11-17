"""Guarded team-page equipment flow, adapted from wbero's b809b196 snapshot.

Code: CC BY-NC-SA 4.0. Template origins/rights: special_equipment/PROVENANCE.json.
Equipment confirmation is dispatched once; a missing result is never success.
"""
from functools import lru_cache
from pathlib import Path
import json
import time

import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition

from .. import native
from .batch import BatchStopped
from .scene import runtime_resource


NAME = 'LabyrinthSpecialEquipment'
READ = 'LabySpecialEquipmentObserved'
TAP = 'LabySpecialEquipmentTapOnce'
TEAM_GUARD = 'LabySpecialEquipmentTeamGuard'
ASSETS = Path(__file__).resolve().parents[3] / 'assets/resource/image/labyrinth/special_equipment'
RECTS = {
    'auto_button': (995, 922, 387, 71),
    'auto_dialog_anchor': (760, 56, 400, 58),
    'auto_dialog_confirm_button': (982, 917, 396, 91),
    'battle_team_button': (1180, 858, 97, 97),
    'dialog_anchor': (46, 42, 50, 58),
    'dialog_title': (835, 59, 264, 54),
    'equip_confirm_disabled': (1428, 917, 396, 81),
    'equip_confirm_enabled': (1428, 917, 396, 81),
}
MAIN_CANCEL = (101, 922, 387, 71)
CORE = (1487, 933, 276, 48)


def applies(strategy, kind):
    return (getattr(strategy, 'special_equipment_mode', 'off') == 'auto_equip'
        and kind in ('NORMAL', 'EX', 'BOSS')
        and kind in getattr(strategy, 'special_equipment_battles', ()))


def scaled(rect, image):
    height, width = image.shape[:2]
    x, y, w, h = rect
    left, top = round(x * width / 1920), round(y * height / 1080)
    return [left, top, round((x + w) * width / 1920) - left,
            round((y + h) * height / 1080) - top]


@lru_cache(maxsize=2)
def _templates(width, height):
    result = {}
    shape = np.empty((height, width, 3), dtype=np.uint8)
    for key, rect in RECTS.items():
        image = cv2.imdecode(np.fromfile(ASSETS / ('special_equipment_' + key + '.png'), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (rect[3], rect[2]):
            raise BatchStopped('special_equipment_resources_invalid')
        _, _, w, h = scaled(rect, shape)
        result[key] = cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
    return result


def recognize(image):
    """Layer identity and button evidence all come from this one image."""
    if (not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3
            or image.shape[0] < 720 or image.shape[1] < 1280
            or image.shape[1] * 9 != image.shape[0] * 16):
        return dict(stage=None, entry=None, confidence=0., enabled=None)
    templates = _templates(image.shape[1], image.shape[0])
    scores = {}
    for key, rect in RECTS.items():
        x, y, w, h = scaled(rect, image)
        score = float(cv2.matchTemplate(image[y:y+h, x:x+w], templates[key], cv2.TM_CCOEFF_NORMED)[0, 0])
        scores[key] = score if np.isfinite(score) else 0.
    auto = min(scores['auto_dialog_anchor'], scores['auto_dialog_confirm_button'])
    if auto >= .70:
        return dict(stage='auto', confidence=auto, enabled=None, entry=None)
    main = min(scores['dialog_anchor'], scores['dialog_title'], scores['auto_button'],
        max(scores['equip_confirm_enabled'], scores['equip_confirm_disabled']))
    if main >= .70:
        x, y, w, h = scaled(CORE, image)
        pixels = image[y:y+h:3, x:x+w:3].astype(float)
        blue, green, red = pixels.mean(axis=(0, 1))
        luminance = red * .2126 + green * .7152 + blue * .0722
        blue_minus_red = blue - red
        enabled = True if luminance >= 120 and blue_minus_red >= 101 else (
            False if luminance < 120 and blue_minus_red < 101 else None)
        return dict(stage='main', confidence=main, enabled=enabled, entry=None,
            luminance=luminance, blue_minus_red=blue_minus_red)
    score = scores['battle_team_button']
    return dict(stage=None, confidence=score, enabled=None,
        entry=scaled(RECTS['battle_team_button'], image) if score >= .70 else None)


class EquipmentRecognition(CustomRecognition):
    def analyze(self, context, argv):
        try:
            params = json.loads(argv.custom_recognition_param)
            if params.get('mode') not in ('read', 'tap') or context.tasker.stopping:
                return None
            observed = recognize(argv.image)
            observed['team_verified'] = False
            if observed['stage'] is None:
                result = context.run_recognition(TEAM_GUARD, argv.image)
                raw = result.raw_detail if result is not None and result.hit else None
                if type(raw) is str:
                    raw = json.loads(raw)
                detail = raw.get('best', {}).get('detail') if type(raw) is dict else None
                expected = params.get('expected_selected')
                verified = (type(detail) is dict and type(expected) is list
                    and expected == sorted(set(expected)) and 1 <= len(expected) <= 5
                    and detail.get('selected') == expected
                    and len(detail.get('members', ())) == len(expected)
                    and all(c.get('trusted') is True for c in detail['members'])
                    and (params.get('page') != 'boss' or detail.get('boss_tab') == params.get('boss_tab')))
                observed['team_verified'] = verified
                observed['team_stable'] = verified and detail.get('stable') is True
                cancel = context.run_recognition('LabySpecialEquipmentTeamCancel', argv.image) if verified else None
                box = list(cancel.box) if cancel is not None and cancel.hit and cancel.box is not None else None
                entry = observed['entry']
                if not (verified and box is not None and entry is not None
                        and entry[0]+entry[2]/2 < box[0]+box[2]/2
                        and abs(entry[1]+entry[3]/2-box[1]-box[3]/2) <= max(entry[3], box[3])/2):
                    observed['entry'] = None
            target = [0, 0, 1, 1]
            if params['mode'] == 'tap':
                tap = params.get('tap')
                if tap == 'entry' and observed['stage'] is None:
                    target = observed['entry']
                elif tap == 'auto' and observed['stage'] == 'main':
                    target = scaled(RECTS['auto_button'], argv.image)
                elif tap == 'auto_confirm' and observed['stage'] == 'auto':
                    target = scaled(RECTS['auto_dialog_confirm_button'], argv.image)
                elif tap == 'equip_confirm' and observed['stage'] == 'main' and observed['enabled'] is True:
                    target = scaled(RECTS['equip_confirm_enabled'], argv.image)
                elif tap == 'cancel' and observed['stage'] == 'main':
                    target = scaled(MAIN_CANCEL, argv.image)
                else:
                    return None
                if target is None:
                    return None
            if context.tasker.stopping:
                return None
            return self.AnalyzeResult(target, observed)
        except Exception:
            return None


def equipment_pipeline():
    from .scene import _anchors
    cancel = _anchors()['BATTLE_TEAM_CANCEL_BUTTON']
    return {
        READ: dict(recognition='Custom', custom_recognition=NAME, action='DoNothing',
            custom_recognition_param=dict(mode='unqualified'), next=[], timeout=1500, pre_delay=0, post_delay=0),
        TAP: dict(recognition='Custom', custom_recognition=NAME, action='Click', target=True,
            custom_recognition_param=dict(mode='unqualified'), max_hit=1, next=[], timeout=1500, pre_delay=0, post_delay=0),
        TEAM_GUARD: dict(recognition='Custom', custom_recognition='LabyrinthCharacters', action='DoNothing',
            custom_recognition_param=dict(mode='unqualified'), next=[], pre_delay=0, post_delay=0),
        'LabySpecialEquipmentTeamCancel': dict(recognition='TemplateMatch', template=cancel['template'],
            roi=cancel['rois'][0], threshold=.70, method=5, action='DoNothing', next=[], pre_delay=0, post_delay=0),
    }


class EquipmentRuntime:
    def __init__(self, tasker):
        self.tasker = tasker
        self.resource = runtime_resource(tasker)
        self.observer = EquipmentRecognition()
        if (not self.resource.override_pipeline(equipment_pipeline())
                or not self.resource.register_custom_recognition(NAME, self.observer)):
            raise BatchStopped('special_equipment_install_failed')
        self.rounds = {}
        self.last_normal_team = None
        self.last_action = -float('inf')

    def _run(self, params, *, tap=False):
        from .game_session import _root
        node = TAP if tap else READ
        if not self.resource.override_pipeline({node: dict(custom_recognition_param=params)}):
            if tap:
                raise native.NativeInputNotSent(dict(input_execution='not_sent', pre_input_validation_failed=True,
                    reason='special_equipment_guard_failed'))
            raise BatchStopped('special_equipment_guard_failed')
        receipt = native.run(self.tasker, node, reset_hits=(node,) if tap else ())
        if tap:
            native.require_native_input(receipt, (node,))
        root = _root(receipt, node)
        if tap:
            if root.get('action', {}).get('success') is not True:
                raise BatchStopped('special_equipment_input_unknown')
            return None, receipt
        detail = root.get('recognition', {}).get('best', {}).get('detail')
        if type(detail) is not dict or detail.get('stage') not in (None, 'main', 'auto'):
            raise BatchStopped('special_equipment_readback_unknown')
        return detail, receipt

    def equip(self, batch, stamp, enter_id, block_id, kind, desired, *, page, roster, boss_tab=None):
        strategy = getattr(batch.settings, 'strategy', None)
        if not applies(strategy, kind) or not desired:
            return False
        signature = tuple(sorted(desired))
        key = (enter_id, block_id, boss_tab, signature)
        if key in self.rounds:
            if self.rounds[key]:
                return False
            raise BatchStopped('special_equipment_already_attempted')
        self.rounds[key] = False
        def complete(performed):
            self.rounds[key] = True
            return performed
        if kind == 'NORMAL' and signature == self.last_normal_team:
            batch.emit_log('特别装备：本场编组与上一场普通战斗相同，跳过穿戴')
            return complete(False)
        params = dict(page=page, expected_selected=list(signature), boss_tab=boss_tab)
        guard = dict(mode='read', page=page, roster=list(roster), members_only=True)
        # The retained character callback owns its matcher; nested reads use the same frame.
        if not self.resource.override_pipeline({TEAM_GUARD: dict(custom_recognition_param=guard)}):
            raise BatchStopped('special_equipment_guard_failed')
        self.last_action = native.active_now()

        def read(deadline):
            batch.checkpoint()
            if native.active_now() >= deadline:
                raise BatchStopped('special_equipment_timeout')
            token = native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(), deadline))
            try:
                value, _ = self._run(dict(params, mode='read'))
                batch.checkpoint()
                if native.active_now() >= deadline:
                    raise BatchStopped('special_equipment_timeout')
                return value
            finally:
                native.RUN_DEADLINE.reset(token)

        def pause(deadline):
            batch.checkpoint()
            time.sleep(min(.05, max(0, deadline-native.active_now())))

        def send(tap, deadline):
            while native.active_now()-self.last_action < .6:
                if native.active_now() >= deadline:
                    raise BatchStopped('special_equipment_timeout')
                pause(deadline)
            fields = dict(enter_id=enter_id, block_id=block_id, equipment_action=tap,
                team=list(signature), **({'boss_tab': boss_tab} if boss_tab is not None else {}))
            def dispatch(oid):
                batch.checkpoint()
                token = native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(), deadline))
                try:
                    _, receipt = self._run(dict(params, mode='tap', tap=tap), tap=True)
                finally:
                    native.RUN_DEADLINE.reset(token)
                self.last_action = native.active_now()
                return stamp(operation_id=oid, source='maa', **fields, native=receipt,
                    input_native=receipt, input_action_verified=True)
            batch.transaction('special_equipment_'+tap, fields, dispatch)

        def deadline():
            return min(native.RUN_DEADLINE.get(), native.active_now()+8)

        def team_returned(limit):
            stable = 0
            while True:
                value = read(limit)
                stable = stable+1 if value.get('stage') is None and value.get('team_stable') is True else 0
                if stable >= 2 and native.active_now()-self.last_action >= .6:
                    return
                pause(limit)

        def abandon(value):
            batch.emit_log('特别装备：当前步骤超时，放弃本次穿戴')
            escape = deadline()
            value = read(escape)
            if value.get('stage') == 'main':
                send('cancel', escape)
                team_returned(escape)
                return complete(True)
            if value.get('team_verified') is True:
                return complete(True)
            raise BatchStopped('special_equipment_dialog_unresolved')

        limit = deadline()
        current = read(limit)
        if current.get('stage') is not None:
            raise BatchStopped('special_equipment_unowned_dialog')
        while not current.get('team_verified'):
            pause(limit)
            current = read(limit)
        if current.get('entry') is None:
            batch.emit_log('特别装备：未找到可信入口，跳过穿戴')
            return complete(False)
        batch.emit_log('特别装备：自动穿戴当前队伍')
        send('entry', limit)
        for expected, action, previous_tap in (('main', 'auto', 'entry'), ('auto', 'auto_confirm', 'auto')):
            limit = deadline()
            attempts = 1
            while True:
                try:
                    current = read(limit)
                except BatchStopped as error:
                    if str(error) != 'special_equipment_timeout':
                        raise
                    return abandon(current)
                if current.get('stage') == expected:
                    send(action, limit)
                    break
                unchanged = ((previous_tap == 'entry' and current.get('team_verified') is True)
                    or (previous_tap == 'auto' and current.get('stage') == 'main'))
                if unchanged and native.active_now()-self.last_action >= .6:
                    if attempts >= 3:
                        return abandon(current)
                    try:
                        send(previous_tap, limit)
                    except BatchStopped as error:
                        # The dialog can open between the read and the guarded retry.
                        if (error.args != ('native_input_not_sent',)
                                or not isinstance(error.__cause__, native.NativeInputNotSent)
                                or read(limit).get('stage') != expected):
                            raise
                        send(action, limit)
                        break
                    attempts += 1
                pause(limit)
        limit = deadline()
        observed_since = native.active_now()
        previous, frames = object(), 0
        attempts = 1
        while True:
            try:
                current = read(limit)
            except BatchStopped as error:
                if str(error) != 'special_equipment_timeout':
                    raise
                return abandon(current)
            if current.get('stage') == 'main':
                evidence = current.get('enabled')
                frames = frames+1 if evidence is previous else 1
                previous = evidence
                if frames >= 2 and native.active_now()-observed_since >= 1:
                    action = 'equip_confirm' if evidence is True else 'cancel'
                    send(action, limit)
                    try:
                        team_returned(deadline())
                    except BatchStopped as error:
                        if str(error) != 'special_equipment_timeout':
                            raise
                        # Confirm was already sent. Only a guarded cancel may escape.
                        if action == 'equip_confirm':
                            return abandon(current)
                        raise
                    batch.emit_log('特别装备：已穿戴，继续开战' if evidence is True else '特别装备：本次未变更，继续开战')
                    return complete(True)
            else:
                previous, frames = object(), 0
                if current.get('stage') == 'auto' and native.active_now()-self.last_action >= .6:
                    if attempts >= 3:
                        return abandon(current)
                    send('auto_confirm', limit)
                    attempts += 1
            pause(limit)


def equipment_runtime(tasker):
    resource = runtime_resource(tasker)
    runtime = getattr(resource, '_labyrinth_special_equipment', None)
    if runtime is None:
        runtime = EquipmentRuntime(tasker)
        resource._labyrinth_special_equipment = runtime
    return runtime
