"""Maintenance of the known consumed deep preset; no fabricated post-counters."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import time
from .abyss_reconcile import require, action_node, verify_native_actions, read_evidence_files, pin_native_log
from .preset_execution import assess, check_quote
from .preset_reconcile import guards, strict_number

DISPOSITION = 'verified_deep_result_postread_incomplete'


def verify_result(original, step):
    require(original.get('state') == 'in_flight' and original.get('slot') == 4 and
            original.get('context') == 'gui-current-state', 'deep_original_invalid')
    require(step.get('name') == 'deep' and step.get('status') == 'stopped', 'deep_step_invalid')
    r = step['result']; preset = r['preset']
    require(preset.get('kind') == 'deep' and preset.get('slot') == original['slot'] and
            r.get('game_day') == original['game_day'] and r.get('count_context') == original['context'] and
            r.get('outcome') == 'unknown_settlement' and r.get('error') == 'Callback scope blocked; no retry' and
            r.get('consumption_sent') is True and r.get('settlement_verified') is False and
            r.get('home_verified') is False and 'after' not in r, 'deep_failure_binding_invalid')
    reading, quote = r['reading'], r['quote']
    status, goal = assess(reading, preset, original['context'])
    require(status == 'needs_execution' and check_quote(quote, reading, goal) == 'ready' and
            original['quote'] == {k: quote[k] for k in
                ('slot', 'identities', 'counts', 'stamina_before', 'stamina_after')}, 'deep_quote_changed')
    fit = r['count_fit']; adjustment = fit['adjustment']
    require(original.get('count_fit') == {k: fit[k] for k in ('original', 'target', 'readback')} and
            fit['original'] == fit['target'] == fit['readback'] and adjustment.get('steps') == [] and
            adjustment.get('verified') is True and
            strict_number(adjustment['initial_evidence'], 'FitCount') == fit['original'],
            'deep_unknown_count_edit')
    submit = r['submission']
    require(all(submit.get(k) is True for k in
                ('consumption_dispatched', 'result_verified', 'preset_return_verified', 'shop_cancelled')),
            'deep_result_or_return_missing')
    commit = action_node(submit['native_receipt'], 'ExecConfirm')
    guards(commit['recognition'], ['ExecConfirmButton', 'ExecConfirmHeader', 'ExecCommitGuard'])
    receipt = submit['result_evidence']
    closed = action_node(receipt, 'ExecResultConfirm')
    guards(closed['recognition'], ['ExecResultButton', 'ExecResultTitle', 'ExecResultComplete', 'ExecResultEvidence'])
    cancelled = action_node(receipt, 'ExecLimitedShopCancel')
    guards(cancelled['recognition'], ['ExecLimitedShopButton', 'ExecLimitedShopTitle',
        'ExecLimitedShopNotice', 'ExecLimitedShopInstruction', 'ExecLimitedShopSession'])
    action_node(receipt, 'ExecReturnedPreset')
    images = submit['result_images']
    require([i.get('kind') for i in images] == ['result', 'limited_shop'] and
            len({i.get('task_id') for i in images}) == 1, 'deep_result_frames_invalid')
    for node, name, image in zip((closed, cancelled), ('ExecResultEvidence', 'ExecLimitedShopSession'), images):
        evidence = [part for part in node['recognition'] if part.get('name') == name]
        require(len(evidence) == 1 and evidence[0]['detail']['best']['detail'] == image,
                'deep_frame_not_action_bound')
    return images


def read_evidence(original, proof, *, journal_directory=None, terminal=None):
    require(proof.get('offline_result_and_shop_verified') is True and proof.get('device_operations') == 0,
            'deep_offline_verification_missing')
    files = proof['files']; require(set(files) == {'step', 'result_frame', 'shop_frame', 'native_log'},
                                    'deep_evidence_incomplete')
    blobs = read_evidence_files(files, journal_directory=journal_directory, terminal=terminal)
    step = json.loads(blobs['step']); images = verify_result(original, step)
    for name, image in zip(('result_frame', 'shop_frame'), images):
        require(files[name]['sha256'] == image['sha256'] and Path(files[name]['path']) == Path(image['path']),
                'deep_result_frame_changed')
    s = step['result']['submission']
    verify_native_actions(blobs['native_log'], original['at'],
        [('ExecConfirm', s['native_receipt']), ('ExecResultConfirm', s['result_evidence']),
         ('ExecLimitedShopCancel', s['result_evidence'])], proof.get('native_log_utc_offset_seconds'))
    return step


def verify_disposition(original, end, *, journal_directory=None):
    require(end.get('state') == 'settled' and end.get('disposition') == DISPOSITION and
            all(end.get(k) == original.get(k) for k in ('game_day', 'slot', 'context')) and
            end.get('identities') == original['quote']['identities'], 'deep_terminal_binding_changed')
    require(end.get('consumption_verified') is True and end.get('settlement_verified') is False and
            end.get('post_counts_verified') is False and end.get('home_verified') is False and
            end.get('original_replay_permitted') is False and 'remaining' not in end,
            'deep_terminal_claims_unobserved_counts')
    read_evidence(original, end['evidence'], journal_directory=journal_directory, terminal=end)


def reconcile_closed_result(journal, proof, *, expected_sha256, apply=False):
    with journal.locked():
        prefix = journal.path.read_bytes()
        require(sha256(prefix).hexdigest() == expected_sha256, 'deep_ledger_changed')
        events = journal.events(); require(events and events[-1]['state'] == 'in_flight', 'deep_no_unresolved_intent')
        original = events[-1]; read_evidence(original, proof)
        end = {k: deepcopy(original[k]) for k in ('game_day', 'slot', 'context')}
        end.update(state='settled', disposition=DISPOSITION, identities=deepcopy(original['quote']['identities']),
            consumption_verified=True, result_close_verified=True, preset_return_verified=True,
            count_edit_was_noop=True, settlement_verified=False, post_counts_verified=False, home_verified=False,
            original_replay_permitted=False, previous_root_sha256=expected_sha256, evidence=deepcopy(proof), at=time.time())
        verify_disposition(original, end)
        if apply:
            pin_native_log(proof, journal.directory)
            journal.append(end); require(journal.path.read_bytes().startswith(prefix), 'deep_original_prefix_changed')
        return dict(outcome=DISPOSITION, applied=apply, consumption_verified=True, settlement_verified=False,
            post_counts_verified=False, home_verified=False, original_replay_permitted=False)
