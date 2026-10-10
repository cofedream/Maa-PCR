"""GUI schedule: enabled one-key, known dialogs, disabled button, home.

The game's current button controls availability. Legacy CLI journals remain
untouched; a fresh GUI invocation does not reconcile an older unknown result.
"""
from copy import deepcopy
from datetime import date
import json
import re
import time
from uuid import uuid4

from . import native
from .preset_execution import Journal
from .schedule_house import completed


MODE = 'gui-onekey'
INPUTS = {
    'OneKeyQuickEnter': ([1017, 575], 1),
    'OneKeyQuickAdventureHome': ([105, 700], 1),
    'OneKeyQuickStart': ([880, 640], 1),
    'OneKeyQuickDungeonConfirm': ([785, 493], 1),
    'OneKeyQuickManaConfirm': ([785, 493], 1),
    'OneKeyQuickExpConfirm': ([785, 493], 1),
    'OneKeyQuickDungeonFinish': ([640, 640], 1),
    'OneKeyQuickSweepResult': ([640, 640], 3),
    'OneKeyQuickLimitedShopCancel': ([790, 634], 3),
    'OneKeyQuickLikeResult': ([640, 493], 1),
    'OneKeyQuickItemResult': ([640, 640], 1),
    'OneKeyQuickClose': ([370, 640], 1),
}


def gui_device(config):
    """Per-Start GUI target, never a cross-restart or historical ledger identity."""
    from .onekey_history import schedule_device
    controller = config.get('controller', {})
    if (config['version'] == 4 and controller.get('kind') == 'adb'
            and not {'ledger_identity', 'manager_identity'} & set(controller)):
        return dict(serial=config['connection']['serial'], source='gui_adb_session_v1',
                    app_package=controller['app_package'])
    return schedule_device(config)


def validate_gui_device(device):
    from .device import validate_serial
    from .onekey_history import stable_device, validate_stable_device
    from .schedule_onekey import validate_fixed_device
    if type(device) is dict and set(device) == {'serial', 'source', 'app_package'}:
        if (device['source'] != 'gui_adb_session_v1'
                or device['app_package'] != 'com.bilibili.priconne'):
            raise ValueError('gui_schedule_session_target_invalid')
        validate_serial(device['serial'])
        if not device['serial'].startswith(('emulator-', '127.0.0.1:')):
            raise ValueError('gui_schedule_local_target_required')
    elif stable_device(device):
        validate_stable_device(device)
    else:
        validate_fixed_device(device, portable=True)


def validate_gui_start(game_day, device, authorizations):
    from .schedule_onekey import ITEMS
    if type(game_day) is not str or date.fromisoformat(game_day).isoformat() != game_day:
        raise ValueError('current_gui_game_day_marker_required')
    validate_gui_device(device)
    if (not isinstance(authorizations, (tuple, list))
            or len(authorizations) != len(ITEMS) or set(authorizations) != set(ITEMS)):
        raise ValueError('current_gui_schedule_start_required')


def verified(result, *, game_day, device, authorizations, invocation_id=None):
    """Validate native completion without claiming nine-row/resource auditing."""
    try:
        if (result['mode'] != MODE or result.get('reason') or result.get('error')
                or result['outcome'] not in ('executed', 'unavailable')
                or result['game_day'] != game_day or result['device'] != device
                or result['authorizations'] != list(authorizations)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', result['invocation_id'])
                or (invocation_id is not None and result['invocation_id'] != invocation_id)
                or result['daily_complete'] is not False
                or result['home_verified'] is not True
                or result['button_unavailable_verified'] is not True):
            return False
        executed = result['outcome'] == 'executed'
        if (result['schedule_executed'] is not executed
                or result['one_key_attempted'] is not executed):
            return False
        records = result['receipts']
        if [r['entry'] for r in records] != ['OneKeyQuickPrepare',
                'OneKeyQuickStart' if executed else 'OneKeyQuickCloseOnly']:
            return False
        prepare, finish = [r['native'] for r in records]
        if (not completed(prepare, 'OneKeyQuickReady' if executed else 'OneKeyQuickUnavailable')
                or not completed(finish, 'OneKeyQuickFinished')
                or not completed(finish, 'OneKeyQuickHome')):
            return False
        counts = dict.fromkeys(INPUTS, 0)
        shop_after_sweep = False
        passive = {'OneKeyQuickPrepare', 'OneKeyQuickReady', 'OneKeyQuickUnavailable',
                   'OneKeyQuickCloseOnly', 'OneKeyQuickFinished', 'OneKeyQuickHome',
                   'OneKeyQuickHomeReady'}
        for receipt in (prepare, finish):
            if receipt['native_ok'] is not True or receipt['tasker_inited'] is not True:
                return False
            for node in receipt['nodes']:
                action = node.get('action') or {}
                name = node['name']
                # Maa ActionDetail.name is the pipeline node, not its action type.
                if (node['completed'] is not True or action.get('name') != name
                        or action.get('success') is not True):
                    return False
                if name not in INPUTS:
                    if name not in passive or action.get('raw') not in (None, {}):
                        return False
                    continue
                point, limit = INPUTS[name]
                counts[name] += 1
                if (counts[name] > limit
                        or action['raw'] != dict(point=point, contact=0, pressure=1)):
                    return False
                if name == 'OneKeyQuickSweepResult':
                    shop_after_sweep = True
                elif name == 'OneKeyQuickLimitedShopCancel':
                    if not shop_after_sweep:
                        return False
                    shop_after_sweep = False
                else:
                    shop_after_sweep = False
        return (counts['OneKeyQuickStart'] == int(executed)
                and counts['OneKeyQuickClose'] == 1
                and (executed or not any(counts[n] for n in INPUTS
                    if n not in ('OneKeyQuickEnter', 'OneKeyQuickAdventureHome', 'OneKeyQuickClose'))))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def schedule_quick(tasker, *, game_day, device, authorizations,
                   invocation_id=None, journal=None):
    invocation_id = invocation_id or uuid4().hex
    report = dict(mode=MODE, outcome='unknown', daily_complete=False,
                  game_day=game_day, device=deepcopy(device),
                  authorizations=list(authorizations), invocation_id=invocation_id,
                  one_key_attempted=False, schedule_executed=False,
                  button_unavailable_verified=False, home_verified=False, receipts=[])
    try:
        from .schedule_onekey import ITEMS
        date.fromisoformat(game_day)
        validate_gui_device(device)
        if (not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', invocation_id)
                or set(authorizations) != set(ITEMS) or len(authorizations) != len(ITEMS)):
            raise ValueError('current_gui_schedule_start_required')
        journal = journal or Journal(native.ROOT / '.runtime/schedule-onekey-work/gui-runs' / invocation_id)
        with journal.locked():
            if journal.path.exists():
                raise ValueError('invocation_already_dispatched_no_retry')
            # Reserve once before rearming this invocation's native input budget.
            journal.append(dict(state='in_flight', operation='schedule_onekey_gui',
                                invocation_id=invocation_id, game_day=game_day,
                                device=report['device'], at=time.time()))
            pipeline = json.loads((native.RESOURCE / 'pipeline/schedule_quick.json').read_text(encoding='utf-8'))
            def run(entry, *, reset_hits=()):
                receipt = native.run(tasker, entry, reset_hits=reset_hits)
                report['receipts'].append(dict(entry=entry, native=receipt))
                return receipt

            prepare = run('OneKeyQuickPrepare', reset_hits=tuple(pipeline))
            if completed(prepare, 'OneKeyQuickReady'):
                report['one_key_attempted'] = True
                finish = run('OneKeyQuickStart')
                report['schedule_executed'] = completed(finish, 'OneKeyQuickStart')
                outcome = 'executed'
            elif completed(prepare, 'OneKeyQuickUnavailable'):
                finish = run('OneKeyQuickCloseOnly')
                outcome = 'unavailable'
            else:
                raise ValueError('schedule_button_not_verified')
            report.update(outcome=outcome,
                          button_unavailable_verified=completed(finish, 'OneKeyQuickFinished'),
                          home_verified=completed(finish, 'OneKeyQuickHome'))
            if not verified(report, game_day=game_day, device=device,
                            authorizations=authorizations, invocation_id=invocation_id):
                raise ValueError('schedule_finish_unverified_no_retry')
            journal.append(dict(state='settled', operation='schedule_onekey_gui',
                                invocation_id=invocation_id, outcome=outcome,
                                game_day=game_day, device=report['device'],
                                receipts=report['receipts'], at=time.time()))
    except (Exception, KeyboardInterrupt) as exc:
        report.update(outcome='unknown', schedule_executed=False,
                      reason=str(exc) or 'interrupted_no_retry')
    return report
