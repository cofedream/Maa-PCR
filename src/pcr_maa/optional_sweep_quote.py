"""Quote and settle one selected CN quest. Only submit() can confirm a spend."""
import re

from .abyss_native import _NoMatch, _Words
from .optional_sweep_count import adjust_button, button, enabled_button, extreme_button
from .game_day import current_game_day
from .optional_sweep_navigation import _counter, navigate, stage_frame, stage_identity


def _unique(words, pattern, region=None):
    hits = []
    for word in words:
        if word.get('score', 0) < .90 or not re.fullmatch(pattern, ''.join(word['text'].split())):
            continue
        x, y, w, h = word['box']
        if region is None or (region[0] <= x+w/2 <= region[2] and region[1] <= y+h/2 <= region[3]):
            hits.append(word)
    return hits[0] if len(hits) == 1 else None


def _stage_controls(port, reading, count, *, adjust=False, expected=None, minimum=False):
    def guard(context, argv):
        words = port.words(context, argv)
        current = stage_frame(words, port.module, port.stage, argv.image)
        if (not current or current['state'] != 'ready'
                or current['remaining'] != reading['remaining']
                or current.get('limit') != reading.get('limit')):
            return None
        for resource, pattern in (('stamina', '体力不足'), ('tickets', '扫荡券不足')):
            shortage = _unique(words, pattern)
            if shortage:
                if expected is not None:
                    return None
                return shortage['box'], dict(shortage=resource)
        selected = button(words)
        if selected is None:
            return None
        value, box = selected
        if expected is not None and value != expected:
            return None
        if expected is not None and not enabled_button(argv, box):
            return None
        if minimum:
            box = extreme_button(words, box, minimum=True)
            if box is None or count != 1 or value <= 1:
                return None
        elif adjust:
            box = adjust_button(context, argv, words, box, count > value)
            if box is None or value == count:
                return None
        return box, dict(selected=value, minimum=extreme_button(words, selected[1], minimum=True) is not None)
    return guard


def _amount(words, label_pattern):
    # Anchor a single value to its on-screen label, including the side-by-side
    # consumed/owned layout. A number from an adjacent column is never selected.
    region = (320, 235, 963, 463)
    tagged = _unique(words, label_pattern + r'[：:]?\d{1,6}(?:张)?', region)
    label = _unique(words, label_pattern + r'[：:]?', region)
    if bool(label) == bool(tagged):
        return None
    if tagged:
        return int(re.search(r'\d+', tagged['text']).group())
    x, y, w, h = label['box']
    boundary = 640 if x < 640 else region[2]
    value = _unique(words, r'\d{1,6}(?:张)?', (x+w, y-2, boundary, y+h+2))
    return int(re.search(r'\d+', value['text']).group()) if value else None


def _cloister_ticket(words, count):
    modal = (295, 138, 984, 558)
    title = _unique(words, '扫荡券确认', (400, 135, 880, 260))
    # Tower costs no stamina. Require that the actual question explicitly
    # describes a ticket-only action; absence of a readable stamina field alone
    # must never be treated as a zero-cost quote.
    question = _unique(words, r'(?:是否|要)?(?:消耗|使用)(?:\d{1,3}张)?扫荡券'
                              r'(?:[，,]?(?:进行|通关|扫荡)(?:回廊探索|回廊|关卡))?(?:吗|确认吗)?[？?]?', modal)
    ok = _unique(words, '确认|确定|OK', (650, 410, 948, 550))
    cancel = _unique(words, '取消', (324, 410, 625, 550))
    if not all((title, question, ok, cancel)):
        return None
    if any('体力' in w['text'] for w in words if w.get('score', 0) >= .9
           and modal[0] <= w['box'][0]+w['box'][2]/2 <= modal[2]
           and modal[1] <= w['box'][1]+w['box'][3]/2 <= modal[3]):
        return None
    cost = _amount(words, r'(?:消耗扫荡券|消耗券)')
    owned = _amount(words, r'(?:持有扫荡券|持有券|持有数|券)')
    explicit_count = re.search(r'(\d+)张', question['text'])
    if (cost != count or owned is None or
            explicit_count and int(explicit_count.group(1)) != cost):
        return None
    return (ok['box'], cancel['box'],
            dict(tickets_cost=cost, tickets_before=owned, stamina_cost=0,
                 stamina_before=None, stamina_applicable=False))


def _ticket(port, count, reading, *, expected=None, cancel=False):
    def guard(context, argv):
        words = port.words(context, argv)
        counter = _counter(words)
        if (not stage_identity(words, port.module, port.stage, argv.image) or counter is None
                or counter != (reading['remaining'], reading['limit'])
                or current_game_day() != reading['game_day']):
            return None
        # The ticket dialog can overlay a recovery offer. Never operate on a
        # visible premium-currency confirmation, even if the quest remains visible.
        if any(re.search('宝石|恢复次数|回复次数|要回复吗|要恢复吗|购买体力', w['text'])
               for w in words if w.get('score', 0) >= .9
               and 310 <= w['box'][0]+w['box'][2]/2 <= 980
               and 140 <= w['box'][1]+w['box'][3]/2 <= 550):
            return None
        base = dict(module=port.module, stage=port.stage, count=count, remaining=counter[0],
                    limit=counter[1], game_day=reading['game_day'])
        tower = _cloister_ticket(words, count) if port.module == 'tower_cloister' else None
        if tower is not None:
            confirm_box, cancel_box, amounts = tower
            observed = dict(base, **amounts)
            if expected is not None and (any(v != expected.get(k) for k, v in observed.items())
                    or not cancel and count > observed['tickets_before']):
                return None
            if current_game_day() != reading['game_day']:
                return None
            return (cancel_box if cancel else confirm_box), observed
        frame = _Words(context, argv.image)
        try:
            # Reuse the small CN ticket modal fields, independent of the
            # separate, multi-row abyss quotation behind that version of it.
            for name in ('Title', 'Question1', 'Question2', 'StaminaLabel', 'CostLabel',
                         'StaminaOwnedLabel', 'CancelButton', 'ConfirmButton'):
                frame.word('AbyssTicket'+name)
            label = frame.recognize('AbyssTicketOwnedLabel')
            if not label or not label.hit:
                return None
            try:
                stamina_cost = frame.integer('AbyssTicketStaminaCost')
            except _NoMatch:
                # The two digits on the survey modal occupy less of the box
                # than three-digit outpost costs. Detect within the same ROI.
                stamina_cost = frame.integer('AbyssTicketStaminaCost',
                    {'AbyssTicketStaminaCost': dict(only_rec=False)})
            observed = dict(base,
                            stamina_cost=stamina_cost,
                            stamina_before=frame.integer('AbyssTicketStaminaOwned'),
                            tickets_cost=frame.integer('AbyssTicketCost'),
                            tickets_before=frame.integer('AbyssTicketOwned'))
            if observed['tickets_cost'] != count or ((port.stage is None) != (observed['stamina_cost'] == 0)):
                return None
            if expected is not None and any(value != expected.get(k) for k, value in observed.items()):
                return None
            if expected is not None and not cancel and (
                    observed['stamina_cost'] > observed['stamina_before']
                    or observed['tickets_cost'] > observed['tickets_before']):
                return None
            target = frame.recognize('AbyssTicketCancelButton' if cancel else 'AbyssTicketConfirmButton')
            if current_game_day() != reading['game_day']:
                return None
            return list(target.box), observed
        except _NoMatch:
            return None
    return guard


def quote(port, count):
    game_day = port.game_day
    if current_game_day() != game_day:
        raise ValueError('optional_sweep_observation_expired')
    reading = port.read_stage()
    reading = dict(reading, game_day=game_day)
    if reading.get('state') != 'ready' or type(count) is not int or not 1 <= count <= reading['remaining']:
        raise ValueError('optional_sweep_quantity_changed')
    controls = _stage_controls(port, reading, count)
    observation = port.frame(controls)
    if 'shortage' in observation:
        return dict(observation, module=port.module, stage=port.stage, count=count,
                    remaining=reading['remaining'], limit=reading['limit'])
    selected = observation['selected']
    # Adjust once per signed step. Wait for its precise successor before
    # sending the next adjustment, including the last click that opens a quote.
    if selected > 1 and count == 1 and observation.get('minimum'):
        port.frame(_stage_controls(port, reading, count, minimum=True, expected=selected), click=True)
    else:
        for _ in range(abs(selected - count)):
            port.frame(_stage_controls(port, reading, count, adjust=True, expected=selected), click=True)
            selected += 1 if count > selected else -1
    port.frame(_stage_controls(port, reading, count, expected=count), click=True)
    offer = port.frame(_ticket(port, count, reading))
    if ((offer['stamina_before'] is not None and offer['stamina_cost'] > offer['stamina_before'])
            or offer['tickets_cost'] > offer['tickets_before']):
        offer['cancel'] = port.frame(_ticket(port, count, reading, expected=offer, cancel=True), click=True)
    return offer


def submit(port, offer):
    # NativeOptionalSweep latches the one-shot dispatch before entering here.
    return port.frame(_ticket(port, offer['count'], offer, expected=offer), click=True)


def _result_guard(port, *, final_only=False):
    def guard(context, argv):
        words = port.words(context, argv)
        title = _unique(words, '扫荡结果', (360, 0, 920, 120))
        if title is None:
            return None
        complete = _unique(words, '扫荡完成[！!]?', (120, 100, 1160, 560))
        if complete:
            confirm = _unique(words, '确认|确定|OK', (460, 550, 820, 690))
            if confirm:
                return confirm['box'], dict(page='complete', text=complete['text'])
        if not final_only:
            skip = _unique(words, '全部跳过|跳过', (400, 540, 900, 690))
            if skip:
                return skip['box'], dict(page='skip')
            finish = context.run_recognition('ExecSweepSkipGuard', argv.image)
            if finish and finish.hit:
                skip = context.run_recognition('ExecSweepSkipButton', argv.image)
                if skip and skip.hit:
                    return list(skip.box), dict(page='skip')
        return None
    return guard


def result(port, offer):
    ended = port.frame(_result_guard(port), click=True, timeout_ms=30000)
    if ended['page'] == 'skip':
        ended = port.frame(_result_guard(port, final_only=True), click=True, timeout_ms=30000)
    # Navigation only admits identified non-consuming screens. The result
    # page itself does not prove a count; reread the same quest's counter.
    after = navigate(port)
    if (after.get('module') != port.module or after.get('stage') != port.stage
            or after.get('state') != 'ready' or after.get('remaining') != offer['remaining'] - offer['count']
            or after.get('limit') != offer['limit'] or current_game_day() != offer['game_day']):
        raise ValueError('optional_sweep_result_counter_mismatch')
    return dict(module=port.module, stage=port.stage, count=offer['remaining']-after['remaining'],
                remaining=after['remaining'], limit=after['limit'], verified=True, result_page=ended, reading=after)
