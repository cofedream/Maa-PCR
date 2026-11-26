"""Calibrated clan formation reader, sharing the existing portrait and HUD checks."""
import hashlib
import json
import re
from pathlib import Path

import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition

from .guide_trial_characters import GuideTrialCharacters
from .frame_recognition import same_frame_context
from .labyrinth.character_recognition import load_character_pack


def box_inside_roi(box, roi):
    return (type(box) is list and len(box) == 4 and type(roi) in (list, tuple) and len(roi) == 4
        and all(type(value) is int for value in (*box, *roi))
        and box[2] > 0 and box[3] > 0 and roi[2] > 0 and roi[3] > 0
        and box[0] >= roi[0] and box[1] >= roi[1]
        and box[0] + box[2] <= roi[0] + roi[2] and box[1] + box[3] <= roi[1] + roi[3])


class ClanBattleCharacters(GuideTrialCharacters):
    def __init__(self, plan, calibration, page):
        # The deep-domain constructor also authorizes a particular source/stage.
        CustomRecognition.__init__(self)
        self.plan, self.team, self.calibration, self.page = plan, plan['unit_ids'], calibration, page
        entries, images, _ = load_character_pack()
        self.templates = {uid: [] for uid in self.team}
        self.carry_templates = {uid: [] for uid in self.team}
        for entry, rgb in zip(entries, images):
            uid = int(entry['character_id']) * 100 + 1
            if uid not in self.templates:
                continue
            for size in (124, 126, 128, *range(56, 65)):
                icon = cv2.resize(rgb[:, :, ::-1], (size, size))
                x, y, right, bottom = int(size*.12), int(size*.22), int(size*.82), int(size*.68)
                bank = self.carry_templates if size < 100 else self.templates
                bank[uid].append((icon[y:bottom, x:right].copy(), x, y, size))
        if any(not values for values in self.templates.values()):
            raise RuntimeError('clan_battle_character_resources_missing')
        root = Path(__file__).resolve().parents[2] / 'assets/resource/image/guide-trial'
        self.controls = {name: cv2.imdecode(np.frombuffer((root / f'battle-{name}.png').read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                         for name in ('auto-on', 'auto-off', 'set-on', 'set-off', 'set-badge', 'set-border-off')}
        if any(image is None for image in self.controls.values()):
            raise RuntimeError('clan_battle_control_resources_missing')
        self.badge_names = []
        for width in range(34, 49):
            name = f'set-badge-{width}'
            self.controls[name] = cv2.resize(self.controls['set-badge'], (width, width+1))
            self.badge_names.append(name)

    def match(self, frame, uid, roi):
        # List-card badge animation lowers portrait contrast; selected slots and HUD remain at .88.
        candidates = self.calibration.get('formation', {}).get('candidates', [])
        threshold = .85 if self.calibration.get('version') == 2 and list(roi) in candidates else .88
        return super().match(frame, uid, roi, threshold=threshold)

    def match_carry(self, frame, uid, roi):
        # Only the sampled compact inventory/selection portraits use these scales.
        x, y, w, h = roi
        best = None
        for template, tx, ty, size in self.carry_templates[uid]:
            _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(frame[y:y+h, x:x+w], template, cv2.TM_CCOEFF_NORMED))
            box = [x+point[0]-tx, y+point[1]-ty, size, size]
            if score >= .88 and box_inside_roi(box, roi) and (best is None or score > best['score']):
                best = dict(unit_id=uid, score=score, box=box)
        return best

    def formation(self, context, image):
        identity = self.page.read(context, image, 'formation')
        layout = self.calibration['formation']
        account_stars = None
        if layout.get('star_source') == 'account_snapshot':
            from .clan_battle import account_battle_stars
            account_stars = account_battle_stars(self.plan)
        members, stars, support, boxes = [], [], [], []
        occupied_count = 0
        for index, roi in enumerate(layout['slots']):
            hits = [row for uid in self.team if (row := self.match(image, uid, roi))]
            empty = self.page.hit(context, image, f'CBEmpty{index}')
            is_support = self.page.hit(context, image, f'CBSupport{index}')
            is_owned = (not empty and not is_support) if self.calibration.get('version') == 2 else self.page.hit(context, image, f'CBOwned{index}')
            occupied = is_support != is_owned
            if len(hits) > 1 or hits and empty or is_support and is_owned or empty == occupied:
                raise RuntimeError('clan_battle_formation_ambiguous')
            if not hits and not empty and not occupied:
                raise RuntimeError('clan_battle_formation_slot_unknown')
            members.append(hits[0]['unit_id'] if hits else None)
            boxes.append(hits[0]['box'] if hits else roi if occupied else None)
            if occupied:
                occupied_count += 1
                if account_stars is not None and hits and not is_support:
                    stars.append(account_stars[hits[0]['unit_id']])
                else:
                    values = [star for star in range(1, 7) if self.page.hit(context, image, f'CBStar{index}_{star}')]
                    stars.append(values[0] if len(values) == 1 else None)
                support.append(hits[0]['unit_id'] if hits and is_support else None)
            else:
                stars.append(None)
                support.append(None)
        chosen = [uid for uid in members if uid is not None]
        if len(set(chosen)) != len(chosen) or sum(uid is not None for uid in support) > 1:
            raise RuntimeError('clan_battle_formation_ambiguous')
        return dict(identity, unit_ids=members, stars=stars, member_count=occupied_count, boxes=boxes,
                    support_unit_id=next((uid for uid in support if uid is not None), None),
                    stars_source='account_snapshot' if account_stars is not None else 'screen')

    def support_candidates(self, context, image):
        identity = self.page.read(context, image, 'support')
        candidates = []
        for index, roi in enumerate(self.calibration['support']['slots']):
            hits = [row for uid in self.team if (row := self.match(image, uid, roi))]
            if len(hits) > 1:
                raise RuntimeError('clan_battle_support_ambiguous')
            if not hits:
                continue
            if not box_inside_roi(hits[0]['box'], roi):
                continue
            stars = [star for star in range(1, 7) if self.page.hit(context, image, f'CBSupportStar{index}_{star}')]
            if len(stars) != 1:
                continue
            identifier = self.page.word(context, image, f'CBSupportId{index}')
            available = self.page.hit(context, image, f'CBSupportAvailable{index}')
            unavailable = self.page.hit(context, image, f'CBSupportUnavailable{index}')
            if available == unavailable:
                continue
            candidates.append(dict(unit_id=hits[0]['unit_id'], stars=stars[0], support_id=identifier,
                                   available=available, box=hits[0]['box']))
        return dict(identity, candidates=candidates)

    def battle_control(self, context, argv, params):
        page = self.page.read(context, argv.image, 'battle')
        if type(page.get('remaining_seconds')) is not int or not 0 <= page['remaining_seconds'] <= 90:
            return None
        if 'expected_remaining_seconds' in params:
            target_second = params['expected_remaining_seconds']
            if (type(target_second) is not int or not 0 <= target_second <= 89
                    or page['remaining_seconds'] != target_second
                    or getattr(self, 'calibration', {}).get('version') == 2
                    and (type(page.get('battle_speed')) is not int or page['battle_speed'] != 1)):
                return None
        if 'opening_expected_seconds' in params and (
                type(params['opening_expected_seconds']) is not int
                or not 1 <= page['remaining_seconds'] <= params['opening_expected_seconds']):
            return None
        state = self.read_controls(argv.image)
        if (type(state.get('auto')) is not bool or len(state.get('sets', [])) != 5
                or any(type(value) is not bool for value in state['sets'])
                or set(state.get('unit_ids', [])) != set(self.team)
                or len(set(state['unit_ids'])) != 5):
            return None
        for key in ('auto', 'sets', 'unit_ids'):
            if key in params and state[key] != params[key]:
                return None
        if 'battle_speed' in params and page.get('battle_speed') != params['battle_speed']:
            return None
        mode, target = params['mode'], [0, 0, 1, 1]
        if mode == 'battle_auto':
            if (type(params.get('previous')) is not bool or type(params.get('desired')) is not bool
                    or params['previous'] is params['desired'] or state['auto'] is not params['previous']):
                return None
            target = [1190, 528, 55, 55]
        elif mode == 'battle_set':
            slot = params.get('slot')
            if (type(slot) is not int or not 0 <= slot < 5 or type(params.get('previous')) is not bool
                    or type(params.get('desired')) is not bool or params['previous'] is params['desired']
                    or state['sets'][slot] is not params['previous']):
                return None
            target = [280+160*slot, 560, 75, 65]
        elif mode != 'battle_read':
            return None
        if context.tasker.stopping:
            return None
        state.update(remaining_seconds=page['remaining_seconds'], same_frame=True, source='maa',
            frame_pixel_sha256=hashlib.sha256(argv.image.tobytes()).hexdigest())
        if getattr(self, 'calibration', {}).get('version') == 2:
            state['battle_speed'] = page.get('battle_speed')
        return self.AnalyzeResult(box=target, detail=state)

    def read_paused_controls(self, context, image, *, prefix, remaining_seconds, speeds=(1, 2, 4)):
        members = self.read_paused(image)
        ids = [row['unit_id'] if row else None for row in members]
        if len(set(ids)) != 5 or set(ids) != set(self.team):
            return None
        sets, evidence = [], []
        for i in range(5):
            hsv = cv2.cvtColor(image[286:346, 358+116*i:363+116*i], cv2.COLOR_BGR2HSV)
            cyan = float(((hsv[:,:,0]>=75)&(hsv[:,:,0]<=105)&(hsv[:,:,1]>=70)&(hsv[:,:,2]>=100)).mean())
            red = float((((hsv[:,:,0]<=15)|(hsv[:,:,0]>=170))&(hsv[:,:,1]>=70)&(hsv[:,:,2]>=100)).mean())
            badge = self.page.hit(context, image, prefix+'Set'+str(i))
            sets.append(True if badge and cyan >= .6 else False if not badge and cyan < .2 and red >= .6 else None)
            evidence.append(dict(badge=badge, cyan_border=cyan, red_border=red))
        auto = [value for value, label in ((True, 'on'), (False, 'off'))
                if self.page.hit(context, image, prefix+'_auto_'+label)]
        speeds = [speed for speed in speeds if self.page.hit(context, image, prefix+'Speed'+str(speed))]
        if any(value is None for value in sets) or len(auto) != 1 or len(speeds) != 1:
            return None
        state = dict(unit_ids=ids, sets=sets, auto=auto[0], battle_speed=speeds[0], paused=True,
            remaining_seconds=remaining_seconds,
            same_frame=True, source='maa', portrait_evidence=members, slot_evidence=evidence,
            frame_pixel_sha256=hashlib.sha256(image.tobytes()).hexdigest())
        return state

    def read_opening_clock(self, context, image, name, roi, alternate_roi=None):
        x, y, width, height = roi
        crop = image[y:y+height, x:x+width]
        scaled = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray_scaled = cv2.cvtColor(cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR), cv2.COLOR_GRAY2BGR)
        readings, seconds = [], set()
        variants = [(name, image, 'original'), (name+'Scaled', scaled, 'crop_2x_cubic'),
                    (name+'Scaled', gray_scaled, 'crop_gray_2x_linear')]
        if alternate_roi is not None:
            variants.append((name+'Alternate', image, 'alternate_roi_original'))
        for node, frame, transform in variants:
            evidence = dict(transform=transform, image_pixel_sha256=hashlib.sha256(frame.tobytes()).hexdigest())
            if transform == 'alternate_roi_original':
                evidence['roi'] = list(alternate_roi)
            try:
                text = self.page.word(context, frame, node, evidence=evidence)
                if re.fullmatch(r'(?:1[:：]?(?:[0-2][0-9]|30)|0[:：]?[0-5][0-9])', text) is None:
                    raise RuntimeError('clan_battle_opening_clock_unknown')
                digits = text.replace(':', '').replace('：', '')
                value = int(digits[0])*60 + int(digits[1:])
                evidence.update(text=text, remaining_seconds=value)
                seconds.add(value)
            except RuntimeError as exc:
                evidence['error'] = str(exc)
            readings.append(evidence)
        if len(seconds) != 1:
            raise RuntimeError('clan_battle_opening_clock_unknown')
        return seconds.pop(), dict(original_roi=list(roi),
            frame_pixel_sha256=hashlib.sha256(image.tobytes()).hexdigest(), readings=readings)

    def begin_opening(self, token, *, control_policy=None, owner_source=None,
            observation_scope_sha256=None, observation_calibration=None, start_seconds=None, action_kind=None, owner_valid=None):
        from copy import deepcopy
        from .clan_battle_opening_observation import OpeningObservation
        self.opening_token, self.opening_started, self.opening_origin = token, False, None
        self.opening_match_fallback = False
        self.opening_control_policy = deepcopy(control_policy)
        self.opening_owner_source = owner_source
        self.opening_start_seconds, self.opening_action_kind = start_seconds, action_kind
        self.opening_owner_valid = owner_valid
        # Callback images are anchors, not captures with trustworthy frame times.
        self.opening_observer = OpeningObservation.begin(token, list(self.team), owner_source=owner_source)
        self.opening_timed_observer = OpeningObservation.begin(token, list(self.team), owner_source=owner_source,
            scope_sha256=observation_scope_sha256, calibration=observation_calibration)

    def invalidate_opening(self, reason):
        self.opening_started = False
        for name in ('opening_observer', 'opening_timed_observer'):
            observer = getattr(self, name, None)
            if observer is not None:
                observer.invalidate(reason)

    def record_opening_frame(self, image, phase, *, capture=None, events=None):
        observer = getattr(self, 'opening_observer', None)
        timed = getattr(self, 'opening_timed_observer', None)
        if timed is not None and capture is not None:
            timed.feed(hashlib.sha256(image.tobytes()).hexdigest(), phase,
                owner_source=self.opening_owner_source, capture=capture, events=events)
        if observer is not None:
            return observer.retain_anchor(image, phase, owner_source=self.opening_owner_source)

    def _opening_rule_owner_valid(self):
        try:
            observer = getattr(self, 'opening_observer', None)
            check = getattr(self, 'opening_owner_valid', None)
            return (self.opening_owner_source is not None and observer is not None
                and observer.owner_source == self.opening_owner_source and callable(check) and check() is True)
        except Exception:
            return False

    def bind_paused_opening(self, state, mode, *, image=None):
        from .clan_battle import USER_CONFIRMED_OPENING_RULE
        carryover = getattr(self, 'opening_action_kind', None) == 'carryover'
        if carryover and (not getattr(self, 'opening_started', False)
                or state.get('formal') is not True or state.get('intent_id') != self.opening_token):
            return None
        origin = getattr(self, 'opening_origin', None)
        if origin is None:
            if mode != 'opening_read':
                return None
            self.opening_origin = origin = dict(state)
            policy = getattr(self, 'opening_control_policy', None)
            if getattr(self, 'opening_started', False):
                origin['opening_start_token'] = self.opening_token
                if carryover:
                    origin['opening_action_kind'] = 'carryover'
                observer = getattr(self, 'opening_observer', None)
                if observer is not None and image is not None and hashlib.sha256(image.tobytes()).hexdigest() == state['frame_pixel_sha256']:
                    state['opening_anchor'] = observer.retain_anchor(image, 'paused', owner_source=self.opening_owner_source)
                timed = getattr(self, 'opening_timed_observer', None)
                if timed is not None:
                    origin['first_ub_observation'] = timed.pending(self.opening_token,
                        state['frame_pixel_sha256'], owner_source=self.opening_owner_source,
                        battle_speed=state.get('battle_speed'))
            if (getattr(self, 'opening_started', False) and type(policy) is dict
                    and policy.get('auto_release_on_ub') is False
                    and 'first_ub_evidence' not in origin):
                origin['first_ub_evidence'] = dict(source='controls_disabled_since_start',
                    start_token=self.opening_token, initial_frame_pixel_sha256=state['frame_pixel_sha256'],
                    control_policy=policy,
                    not_fired_unit_ids=[uid for uid, enabled in zip(state['unit_ids'], state['sets'])
                        if state['auto'] is False and enabled is False])
            # User-confirmed 90s rule; this is independent of capture qualification.
            if (getattr(self, 'opening_started', False) and self._opening_rule_owner_valid()
                    and type(self.opening_token) is str and bool(self.opening_token)
                    and type(self.opening_start_seconds) is int and self.opening_start_seconds == 90
                    and self.opening_action_kind in ('simulation', 'regular')
                    and state.get('paused') is True and state.get('same_frame') is True and state.get('source') == 'maa'
                    and type(state.get('remaining_seconds')) is int and 84 < state['remaining_seconds'] <= 90
                    and type(state.get('unit_ids')) is list and len(state['unit_ids']) == 5
                    and all(type(uid) is int for uid in state['unit_ids']) and len(set(state['unit_ids'])) == 5
                    and set(state['unit_ids']) == set(self.team)
                    and image is not None and image.shape == (720, 1280, 3)
                    and hashlib.sha256(image.tobytes()).hexdigest() == state.get('frame_pixel_sha256')):
                origin.update(opening_start_seconds=90, opening_action_kind=self.opening_action_kind,
                    opening_owner_bound=True, opening_initial_frame_pixel_sha256=state['frame_pixel_sha256'])
                origin['first_ub_evidence'] = dict(source=USER_CONFIRMED_OPENING_RULE, basis='user_confirmed_game_rule',
                    start_token=self.opening_token, initial_frame_pixel_sha256=state['frame_pixel_sha256'],
                    owner_bound=True, first_pause=True, start_seconds=90, action_kind=self.opening_action_kind,
                    remaining_seconds=state['remaining_seconds'], unit_ids=list(state['unit_ids']),
                    not_fired_unit_ids=list(state['unit_ids']))
        if origin.get('first_ub_evidence', {}).get('source') == USER_CONFIRMED_OPENING_RULE:
            if (not getattr(self, 'opening_started', False) or not self._opening_rule_owner_valid()
                    or self.opening_token != origin.get('opening_start_token')
                    or self.opening_start_seconds != origin.get('opening_start_seconds')
                    or self.opening_action_kind != origin.get('opening_action_kind')):
                return None
        if (not carryover and state['remaining_seconds'] != origin['remaining_seconds']
                or carryover and (origin.get('opening_start_token') != self.opening_token
                    or origin.get('opening_action_kind') != 'carryover')
                or state['unit_ids'] != origin['unit_ids']):
            return None
        for key in ('opening_start_token', 'first_ub_evidence', 'first_ub_observation', 'opening_start_seconds',
                'opening_action_kind', 'opening_owner_bound', 'opening_initial_frame_pixel_sha256'):
            if key in origin:
                state[key] = origin[key]
        return state

    def opening_control(self, context, argv, params):
        spec = self.calibration.get('auto_battle')
        mode = params['mode']
        if not spec or self.plan.get('switches') or self.page.current_page != 'battle':
            return None
        # Reject a still-running HUD before running two OCR checks on it.
        if not self.page.hit(context, argv.image, 'CBOpening_paused_guards_1'):
            return None
        if self.page.hit(context, argv.image, 'LabyDisconnect6002Observed'):
            return None
        if not self.page.hit(context, argv.image, 'CBOpening_paused_guards_0'):
            return None
        if not self.page.hit(context, argv.image, 'CBOpeningBoss'):
            return None
        actual, clock_evidence = self.read_opening_clock(context, argv.image, 'CBOpeningClock', spec['paused_guards'][2]['roi'],
            spec.get('paused_clock_alternate_roi'))
        if not 1 <= actual <= 90:
            return None
        state = self.read_paused_controls(context, argv.image, prefix='CBOpening', remaining_seconds=actual)
        if state is None:
            return None
        state['clock_evidence'] = clock_evidence
        state = self.bind_paused_opening(state, mode, image=argv.image)
        if state is None:
            return None
        if mode != 'opening_read' and not all(key in params for key in ('unit_ids', 'sets', 'auto', 'battle_speed')):
            return None
        if ('battle_speed' in params and type(params['battle_speed']) is not int
                or 'remaining_seconds' in params and type(params['remaining_seconds']) is not int):
            return None
        if any(key in params and state[key] != params[key] for key in ('unit_ids', 'sets', 'auto', 'battle_speed', 'remaining_seconds')):
            return None
        # This menu layout is calibrated with the 1280x720 clan HUD; every click is read back while paused.
        target = [0, 0, 1, 1]
        if mode in ('opening_auto', 'opening_set', 'opening_speed'):
            from .clan_battle import require_first_ub
            slot = params.get('slot')
            affected = ([state['unit_ids'][slot]] if mode == 'opening_set' and type(slot) is int and 0 <= slot < 5
                else state['unit_ids'])
            require_first_ub(state, affected)
        if mode == 'opening_auto':
            if (type(params.get('previous')) is not bool or type(params.get('desired')) is not bool
                    or params['previous'] is params['desired'] or state['auto'] is not params['previous']):
                return None
            target = [634, 473, 6, 6]
        elif mode == 'opening_set':
            slot = params.get('slot')
            if (type(slot) is not int or not 0 <= slot < 5 or type(params.get('previous')) is not bool
                    or type(params.get('desired')) is not bool or params['previous'] is params['desired']
                    or state['sets'][slot] is not params['previous']):
                return None
            target = [403+116*slot, 311, 6, 6]
        elif mode == 'opening_speed':
            target = [830, 473, 6, 6]
        elif mode == 'opening_resume':
            if not all(key in params for key in ('unit_ids', 'sets', 'auto', 'battle_speed')):
                return None
            target = [442, 574, 6, 6]
        elif mode != 'opening_read':
            return None
        if context.tasker.stopping:
            return None
        return self.AnalyzeResult(box=target, detail=state)

    def analyze(self, context, argv):
        try:
            if context.tasker.stopping or argv.image is None or argv.image.shape != (720, 1280, 3):
                return None
            context = same_frame_context(context, argv.image)
            params = json.loads(argv.custom_recognition_param)
            mode = params.get('mode')
            if mode == 'opening_observe':
                anchor = self.record_opening_frame(argv.image, 'battle')
                return self.AnalyzeResult(box=[0, 0, 1, 1],
                    detail=dict(capture_interval='unknown', source='owner_callback_image', opening_anchor=anchor))
            if mode == 'opening_match':
                if (not getattr(self, 'opening_started', False) or self.plan.get('switches')
                        or getattr(self, 'opening_match_fallback', False)
                        or params.get('opening_start_token') != getattr(self, 'opening_token', None)
                        or type(params.get('battle_speed')) is not int or params['battle_speed'] != 2
                        or type(params.get('start_seconds')) is not int or not 1 <= params['start_seconds'] <= 90):
                    return None
                result = self.battle_control(context, argv, dict(mode='battle_read',
                    opening_expected_seconds=params['start_seconds']))
                if result is None:
                    return None
                state = dict(result.detail)
                if context.tasker.stopping:
                    return None
                if (type(state.get('battle_speed')) is not int or state['battle_speed'] != 2
                        or state.get('auto') is not self.plan.get('auto')
                        or {str(uid): value for uid, value in zip(state['unit_ids'], state['sets'])}
                            != self.plan.get('set_by_unit')):
                    # A later HUD cannot erase an observed difference or unknown speed in this Start.
                    self.opening_match_fallback = True
                    return None
                # This same-frame match needs no input and is not first-UB evidence.
                state.update(opening_direct=True, opening_start_token=self.opening_token, paused=False)
                return self.AnalyzeResult(box=[0, 0, 1, 1], detail=state)
            if mode == 'pause_retry':
                if (not getattr(self, 'opening_started', False) or self.page.current_page != 'battle'
                        or self.page.hit(context, argv.image, 'LabyDisconnect6002Observed')
                        or any(self.page.hit(context, argv.image, f'CBOpening_paused_guards_{i}') for i in range(2))
                        or context.tasker.stopping):
                    return None
                # This check must not replace the battle anchor recorded before the first click.
                return self.AnalyzeResult(box=[0, 0, 1, 1], detail={'opening_start_token': self.opening_token})
            if str(mode).startswith('opening_'):
                return self.opening_control(context, argv, params)
            if str(mode).startswith('battle_'):
                if mode != 'battle_read':
                    self.invalidate_opening('unexpected_control_before_pause')
                return self.battle_control(context, argv, params)
            if mode in ('support_read', 'support_add'):
                detail = self.support_candidates(context, argv.image)
                target = [0, 0, 1, 1]
                if mode == 'support_add':
                    chosen = [row for row in detail['candidates'] if row['support_id'] == params.get('support_id')
                              and row['unit_id'] == params.get('unit_id') and row['stars'] == params.get('stars')
                              and row['available'] is True]
                    if len(chosen) != 1:
                        return None
                    target = chosen[0]['box']
                    if not any(box_inside_roi(target, roi) for roi in self.calibration['support']['slots']):
                        return None
            else:
                detail = self.formation(context, argv.image)
                target = [0, 0, 1, 1]
                if mode == 'clear':
                    if detail['member_count'] != params.get('count') or not detail['member_count']:
                        return None
                    target = next(box for box in reversed(detail['boxes']) if box)
                elif mode == 'add':
                    uid = params.get('unit_id')
                    if uid not in self.team or uid in detail['unit_ids'] or detail['member_count'] >= 5:
                        return None
                    hits = [(row, roi) for roi in self.calibration['formation']['candidates']
                            if (row := self.match(argv.image, uid, roi))]
                    if len(hits) != 1 or not box_inside_roi(hits[0][0]['box'], hits[0][1]):
                        return None
                    target = hits[0][0]['box']
                elif mode == 'start':
                    self.page.require_team(detail)
                    token = getattr(self, 'opening_token', None)
                    if token is not None and params.get('opening_start_token') != token:
                        return None
                    target = self.page.action(context, argv.image, 'start')
                    if token is not None:
                        detail['opening_anchor'] = self.record_opening_frame(argv.image, 'before_start')
                        self.opening_started = True
                    if self.calibration.get('auto_battle') and not self.plan['switches']:
                        self.page.current_page, self.page.expected_page = 'battle', None
                elif mode != 'read':
                    return None
            if mode in ('clear', 'add', 'support_add'):
                x, y, width, height = target
                target = [x+width//2-3, y+height//2-3, 6, 6]
            if context.tasker.stopping:
                return None
            return self.AnalyzeResult(box=target, detail=detail)
        except Exception:
            return None
