"""Retain unknown outcomes while admitting a freshly confirmed daily cycle.

This is a root-journal lifecycle record, never a claim of old game settlement.
The caller holds the existing root lock. No controller or input is created here.
"""
from datetime import date, datetime
from hashlib import sha256
import re


def require(value, reason='new_cycle_evidence_invalid'):
    if not value:
        raise ValueError(reason)


def verify_observation(proof, original, game_day, device):
    from .game_day import current_game_day
    from .schedule_house import completed
    from .schedule_onekey import ITEMS
    day = proof['game_day']
    require(date.fromisoformat(day).isoformat() == day
            and date.fromisoformat(original['game_day']).isoformat() == original['game_day']
            and original['game_day'] < day <= game_day,
            'new_cycle_must_follow_original_day')
    require(proof['confirmation'] == 'user-confirmed-game-refresh'
            and original['device'] == device)
    context = proof['navigation_context']
    require(context['game_day'] == day and context['device'] == device
            and re.fullmatch('[0-9a-f]{32}', context['run_id'])
            and re.fullmatch('[0-9a-f]{32}', context['session_id'])
            and re.fullmatch('run-[0-9a-f]{32}', context['invocation_id'])
            and context['invocation_id'] != original['invocation_id'])
    receipts = proof['receipts']
    require(receipts and receipts[0]['reading']['state'] == 'home'
            and receipts[-1]['reading']['state'] == 'schedule'
            and receipts[-1]['reading']['bottom'] is True)
    resources = receipts[0]['reading']['resources']
    require(all(type(resources[k]) is int and resources[k] >= 0 for k in ('ap', 'jewels')))
    wanted = [date.fromisoformat(day).month, date.fromisoformat(day).day]
    catalog, dispatched, count = {}, [], None
    last_index, last_at = 0, None
    for receipt in receipts:
        entry, page = receipt['entry'], receipt['reading']
        require(entry in ('OneKeyExecRead', 'OneKeyDo_enter', 'OneKeyDo_up', 'OneKeyDo_down'))
        require(completed(receipt['native'], 'OneKeyExecSnapshot' if entry == 'OneKeyExecRead'
                          else entry.replace('Do_', 'Input_')))
        require(all(not node.get('name', '').startswith('OneKeyInput_') or node['name'] in
                    ('OneKeyInput_enter', 'OneKeyInput_up', 'OneKeyInput_down')
                    for node in receipt['native']['nodes']))
        captured = datetime.fromisoformat(page['captured_at'])
        require(page['acquisition'] == 'current_tasker_capture'
                and page['session_id'] == context['session_id']
                and type(page['capture_index']) is int and page['capture_index'] > last_index
                and captured.tzinfo is not None and current_game_day(captured) == day
                and (last_at is None or captured >= last_at)
                and re.fullmatch('[0-9a-f]{64}', page['image_sha256']))
        last_index, last_at = page['capture_index'], captured
        if entry != 'OneKeyExecRead':
            dispatched.append(entry.removeprefix('OneKeyDo_'))
        if page['state'] == 'schedule':
            require(page['date'] == wanted, 'new_cycle_game_date_required')
            if 'delegated' in page['sections']:
                current = page['sections']['delegated']
                require(current['total'] == 9 and type(current['completed']) is int
                        and 0 <= current['completed'] <= 9
                        and (count is None or current == count))
                count = current
            if count is not None:
                for key, row in sorted(page['rows'].items(), key=lambda pair: pair[1]['y']):
                    require(key in ITEMS and row['state'] in ('pending', 'complete')
                            and (key not in catalog or catalog[key] == row['state']))
                    catalog[key] = row['state']
        else:
            require(page['state'] == 'home' and not catalog)
    require(list(catalog) == list(ITEMS) and catalog == proof['rows'] and count is not None
            and sum(state == 'complete' for state in catalog.values()) == count['completed'])
    require(dispatched and dispatched[0] == 'enter' and dispatched.count('enter') == 1
            and len(dispatched) <= 25)


def retain_unknown_for_new_cycle(journal, original, report, *, confirmed):
    from copy import deepcopy
    require(confirmed is True, 'new_cycle_refresh_confirmation_required')
    proof = deepcopy(dict(game_day=report['game_day'], confirmation='user-confirmed-game-refresh',
        navigation_context=report['navigation_context'], receipts=report['receipts'], rows=report['before_rows']))
    verify_observation(proof, original, report['game_day'], report['device'])
    require(journal.events()[-1] == original, 'recovery_history_changed')
    actions = journal.directory / 'actions' / original['intent_id'] / 'transactions.jsonl'
    end = {key: deepcopy(original[key]) for key in
           ('operation', 'game_day', 'device', 'intent_id', 'session_id', 'invocation_id')}
    end.update(state='historical_unknown', outcome='unknown', settlement_verified=False,
               original_replay_permitted=False, recovery=proof,
               previous_root_sha256=sha256(journal.path.read_bytes()).hexdigest(),
               original_actions_sha256=sha256(actions.read_bytes()).hexdigest())
    journal.append(end)
    return dict(original_intent_id=original['intent_id'], original_game_day=original['game_day'],
                game_day=report['game_day'], old_outcome='unknown', old_settlement_verified=False,
                confirmation='user-confirmed-game-refresh')


def verify_historical_unknown(journal, original, end, game_day, device):
    require(end.get('state') == 'historical_unknown' and end.get('outcome') == 'unknown'
            and end.get('settlement_verified') is False and end.get('original_replay_permitted') is False
            and not end.get('disposition') and not end.get('retry_of'))
    require(all(end.get(key) == original.get(key) for key in
                ('operation', 'game_day', 'device', 'intent_id', 'session_id', 'invocation_id')))
    require(original['device'] == device, 'new_cycle_device_binding_mismatch')
    lines = journal.path.read_bytes().splitlines(keepends=True)
    history = journal.events()
    indices = [i for i, event in enumerate(history) if event == end]
    require(len(indices) == 1 and indices[0] % 2 == 1)
    index = indices[0]
    require(history[index-1] == original
            and sha256(b''.join(lines[:index])).hexdigest() == end['previous_root_sha256'],
            'recovery_history_changed')
    actions = journal.directory / 'actions' / original['intent_id'] / 'transactions.jsonl'
    require(sha256(actions.read_bytes()).hexdigest() == end['original_actions_sha256'],
            'recovery_actions_changed')
    verify_observation(end['recovery'], original, game_day, device)
