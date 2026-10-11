"""One bounded daily-missions → gifts pass, using production Maa JSON only.

No retry after an attempted claim. Unknown dialogs stay on screen for the parent.
This does not execute the daily schedule and never writes the sweep ledger.
"""
import time
from uuid import uuid4

import cv2

from . import native


def completed(receipt, name):
    return receipt.get('native_ok') is True and any(
        node.get('name') == name and node.get('completed') is True
        for node in receipt.get('nodes', []))


def claim_rewards(tasker, *, execute=False, only=None, source='live rewards', collect_stamina=False):
    if type(execute) is not bool or type(collect_stamina) is not bool or only not in (None, 'missions', 'gifts'):
        raise ValueError('Rewards accepts only missions/gifts and a boolean execute')
    directory = native.ROOT / '.runtime/rewards-work' / ('run-' + uuid4().hex)
    directory.mkdir(parents=True)
    report = dict(source=source, execute=execute, collect_stamina=collect_stamina, daily_complete=False,
                  home_verified=False, outcome='unknown', items={}, evidence_dir=str(directory))

    def evidence(item, label, *, fresh=False):
        # Read-only recognition just completed: preserve its actual cached frame,
        # rather than a later screenshot that may already show another dialog.
        image = tasker.controller.cached_image
        if fresh:
            job = native.wait_job(tasker.controller.post_screencap())
            image = job.get() if job.succeeded else None
        path = directory / (label + '.png')
        if image is None or not native.write_png(path, image):
            raise RuntimeError('Cannot preserve reward evidence: ' + label)
        item['evidence'][label] = str(path)

    def run(item, entry, *, reset_hits=()):
        receipt = native.run(tasker, entry, reset_hits=reset_hits)
        item['native_receipts'].append(dict(entry=entry, receipt=receipt))
        return receipt

    def require(item, entry, node, *, reset_hits=()):
        receipt = run(item, entry, reset_hits=reset_hits)
        if not completed(receipt, node):
            raise RuntimeError('Unverified reward stage: ' + entry)
        return receipt

    item = None
    try:
        for kind in ([only] if only else ['missions', 'gifts']):
            prefix = 'Rewards' + kind.title()
            item = dict(status='unknown', home_verified=False, claim_attempted=False,
                        result_verified=False, claimable_before=None, claimable_after=None,
                        evidence={}, native_receipts=[])
            report['items'][kind] = item
            if kind == 'gifts' and report['items'].get('missions', {}).get('home_verified') is True:
                # No input since missions' home read; OpenStart still guards its current action frame.
                item['start_home_reused_from'] = 'missions'
            else:
                require(item, 'RewardsHomeRead', 'ObserveHome')
            evidence(item, kind + '-start-home')
            require(item, prefix + 'OpenStart', prefix + 'Open')
            if kind == 'gifts' and execute:
                mode = 'Include' if collect_stamina else 'Exclude'
                require(item, 'RewardsGifts' + mode + 'StaminaStart',
                        'RewardsGifts' + mode + 'Stamina',
                        reset_hits=('RewardsGifts' + mode + 'StaminaToggle',))
            before = run(item, prefix + 'Read')
            evidence(item, kind + '-before')
            ready = completed(before, prefix + 'Ready')
            empty = completed(before, prefix + 'Empty')
            deferred = kind == 'gifts' and completed(before, 'RewardsGiftsStaminaDeferred')
            if sum((ready, empty, deferred)) != 1:
                raise RuntimeError('Reward page or claimable state unknown')
            item['claimable_before'] = ready
            if deferred:
                item.update(status='deferred_stamina', stamina_deferred_verified=True)
            elif not ready:
                item['status'] = 'no_claimable'
            elif not execute:
                item['status'] = 'remaining'
            else:
                # Set before dispatch, including uncertain native job failures.
                item['claim_attempted'] = True
                claim = ('RewardsGiftsWithStaminaClaim' if kind == 'gifts' and collect_stamina
                         else prefix + 'Claim')
                require(item, claim + 'Start', claim, reset_hits=(claim,))
                if kind == 'gifts':
                    dispatched = run(item, 'RewardsGiftsAfterClaim')
                    evidence(item, 'gifts-dispatch')
                    if completed(dispatched, 'RewardsGiftsConfirm'):
                        require(item, 'RewardsGiftsConfirmClickStart', 'RewardsGiftsConfirmClick',
                                reset_hits=('RewardsGiftsConfirmClick',))
                    elif not completed(dispatched, 'RewardsGiftsResult'):
                        raise RuntimeError('Unknown gift confirmation or result; do not resend')
                require(item, prefix + 'ResultRead', prefix + 'Result')
                evidence(item, kind + '-result')
                item['result_verified'] = True
                require(item, prefix + 'CloseStart', prefix + 'Close',
                        reset_hits=(prefix + 'Close',))
                after = run(item, prefix + 'Read')
                evidence(item, kind + '-after')
                remaining = completed(after, prefix + 'Ready')
                empty = completed(after, prefix + 'Empty')
                deferred = kind == 'gifts' and completed(after, 'RewardsGiftsStaminaDeferred')
                if sum((remaining, empty, deferred)) != 1:
                    raise RuntimeError('Post-claim state unknown; do not resend')
                item['claimable_after'] = remaining
                if deferred:
                    item.update(status='deferred_stamina', stamina_deferred_verified=True)
                else:
                    item['status'] = 'remaining' if remaining else 'claimed'
            if not item['claim_attempted']:
                item['claimable_after'] = ready
                evidence(item, kind + '-after')
            require(item, prefix + 'HomeStart', prefix + 'Home')
            require(item, 'RewardsHomeRead', 'ObserveHome')
            evidence(item, kind + '-home')
            item['home_verified'] = True
        report['home_verified'] = True
        report['outcome'] = 'complete'
    except (Exception, KeyboardInterrupt) as exc:
        report['error'] = str(exc) or 'interrupted'
        if item is not None:
            item['error'] = report['error']
            # Preserve verified claim evidence even if returning home fails.
            try:
                # The cached frame remains readable even if a borrowed run is blocked.
                # Capture it before quarantining this entire callback root.
                evidence(item, 'stopped-' + str(time.time_ns()),
                         fresh=not isinstance(tasker, native.ContextTasker))
            except Exception as capture_error:
                item['capture_error'] = str(capture_error)
            finally:
                if isinstance(tasker, native.ContextTasker):
                    # StopTask may succeed as a nested lane while the business
                    # outcome is unknown. No later call may rearm that root.
                    tasker._blocked = True
                    if tasker._callback_scope is not None:
                        tasker._callback_scope.fail(exc)
    finally:
        import json
        (directory / 'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
