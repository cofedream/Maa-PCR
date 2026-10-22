"""Same-frame portrait checks for the sampled CN deep-domain formation layout."""
import json
import cv2
from pathlib import Path
import numpy as np
from maa.custom_recognition import CustomRecognition
from .guide_trial import TEAM, STARS, correct_team, team_matches
from .labyrinth.character_recognition import load_character_pack, member_slots


class GuideTrialCharacters(CustomRecognition):
    def __init__(self, stage='红焰深域 7-3', *, plan=None):
        super().__init__()
        self.stage = stage
        from .guide_identity import read_source, require_source_team
        source = require_source_team(plan) if plan is not None else read_source(stage)
        self.plan = plan or dict(unit_ids=list(TEAM), stars=list(STARS))
        self.team = self.plan['unit_ids']
        self.source_ids = {m['UnitId'] for m in source['Members']}
        if self.source_ids != set(self.team):
            raise RuntimeError('guide_source_plan_mismatch')
        entries, images, _ = load_character_pack()
        self.templates = {uid: [] for uid in self.team}
        for entry, rgb in zip(entries, images):
            uid = int(entry['character_id']) * 100 + 1
            if uid not in self.templates:
                continue
            # Cropped interior avoids level, check mark, stars and attribute overlays.
            for size in (124,126,128):
                icon = cv2.resize(rgb[:, :, ::-1], (size, size))
                x, y, right, bottom = int(size*.12), int(size*.22), int(size*.82), int(size*.68)
                self.templates[uid].append((icon[y:bottom, x:right].copy(), x, y, size))
        if any(not templates for templates in self.templates.values()):
            raise RuntimeError('guide_trial_character_resources_missing')
        image_root = Path(__file__).resolve().parents[2] / 'assets/resource/image/guide-trial'
        self.controls = {name: cv2.imdecode(np.frombuffer((image_root / f'battle-{name}.png').read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                         for name in ('auto-on','auto-off','set-on','set-off','set-badge','set-border-off')}
        if any(image is None for image in self.controls.values()):
            raise RuntimeError('guide_trial_control_resources_missing')
        # Saved frames cover 38–43px; a bounded pulse range keeps the confidence/ring gates unchanged.
        self.badge_names = []
        for width in range(34,49):
            name = f'set-badge-{width}'
            self.controls[name] = cv2.resize(self.controls['set-badge'], (width,width+1))
            self.badge_names.append(name)

    def read_controls(self, frame):
        if frame is None or frame.shape != (720,1280,3):
            raise ValueError('guide_trial_control_layout_unknown')
        def score(name, roi):
            x,y,w,h = roi
            return cv2.minMaxLoc(cv2.matchTemplate(frame[y:y+h,x:x+w], self.controls[name], cv2.TM_CCOEFF_NORMED))[1]
        def button(name, y):
            on,off = (score(name+'-'+state, (1196,y+16,48,48)) for state in ('on','off'))
            return True if on >= .9 and on-off >= .12 else False if off >= .9 and off-on >= .12 else None
        slots, evidence, members = [], [], []
        # shortcut: calibrated five-slot 1280x720 battle HUD; other layouts require new saved-frame checks.
        for i in range(5):
            badge = max(score(name, (350+160*i,500,65,65)) for name in self.badge_names)
            strip = cv2.cvtColor(frame[545:620,255+160*i:264+160*i], cv2.COLOR_BGR2HSV)
            ring = float(((strip[:,:,0]>=75)&(strip[:,:,0]<=105)&(strip[:,:,1]>=70)&(strip[:,:,2]>=100)).mean())
            off = score('set-border-off', (255+160*i,545,10,75))
            slots.append(True if badge >= .88 and ring >= .6 else False if badge < .65 and ring < .2 and off >= .85 else None)
            evidence.append(dict(badge=badge, ring=ring, plain_border=off))
            matches = [m for uid in self.team if (m := self.match(frame,uid,(255+160*i,522,135,135)))]
            members.append(matches[0] if len(matches)==1 else None)
        ids=[m['unit_id'] if m else None for m in members]
        return dict(auto=button('auto',516), set_button=button('set',419), sets=slots, slot_evidence=evidence,
            unit_ids=ids, portrait_evidence=members)

    def battle_control(self, context, argv, params):
        if (not context.run_recognition('GTBattleMenu', argv.image).hit
                or not context.run_recognition('GTBattleTimer', argv.image).hit
                or context.run_recognition('GTPauseTitle', argv.image).hit):
            return None
        state = self.read_controls(argv.image)
        if state['auto'] is None or any(value is None for value in state['sets']):
            return None
        if (not correct_team(state.get('unit_ids'), self.team)
                or 'unit_ids' in params and state['unit_ids'] != params['unit_ids']
                or 'sets' in params and state['sets'] != params['sets']
                or 'auto' in params and state['auto'] is not params['auto']):
            return None
        mode = params['mode'];target = [0,0,1,1]
        if mode == 'battle_read':
            if ('auto' in params and state['auto'] is not params['auto']
                    or 'sets' in params and state['sets'] != params['sets']):
                return None
        elif mode == 'battle_auto':
            if state['auto'] is not False:return None
            target = [1190,528,55,55]
        elif mode == 'battle_all_set':
            if state['auto'] is not True or state['set_button'] is not False or any(state['sets']):return None
            target = [1190,432,55,55]
        elif mode == 'battle_set':
            slot = params.get('slot');previous = params.get('previous')
            if (type(slot) is not int or not 0 <= slot < 5 or type(previous) is not bool
                    or state['auto'] is not True or state['sets'][slot] is not previous):return None
            target = [280+160*slot,560,75,65]
        else:return None
        if context.tasker.stopping:return None
        return self.AnalyzeResult(box=target, detail=state)

    def match(self, frame, uid, roi):
        x, y, w, h = roi
        best = None
        for template, tx, ty, size in self.templates[uid]:
            scores = cv2.matchTemplate(frame[y:y+h, x:x+w], template, cv2.TM_CCOEFF_NORMED)
            _, score, _, point = cv2.minMaxLoc(scores)
            box = [x+point[0]-tx, y+point[1]-ty, size, size]
            if score >= .88 and (best is None or score > best['score']):
                best = dict(unit_id=uid, score=score, box=box)
        return best

    def read(self, frame):
        members = []
        for index in range(5):
            roi = (55+146*index, 535, 144, 145)
            matches = [value for uid in self.team if (value := self.match(frame, uid, roi))]
            if len(matches) > 1:
                raise RuntimeError('guide_trial_portrait_ambiguous')
            members.append(matches[0] if matches else None)
        return members

    def read_paused(self, frame):
        # The sampled pause menu uses smaller portraits; reuse the calibrated face interiors.
        frame = cv2.resize(frame, None, fx=1.3, fy=1.3)
        members = []
        for index in range(5):
            roi = tuple(int(v*1.3) for v in (354+116*index, 262, 110, 105))
            matches = [value for uid in self.team if (value := self.match(frame, uid, roi))]
            members.append(matches[0] if len(matches) == 1 else None)
        return members

    def analyze(self, context, argv):
        try:
            if context.tasker.stopping:
                return None
            params = json.loads(argv.custom_recognition_param)
            if str(params.get('mode','')).startswith('battle_'):
                return self.battle_control(context, argv, params)
            if params.get('mode') == 'open_stage':
                if self.stage not in ('红焰深域 7-4', '红焰深域 7-5', '红焰深域 8-2', '红焰深域 8-3', '红焰深域 8-4', '红焰深域 8-5', '红焰深域 8-6', '红焰深域 8-8') or not context.run_recognition('GTFireMap', argv.image).hit:
                    return None
                number = self.stage.split()[-1].replace('-', '')
                label = context.run_recognition(f'GTStage{number}Label', argv.image)
                marker = context.run_recognition(f'GTStage{number}Next', argv.image)
                if not label.hit or not marker.hit:
                    return None
                x,y,w,h = label.box; nx,ny,nw,nh = marker.box
                # Reopening the map recenters it; NEXT must still belong to this exact label.
                if abs(x+w/2-nx-nw/2) > 25 or not 175 <= y-ny <= 235 or context.tasker.stopping:
                    return None
                return self.AnalyzeResult(box=list(label.box), detail=dict(stage=self.stage, uncleared=True))
            for node in ('LabyTeamTitle', 'LabyTeamMembersLabel', 'LabyTeamBattleStartLabel'):
                if not context.run_recognition(node, argv.image).hit:
                    return None
            members = self.read(argv.image)
            ids = [row['unit_id'] if row else None for row in members]
            boxes = member_slots(argv.image)
            detail = dict(unit_ids=ids, members=members, member_count=len(boxes))
            stars = []
            if correct_team(ids, self.team):
                expected_stars = dict(zip(self.plan['unit_ids'], self.plan['stars']))
                for i in range(5):
                    # Avoid twenty OCR calls per frame while five/six-star badges rotate to EX.
                    candidates = (5,6) if expected_stars[ids[i]] >= 5 else range(1,7)
                    hits = [star for star in candidates if context.run_recognition(f'GTStar{i+1}_{star}',argv.image).hit]
                    stars.append(hits[0] if len(hits)==1 else None)
            detail['stars'] = stars
            target = [0, 0, 1, 1]
            mode = params.get('mode')
            if mode == 'clear':
                if not boxes or len(boxes) != params.get('count'):
                    return None
                target = list(boxes[-1])
            elif mode == 'add':
                uid = params.get('unit_id')
                if uid not in self.team or uid in ids or len(boxes) >= 5:
                    return None
                found = self.match(argv.image, uid, (65, 155, 1145, 340))
                if not found or found['box'][1] < 155 or found['box'][1]+found['box'][3] > 495:
                    return None
                target = found['box']; detail['target'] = found
            elif mode == 'start':
                if not team_matches(detail,self.plan) or set(ids) != self.source_ids or len(boxes) != 5:
                    return None
                target = list(context.run_recognition('LabyTeamBattleStartLabel', argv.image).box)
            elif mode != 'read':
                return None
            if mode in ('clear','add'):
                x,y,w,h = target
                target = [x+w//2-3,y+h//2-3,6,6]
            if context.tasker.stopping:
                return None
            return self.AnalyzeResult(box=target, detail=detail)
        except Exception:
            return None
