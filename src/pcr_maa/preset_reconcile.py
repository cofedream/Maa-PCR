"""Local, fail-closed reconciliation of one already-dispatched hard preset.

No Maa tasks, navigation, screenshot reads, or consumption are performed here.
Hashes bind supplied evidence; they are not authentication of its live origin.
"""
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
from types import SimpleNamespace

from .preset_execution import Journal, assess, check_quote
from .preset_execution_native import number
from .preset_confirmation import ConfirmationCollector, check_confirmation
from .preset_scan import Collector, Page, checked_glyph_detail, key


ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / '.runtime/preset-execution-work/ledger'
SOURCE = 'post-popup-native-result-and-full-counts'
ROLES = ('report', 'result', 'after', 'fit')
QUOTE_FIELDS = ('slot', 'identities', 'counts', 'stamina_before', 'stamina_after')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _object(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, 'duplicate_json_key:' + name)
        result[name] = value
    return result


def decode(data):
    def invalid(value):
        raise ValueError('nonfinite_json:' + value)
    return json.loads(data, object_pairs_hook=_object, parse_constant=invalid)


def _hash(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'invalid_sha256')


def _legacy_evidence_map(marker):
    if type(marker) is not dict or len(marker) != 5:
        return False
    paths = list(marker)
    if not all(isinstance(path, str) and path and PureWindowsPath(path).is_absolute()
               and PureWindowsPath(path).suffix in ('.log', '.png') for path in paths):
        return False
    if sum(PureWindowsPath(path).suffix == '.log' for path in paths) != 2:
        return False
    if len({PureWindowsPath(path) for path in paths}) != 5:
        return False
    values = list(marker.values())
    return (all(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                for value in values) and len(set(values)) == 5)


def strict_number(receipt, name):
    words = node(receipt, name)['recognition']['filtered']
    require(len(words) == 1 and type(words[0].get('score')) in (int, float)
            and .9 <= words[0]['score'] <= 1 and isinstance(words[0].get('text'), str)
            and re.fullmatch('[0-9]{1,9}', words[0]['text'].strip()), 'single_high_confidence_integer:' + name)
    return number(receipt, name)


def node(receipt, name, *, native_ok=True):
    require(receipt.get('tasker_inited') is True, name + ':tasker_unverified')
    if native_ok:
        require(receipt.get('native_ok') is True, name + ':native_failed')
    found = [n for n in receipt['nodes'] if n['name'] == name]
    require(len(found) == 1 and found[0].get('completed') is True, name + ':missing_or_duplicate')
    require((found[0].get('action') or {}).get('success') is True, name + ':action_unverified')
    return found[0]


def guards(detail, names):
    """Maa And raw_detail is a list of named recognition results, recursively."""
    seen = []
    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            if 'name' in value and 'algorithm' in value:
                seen.append(value)
            visit(value.get('detail'))
    visit(detail)
    for name in names:
        matches = [v for v in seen if v['name'] == name]
        # Nested And nodes can legitimately reuse a named detector (the
        # confirmation header also contains ExecConfirmButton).
        require(matches and all(isinstance(v.get('box'), list) and len(v['box']) == 4
                                and all(type(n) is int and n >= 0 for n in v['box'])
                                and v['box'][2] > 0 and v['box'][3] > 0 and v['algorithm'] != 'DirectHit'
                                for v in matches), 'guard_unproven:' + name)
    if 'SelectedSlot1' in names:
        require(not any(v['name'].startswith('SelectedSlot') and v['name'] != 'SelectedSlot1'
                        for v in seen), 'conflicting_slot_guard')


def scan_evidence(reading):
    # Historical r23 scans contain bound TemplateMatch receipts. Keep their
    # read-only protocol separate from the live color recognizer.
    class HistoricalPage(Page):
        def _checked(self, context, image, roi, *, evidence=None):
            require(image is None, 'historical_scan_replay_only')
            found = context.run_recognition('ScanCheckGlyph', image, {'ScanCheckGlyph': {'roi': roi}})
            raw = found.raw_detail if found else None
            if evidence is not None:
                evidence.update(raw=raw, hit=bool(found and found.hit))
            if not found or not found.hit or type(raw) is not dict:
                return False
            best, matches = raw.get('best'), raw.get('filtered')
            if type(best) is not dict or type(matches) is not list or len(matches) != 1:
                return False
            if 'count' in best or any(type(part) is dict and 'count' in part for part in matches):
                return checked_glyph_detail(raw, roi)
            score = best.get('score')
            return (type(raw.get('all')) is list and best == matches[0]
                    and type(score) in (int, float) and .9 <= score <= 1
                    and not any(type(part) is dict and 'count' in part for part in raw.get('all', [])))

    require(reading.get('requested_slot') == 1 and type(reading['requested_slot']) is int
            and reading.get('selected_slot') == 1 and type(reading['selected_slot']) is int,
            'scan_slot_mismatch')
    node(reading, 'ScanSlotReady')
    pages = reading['pages']
    require(isinstance(pages, list) and 1 <= len(pages) <= 9, 'scan_pages_missing')
    # A top-level list_complete/rows assertion cannot replace native collection.
    collected = []
    for n in reading['nodes']:
        if n.get('name') in ('ScanTop', 'ScanPage', 'ScanBottom', 'ScanShort'):
            require(n.get('completed') is True and (n.get('action') or {}).get('success') is True,
                    'scan_collection_failed')
            detail = n['recognition']['best']['detail']
            collected.append(decode(detail) if isinstance(detail, str) else detail)
    require(collected == pages, 'scan_pages_not_bound_to_native_nodes')
    collector = Collector()
    for page in pages:
        require(not collector.complete, 'scan_pages_after_completion')
        require(page.get('view') == 'checked', 'scan_page_view_unverified')
        evidence = page['evidence']
        require(evidence['ScanSlotGuard']['hit'] is True
                and evidence['ScanCheckedLabel']['hit'] is True, 'scan_page_guard_unverified')
        guards(evidence['ScanSlotGuard']['raw'], ['PresetsReached', 'SelectedSlot1'])
        unused_rows = list(page['rows'])
        def recognize(name, image, override=None):
            if name == 'ScanCheckGlyph':
                require(type(override) is dict and type(override.get('ScanCheckGlyph')) is dict,
                        'scan_check_glyph_roi_missing')
                roi = override['ScanCheckGlyph'].get('roi')
                require(type(roi) is list and len(roi) == 4
                        and all(type(n) is int for n in roi), 'scan_check_glyph_roi_invalid')
                matches = [row for row in page['rows']
                           if type(row.get('ocr_box')) is list and len(row['ocr_box']) == 4
                           and type(row['ocr_box'][1]) is int
                           and roi == [1098, max(230, row['ocr_box'][1] - 8), 80, 76]]
                require(len(matches) == 1 and any(row is matches[0] for row in unused_rows),
                        'scan_check_glyph_roi_mismatch')
                row = matches[0]
                unused_rows.remove(row)
                raw = row.get('check_evidence')
                best = raw.get('best') if type(raw) is dict else None
                box = best.get('box') if type(best) is dict else None
                require(row.get('checked') is True and type(box) is list and len(box) == 4
                        and all(type(n) is int and n >= 0 for n in box)
                        and box[2] > 0 and box[3] > 0
                        and roi[0] <= box[0] and roi[1] <= box[1]
                        and box[0] + box[2] <= roi[0] + roi[2]
                        and box[1] + box[3] <= roi[1] + roi[3],
                        'scan_check_glyph_evidence_mismatch')
                return SimpleNamespace(hit=True, raw_detail=raw)
            saved = evidence[name]
            return SimpleNamespace(hit=saved['hit'], raw_detail=saved['raw'])
        replay = HistoricalPage().analyze(SimpleNamespace(run_recognition=recognize), SimpleNamespace(
            image=None, custom_recognition_param=json.dumps(page['kind'])))
        require(replay is not None, 'scan_page_raw_evidence_failed')
        require(not unused_rows, 'scan_check_glyph_row_missing')
        detail = decode(replay.detail) if isinstance(replay.detail, str) else replay.detail
        require(detail == page, 'scan_page_raw_evidence_mismatch')
        require(collector.run(None, SimpleNamespace(reco_detail=SimpleNamespace(
            raw_detail={'best': {'detail': page}}))), 'scan_collection:' + str(collector.reason))
    require(collector.complete and collector.rows == reading['rows'], 'scan_aggregate_mismatch')
    require(len(reading['rows']) == 6, 'six_rows_required')
    for row in reading['rows']:
        require(row.get('checked') is True and type(row['remaining']) is int
                and type(row['limit']) is int and 0 <= row['remaining'] <= row['limit']
                and type(row.get('title_score')) in (int, float)
                and .8 <= row['title_score'] <= 1, 'scan_row_unverified')


def confirmation_evidence(confirmation):
    native = confirmation['evidence']
    require(native.get('native_ok') is True and native.get('tasker_inited') is True,
            'confirmation_native_failed')
    pages = []
    for n in native['nodes']:
        if n.get('name') in ('ExecScanTop', 'ExecScanPage', 'ExecScanBottom', 'ExecScanShort'):
            require(n.get('completed') is True and (n.get('action') or {}).get('success') is True,
                    'confirmation_collection_failed')
            detail = n['recognition']['best']['detail']
            pages.append(decode(detail) if isinstance(detail, str) else detail)
    require(pages and len(pages) <= 9 and pages == confirmation['pages'],
            'confirmation_pages_not_bound_to_native_nodes')
    collector = ConfirmationCollector()
    for page in pages:
        require(not collector.complete and collector.run(None, SimpleNamespace(
            reco_detail=SimpleNamespace(raw_detail={'best': {'detail': page}}))),
            'confirmation_collection_invalid')
    require(collector.complete and collector.rows == confirmation['rows']
            and pages[-1]['summary'] == confirmation['summary'], 'confirmation_aggregate_mismatch')


def _journal(data):
    require(data and data.endswith(b'\n'), 'journal_empty_or_torn_tail')
    lines = data.splitlines(keepends=True)
    events = [decode(line) for line in lines]
    intents_seen = set()
    for i, event in enumerate(events):
        require(event['state'] == ('in_flight' if i % 2 == 0 else 'settled'), 'journal_sequence_invalid')
        require(date.fromisoformat(event['game_day']).isoformat() == event['game_day'], 'journal_day_invalid')
        require(type(event['slot']) is int and 1 <= event['slot'] <= 7, 'journal_slot_invalid')
        if i % 2:
            intent = events[i - 1]
            require(event['game_day'] == intent['game_day'] and event['slot'] == intent['slot']
                    and event['identities'] == intent['quote']['identities'], 'journal_pair_mismatch')
            if 'context' in event:
                require(event['context'] == intent['context'], 'journal_pair_mismatch')
            else:
                # Only the first historical parent-assisted settlement predates
                # the context field. Check its five-file hash map's structure;
                # the historical files themselves are verified by the parent.
                marker = event.get('evidence_sha256')
                require(i == 1 and event['slot'] == 4 and event['game_day'] == '2026-09-23'
                        and event.get('verification_source') ==
                        'parent-assisted visual result + native complete post-counts; not automatic settlement'
                        and event.get('daily_complete') is False
                        and isinstance(event.get('manual_actions'), list)
                        and len(event['manual_actions']) == 2
                        and all(isinstance(action, str) for action in event['manual_actions'])
                        and _legacy_evidence_map(marker),
                        'journal_legacy_settlement_invalid')
            require(len(event['remaining']) == len(event['identities'])
                    and all(type(n) is int and n >= 0 for n in event['remaining']), 'journal_remaining_invalid')
        else:
            fingerprint = json.dumps(event, sort_keys=True, ensure_ascii=False)
            require(fingerprint not in intents_seen, 'duplicate_journal_intent')
            intents_seen.add(fingerprint)
            q = event['quote']
            require(q['slot'] == event['slot'] and len(q['identities']) == len(q['counts'])
                    and len(q['counts']) > 0 and all(type(n) is int and n > 0 for n in q['counts'])
                    and len({tuple(k) for k in q['identities']}) == len(q['identities'])
                    and type(q['stamina_before']) is int and type(q['stamina_after']) is int
                    and q['stamina_before'] > q['stamina_after'] >= 0, 'journal_quote_invalid')
    return lines, events


def validate(evidence, ledger_bytes, *, ledger_sha256, game_day, bindings):
    """Pure validation of parsed evidence and exact original journal bytes."""
    _hash(ledger_sha256)
    require(date.fromisoformat(game_day).isoformat() == game_day, 'invalid_game_day')
    lines, events = _journal(ledger_bytes)
    already = len(events) % 2 == 0
    prefix = b''.join(lines[:-1]) if already else ledger_bytes
    require(sha256(prefix) == ledger_sha256, 'ledger_prefix_sha256_mismatch')
    intent = events[-2] if already else events[-1]
    intent_id = sha256(lines[-2] if already else lines[-1])
    report, result, after, fit = (evidence[r] for r in ROLES)
    require(report['mode'] == 'execute-preset' and report['outcome'] == 'unknown_settlement'
            and report['consumption_sent'] is True and report['settlement_verified'] is False
            and report['daily_complete'] is False, 'original_report_not_postresult_unknown')
    preset = report['preset']
    require(preset['kind'] == 'hard' and type(preset['slot']) is int and preset['slot'] == 1
            and type(preset['target_per_stage']) is int and preset['target_per_stage'] == 3,
            'hard_slot1_target3_required')
    require(report['game_day'] == intent['game_day'] == game_day and intent['slot'] == 1,
            'intent_day_or_slot_mismatch')
    context = report['count_context']
    require(context == intent['context'] and context in ('user-reported-unreset', 'same-day-unreset'),
            'counter_context_unconfirmed')
    before, quote = report['reading'], report['quote']
    scan_evidence(before)
    status, goal = assess(before, preset, context)
    require(status == 'needs_execution' and check_quote(quote, before, goal) == 'ready', 'original_quote_not_safe')
    require(quote['counts'] == [3] * 6 and all(type(n) is int for n in quote['counts'])
            and type(quote['slot']) is int and quote['slot'] == 1
            and quote.get('identity_verified') is True, 'original_quote_not_six_threes')
    require(intent['quote'] == {k: quote[k] for k in QUOTE_FIELDS}, 'intent_quote_mismatch')
    qreceipt = quote['evidence']
    guards(node(qreceipt, 'ExecPresetGuardRead')['recognition'],
           ['PresetsReached', 'SelectedSlot1', 'ScanCheckedLabel'])
    node(qreceipt, 'ExecQuoteDone')
    for name, expected in [('ExecCount', 3), ('ExecStaminaBefore', quote['stamina_before']),
                           ('ExecStaminaAfter', quote['stamina_after'])]:
        require(strict_number(qreceipt, name) == expected, 'quote_ocr_mismatch:' + name)
    shortage = quote['shortage_evidence']
    require(not any(n.get('completed') is True and n.get('name') in
                    ('ExecStaminaShortage', 'ExecTicketsShortage') for n in shortage['nodes']),
            'explicit_resource_shortage')
    confirmation = report['confirmation']
    require(confirmation.get('verified') is True and check_confirmation(confirmation, before, quote),
            'confirmation_mismatch')
    node(confirmation['opening'], 'ExecConfirmHeader')
    confirmation_evidence(confirmation)
    submission = report['submission']
    # save_receipt serializes before the result_receipt path is added.
    require({k: v for k, v in submission.items() if k != 'result_receipt'} == result,
            'result_receipt_report_mismatch')
    require(Path(submission['result_receipt']).resolve() == Path(bindings['result']['path']),
            'original_result_receipt_path_mismatch')
    require(result['consumption_dispatched'] is True and result['result_verified'] is True
            and result['preset_return_verified'] is False and result['settlement_verified'] is False,
            'native_result_flags_mismatch')
    guards(node(result['native_receipt'], 'ExecConfirm')['recognition'],
           ['ExecConfirmButton', 'ExecConfirmHeader', 'ExecCommitGuard'])
    # Return failure makes the whole result task fail; the completed result node
    # remains evidence. Do not demand native_ok for that task.
    guards(node(result['result_evidence'], 'ExecResultConfirm', native_ok=False)['recognition'],
           ['ExecResultButton', 'ExecResultTitle', 'ExecResultComplete', 'ExecResultEvidence'])
    require(not any(n.get('name') == 'ExecReturnedPreset' and n.get('completed')
                    for n in result['result_evidence']['nodes']), 'contradictory_preset_return')
    require(after.get('source') == 'live preset inspection' and after.get('outcome') == 'ok'
            and after.get('navigation_complete') is True, 'need_independent_live_inspect_receipt')
    device = after['device']
    require(device['serial'] == '127.0.0.1:16384' and type(device['input_display']) is int
            and device['input_display'] == 2 and re.fullmatch('[0-9]{1,20}', device['screenshot_display']),
            'after_device_binding_unproven')
    require(after != before and after != report.get('after'), 'after_not_independent')
    for r in (after, fit):
        if 'game_day' in r:
            require(r['game_day'] == game_day, 'post_game_day_conflict')
        if 'device' in r:
            require(r['device'] == device, 'post_device_conflict')
    scan_evidence(after)
    status, after_goal = assess(after, preset, context)
    require(status == 'skipped_goal_met', 'after_goal_unverified')
    require([key(r) for r in after['rows']] == [key(r) for r in before['rows']], 'after_identity_order_mismatch')
    require(all(a['limit'] == b['limit'] and a['remaining'] == b['remaining'] - n
                for a, b, n in zip(after['rows'], before['rows'], quote['counts'])), 'after_counts_mismatch')
    count_fit = report['count_fit']
    require(count_fit.get('enabled') is True and count_fit.get('attempted') is True
            and all(type(count_fit[k]) is int and count_fit[k] == 3 for k in ('original', 'target', 'readback'))
            and intent['count_fit'] == {k: count_fit[k] for k in ('original', 'target', 'readback')},
            'original_count_fit_mismatch')
    # FitRead is a separate native receipt, not a count summary or image.
    require([n['name'] for n in fit['nodes']] == ['FitRead', 'FitReadGuard', 'FitCount'],
            'need_separate_read_only_FitRead')
    for name in ('FitRead', 'FitReadGuard', 'FitCount'):
        node(fit, name)
    guards(node(fit, 'FitReadGuard')['recognition'],
           ['FitPreset', 'PresetsReached', 'SelectedSlot1', 'ScanCheckedLabel', 'FitLabel'])
    require(strict_number(fit, 'FitCount') == count_fit['original'], 'fit_count_not_original')
    fact = dict(state='settled', game_day=game_day, slot=1, identities=quote['identities'],
                remaining=[r['remaining'] for r in after['rows']], context=context,
                verification_source=SOURCE, intent_id=intent_id, ledger_prefix_sha256=ledger_sha256,
                evidence=bindings, result_receipt=bindings['result']['path'],
                after_receipt=bindings['after']['path'], fit_receipt=bindings['fit']['path'],
                count_fit_verified=3, after_goal=after_goal, daily_complete=False,
                automatic_chain_success=False, consumption_authorized=False,
                game_day_source='caller_assertion; original report match',
                fit_source='native receipt; controller origin not recorded')
    if already:
        require(json.dumps(events[-1], sort_keys=True) == json.dumps(fact, sort_keys=True),
                'conflicting_or_unrelated_settlement')
    return fact, already


def reconcile(directory, files, hashes, *, ledger_sha256, game_day, apply=False):
    """Testable local API; only main() chooses the fixed production directory."""
    journal = Journal(directory)
    result = dict(eligible=False, reason='unverified', applied=False, daily_complete=False,
                  automatic_chain_success=False, consumption_authorized=False,
                  ledger_prefix_sha256=ledger_sha256)
    def check():
        require(journal.directory.is_dir() and journal.path.is_file(), 'existing_journal_required')
        require(set(files) == set(hashes) == set(ROLES), 'four_evidence_files_required')
        paths = {r: Path(files[r]).resolve(strict=True) for r in ROLES}
        require(len(set(paths.values())) == 4, 'independent_files_required')
        evidence, bindings = {}, {}
        for role, path in paths.items():
            _hash(hashes[role])
            data = path.read_bytes()
            require(sha256(data) == hashes[role], 'sha256_mismatch:' + role)
            evidence[role] = decode(data)
            bindings[role] = dict(path=str(path), sha256=hashes[role])
        require(len(set(hashes.values())) == 4, 'independent_evidence_required')
        result['evidence'] = bindings
        return validate(evidence, journal.path.read_bytes(), ledger_sha256=ledger_sha256,
                        game_day=game_day, bindings=bindings)
    try:
        fact, already = check()  # Never create a directory/lock on dry-run or invalid evidence.
        if apply:
            with journal.locked():
                fact, already = check()  # Re-read every supplied byte and the prefix under the same lock.
                if not already:
                    journal.append(fact)
                    result['applied'] = True
        result.update(eligible=True, reason='already_reconciled' if already else 'applied' if apply else 'eligible',
                      intent_id=fact['intent_id'], settlement=fact)
    except (ValueError, TypeError, KeyError, IndexError, OSError, AttributeError, StopIteration) as exc:
        result.update(reason=str(exc) or type(exc).__name__, required_evidence=
                      'Original full report/result, independent live inspect with native pages, '
                      'separate FitRead with SelectedSlot1 guard, matching unmodified journal prefix; '
                      'do not add invented fields or retry consumption.')
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument('--apply', action='store_true', help='append one verified fact to the existing fixed journal')
    p.add_argument('--game-day', required=True)
    p.add_argument('--ledger-sha256', required=True, help='SHA256 of the exact original in_flight prefix')
    for role in ROLES:
        p.add_argument('--' + role, required=True, type=Path)
        p.add_argument('--' + role + '-sha256', required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    # A different working directory or a junction must not select a new ledger.
    if LEDGER.resolve() != LEDGER.absolute() or (LEDGER / 'transactions.jsonl').is_symlink():
        print(json.dumps(dict(eligible=False, reason='redirected_ledger_refused', daily_complete=False)))
        return 2
    result = reconcile(LEDGER, {r: getattr(args, r) for r in ROLES},
                       {r: getattr(args, r + '_sha256') for r in ROLES},
                       ledger_sha256=args.ledger_sha256, game_day=args.game_day, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['eligible'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
