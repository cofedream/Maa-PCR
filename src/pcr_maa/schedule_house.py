"""One visible free house row. All recognition/input runs through Maa JSON.

No scroll, generic confirmation, cleanup, retry, or schedule orchestration.
"""
import hashlib
import json
import math
import re
from datetime import date
from uuid import uuid4

import cv2
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum

from . import native
from .game_day import current_game_day
from .preset_execution import Journal

LABEL = '领取公会之家的道具吧'
BLOCKED = r'购买|宝石|钻石|充值|重置|日期.*变更|连接|通信|重试|重新登录|确认购买'


def text(word):
    return re.sub(r'\s+', '', word['text'])


def words_from(result):
    if result is None or result.algorithm != AlgorithmEnum.OCR:
        raise ValueError('ocr_missing')
    raw = result.raw_detail
    if not isinstance(raw, dict) or not isinstance(raw.get('all'), list):
        raise ValueError('ocr_malformed')
    for w in raw['all']:
        if (not isinstance(w, dict) or not isinstance(w.get('text'), str)
                or not isinstance(w.get('box'), list) or len(w['box']) != 4
                or any(type(n) is not int for n in w['box'])
                or type(w.get('score')) not in (int, float)
                or not math.isfinite(w['score']) or not 0 <= w['score'] <= 1):
            raise ValueError('ocr_malformed')
    return raw['all']


def one(words, label, roi):
    x, y, width, height = roi
    found = [w for w in words if re.fullmatch(label, text(w))
             and x <= w['box'][0] and y <= w['box'][1]
             and w['box'][0]+w['box'][2] <= x+width
             and w['box'][1]+w['box'][3] <= y+height]
    return found[0] if len(found) == 1 and found[0]['score'] >= .9 else None


class HousePage(CustomRecognition):
    def __init__(self, game_day=None, *, require_current_day=False):
        super().__init__()
        self.game_day = game_day
        self.require_current_day = require_current_day
        self.last = None

    def analyze(self, context, argv):
        detail = dict(status='unknown', reason='unverified', evidence={})
        try:
            if argv.image.shape != (720, 1280, 3) or argv.image.dtype.name != 'uint8':
                raise ValueError('image_shape')
            path = native.RUNTIME / ('house-frame-' + uuid4().hex + '.png')
            if not native.write_png(path, argv.image):
                raise ValueError('evidence_write_failed')
            detail.update(screenshot=str(path), image_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            self.read(context, argv.image, detail)
        except Exception as exc:
            detail.update(status='unknown', reason=str(exc))
        self.last = detail
        try:
            kind = json.loads(argv.custom_recognition_param)
        except (ValueError, TypeError):
            return None
        if kind not in ('observe', 'ready', 'result', 'already_claimed'):
            return None
        if kind != 'observe' and detail['status'] != kind:
            return None
        box = detail['button']['box'] if kind == 'ready' else (
            detail['close']['box'] if kind == 'result' else [250, 118, 790, 478])
        return self.AnalyzeResult(box, detail)

    def read(self, context, image, detail):
        if self.require_current_day and self.game_day != current_game_day():
            raise ValueError('game_day_changed_reobserve_required')

        def reco(name, roi=None):
            r = context.run_recognition(name, image, {name: {'roi': roi}} if roi else None)
            if r is None:
                raise ValueError('recognition_missing:' + name)
            detail['evidence'][name] = dict(hit=r.hit, raw=r.raw_detail, reco_id=r.reco_id)
            return r

        def color(name, roi):
            r = reco(name, roi)
            raw = r.raw_detail
            if (r.algorithm != AlgorithmEnum.ColorMatch or not isinstance(raw, dict)
                    or not isinstance(raw.get('all'), list) or not isinstance(raw.get('filtered'), list)
                    or (r.hit and not isinstance(raw.get('best'), dict))
                    or (not r.hit and (raw['filtered'] != [] or raw.get('best', False) is not None))):
                raise ValueError('color_evidence_invalid:' + name)
            return r

        words = words_from(reco('HouseOCR'))
        detail['words'] = words
        # Only text intersecting the active schedule/result panel can be
        # evidence of a purchase dialog. The surrounding book/home artwork
        # is not a control, regardless of its wording. Confirmation controls
        # are not part of either supported target page.
        panel_words = [w for w in words if w['box'][0] < 1040
                       and w['box'][0] + w['box'][2] > 250
                       and w['box'][1] < 680 and w['box'][1] + w['box'][3] > 20]
        if any(re.search(BLOCKED, text(w)) or re.fullmatch(r'确定|确认|取消|重试', text(w))
               for w in panel_words):
            raise ValueError('blocked_dialog_or_purchase_or_connection')
        # Refresh/disconnection notices invalidate observations even outside
        # the panel; calendar OCR below never proves a game refresh.
        if any(re.search(r'日期.*变更|通信|连接|重新登录|重试', text(w)) for w in words):
            raise ValueError('blocked_dialog_or_purchase_or_connection')
        title = one(words, '全部收取', [540, 25, 210, 60])
        body = one(words, '收取了以下道具[。.]?', [520, 85, 230, 45])
        close = one(words, '关闭', [560, 605, 170, 65])
        if title and body and close:
            detail.update(status='result', reason=None, title=title, body=body, close=close)
            return
        if not reco('ScheduleGuard').hit:
            raise ValueError('schedule_page_unverified_or_occluded')
        detail.update(visible_month_day=None, date_role='auxiliary_calendar_not_game_day')
        try:
            date_words = words_from(reco('HouseDate'))
            match = (re.fullmatch(r'(\d{1,2})[/／](\d{1,2})', text(date_words[0]))
                     if len(date_words) == 1 and date_words[0]['score'] >= .8 else None)
            if match:
                month_day = tuple(map(int, match.groups()))
                date(2000, *month_day)  # Validate syntax only, not game-day identity.
                detail['visible_month_day'] = list(month_day)
            else:
                detail['date_note'] = 'schedule_date_unreadable'
        except ValueError as exc:
            detail['date_note'] = str(exc)
        headers = [w for w in words if text(w) == '公会之家'
                   and 290 <= w['box'][0] <= 410 and 120 <= w['box'][1] <= 590]
        if len(headers) != 1 or headers[0]['score'] < .9:
            raise ValueError('house_header_absent_or_ambiguous')
        header = headers[0]
        x, y, width, height = header['box']
        detail['header'] = header
        # Complete 90px row, with the body and lower separator inside the list.
        if y-12 < 120 or y+70 > 590:
            raise ValueError('house_row_clipped')
        fragments = sorted([w for w in words if 260 <= w['box'][0] < 650
                            and 24 <= w['box'][1]-y <= 42], key=lambda w: w['box'][0])
        if (not fragments or any(w['score'] < .9 for w in fragments)
                or ''.join(text(w) for w in fragments) != LABEL
                or any(w['box'][1]+w['box'][3] > y+65 for w in fragments)):
            raise ValueError('house_text_unverified')
        detail['row_words'] = fragments
        if not color('HouseRowBright', [700, y+5, 120, 10]).hit:
            raise ValueError('house_row_dimmed_or_occluded')
        strike = color('HouseStrike', [480, y+25, 360, 40])
        buttons = [w for w in words if text(w) == '执行' and 915 <= w['box'][0] <= 1010
                   and y+5 <= w['box'][1] <= y+30]
        if strike.hit:
            box = strike.raw_detail['best']['box']
            if buttons or box[2] < 300 or box[3] > 15:
                raise ValueError('house_state_conflict')
            detail.update(status='already_claimed', reason=None)
            return
        if len(buttons) != 1 or buttons[0]['score'] < .9:
            raise ValueError('house_button_absent_or_ambiguous')
        button = buttons[0]
        bx, by, bw, bh = button['box']
        if bx+bw > 1025 or by+bh > y+60:
            raise ValueError('house_button_clipped')
        if not color('HouseButtonBlue', [bx-3, by-3, bw+6, bh+6]).hit:
            raise ValueError('house_button_not_enabled')
        detail.update(status='ready', reason=None, button=button)


def completed(receipt, name):
    return receipt.get('native_ok') is True and any(
        n['name'] == name and n['completed'] and (n.get('action') or {}).get('success') is True
        for n in receipt['nodes'])


def schedule_house(tasker, *, execute=False, game_day=None, device=None, journal=None):
    """Read current frame by default. An uncertain dispatch remains in_flight."""
    report = dict(outcome='unknown', daily_complete=False, claim_attempted=False,
                  claim_click_completed=False, result_verified=False, home_verified=False,
                  schedule_executed=False, receipts=[], source='bound Maa tasker',
                  scope='only visible free house row; general schedule remains unsupported',
                  action_limits=dict(claim=1, result_close=1, schedule_close=1, swipes=0))
    if type(execute) is not bool:
        raise ValueError('execute must be a boolean')
    if game_day is not None and date.fromisoformat(game_day).isoformat() != game_day:
        raise ValueError('game-day must be YYYY-MM-DD')
    if execute:
        from .device import fixed_config, SERIAL
        if not game_day or not isinstance(device, dict) or set(device) != {'serial', 'input_display', 'screenshot_display'}:
            raise ValueError('Explicit game-day and fixed device display binding required')
        if device['serial'] != SERIAL:
            raise ValueError('Fixed device required')
        fixed_config(device['input_display'], device['screenshot_display'])
        if game_day != current_game_day():
            raise ValueError('game_day_not_current')
        journal = journal or Journal(native.ROOT / '.runtime/schedule-house-work/ledger')
    page = HousePage(game_day, require_current_day=execute)
    resource = tasker.resource
    if not resource.register_custom_recognition('HousePage', page):
        raise RuntimeError('Cannot register house reader')

    def run(entry):
        receipt = native.run(tasker, entry)
        report['receipts'].append(dict(entry=entry, receipt=receipt, reading=page.last))
        return receipt

    def read():
        page.last = None
        r = run('HouseRead')
        if not completed(r, 'HouseSnapshot') or page.last is None:
            raise ValueError('house_read_failed')
        return page.last

    def perform():
        before = report['reading'] = read()
        if before['status'] == 'already_claimed':
            report['outcome'] = 'already_claimed_not_executed'
            return
        if before['status'] != 'ready':
            report['reason'] = before['reason'] or 'result_without_this_call_intent'
            return
        if not execute:
            report['outcome'] = 'ready_not_executed'
            return
        # Durable intent precedes even the native job dispatch; a timeout or
        # death before/after its input is deliberately not safe to resend.
        intent = dict(state='in_flight', operation='schedule_house', game_day=game_day,
                      device=device, intent_id=uuid4().hex, image_sha256=before['image_sha256'])
        journal.append(intent)
        report['claim_attempted'] = True
        try:
            claim = run('HouseClaim')
            native.require_native_input(claim, ('HouseClaimClick',))
        except native.NativeInputNotSent as exc:
            journal.append_not_sent(intent, exc.receipt)
            report.update(outcome='native_input_not_sent', input_execution='not_sent',
                          consumption_sent=False, native_evidence=exc.receipt)
            return
        if not completed(claim, 'HouseClaimClick'):
            raise ValueError('claim_dispatch_unverified_no_retry')
        report['claim_click_completed'] = True
        result = report['result'] = read()
        if result['status'] != 'result':
            raise ValueError('claim_result_unknown_preserve_scene')
        report['result_verified'] = True
        if not completed(run('HouseResultClose'), 'HouseResultCloseClick'):
            raise ValueError('result_close_unverified_no_retry')
        after = report['after'] = read()
        if after['status'] != 'already_claimed':
            raise ValueError('claimed_row_readback_unknown_preserve_scene')
        # Reuse the existing navigation but strengthen its click guard only for
        # this dedicated call. Restore the original node even on a native error.
        close_node = json.loads((native.RESOURCE/'pipeline/schedule.json').read_text(encoding='utf-8'))['ScheduleCloseClick']
        if not resource.override_pipeline({'ScheduleCloseClick': {
                'all_of': close_node['all_of'] + ['HouseBeforeHome']}}):
            raise ValueError('cannot_install_house_home_guard')
        try:
            home = run('CloseSchedule')
        finally:
            if not resource.override_pipeline({'ScheduleCloseClick': close_node}):
                raise ValueError('cannot_restore_schedule_close_guard')
        report['home_verified'] = completed(home, 'ObserveHome')
        if not report['home_verified']:
            raise ValueError('home_unverified_no_retry')
        journal.append(dict(state='settled', operation='schedule_house', game_day=game_day,
                            device=device, image_sha256=after['image_sha256']))
        report.update(outcome='claimed', schedule_executed=True)

    try:
        if not execute:
            perform()
        else:
            with journal.locked():
                events = journal.events()
                if any(e.get('operation') != 'schedule_house' for e in events):
                    raise ValueError('journal_scope_mismatch_parent_reconciliation_required')
                if events and events[-1]['state'] == 'in_flight':
                    report['reason'] = 'in_flight_parent_reconciliation_required'
                else:
                    recorded = []
                    for intent, terminal in zip(events[::2], events[1::2]):
                        if (terminal.get('input_execution') == 'not_sent'
                                and isinstance(intent.get('intent_id'), str)
                                and re.fullmatch('[0-9a-f]{32}', intent['intent_id'])
                                and (terminal.get('native_evidence') or {}).get('native_evidence', {}).get('entry') == 'HouseClaim'
                                and terminal.get('consumption_sent') is False
                                and terminal.get('settlement_verified') is False
                                and terminal.get('business_result_verified') is False
                                and all(terminal.get(key) == value for key, value in intent.items() if key != 'state')):
                            try:
                                native.require_native_input(terminal.get('native_evidence') or {}, ('HouseClaimClick',))
                            except native.NativeInputNotSent:
                                continue  # This exact pair proved no claim input; read fresh on a new Start.
                        recorded.extend((intent, terminal))
                    if any(e['game_day'] == game_day and e['device']['serial'] == device['serial'] for e in recorded):
                        report['outcome'] = 'already_recorded_not_executed'
                    else:
                        perform()
    except (Exception, KeyboardInterrupt) as exc:
        report.update(outcome='unknown', reason=str(exc) or 'interrupted_no_retry')
    finally:
        if not resource.unregister_custom_recognition('HousePage'):
            report.update(outcome='unknown', reason='reader_release_failed')
    return report
