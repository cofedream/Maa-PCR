"""Same-frame daily page recognition and one-use input guards.

The orchestration module retains the shared current/historical day policy.
"""
import hashlib
import re
from datetime import date
from uuid import uuid4

from maa.custom_recognition import CustomRecognition

from . import native
from .schedule import section_counts

ITEMS = ('arena_coins', 'princess_arena_coins', 'dungeon_skip', 'mana_skip',
         'exp_skip', 'normal_gacha_free', 'guild_like', 'house_items', 'memorial_rewards')


# The native button starts a game-managed batch, without an item allowlist.
# Nine visible rows and a later jewel delta do not bound hidden purchases/reset.
# Default closed. Only an explicit current-invocation operator attestation may
# supply the player-managed plan scope; it is never a machine settings audit.
ONEKEY_SCOPE_BLOCKER = 'onekey_hidden_paid_scope_unproven'


# These are row identities, not buttons to navigate/claim independently.
ROW_LABELS = dict(zip(ITEMS, (
    '领取1次竞技场币吧', '领取1次公主竞技场币吧', '挑战1次地下城吧',
    '用掉玛那关卡剩余挑战次数吧', '用掉经验值关卡剩余挑战次数吧',
    '抽取普通扭蛋吧', '给行会成员点赞吧', '领取公会之家的道具吧', '领取追忆战的报酬吧')))
HEADERS = dict(zip(ITEMS, ('竞技场', '竞技场', '地下城', '探索', '探索', '扭蛋', '行会', '公会之家', '追忆的战场')))


# Exact labels/options seen in parent's read-only scope frames. Ordinary gacha
# being free does not establish batch/reset safety. Diagnostic use only.
SETTINGS = {
    '普通商店设定': (('不显示','显示'), '不显示'),
    '限定商店设定': (('不显示','显示'), '不显示'),
    '地下城设定': (('不显示','显示','跳过'), '跳过'),
    '经验值关卡设定': (('不显示','显示','跳过'), '跳过'),
    '玛那关卡设定': (('不显示','显示','跳过'), '跳过'),
    '竞技场币收取设定': (('不显示','显示','领取'), '领取'),
    '公主竞技场币获得设定': (('不显示','显示','领取'), '领取'),
    '普通扭蛋设定': (('不显示','显示','抽取扭蛋'), '抽取扭蛋'),
    '普通扭蛋详细设定': (('重置','不重置'), '不重置'),
    '点赞设定': (('不显示','显示','点赞'), '点赞'),
    '公会之家设定': (('不显示','显示','领取'), '领取'),
    '玛那购买设定': (('不显示','显示','购买'), '不显示'),
    '玛那购买详细设定': (('消耗','不消耗'), '不消耗'),
    '追忆战报酬领取设定': (('不显示','显示','领取'), '领取'),
}
SETTINGS_PASSIVE = {'活动的每日任务设定','活动首领设定','普通商店详细设定',
    '限定商店详细设定','竞技场设定','公主竞技场设定','玛那购买次数设定',
    '团队战设定','追忆战・霸设定','追忆战·霸设定'}


class OneKeyPage(CustomRecognition):
    """Same-frame Maa OCR/ColorMatch; custom code associates rows and states only."""
    def __init__(self, game_day, *, allow_home_banner=False, attest_current_plan=False, gui_start_intent=False):
        super().__init__()
        self.game_day = game_day
        # Compatibility argument only; page/control guards do not classify ads.
        self.allow_home_banner = allow_home_banner
        self.last = None
        self.expected = None
        self.allowed_action = None
        self.session_id = uuid4().hex
        self.capture_index = 0
        self.attest_current_plan = attest_current_plan is True
        self.gui_start_intent = gui_start_intent is True
        self.onekey_guard_used = False
        self.dungeon_finish_guard_used = False
        self.exploration_stage = None
        self.exploration_skip_used = set()
        self.readonly_unresolved = False
        self.recovery_result_actions = set()

    @staticmethod
    def signature(detail):
        return {k: detail.get(k) for k in ('state', 'rows', 'sections', 'bottom', 'quote', 'resources', 'settings', 'settings_top')}

    @staticmethod
    def dungeon_summary_advanced(expected, current):
        return (expected == OneKeyPage.signature({'state': 'dungeon_finish'})
                and current == OneKeyPage.signature({'state': 'sweep_result'}))

    @staticmethod
    def navigation_matches(expected, current):
        """Non-consuming schedule navigation tolerates OCR row omissions and 2px jitter."""
        if not expected or expected.get('state') != 'schedule' or current.get('state') != 'schedule':
            return False
        before, after = expected.get('rows'), current.get('rows')
        if not isinstance(before, dict) or not isinstance(after, dict):
            return False
        common = before.keys() & after.keys()
        if not common and (before or after):
            return False
        for rows in (before, after):
            for key, row in rows.items():
                if (key not in ROW_LABELS or not isinstance(row, dict)
                        or type(row.get('y')) is not int
                        or row.get('state') not in ('pending', 'complete')
                        or (key not in common and row.get('label') != ROW_LABELS[key])):
                    return False
            if len({row['y'] for row in rows.values()}) != len(rows):
                return False
        for key in common:
            row, other = before[key], after[key]
            if (abs(row['y'] - other['y']) > 2
                    or row | {'y': other['y']} != other):
                return False
        # Shared rows anchor the same viewport; crossing them is not OCR jitter.
        if (sorted(common, key=lambda key: before[key]['y'])
                != sorted(common, key=lambda key: after[key]['y'])):
            return False
        return expected | {'rows': after} == current
    def analyze(self, context, argv):
        import json
        kind = json.loads(argv.custom_recognition_param)
        permitted = self.allowed_action == kind
        if kind not in ('observe', 'skip_result_ready'):
            self.allowed_action = None  # Any failed/unknown guard consumes this dispatch.
        detail = dict(state='unknown', reason='unverified', evidence={})
        try:
            from .schedule_onekey import check_day, now_cn
            check_day(self.game_day)
            self.capture_index += 1
            detail.update(session_id=self.session_id, capture_index=self.capture_index,
                          captured_at=now_cn().isoformat(), acquisition='current_tasker_capture')
            path = native.RUNTIME / ('onekey-exec-' + uuid4().hex + '.png')
            if not native.write_png(path, argv.image):
                raise ValueError('cannot_save_frame')
            detail.update(screenshot=str(path), image_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.read(context, argv.image, detail)
        except Exception as exc:
            detail.update(state='unknown', reason=str(exc))
        self.last = detail
        if self.readonly_unresolved:
            if (detail.get('state') == 'schedule'
                    and detail.get('date') != [date.fromisoformat(self.game_day).month,
                                              date.fromisoformat(self.game_day).day]):
                detail.update(state='unknown', reason='unresolved_trusted_same_day_required')
            if kind not in ('observe', 'enter', 'up', 'down', 'close') and kind not in self.recovery_result_actions:
                return None
        if kind == 'observe':
            return self.AnalyzeResult([0, 0, 1280, 720], detail)
        if kind == 'skip_result_ready':
            if (self.allowed_action in ('mana_skip_finish', 'exp_skip_finish')
                    and self.exploration_stage == self.allowed_action.split('_')[0]
                    and detail['state'] == 'sweep_result'
                    and detail.get('evidence', {}).get('OneKeyGuard_sweep_result', {}).get('hit') is True):
                return self.AnalyzeResult([0, 0, 1280, 720], detail)
            return None
        required_state = {
            'enter': 'home', 'onekey': 'schedule', 'close': 'schedule',
            'up': 'schedule', 'down': 'schedule', 'settings_open': 'schedule',
            'settings_down': 'settings', 'settings_close': 'settings',
            'dungeon_confirm': 'dungeon_confirm', 'mana_confirm': 'mana_confirm',
            'exp_confirm': 'exp_confirm', 'dungeon_result': 'sweep_result',
            'dungeon_finish': 'dungeon_finish',
            'mana_result': 'sweep_result', 'exp_result': 'sweep_result',
            'mana_skip_finish': 'sweep_animation', 'exp_skip_finish': 'sweep_animation',
            'like_result': 'like_result', 'item_result': 'item_result',
        }.get(kind)
        if kind == 'dungeon_finish':
            used = self.dungeon_finish_guard_used
            self.dungeon_finish_guard_used = True  # Unknown/failure cannot be rearmed.
            if used:
                return None
        if kind == 'onekey':
            used = self.onekey_guard_used
            self.onekey_guard_used = True  # Failure also consumes this session's one-key guard.
            if not (self.attest_current_plan or self.gui_start_intent) or used:
                if detail['state'] != 'unknown':
                    detail['reason'] = ONEKEY_SCOPE_BLOCKER if not (self.attest_current_plan or self.gui_start_intent) else 'duplicate_onekey_guard'
                return None
            if detail['state'] != 'schedule' or detail.get('bottom') is not True:
                return None
        current = self.signature(detail)
        summary_advanced = (kind == 'dungeon_finish'
                            and self.dungeon_summary_advanced(self.expected, current)
                            and detail.get('evidence', {}).get('OneKeyGuard_sweep_result', {}).get('hit') is True)
        if summary_advanced:
            required_state = 'sweep_result'
        matches = (self.navigation_matches(self.expected, current) if kind in ('up', 'down', 'close')
                   else self.expected == current or summary_advanced)
        if (not permitted or not matches
                or required_state is None or detail['state'] != required_state):
            return None
        if kind in ('mana_skip_finish', 'exp_skip_finish'):
            stage = kind.split('_')[0]
            if self.exploration_stage != stage or stage in self.exploration_skip_used:
                return None
            self.exploration_skip_used.add(stage)
            return self.AnalyzeResult(detail['skip_box'], detail)
        if kind in ('mana_confirm', 'exp_confirm'):
            self.exploration_stage = kind.split('_')[0]
        elif kind in ('mana_result', 'exp_result'):
            self.exploration_stage = None
        return self.AnalyzeResult([0, 0, 1280, 720], detail)

    def read(self, context, image, detail):
        from .schedule_house import words_from, one, text  # Generic validators only.
        from .schedule_onekey import observed_date

        def reco(name, roi=None):
            r = context.run_recognition(name, image, {name: {'roi': roi}} if roi else None)
            if r is None:
                raise ValueError('missing_recognition:' + name)
            detail['evidence'][name + (str(roi) if roi else '')] = dict(hit=r.hit, raw=r.raw_detail)
            return r

        def require(name):
            if not reco(name).hit:
                raise ValueError('unverified:' + name)

        if reco('HomeDataConnection').hit:
            raise ValueError('page_connection_in_progress')
        if self.exploration_stage is not None:
            animation = reco('ExecSweepSkipGuard')
            if animation.hit:
                detail.update(state='sweep_animation', reason=None, skip_box=list(animation.box))
                return
        words = words_from(reco('OneKeyOCR'))
        detail['words'] = words
        def region_number(pattern, roi):
            word = one(words, pattern, roi)
            if not word:
                raise ValueError('unreadable_number_in:' + str(roi))
            return text(word).replace(',', '')
        titles = {text(w) for w in words if w['score'] >= .8}
        if one(words, '计划整理', [530, 30, 230, 60]):
            self.read_settings(words, reco, require, detail)
            return
        home = '日程表' in titles and '礼物' in titles and reco('ObserveHome').hit
        # Same-frame home anchors and unobstructed controls, independent of ad copy.
        if home:
            # Home navigation has no blue dialog action in the central panel.
            # Check its pixels, not its caption: blank/renamed buttons also stop.
            if reco('OneKeyHomeDialogBlue').hit:
                raise ValueError('unexpected_home_dialog_control')
            def home_number(pattern, roi):
                word = one(words, pattern, roi)
                if not word:
                    raise ValueError('unreadable_home_resource')
                return text(word).replace(',', '')
            ap = home_number(r'\d+[/／]\d+', [320, 5, 90, 30])
            detail.update(state='home', reason=None, resources=dict(
                ap=int(re.split('[/／]', ap)[0]),
                jewels=int(home_number(r'[0-9,]+', [330, 65, 80, 30]))))
            return
        for kind, title, roi in (
                ('dungeon_finish', '完成地下城战斗1次吧', [530, 5, 215, 40]),
                ('dungeon_confirm', '地下城跳过确认', [410, 165, 450, 65]),
                ('mana_confirm', '玛那关卡跳过确认', [410, 165, 450, 65]),
                ('exp_confirm', '经验值关卡跳过确认', [410, 165, 450, 65]),
                ('sweep_result', '扫荡结果', [500, 15, 290, 65]),
                ('like_result', '点赞完成', [480, 165, 320, 65]),
                ('item_result', '收取报酬', [490, 15, 320, 65])):
            if not one(words, title, roi):
                continue
            require('OneKeyGuard_' + kind)
            if kind in ('mana_confirm', 'exp_confirm'):
                # Only the actual skip-confirmation body supplies ticket facts.
                body = [text(w) for w in words if w['score'] >= .8
                        and 330 <= w['box'][0] < w['box'][0] + w['box'][2] <= 950
                        and 250 <= w['box'][1] < w['box'][1] + w['box'][3] <= 440]
                if any('扫荡券' in t for t in body) and any('不足' in t or '不够' in t for t in body):
                    raise ValueError('insufficient_sweep_tickets')
                def quote_number(roi):
                    # Tiny digits are unreliable in whole-screen detection. Read
                    # this exact field again on the same image, without relaxing
                    # uniqueness, numeric syntax or the .9 confidence requirement.
                    result = reco('OneKeyQuoteNumber', roi)
                    word = one(words_from(result), r'[0-9]+', roi)
                    if not result.hit or word is None:
                        raise ValueError('unreadable_quote_number_in:' + str(roi))
                    return int(text(word))
                detail['quote'] = {k: quote_number(roi) for k, roi in {
                    'times': [600, 337, 40, 30], 'remaining': [895, 337, 40, 30],
                    'tickets': [600, 373, 40, 30]}.items()}
                q = detail['quote']
                if q['times'] != 2 or q['remaining'] != 2 or q['tickets'] != 2:
                    raise ValueError('unseen_or_insufficient_two_times_two_tickets_quote')
            elif kind == 'like_result':
                before = int(region_number(r'\d+', [695, 354, 70, 38]))
                after = int(region_number(r'\d+', [872, 354, 70, 38]))
                if after - before != 10:
                    raise ValueError('like_ap_delta_not_ten')
                detail['quote'] = dict(ap_before=before, ap_after=after, ap_delta=10)
            detail.update(state=kind, reason=None)
            return
        require('ScheduleGuard')
        require('OneKeyLabel')
        require('OneKeyPanelBright')
        md = observed_date(words, reco, self.game_day,
            captured_at=detail.get('captured_at') if detail.get('acquisition') == 'current_tasker_capture' else None)
        sections = section_counts(words)
        rows = {}
        for key, label in ROW_LABELS.items():
            matches = [w for w in words if text(w) == label and 260 <= w['box'][0] < 500
                       and 150 <= w['box'][1] <= 563 and w['score'] >= .9]
            if len(matches) > 1:
                raise ValueError('duplicate_row:' + key)
            if not matches:
                continue
            body = matches[0]
            y = body['box'][1]
            if not one(words, HEADERS[key], [285, y-45, 155, 42]):
                continue  # Clipped header is not a row proof.
            if not reco('OneKeyRowBright', [700, y-25, 120, 10]).hit:
                raise ValueError('dimmed_row:' + key)
            strike = reco('OneKeyStrike', [480, y-3, 390, 23])
            stamp = reco('OneKeyStamp', [925, y-40, 90, 74])
            buttons = [w for w in words if text(w) == '执行' and 915 <= w['box'][0] < 1015
                       and y-32 <= w['box'][1] <= y+8 and w['score'] >= .9]
            if strike.hit and stamp.hit and not buttons:
                box = strike.raw_detail['best']['box']
                if box[2] < 300 or box[3] > 20:
                    raise ValueError('invalid_completion_mark')
                state = 'complete'
            elif len(buttons) == 1 and not strike.hit and not stamp.hit:
                if not reco('OneKeyButtonBlue', [925, y-28, 85, 42]).hit:
                    raise ValueError('disabled_execute_button')
                state = 'pending'
            else:
                raise ValueError('row_state_conflict:' + key)
            rows[key] = dict(state=state, y=y, label=label)
        # Every complete visible Execute row must have an allowed identity.
        for w in words:
            if text(w) == '执行' and 915 <= w['box'][0] < 1015 and 150 <= w['box'][1] <= 530:
                if not any(r['y']-32 <= w['box'][1] <= r['y']+8 for r in rows.values()):
                    raise ValueError('unrecognized_executable_row')
        detail.update(state='schedule', reason=None, rows=rows, date=md,
                      sections={k: {f: v[f] for f in ('completed', 'total')} for k, v in sections.items()},
                      bottom=reco('OneKeyBottom').hit)

    @staticmethod
    def read_settings(words, reco, require, detail):
        """Read radio groups only. No action ever targets a radio or reset button."""
        from .schedule_house import one, text
        require('OneKeySettingsTitle')
        require('OneKeySettingsClose')
        require('OneKeySettingsBright')
        headers = sorted((w for w in words if 70 <= w['box'][0] <= 90
                          and 96 <= w['box'][1] < 580 and text(w).endswith('设定')
                          and w['score'] >= .9), key=lambda w: w['box'][1])
        selections = {}
        for header in headers:
            label = text(header)
            if label not in SETTINGS:
                if label not in SETTINGS_PASSIVE:
                    raise ValueError('unknown_settings_group:' + label)
                continue
            y = header['box'][1] + header['box'][3]
            end = min([w['box'][1] for w in headers if w['box'][1] > y] + [592])
            options, expected = SETTINGS[label]
            labels = [one(words, re.escape(option), [x+29, y, 145, max(0, end-y)])
                      for x, option in zip((714,1018) if len(options)==2 else (411,714,1018), options)]
            if any(w is None for w in labels):
                continue  # Clipped groups provide no proof; require coverage later.
            ys = [w['box'][1]+w['box'][3]//2 for w in labels]
            if max(ys)-min(ys) > 6 or min(ys)-y < 40:
                raise ValueError('settings_option_association_unknown:' + label)
            selected=[]
            for x, option, cy in zip((714,1018) if len(options)==2 else (411,714,1018), options, ys):
                if reco('OneKeyRadioBlue', [x-12,cy-12,24,24]).hit:
                    selected.append(option)
                elif not reco('OneKeyRadioWhite', [x-10,cy-10,20,20]).hit:
                    raise ValueError('settings_radio_unreadable:' + label)
            if selected != [expected]:
                raise ValueError('unsafe_setting:' + label + ':' + ','.join(selected))
            selections[label] = expected
        detail.update(state='settings', reason=None, settings=selections,
                      settings_top=bool(one(words, '活动的每日任务设定', [70,100,400,65])))
