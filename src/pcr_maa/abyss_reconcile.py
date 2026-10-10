"""Verify a consumed abyss sweep whose result close succeeded before navigation failed.

This maintenance disposition records known consumption and preserves the failed
report. It never claims post-counters/home, never replays the original consumer,
and blocks further consumption in the original game day unless zero is observed.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time

from .abyss import SCOPE, assess_home, check_quote, check_ticket

DISPOSITION = 'verified_result_close_navigation_incomplete'


def require(ok, reason):
    if not ok:
        raise ValueError('abyss_reconcile:' + reason)


def action_node(receipt, name, *, root_ok=True):
    require(receipt.get('tasker_inited') is True and
            (not root_ok or receipt.get('native_ok') is True), 'native_receipt_invalid')
    nodes = [n for n in receipt.get('nodes', []) if n.get('name') == name]
    require(len(nodes) == 1 and nodes[0].get('completed') is True and
            (nodes[0].get('action') or {}).get('name') == name and
            (nodes[0].get('action') or {}).get('success') is True, 'action_not_verified:' + name)
    return nodes[0]


def detail(node):
    value = (node.get('recognition') or {}).get('best', {}).get('detail')
    return json.loads(value) if isinstance(value, str) else value


def verify_result(original, step):
    require(original.get('state') == 'in_flight' and original.get('scope') == SCOPE and
            original.get('operation') == 'abyss_outpost' and
            re.fullmatch('[0-9a-f]{32}', original.get('intent_id', '')), 'original_intent_invalid')
    require(step.get('name') == 'abyss' and step.get('status') == 'stopped', 'step_invalid')
    result = step['result']
    require(all(result.get(k) == original.get(k) for k in
                ('scope', 'intent_id', 'game_day', 'device')), 'intent_binding_changed')
    require(result.get('outcome') == 'unknown_settlement' and
            result.get('consumer_dispatched') is True and result.get('consumption_sent') is True and
            result.get('settlement_verified') is False and result.get('home_verified') is False,
            'not_consumed_navigation_failure')
    reading, quote = result['reading'], original['quote']
    require(assess_home(reading) == 'needs_execution' and check_quote(quote, reading) == 'ready' and
            result.get('quote') == quote and reading['abyss_identity'] == original['abyss_identity'],
            'original_quote_invalid')
    require(result['confirmation'].get('reading') == original['final_confirmation'] and
            result['confirmation'].get('opening_receipt') == original['opening_receipt'],
            'opening_binding_changed')
    submission = result['submission']
    require(submission.get('consumption_dispatched') is True and
            submission.get('abyss_return_verified') is False, 'submission_invalid')
    commit = action_node(submission['native_receipt'], 'AbyssTicketCommit')
    require(check_ticket(detail(commit), quote, reading) == 'ready', 'committed_quote_changed')
    receipt = submission['result_evidence']
    require(receipt.get('native_ok') is False and
            not any(n.get('completed') and n.get('name') != 'AbyssResultClose'
                    for n in receipt.get('nodes', [])), 'failure_not_result_navigation')
    closed = action_node(receipt, 'AbyssResultClose', root_ok=False)
    images = submission['result_images']
    require(len(images) == 1 and detail(closed) == images[0], 'result_frame_not_action_bound')
    image = images[0]
    require(image.get('kind') == 'result' and image.get('scope') == SCOPE and
            image.get('abyss_identity') == original['abyss_identity'] and
            image.get('counts') == [row['times'] for row in quote['rows']] and
            re.fullmatch('[0-9a-f]{64}', image.get('sha256', '')), 'result_identity_changed')
    for name, text in [('ExecResultTitle', '扫荡结果'), ('ExecResultComplete', '扫荡完成！'),
                       ('ExecResultButton', '确认')]:
        evidence = image['evidence'][name]
        words = evidence['raw']['filtered']
        require(evidence.get('hit') is True and len(words) == 1 and
                words[0].get('text') == text and words[0].get('score', 0) >= .9,
                'result_anchor_invalid:' + name)
    return image


def verify_native_actions(log_bytes, intent_at, expected, offset):
    require(type(offset) is int and -50400 <= offset <= 50400, 'log_timezone_missing')
    found = []
    for line in log_bytes.decode('utf-8', errors='replace').splitlines():
        if 'EventDispatcher.hpp' not in line or not any(
                '[msg=Node.Action.' + state + ']' in line for state in ('Starting', 'Succeeded')):
            continue
        match = re.search(r'^\[([^]]+)\].*?\[Px([0-9a-fA-F]+)\].*\[details=(\{.*\})\]', line)
        if not match:
            continue
        at = datetime.strptime(match[1], '%Y-%m-%d %H:%M:%S.%f').replace(
            tzinfo=timezone(timedelta(seconds=offset))).timestamp()
        if not intent_at <= at <= intent_at + 180:
            continue
        event = json.loads(match[3])
        if any(event.get('name') == name and event.get('task_id') == receipt['task_id']
               for name, receipt in expected):
            state = 'Starting' if '[msg=Node.Action.Starting]' in line else 'Succeeded'
            found.append((event['name'], state, match[2], event))
    require([(name, state) for name, state, _, _ in found] ==
            [(name, state) for name, _ in expected for state in ('Starting', 'Succeeded')]
            and len({process for _, _, process, _ in found}) == 1, 'native_actions_missing_or_repeated')
    for index, (name, receipt) in enumerate(expected):
        event = found[index * 2 + 1][3]
        action = event.get('action_details', {})
        require(action.get('success') is True and action.get('action') == 'Click' and
                action.get('detail') == action_node(receipt, name, root_ok=False)['action']['raw'] and
                event.get('action_id') == found[index * 2][3].get('action_id'),
                'native_action_binding_changed')


def pinned_native_log_path(journal_directory, digest):
    require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest),
            'evidence_sha256_invalid')
    path = Path(journal_directory).resolve() / 'result-evidence' / (digest + '.log')
    require(not path.parent.is_symlink() and not path.is_symlink() and path.resolve() == path,
            'evidence_cache_path_invalid')
    return path


def read_stored_native_log(row, journal_directory, terminal):
    path = pinned_native_log_path(journal_directory, row['sha256'])
    try:
        path.stat()
    except FileNotFoundError:
        require(isinstance(terminal, dict), 'evidence_recovery_terminal_missing')
        manifest_path = path.with_suffix('.recovery.json')
        require(manifest_path.is_file() and not manifest_path.is_symlink() and
                manifest_path.resolve() == manifest_path, 'evidence_recovery_file_invalid')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        require(isinstance(manifest, dict) and set(manifest) == {'version', 'previous_root_sha256',
            'terminal_sha256', 'original_native_log_sha256', 'replacement_native_log_sha256',
            'replacement_bytes'}, 'evidence_recovery_invalid')
        require(type(manifest['version']) is int and manifest['version'] == 1 and
                manifest['previous_root_sha256'] == terminal.get('previous_root_sha256') and
                manifest['terminal_sha256'] == sha256(json.dumps(terminal, ensure_ascii=False,
                    sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest() and
                manifest['original_native_log_sha256'] == row['sha256'] and
                type(manifest['replacement_bytes']) is int and manifest['replacement_bytes'] > 0,
                'evidence_recovery_binding_changed')
        digest = manifest['replacement_native_log_sha256']
        replacement = pinned_native_log_path(journal_directory, digest)
        blob = read_evidence_files({'native_log': dict(path=str(replacement), sha256=digest)})['native_log']
        require(len(blob) == manifest['replacement_bytes'], 'evidence_recovery_bytes_changed')
        return blob
    return read_evidence_files({'native_log': dict(path=str(path), sha256=row['sha256'])})['native_log']


def read_evidence_files(files, *, journal_directory=None, terminal=None):
    blobs = {}
    for name, row in files.items():
        path, digest = Path(row['path']), row['sha256']
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest),
                'evidence_sha256_invalid')
        require(path.is_absolute() and not path.is_symlink(), 'evidence_file_invalid')
        try:
            path.stat()
        except FileNotFoundError:
            if name == 'native_log' and journal_directory is not None:
                blobs[name] = read_stored_native_log(row, journal_directory, terminal)
                continue
        require(path.is_file() and not path.is_symlink(), 'evidence_file_invalid')
        blobs[name] = path.read_bytes()
        require(sha256(blobs[name]).hexdigest() == digest, 'evidence_changed:' + name)
    return blobs


def pin_native_log(proof, journal_directory):
    """Preserve the verified original before its rotating debug log disappears."""
    row = proof['files']['native_log']
    blob = read_evidence_files({'native_log': row})['native_log']
    path = pinned_native_log_path(journal_directory, row['sha256'])
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        require(path.is_file() and not path.is_symlink() and
                sha256(path.read_bytes()).hexdigest() == row['sha256'], 'evidence_cache_changed')


def read_evidence(original, proof, *, journal_directory=None, terminal=None):
    require(proof.get('offline_result_and_shop_verified') is True and
            proof.get('device_operations') == 0, 'offline_verification_missing')
    files = proof['files']
    require(set(files) == {'step', 'result_frame', 'failed_shop_frame', 'native_log'}, 'evidence_incomplete')
    blobs = read_evidence_files(files, journal_directory=journal_directory, terminal=terminal)
    step = json.loads(blobs['step'])
    image = verify_result(original, step)
    require(files['result_frame']['sha256'] == image['sha256'] and
            Path(files['result_frame']['path']) == Path(image['path']), 'result_frame_changed')
    verify_native_actions(blobs['native_log'], original['at'],
        [('AbyssTicketCommit', step['result']['submission']['native_receipt']),
         ('AbyssResultClose', step['result']['submission']['result_evidence'])],
        proof.get('native_log_utc_offset_seconds'))
    return step


def verify_disposition(original, end, *, device, journal_directory=None):
    require(end.get('state') == 'settled' and end.get('disposition') == DISPOSITION and
            original.get('device') == device and
            all(end.get(k) == original.get(k) for k in
                ('scope', 'operation', 'intent_id', 'game_day', 'device', 'abyss_identity')),
            'terminal_binding_changed')
    require(end.get('consumption_verified') is True and end.get('result_close_verified') is True and
            end.get('settlement_verified') is False and end.get('post_counts_verified') is False and
            end.get('home_verified') is False and end.get('original_replay_permitted') is False and
            'remaining' not in end, 'terminal_claims_unobserved_counts')
    read_evidence(original, end['evidence'], journal_directory=journal_directory, terminal=end)


def reconcile_closed_result(journal, proof, *, expected_sha256, apply=False):
    """Caller selects and pins the original ledger; default is read-only."""
    with journal.locked():
        prefix = journal.path.read_bytes()
        require(sha256(prefix).hexdigest() == expected_sha256, 'ledger_changed')
        events = journal.events()
        require(events and events[-1]['state'] == 'in_flight', 'no_unresolved_intent')
        original = events[-1]
        read_evidence(original, proof)
        end = {k: deepcopy(original[k]) for k in
               ('scope', 'operation', 'intent_id', 'game_day', 'device', 'abyss_identity')}
        end.update(state='settled', disposition=DISPOSITION, consumption_verified=True,
                   result_close_verified=True, settlement_verified=False, post_counts_verified=False,
                   home_verified=False, original_replay_permitted=False, evidence=deepcopy(proof),
                   previous_root_sha256=expected_sha256, at=time.time())
        verify_disposition(original, end, device=original['device'])
        if apply:
            pin_native_log(proof, journal.directory)
            journal.append(end)
            require(journal.path.read_bytes().startswith(prefix), 'original_prefix_changed')
        return dict(outcome=DISPOSITION, applied=apply, original_intent_id=original['intent_id'],
                    consumption_verified=True, settlement_verified=False, post_counts_verified=False,
                    home_verified=False, original_replay_permitted=False)
