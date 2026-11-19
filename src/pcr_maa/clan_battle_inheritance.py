"""Preserve battle inheritance and reconcile only read-back legacy restore records."""
from copy import deepcopy
import hashlib
import json
import re

import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition

from . import native
from .frame_recognition import same_frame_context
from .preset_execution import Journal


OPERATION = 'clan_battle_inheritance'


def inheritance_pipeline(spec):
    from .clan_battle_native import _node
    if (type(spec) is not dict or set(spec) - {'ub_auto_clear'} != {'battle_selected', 'radio_on', 'radio_off', 'samples'}
            or any(type(spec[key]) is not dict or spec[key].get('recognition') != 'TemplateMatch'
                   for key in ('battle_selected', 'radio_on', 'radio_off'))):
        raise ValueError('clan_battle_inheritance_calibration_invalid')
    nodes = {'CBInheritance'+name: _node(spec[key]) for name, key in (
        ('BattleSelected', 'battle_selected'), ('RadioOn', 'radio_on'), ('RadioOff', 'radio_off'))}
    for name, expected, roi in (
            ('SettingsTitle', '^设定$', [575, 28, 135, 60]),
            ('SettingsEntry', '^设定$', [180, 235, 115, 70]),
            ('BattleLabel', '^战斗$', [445, 90, 105, 65]),
            ('AllLabel', '^所有$', [130, 185, 95, 65]),
            ('AllExpanded', '^连结爆发立即发动模式的设定$', [40, 270, 550, 50]),
            ('Description', '^自动战斗[、，,]连结爆发立即发动模式和战斗速度[。.]?$', [40, 160, 1135, 370]),
            ('OnLabel', '^继承$', [620, 180, 140, 395]),
            ('OffLabel', '^无法继承$', [930, 180, 180, 395]),
            ('CloseLabel', '^关闭$', [565, 605, 160, 70])):
        nodes['CBInheritance'+name] = _node(dict(roi=roi, expected=expected))
    if 'ub_auto_clear' in spec:
        policy = spec['ub_auto_clear']
        if (type(policy) is not dict or set(policy) != {'description', 'on_label', 'off_label'}
                or any(type(policy[key]) is not dict or policy[key].get('recognition') != 'OCR'
                    for key in policy)):
            raise ValueError('clan_battle_ub_auto_clear_calibration_invalid')
        for name, key in (('Description', 'description'), ('OnLabel', 'on_label'), ('OffLabel', 'off_label')):
            nodes['CBSetReset'+name] = _node(policy[key])
        nodes['CBSetResetRead'] = dict(recognition='Custom', custom_recognition='ClanBattleInheritance',
            custom_recognition_param='read_ub_auto_clear', action='DoNothing', next=[],
            timeout=1000, pre_delay=0, post_delay=0)
    base = ['CBInheritanceSettingsTitle', 'CBInheritanceCloseLabel']
    nodes['CBInheritanceSettingsObserved'] = dict(recognition='And', all_of=base,
        action='DoNothing', next=[], timeout=1000, pre_delay=0, post_delay=0)
    for name, guards, target in (
            ('Open', ['CBInheritanceSettingsEntry', 'LabyClientMainMenuTitle', 'LabyClientMainMenuProfileLabel'], True),
            ('Battle', ['CBInheritanceBattleLabel', *base], True),
            ('Expand', ['CBInheritanceAllLabel', *base, 'CBInheritanceBattleSelected'], True),
            ('Close', ['CBInheritanceCloseLabel', 'CBInheritanceSettingsTitle'], True)):
        nodes['CBInheritance'+name] = dict(recognition='And', all_of=guards, action='Click', target=target,
            next=[], max_hit=1, timeout=3000, pre_delay=0, post_delay=500)
    nodes['CBInheritanceDown'] = dict(recognition='And', all_of=[*base, 'CBInheritanceBattleSelected'],
        action='Swipe', begin=[1180, 555], end=[1180, 335], duration=500,
        next=[], max_hit=1, timeout=3000, pre_delay=0, post_delay=700)
    for name, mode in (('Read', 'read'), ('Enable', 'enable'), ('Disable', 'disable')):
        nodes['CBInheritance'+name] = dict(recognition='Custom', custom_recognition='ClanBattleInheritance',
            custom_recognition_param=mode, action='DoNothing' if mode == 'read' else 'Click',
            target=True, next=[], timeout=3000, pre_delay=0, post_delay=500 if mode != 'read' else 0)
        if mode != 'read':
            nodes['CBInheritance'+name]['max_hit'] = 1
    return nodes


class InheritancePage(CustomRecognition):
    def __init__(self, spec, reader):
        super().__init__()
        self.reader = reader
        self.ub_auto_clear = 'ub_auto_clear' in spec
        self.radios = {state: cv2.imdecode(np.frombuffer(
            (native.RESOURCE/'image'/spec['radio_'+state]['template']).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                       for state in ('on', 'off')}
        if any(image is None or image.shape != (18, 18, 3) for image in self.radios.values()):
            raise ValueError('clan_battle_inheritance_radio_invalid')

    def read(self, context, image, *, prefix='CBInheritance', field='inherit'):
        if context.tasker.stopping or image is None or image.shape != (720, 1280, 3):
            raise RuntimeError('clan_battle_inheritance_unknown')
        for name in ('SettingsTitle', 'BattleSelected'):
            if not self.reader.hit(context, image, 'CBInheritance'+name):
                raise RuntimeError('clan_battle_inheritance_unknown')
        self.reader.word(context, image, prefix+'Description')
        boxes = []
        for name in ('OnLabel', 'OffLabel'):
            self.reader.word(context, image, prefix+name)
            boxes.append(list(context.run_recognition(prefix+name, image).box))
        left, right = boxes
        if abs(left[1]+left[3]/2-right[1]-right[3]/2) > 5 or not 250 < right[0]-left[0] < 360:
            raise RuntimeError('clan_battle_inheritance_unknown')
        if prefix == 'CBSetReset':
            description = list(context.run_recognition(prefix+'Description', image).box)
            if not 30 < left[1]+left[3]/2-description[1]-description[3] < 140:
                raise RuntimeError('clan_battle_inheritance_unknown')
        centers = [(box[0]-39, int(box[1]+box[3]/2)) for box in boxes]
        states = []
        for x, y in centers:
            patch = image[y-17:y+18, x-17:x+18]
            scores = {key: float(cv2.minMaxLoc(cv2.matchTemplate(patch, template, cv2.TM_SQDIFF_NORMED))[0])
                      for key, template in self.radios.items()}
            matches = [key for key, score in scores.items() if score < .025]
            if len(matches) != 1:
                raise RuntimeError('clan_battle_inheritance_unknown')
            states.append(matches[0])
        if states not in (['on', 'off'], ['off', 'on']):
            raise RuntimeError('clan_battle_inheritance_unknown')
        return dict({field: states[0] == 'on'}, same_frame=True,
            frame_pixel_sha256=hashlib.sha256(image.tobytes()).hexdigest()), centers

    def analyze(self, context, argv):
        try:
            mode = json.loads(argv.custom_recognition_param)
            context = same_frame_context(context, argv.image)
            if mode == 'read_ub_auto_clear':
                if not self.ub_auto_clear:
                    return None
                reading, _ = self.read(context, argv.image, prefix='CBSetReset', field='auto_release_on_ub')
                return self.AnalyzeResult(box=[0, 0, 1, 1], detail=reading)
            reading, centers = self.read(context, argv.image)
            if mode not in ('read', 'enable', 'disable') or mode != 'read' and reading['inherit'] is (mode == 'enable'):
                return None
            x, y = centers[0 if mode == 'enable' else 1]
            return self.AnalyzeResult(box=[x-3, y-3, 6, 6], detail=reading)
        except Exception:
            return None


class BattleInheritance:
    def __init__(self, port, journal):
        self.port, self.journal = port, journal
        self.intent = None
        self.evidence = None

    def _pending(self):
        events = self.journal.events()
        for index, intent in enumerate(events[::2]):
            if (intent.get('operation') != OPERATION or type(intent.get('account_id')) is not str
                    or re.fullmatch('[0-9]{1,19}', intent['account_id']) is None
                    or intent.get('original') is not True):
                raise RuntimeError('clan_battle_inheritance_ledger_invalid')
            if 2*index+1 == len(events):
                if intent['account_id'] != self.port.account:
                    raise RuntimeError('clan_battle_inheritance_pending_other_account')
                return intent
            result = events[2*index+1]
            if (any(result.get(key) != intent.get(key) for key in ('intent_id', 'operation', 'account_id', 'original'))
                    or result.get('restored') is not True):
                raise RuntimeError('clan_battle_inheritance_ledger_invalid')
        return None

    def _restore(self, intent):
        state = self.port.read_inheritance()
        if state['inherit'] is not intent['original']:
            # A later human choice cannot be distinguished from our old temporary disable.
            raise RuntimeError('clan_battle_inheritance_user_state_confirmation_required')
        self.journal.append(dict(intent, state='settled', restored=True, restore_evidence=state))
        return state

    def recover(self):
        with self.journal.locked():
            pending = self._pending()
            if pending is None:
                return None
            self.intent = pending
            self.evidence = dict(original=pending['original'], restored=False)
            state = self._restore(pending)
            self.evidence.update(restored=True, restore_evidence=deepcopy(state))
            self.intent = None
            return deepcopy(self.evidence)

    def prepare(self):
        return self.recover()

    def restore(self):
        if self.intent is not None:
            with self.journal.locked():
                if self._pending() != self.intent:
                    raise RuntimeError('clan_battle_inheritance_ledger_changed')
                state = self._restore(self.intent)
                self.evidence.update(restored=True, restore_evidence=deepcopy(state))
                self.intent = None
        return deepcopy(self.evidence)

    @classmethod
    def for_port(cls, port):
        return cls(port, Journal(native.ROOT/'.runtime/clan-battle-simulation-work/inheritance'))
