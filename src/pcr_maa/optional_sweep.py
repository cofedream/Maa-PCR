"""Player-selected survey and tower sweeps, with a durable intent before the final tap."""
import time
from uuid import uuid4

from .game_day import current_game_day


SURVEYS = ('sanctum_survey', 'temple_survey')
LABELS = {'sanctum_survey': '圣迹调查', 'temple_survey': '神殿调查', 'tower_cloister': '露娜塔回廊'}


def _integer(value, low=0, high=999999):
    return type(value) is int and low <= value <= high


def validate_investigations(settings):
    if type(settings) is not dict or set(settings) - set(SURVEYS):
        raise ValueError('调查设置只能包含圣迹调查与神殿调查')
    for item in settings.values():
        if (type(item) is not dict or set(item) != {'stage', 'daily_target'}
                or not _integer(item['stage'], 1, 999) or not _integer(item['daily_target'], 0, 999)):
            raise ValueError('调查关卡须为 1 至 999，每日目标须为 0 至 999')


def _matches(value, module, stage):
    return (isinstance(value, dict) and value.get('module') == module and 'stage' in value
            and type(value['stage']) is type(stage) and value['stage'] == stage)


def _count(reading, target):
    remain = reading.get('remaining')
    if not _integer(remain) or 'limit' not in reading:
        raise ValueError('optional_sweep_remaining_unknown')
    limit = reading.get('limit')
    if target is None and limit is None:
        return remain
    if not _integer(limit, 1) or remain > limit:
        raise ValueError('optional_sweep_daily_limit_unknown')
    if target is None:
        return remain
    return min(remain, max(0, target - (limit - remain)))


def _same_limit(value, reading):
    return 'limit' in value and type(value['limit']) is type(reading['limit']) and value['limit'] == reading['limit']


def _at_daily_limit(reading, target):
    return target is not None and target > reading['limit']


def _check_quote(quote, reading, count):
    if (not _matches(quote, reading['module'], reading['stage'])
            or not _integer(quote.get('count'), 1) or quote['count'] != count
            or not _same_limit(quote, reading)
            or not _integer(quote.get('remaining')) or quote['remaining'] != reading['remaining']):
        raise ValueError('optional_sweep_quote_changed')
    if 'shortage' in quote:
        if (quote['shortage'] not in ('stamina', 'tickets') or quote.get('native_ok') is not True
                or reading['module'] == 'tower_cloister' and quote['shortage'] == 'stamina'):
            raise ValueError('optional_sweep_shortage_unknown')
        return 'skipped_insufficient_' + quote['shortage']
    if (not _integer(quote.get('tickets_cost'), 1) or quote['tickets_cost'] != count
            or not _integer(quote.get('stamina_cost'))
            or (reading['module'] == 'tower_cloister') != (quote['stamina_cost'] == 0)):
        raise ValueError('optional_sweep_resource_quote_unknown')
    resources = ('stamina', 'tickets')
    if quote.get('stamina_applicable') is False:
        if (reading['module'] != 'tower_cloister' or not _integer(quote.get('stamina_cost'), 0, 0)
                or 'stamina_before' not in quote or quote['stamina_before'] is not None):
            raise ValueError('optional_sweep_resource_quote_unknown')
        resources = ('tickets',)
    for resource in resources:
        cost, before = quote.get(resource + '_cost'), quote.get(resource + '_before')
        if not _integer(cost) or not _integer(before):
            raise ValueError('optional_sweep_resource_quote_unknown')
        if cost > before:
            return 'skipped_insufficient_' + resource
    return 'ready'


def _settled(result, reading, count):
    return (_matches(result, reading['module'], reading['stage']) and result.get('verified') is True
            and _integer(result.get('count'), 1) and result['count'] == count
            and _same_limit(result, reading)
            and _integer(result.get('remaining')) and result['remaining'] == reading['remaining'] - count)


def _returned_home(port, report):
    receipt = port.home()
    report['home_receipt'] = receipt
    report['home_verified'] = isinstance(receipt, dict) and receipt.get('native_ok') is True
    if not report['home_verified']:
        report['error'] = 'optional_sweep_home_unverified'
    return report


def execute_optional_sweep(port, journal, *, module, stage, daily_target, game_day, device,
                           context, execute=False):
    if module in SURVEYS:
        validate_investigations({module: {'stage': stage, 'daily_target': daily_target}})
    elif module != 'tower_cloister' or stage is not None or daily_target is not None:
        raise ValueError('invalid_optional_sweep_selection')
    if execute is not True or context not in ('gui-current-state', 'user-reported-unreset'):
        raise ValueError('optional_sweep_selection_and_count_context_required')
    if context == 'gui-current-state':
        from .schedule_quick import validate_gui_device
        validate_gui_device(device)
    else:
        from .onekey_history import validate_stable_device
        validate_stable_device(device)
    report = dict(module=module, stage=stage, daily_target=daily_target, game_day=game_day,
                  outcome='stopped', consumption_sent=False, settlement_verified=False,
                  home_verified=False, daily_complete=False)
    start = time.monotonic()

    def fresh():
        if current_game_day() != game_day or time.monotonic() - start > 180:
            raise ValueError('optional_sweep_observation_expired')

    try:
        with journal.locked():
            events = journal.events()
            if events and events[-1]['state'] == 'in_flight':
                report.update(outcome='blocked_in_flight', original_execution_unresolved=True)
                return report
            fresh()
            if daily_target == 0:
                report['outcome'] = 'skipped_disabled'
                return report
            reading = report['reading'] = port.read()
            if not _matches(reading, module, stage):
                raise ValueError('optional_sweep_wrong_stage')
            state = reading.get('state')
            if state == 'locked' or state == 'closed' and module == 'tower_cloister':
                report['outcome'] = 'skipped_not_cleared' if state == 'locked' else 'skipped_event_unavailable'
                return _returned_home(port, report)
            if state != 'ready':
                raise ValueError('optional_sweep_page_unknown')
            count = _count(reading, daily_target)
            # Rechecking on the same day may show additional manual consumption.
            # An increase could mean a reset or a different account on the device.
            previous = next((event for event in reversed(events) if event.get('state') == 'settled'
                             and event.get('settlement_verified') is True and event.get('game_day') == game_day
                             and event.get('device') == device and _matches(event, module, stage)), None)
            if previous and (reading['remaining'] > previous['remaining']
                             or daily_target is not None and reading['limit'] != previous.get('limit')):
                raise ValueError('optional_sweep_counter_reset')
            if count == 0:
                report['outcome'] = 'skipped_no_attempts' if _at_daily_limit(reading, daily_target) else 'skipped_goal_met'
                return _returned_home(port, report)
            quote = report['quote'] = port.quote(count)
            status = _check_quote(quote, reading, count)
            if status != 'ready':
                report['outcome'] = status
                return _returned_home(port, report)
            fresh()
            intent = dict(state='in_flight', intent_id=uuid4().hex, operation=module,
                          module=module, stage=stage, device=device, game_day=game_day,
                          count=count, limit=reading.get('limit'), remaining=reading['remaining'],
                          quote=quote, at=time.time())
            journal.append(intent)
            report.update(intent_id=intent['intent_id'], consumption_sent=True)
            from .native import NativeInputNotSent
            try:
                sent = report['submission'] = port.submit(quote)
            except NativeInputNotSent as exc:
                journal.append_not_sent(intent, exc.receipt)
                report.update(consumption_sent=False, error='native_input_not_sent')
                return report
            if not isinstance(sent, dict) or sent.get('native_ok') is not True:
                raise ValueError('optional_sweep_submission_unknown')
            result = report['settlement'] = port.result(quote)
            fresh()
            if not _settled(result, reading, count):
                raise ValueError('optional_sweep_settlement_unknown')
            journal.append(dict(intent, state='settled', remaining=result['remaining'],
                                settlement_verified=True, result=result, at=time.time()))
            report.update(outcome='executed', settlement_verified=True)
            return _returned_home(port, report)
    except (Exception, KeyboardInterrupt) as exc:
        report['error'] = str(exc) or 'optional_sweep_interrupted'
        if report['consumption_sent'] and not report['settlement_verified']:
            report['original_execution_unresolved'] = True
    return report


def step_status(module, result):
    """Only show a successful or skipped step for evidence from the selected target."""
    stage = result.get('stage')
    target = result.get('daily_target')
    if (result.get('module') != module or result.get('error') or result.get('daily_complete') is not False
            or (module in SURVEYS and (not _integer(stage, 1, 999) or not _integer(target, 0, 999)))
            or (module == 'tower_cloister' and (stage is not None or target is not None))):
        return 'stopped'
    outcome = result.get('outcome')
    no_input = result.get('consumption_sent') is False and result.get('settlement_verified') is False
    if outcome == 'skipped_disabled' and target == 0 and no_input:
        return outcome
    reading = result.get('reading')
    if not _matches(reading, module, stage) or result.get('home_verified') is not True:
        return 'stopped'
    if outcome == 'skipped_not_cleared' and reading.get('state') == 'locked' and no_input:
        return outcome
    if outcome == 'skipped_event_unavailable' and module == 'tower_cloister' and reading.get('state') == 'closed' and no_input:
        return outcome
    if reading.get('state') != 'ready':
        return 'stopped'
    try:
        count = _count(reading, target)
        if count == 0 and no_input:
            expected = 'skipped_no_attempts' if _at_daily_limit(reading, target) else 'skipped_goal_met'
            return expected if outcome == expected else 'stopped'
        decision = _check_quote(result.get('quote'), reading, count)
        if decision != 'ready' and outcome == decision and no_input:
            return outcome
        if (outcome == 'executed' and decision == 'ready' and _settled(result.get('settlement'), reading, count)
                and result.get('consumption_sent') is True and result.get('settlement_verified') is True
                and isinstance(result.get('submission'), dict) and result['submission'].get('native_ok') is True):
            return 'partial' if _at_daily_limit(reading, target) else outcome
    except ValueError:
        pass
    return 'stopped'
