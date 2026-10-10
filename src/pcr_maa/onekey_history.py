"""Small locked history reader; legacy incident verifiers are loaded only on demand.

No migration, reconciliation/apply, native input, or automatic closure here.
"""
from pathlib import Path
from contextvars import ContextVar
import re
import stat

# Used only while verifying the fixed parent-assisted archive in a full copy.
# Original absolute screenshot references resolve to copied files, never originals.
_EVIDENCE_RELOCATION = ContextVar('onekey_parent_assisted_evidence_relocation', default=None)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def schedule_device(config):
    """Ledger identity from validated config, never a live display qualification.

    v3 and v4 MuMu describe the same instance/clone/package. Legacy display-only
    records cannot be attributed to that instance just by matching a serial.
    """
    from copy import deepcopy
    from .device import SERIAL
    device = dict(serial=config.get('connection', {}).get('serial', SERIAL))
    if config['version'] == 3:
        device['mumu'] = deepcopy(config['mumu'])
    elif config['version'] == 4:
        controller = config['controller']
        if controller['kind'] == 'adb':
            if 'manager_identity' in controller:
                device['manager'] = deepcopy(controller['manager_identity'])
                validate_stable_device(device)
                return device
            require('ledger_identity' in controller,
                    '日程缺少可靠的身份来源：标准 ADB 显示映射不能证明 MuMu 实例/克隆；'
                    '需要维护端补齐可核验的设备身份来源，无需玩家准备旧 JSON 或填写工程字段。'
                    '[schedule_identity_source_unverified]')
            device['mumu'] = deepcopy(controller['ledger_identity'])
        else:
            require(controller['kind'] == 'mumu_extras', 'schedule_stable_instance_identity_required')
            device['mumu'] = deepcopy({k: v for k, v in controller.items() if k != 'kind'})
    else:
        device.update(config['device'])
    return device


def validate_stable_device(device):
    from .daily import validate_mumu, validate_manager_identity
    from .device import validate_serial
    require(type(device) is dict and set(device) in ({'serial', 'mumu'}, {'serial', 'manager'}),
            'schedule_stable_instance_identity_required')
    validate_serial(device['serial'])
    if 'manager' in device:
        validate_manager_identity(device['manager'])
    else:
        validate_mumu(device['mumu'], bounded_index=False)


def stable_device(device):
    return isinstance(device, dict) and ('mumu' in device or 'manager' in device)


def compatible_closed_device(original, current):
    """Only closed native-Extras evidence can precede the local ADB marker.

    No claim that clone 0 or a creation timestamp proves clone/import identity.
    Non-default clones and different installations/instances remain unsupported.
    Once Manager evidence exists its complete marker must match exactly.
    """
    validate_stable_device(original)
    validate_stable_device(current)
    if original == current:
        return True
    if 'mumu' not in original or 'manager' not in current:
        return False
    old, new = original['mumu'], current['manager']
    return (original['serial'] == current['serial'] and old['index'] == new['index']
            and old['app_cloned_index'] == 0 and old['app_package'] == new['app_package']
            and Path(old['path']) == Path(new['installation']))


def verify_execution(result, start, end, actions, *, invocation_id=None):
    """Consistency of producer receipts and paired Journal evidence, not a signature.

    No filesystem reads: report inspection remains selected_report_only. Historical
    callers supply the actual locked action ledger, not the report's copy.
    """
    from .schedule_onekey import ITEMS, OneKeyPage
    from .schedule_house import completed
    reason = 'execution_evidence_unverified'
    context = result.get('navigation_context', {})
    intent, session = start.get('intent_id'), start.get('session_id')
    require(isinstance(intent, str) and re.fullmatch('[0-9a-f]{32}', intent)
            and isinstance(session, str) and re.fullmatch('[0-9a-f]{32}', session), reason)
    require(context == dict(run_id=intent, session_id=session, device=start.get('device'),
                           game_day=start.get('game_day'), invocation_id=start.get('invocation_id')),
            reason)
    require(bool(start.get('invocation_id')) and (invocation_id is None or
            start['invocation_id'] == invocation_id), reason)
    for key in ('intent_id', 'session_id', 'device', 'game_day', 'invocation_id'):
        require(start.get(key) == end.get(key), reason)
    for key in ('home_verified', 'settlement_verified', 'nine_rows_verified'):
        require(result.get(key) is True and end.get(key) is True, reason)
    require(start.get('state') == 'in_flight' and end.get('state') == 'settled'
            and start.get('operation') == end.get('operation') == 'schedule_onekey'
            and start.get('device') == result.get('device') and start.get('game_day') == result.get('game_day')
            and start.get('authorizations') == result.get('authorizations')
            and end.get('outcome') == result.get('outcome') == 'executed'
            and end.get('rows') == result.get('after_rows') == dict.fromkeys(ITEMS, 'complete')
            and result.get('settlement_verified') is True and result.get('home_verified') is True
            and result.get('nine_rows_verified') is True, reason)
    resources = result.get('resources', {})
    require(resources == end.get('resources') and resources.get('jewels_delta') == 0
            and type(resources.get('jewels_delta')) is int, reason)
    receipts = result.get('receipts')
    require(isinstance(receipts, list) and receipts and receipts == end.get('receipts'), reason)
    tasks, inputs, reads = set(), [], []
    last_capture = 0
    for receipt in receipts:
        native, page = receipt['native'], receipt['reading']
        task = native.get('task_id')
        require(type(task) is int and task > 0 and task not in tasks and native.get('native_ok') is True, reason)
        tasks.add(task)
        require(isinstance(page, dict) and page.get('session_id') == session
                and page.get('acquisition') == 'current_tasker_capture'
                and isinstance(page.get('image_sha256'), str)
                and re.fullmatch('[0-9a-f]{64}', page['image_sha256'])
                and type(page.get('capture_index')) is int and page['capture_index'] > last_capture, reason)
        last_capture = page['capture_index']
        entry = receipt['entry']
        if entry == 'OneKeyExecRead':
            require(completed(native, 'OneKeyExecSnapshot'), reason)
            reads.append(page)
        else:
            require(entry.startswith('OneKeyDo_'), reason)
            op = entry[len('OneKeyDo_'):]
            name = 'OneKeyInput_' + op
            require(completed(native, name), reason)
            nodes = [n for n in native['nodes'] if n.get('name') == name]
            require(len(nodes) == 1 and nodes[0]['action'].get('name') == name
                    and isinstance(nodes[0]['action'].get('raw'), dict)
                    and bool(nodes[0]['action']['raw']), reason)
            if op == 'onekey':
                require(nodes[0]['action']['raw'] == dict(point=[880,640],contact=0,pressure=1), reason)
            inputs.append((op, task, page['image_sha256']))
    require([op for op, _, _ in inputs].count('onekey') == 1, reason)
    require(reads[0].get('state') == reads[-1].get('state') == 'home'
            and reads[0].get('resources') == resources.get('before')
            and reads[-1].get('resources') == resources.get('after')
            and reads[-1]['image_sha256'] == end.get('image_sha256'), reason)
    before, after = resources['before'], resources['after']
    require(type(before.get('jewels')) is int and before['jewels'] >= 0
            and type(after.get('jewels')) is int and before['jewels'] == after['jewels'], reason)
    require(type(before.get('ap')) is int and before['ap'] >= 0
            and type(after.get('ap')) is int and after['ap'] >= 0
            and type(resources.get('ap_delta')) is int
            and resources['ap_delta'] == after['ap'] - before['ap'], reason)
    # Final scan after the last consumer/result must independently cover nine rows.
    final_rows, counter_verified = {}, False
    for receipt in reversed(receipts[:-1]):
        if receipt['entry'].startswith('OneKeyDo_') and receipt['entry'][len('OneKeyDo_'):] not in ('close','down','up'):
            break
        page = receipt['reading']
        if page.get('state') == 'schedule':
            count = page.get('sections', {}).get('delegated')
            if count is not None:
                require(count == dict(completed=9,total=9), reason)
                counter_verified = True
            for key, row in page.get('rows', {}).items():
                require(row.get('state') == 'complete', reason)
                final_rows[key] = row['state']
    require(final_rows == end['rows'] and counter_verified, reason)
    require(isinstance(actions, list) and len(actions) == 2 * len(inputs), reason)
    for (op, task, guard_sha), a, b in zip(inputs, actions[::2], actions[1::2]):
        require(a.get('state') == 'in_flight' and b.get('state') == 'settled'
                and a.get('operation') == b.get('operation')
                and a['operation'].split(':')[0] == op and b.get('native_task_id') == task, reason)
        for key in ('intent_id', 'session_id', 'device', 'game_day'):
            require(a.get(key) == b.get(key) == start.get(key), reason)
        index = next(i for i, r in enumerate(receipts) if r['native']['task_id'] == task)
        require(index > 0 and receipts[index-1]['entry'] == 'OneKeyExecRead', reason)
        before, guard = receipts[index-1]['reading'], receipts[index]['reading']
        require(a.get('image_sha256') == before['image_sha256'], reason)
        expected, current = OneKeyPage.signature(before), OneKeyPage.signature(guard)
        summary_advanced = (op == 'dungeon_finish'
                            and OneKeyPage.dungeon_summary_advanced(expected, current)
                            and guard.get('evidence', {}).get('OneKeyGuard_sweep_result', {}).get('hit') is True
                            and any(n.get('name') == 'OneKeyInput_dungeon_finish'
                                    and n['action']['raw'] == dict(point=[640,640],contact=0,pressure=1)
                                    for n in receipts[index]['native']['nodes']))
        require(OneKeyPage.navigation_matches(expected, current) if op in ('up','down','close')
                else expected == current or summary_advanced, reason)
        following = []
        for receipt in receipts[index+1:]:
            if receipt['entry'] != 'OneKeyExecRead':
                break
            following.append(receipt['reading'])
        require(following and b.get('image_sha256') == following[-1]['image_sha256']
                and following[-1].get('state') != 'unknown', reason)


def preflight_history(game_day, device):
    """Before Resource/connection; existing history only, and rechecked under root lock later."""
    from . import native
    from .preset_execution import Journal
    journal = Journal(native.ROOT / '.runtime/schedule-onekey-work/ledger')
    if not journal.directory.exists():
        return
    path_in(native.ROOT, journal.directory)
    with journal.locked():
        read_history(journal, game_day, device)


def path_in(root, value):
    path = Path(value)
    require('..' not in path.parts, 'path_traversal')
    relocation = _EVIDENCE_RELOCATION.get()
    if relocation is not None:
        original, target = relocation
        if root == target and path.is_absolute() and not path.is_relative_to(target):
            require(path.is_relative_to(original), 'path_outside_original_project')
            path = target / path.relative_to(original)
    if not path.is_absolute():
        path = root / path
    require(path.is_relative_to(root), 'path_outside_project')
    info = root.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 1024,
            'linked_project')
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 1024,
                'linked_or_reparse_path')
        if stat.S_ISREG(info.st_mode):
            require(info.st_nlink == 1, 'hardlinked_evidence')
    return path



def settled_history(journal, events, game_day, device, *, prefix_intents=(), readonly=False):
    """Root lock is held. No terminal label alone proves game settlement."""
    from .preset_execution import Journal
    from .schedule_onekey import ITEMS
    completed_rows, intents = set(), set(prefix_intents)
    for start, end in zip(events[::2], events[1::2]):
        intent = start.get('intent_id')
        if end.get('state') == 'historical_unknown':
            from .schedule_recovery import verify_historical_unknown
            require(intent not in intents, 'duplicate_history_intent')
            verify_historical_unknown(journal, start, end, game_day, device)
            intents.add(intent)
            continue
        binding = start.get('device') if 'manager' in device else device
        if 'manager' in device:
            require(stable_device(binding) and compatible_closed_device(binding, device),
                    'history_identity_unverified:' + str(intent))
        if (not isinstance(intent, str) or not re.fullmatch('[0-9a-f]{32}', intent)
                or intent in intents or end.get('intent_id') != intent
                or start.get('game_day') != end.get('game_day')
                or start.get('device') != binding or end.get('device') != binding
                or end.get('disposition') or end.get('outcome') not in ('executed', 'already_complete')):
            raise ValueError('settled_history_unverified')
        intents.add(intent)
        rows = end.get('rows')
        if (not isinstance(rows, dict) or set(rows) != set(ITEMS)
                or any(s not in ('pending', 'complete') for s in rows.values())):
            raise ValueError('settled_history_rows_unverified')
        actions = Journal(journal.directory / 'actions' / intent)
        # No creation of a missing historical ledger as substitute evidence.
        path_in(journal.directory, actions.path)
        from contextlib import nullcontext
        with nullcontext() if readonly else actions.locked():
            if readonly:
                from .navigation_abort import events as parse_events
                history = parse_events(actions.path.read_bytes())
            else:
                history = actions.events()
            if not history or history[-1]['state'] == 'in_flight':
                raise ValueError('unsettled_action_any_day_no_resend')
            for before, after in zip(history[::2], history[1::2]):
                if stable_device(binding):
                    validate_stable_device(before.get('device'))
                    validate_stable_device(after.get('device'))
                if (before.get('operation') != after.get('operation')
                        or before.get('game_day') != start['game_day']
                        or after.get('game_day') != start['game_day'] or after.get('disposition')):
                    raise ValueError('settled_action_history_unverified')
                for key in ('device', 'intent_id', 'session_id'):
                    if ('session_id' in start or key in before or key in after) and (
                            before.get(key) != start.get(key) or after.get(key) != start.get(key)):
                        raise ValueError('settled_action_history_unverified')
            if end['outcome'] == 'executed':
                result = dict(outcome='executed', game_day=start['game_day'], device=binding,
                    authorizations=start.get('authorizations'), after_rows=rows,
                    receipts=end.get('receipts'), resources=end.get('resources'),
                    navigation_context=dict(run_id=intent, session_id=start.get('session_id'),
                        invocation_id=start.get('invocation_id'), device=binding, game_day=start['game_day']),
                    **{key:end.get(key) for key in ('home_verified','settlement_verified','nine_rows_verified')})
                verify_execution(result, start, end, history)
            elif any(e.get('operation', '').split(':')[0] in
                     ('onekey', 'dungeon_confirm', 'mana_confirm', 'exp_confirm') for e in history):
                raise ValueError('already_complete_contains_consumer')
        if start['game_day'] == game_day:
            if any(rows[k] != 'complete' for k in completed_rows):
                raise ValueError('same_day_completed_row_regressed')
            completed_rows.update(k for k, state in rows.items() if state == 'complete')
    directory = journal.directory / 'actions'
    if directory.exists() and {p.name for p in directory.iterdir()} != intents:
        raise ValueError('orphan_action_history_unverified')
    return completed_rows



def read_history(journal, game_day, device):
    """Caller holds the root lock; preserve all historical retry constraints."""
    prior_input_evidence = None
    events = journal.events()
    if any(e.get('operation') != 'schedule_onekey' for e in events):
        raise ValueError('foreign_journal_parent_reconciliation_required')
    if events and events[-1]['state'] == 'in_flight':
        raise ValueError('unsettled_intent_any_day_no_resend')
    # A closed legacy proof keeps its original binding. It is not attribution
    # of old completion/retry rights to a newly introduced stable identity.
    if stable_device(device) or any(stable_device(e.get('device')) for e in events):
        return stable_history(journal, events, game_day, device)
    from .onekey_completed_reconcile import PARENT_DISPOSITION
    if any(e.get('disposition') == PARENT_DISPOSITION for e in events):
        return parent_assisted_history(journal, events, game_day, device)
    # A still-unused incident retry is bound to its original device. Do not
    # reinterpret a changed display as a fresh invocation through navigation_history.
    if len(events) >= 2 and events[-2].get('device') != device:
        disposition = events[-1].get('disposition')
        if disposition in ('verified_pre_consumer_abort', 'verified_settings_navigation_abort'):
            raise ValueError('pre_consumer_reconciliation_invalid_or_stale_no_retry')
        if disposition == 'verified_pre_input_abort':
            raise ValueError('pre_input_reconciliation_invalid_or_stale_no_retry')
    if (any(e.get('disposition') == 'verified_navigation_abort_v1' for e in events)
            or any(isinstance(e.get('device'), dict) and e['device'] != device for e in events[::2])):
        return navigation_history(journal, journal.path.read_bytes(), game_day, device), None, None
    retry_of = None
    prior_complete = set()
    # A consumed historical retry is a normal settled suffix, never a new retry
    # token. Validate it before exposing the bounded prefix to frozen verifiers.
    dispositions = [i for i, e in enumerate(events) if e.get('disposition')]
    if dispositions and not events[-1].get('disposition'):
        prefix_length = dispositions[-1] + 1
        require(prefix_length % 2 == 0 and prefix_length < len(events), 'settled_history_unverified')
        prefix, suffix = events[:prefix_length], events[prefix_length:]
        ids = [e.get('intent_id') for e in prefix[::2]]
        require(all(isinstance(i, str) and re.fullmatch('[0-9a-f]{32}', i) for i in ids)
                and len(set(ids)) == len(ids), 'settled_history_unverified')
        require(suffix[0].get('retry_of') == ids[-1]
                and suffix[0].get('game_day') == prefix[-2].get('game_day')
                and all('retry_of' not in e for e in suffix[2:]), 'settled_history_unverified')
        prior_complete = settled_history(journal, suffix, game_day, device, prefix_intents=ids)
        from .onekey_reconcile import verify_closed_prefix
        verify_closed_prefix(journal, events, prefix_length, device)
        return prior_complete, None, None
    if events and events[-1].get('disposition') in ('verified_pre_consumer_abort', 'verified_settings_navigation_abort'):
        from .onekey_reconcile import consumer_retry_authorized
        retry_of = consumer_retry_authorized(journal, events, game_day, device)
        if retry_of is None:
            raise ValueError('pre_consumer_reconciliation_invalid_or_stale_no_retry')
        prior_input_evidence = events[-1]['disposition']
    elif events and events[-1].get('disposition') == 'verified_pre_input_abort':
        from .onekey_reconcile import retry_authorized
        retry_of = retry_authorized(journal, events, game_day, device)
        if retry_of is None:
            raise ValueError('pre_input_reconciliation_invalid_or_stale_no_retry')
        prior_input_evidence = 'verified_pre_input_abort'
    else:
        prior_complete = settled_history(journal, events, game_day, device)
    return prior_complete, retry_of, prior_input_evidence


def inspect_history(journal, game_day, device):
    """Lock-free static precheck for a stable config; never execution permission.

    Uses the same complete verifier as execution. The execution caller must
    still hold its root lock and qualify the current device and live pages.
    """
    validate_stable_device(device)
    return read_history(journal, game_day, device)


def stable_history(journal, history, game_day, device, *, unresolved_observation=False):
    """Verify original closed bytes, then inherit only exact stable suffixes."""
    from .navigation_abort import events
    from .onekey_completed_reconcile import PARENT_DISPOSITION
    validate_stable_device(device)
    stable_seen = False
    legacy = False
    for index, event in enumerate(history):
        original = event.get('device')
        reason = 'history_identity_unverified:' + str(event.get('intent_id', 'missing'))
        # The first frozen pre-input terminal omitted identity fields. Only its
        # full original incident verifier can accept that historical shape.
        if original is None and index % 2 and not stable_device(history[index - 1].get('device')):
            continue
        require(isinstance(original, dict), reason)
        if stable_device(original):
            validate_stable_device(original)
            require(compatible_closed_device(original, device), reason)
            stable_seen = True
            require(not event.get('disposition') and 'retry_of' not in event,
                    'stable_history_reconciliation_unverified:' + str(event.get('intent_id', 'missing')))
        else:
            # Same serial is a target constraint, not proof of the same account.
            require(not stable_seen and set(original) == {'serial', 'input_display', 'screenshot_display'}
                    and original['serial'] == device['serial']
                    and type(original['input_display']) is int and 0 <= original['input_display'] <= 255
                    and isinstance(original['screenshot_display'], str)
                    and re.fullmatch('[0-9]{1,20}', original['screenshot_display']), reason)
            legacy = True
    # Take no locks and never create a missing historical child as evidence.
    files = {}
    if journal.path.exists():
        files[journal.path] = path_in(journal.directory, journal.path).read_bytes()
        require(events(files[journal.path]) == history, 'history_snapshot_changed')
    ids = {e.get('intent_id') for e in history[::2]}
    directory = journal.directory / 'actions'
    inventory = {p.name for p in directory.iterdir()} if directory.exists() else set()
    require(inventory == ids, 'orphan_action_history_unverified')
    for intent in ids:
        require(isinstance(intent, str) and re.fullmatch('[0-9a-f]{32}', intent), 'invalid_history_intents')
        path = path_in(journal.directory, directory / intent / 'transactions.jsonl')
        files[path] = path.read_bytes()
    if any(e.get('disposition') == PARENT_DISPOSITION for e in history):
        result = parent_assisted_history(journal, history, game_day, device,
                                         unresolved_observation=unresolved_observation)
    elif legacy:
        require(not unresolved_observation, 'unresolved_legacy_prefix_not_supported')
        result = (navigation_history(journal, files[journal.path], game_day, device), None, None)
    else:
        result = (settled_history(journal, history[:-1] if unresolved_observation else history,
                  game_day, device, readonly=True,
                  prefix_intents=[history[-1]['intent_id']] if unresolved_observation else ()), None, None)
    require(all(path_in(journal.directory, p).read_bytes() == data for p, data in files.items()),
            'evidence_changed')
    require(({p.name for p in directory.iterdir()} if directory.exists() else set()) == inventory,
            'orphan_action_history_unverified')
    return result


def parent_assisted_history(journal, history, game_day, device, *, unresolved_observation=False):
    """Fixed closure proof plus every ordinary suffix; no apply or retry authority.

    Only the root's first ten lines are projected into the frozen closure proof.
    The complete root, all child bytes and the entire directory inventory are
    independently checked before and after validation. No files are rewritten.
    """
    from datetime import date
    from .navigation_abort import Evidence, events
    from .onekey_completed_reconcile import _parent_assisted_proof, PARENT_INTENT
    from .schedule_onekey import ITEMS
    root = journal.directory.parents[2]
    require(journal.directory == root/'.runtime/schedule-onekey-work/ledger', 'wrong_history_ledger')
    evidence = Evidence(root)
    data = evidence.read(journal.path)
    require(events(data) == history and len(history) >= 10
            and len(history) % 2 == int(unresolved_observation),
            'parent_assisted_history_sequence')
    ids = [e.get('intent_id') for e in history[::2]]
    require(all(isinstance(i, str) and re.fullmatch('[0-9a-f]{32}', i) for i in ids)
            and len(ids) == len(set(ids)), 'invalid_history_intents')
    directory = path_in(root, journal.directory/'actions')
    require({p.name for p in directory.iterdir()} == set(ids), 'orphan_action_history_unverified')
    for intent in ids:
        evidence.read(directory/intent/'transactions.jsonl')
    prefix = b''.join(data.splitlines(keepends=True)[:10])

    class ClosedPrefixEvidence(Evidence):
        def read(self, path):
            p = path_in(root, path)
            actual = evidence.read(p)
            value = prefix if p == journal.path else actual
            self.files[p] = value
            return value

        def recheck(self):
            evidence.recheck()

    original_device = history[8].get('device') if stable_device(device) else device
    proof = _parent_assisted_proof(root, intent_id=PARENT_INTENT,
        game_day='2026-09-26', device=original_device, _history_evidence=ClosedPrefixEvidence(root),
        _history_intents=set(ids))[0]
    require(proof['outcome'] == 'already_parent_assisted_completed', 'parent_assisted_not_closed')
    import json
    def identity(binding, day):
        return json.dumps(binding, sort_keys=True), day
    completed = {identity(original_device, proof['game_day']): set(ITEMS)}
    previous_day = proof['game_day']
    for start, end in zip(history[10::2], history[11::2]):
        require(all('retry_of' not in e and not e.get('disposition') for e in (start, end)),
                'parent_assisted_is_not_retry_permission')
        day = start.get('game_day')
        require(isinstance(day, str) and re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}', day)
                and date.fromisoformat(day).isoformat() == day and day >= previous_day,
                'parent_assisted_suffix_day_mismatch')
        binding = start['device'] if stable_device(device) else device
        current = settled_history(journal, [start, end],
            game_day if end.get('state') == 'historical_unknown' else day, binding,
            prefix_intents=set(ids)-{start['intent_id']}, readonly=True)
        if end.get('state') == 'historical_unknown':
            previous_day = day
            continue
        key = identity(binding, day)
        require(completed.get(key, set()) <= current, 'same_day_completed_row_regressed')
        completed[key] = current
        previous_day = day
    evidence.recheck()
    require({p.name for p in directory.iterdir()} == set(ids), 'orphan_action_history_unverified')
    return (completed.get(identity(device, game_day), set()), None,
            'parent_assisted_completed' if device == original_device and game_day == proof['game_day'] else None)


def unresolved_observation_history(journal, game_day, device):
    """Caller holds root lock. Verify history without closing it or granting retry.

    Only the modern, stable-identity producer is supported. Historical closed
    prefixes retain their original full verifiers; the pending tail remains
    unknown even if fresh observation later finds every row complete.
    """
    from .navigation_abort import Evidence, events
    from .schedule_onekey import ITEMS
    validate_stable_device(device)
    evidence = Evidence(journal.directory.parents[2])
    require(journal.directory == evidence.root / '.runtime/schedule-onekey-work/ledger',
            'wrong_history_ledger')
    history = events(evidence.read(journal.path))
    require(all(e.get('operation') == 'schedule_onekey' for e in history),
            'foreign_journal_parent_reconciliation_required')
    require(history and len(history) % 2 == 1 and history[-1]['state'] == 'in_flight',
            'unresolved_root_required')
    parent = history[-1]
    require(isinstance(parent.get('game_day'), str) and parent['game_day'] <= game_day,
            'unresolved_future_day_no_observation')
    require(parent.get('device') == device, 'unresolved_device_binding_mismatch')
    require(parent.get('operation') == 'schedule_onekey' and 'retry_of' not in parent
            and not parent.get('disposition'), 'unresolved_root_binding_invalid')
    require(isinstance(parent.get('session_id'), str)
            and re.fullmatch('[0-9a-f]{32}', parent['session_id'])
            and isinstance(parent.get('invocation_id'), str)
            and re.fullmatch('run-[0-9a-f]{32}', parent['invocation_id']),
            'unresolved_source_binding_missing')
    require(parent.get('authorizations') == list(ITEMS), 'unresolved_target_scope_mismatch')
    intent = parent.get('intent_id')
    require(isinstance(intent, str) and re.fullmatch('[0-9a-f]{32}', intent), 'invalid_history_intents')
    directory = journal.directory / 'actions'
    inventory = {p.name for p in directory.iterdir()}
    for name in inventory:
        evidence.read(directory / name / 'transactions.jsonl')
    actions = events(evidence.read(directory / intent / 'transactions.jsonl'))
    require(bool(actions), 'unresolved_action_history_missing')
    seen = set()
    for index, event in enumerate(actions):
        require(all(event.get(key) == parent.get(key)
                    for key in ('game_day', 'device', 'intent_id', 'session_id')),
                'unresolved_action_binding_mismatch')
        op = event.get('operation')
        require(isinstance(op, str) and (op in ('enter', 'onekey', 'dungeon_confirm',
            'dungeon_finish', 'dungeon_result', 'mana_confirm', 'mana_result', 'exp_confirm',
            'exp_result', 'like_result', 'item_result', 'close')
            or re.fullmatch('(up|down):([0-9]|1[0-9]|2[0-3])', op)),
            'unresolved_action_scope_invalid')
        require(isinstance(event.get('image_sha256'), str)
                and re.fullmatch('[0-9a-f]{64}', event['image_sha256']), 'unresolved_image_binding_missing')
        if index % 2:
            require(op == actions[index-1]['operation'] and type(event.get('native_task_id')) is int
                    and event['native_task_id'] > 0, 'unresolved_action_pair_invalid')
        else:
            require(op not in seen, 'unresolved_duplicate_action')
            seen.add(op)
            if index:
                require(event['image_sha256'] == actions[index-1]['image_sha256'],
                        'unresolved_action_image_chain_changed')
            if op == 'onekey':
                # The producer persists the root immediately before the first
                # consumer, after buffering the preceding navigation actions.
                require(event['image_sha256'] == parent.get('image_sha256'),
                        'unresolved_root_image_binding_changed')
    require(actions[0]['operation'] == 'enter' and 'onekey' in seen,
            'unresolved_consumer_history_missing')
    # Existing verifiers validate every closed root/action pair and every legacy
    # evidence bundle. They never interpret the odd tail as a settled pair.
    stable_history(journal, history, game_day, device, unresolved_observation=True)
    require(all(e.get('game_day', '') <= game_day for e in history[::2]),
            'unresolved_history_day_order_invalid')
    evidence.recheck()
    require({p.name for p in directory.iterdir()} == inventory, 'orphan_action_history_unverified')
    return evidence, parent


def navigation_history(journal, data, game_day, device, *, evidence=None, pending=False):
    """Revalidate every pair and child. Navigation termination is never a retry token."""
    from .navigation_abort import Evidence, LEDGER, DISPOSITION, events, verify_bundle
    root = journal.directory.parents[2]
    require(journal.directory == root/LEDGER, 'wrong_history_ledger')
    evidence = evidence or Evidence(root)
    require(evidence.read(journal.path) == data, 'history_snapshot_changed')
    history = events(data)
    require(pending or len(history) % 2 == 0, 'unsettled_intent_any_day_no_resend')
    ids = [e.get('intent_id') for e in history[::2]]
    require(all(isinstance(i, str) and re.fullmatch('[0-9a-f]{32}', i) for i in ids)
            and len(ids) == len(set(ids)), 'invalid_history_intents')
    require(all(e.get('operation') == 'schedule_onekey' for e in history),
            'history_device_binding_mismatch')
    # A pending reconciliation still belongs to one exact binding. Closed abort
    # proofs may differ only in display mapping; each verifier uses original bytes.
    stable = stable_device(device)
    for original in ([] if stable else [device]) + [e.get('device') for e in history[::2]]:
        if stable and stable_device(original):
            validate_stable_device(original)
            require(compatible_closed_device(original, device) and not pending, 'history_device_binding_mismatch')
            continue
        require(isinstance(original, dict)
                and set(original) == {'serial', 'input_display', 'screenshot_display'}
                and isinstance(original['serial'], str) and bool(original['serial'])
                and type(original['input_display']) is int and 0 <= original['input_display'] <= 255
                and isinstance(original['screenshot_display'], str)
                and re.fullmatch('[0-9]{1,20}', original['screenshot_display'])
                and original['serial'] == device['serial']
                and (not pending or original == device), 'history_device_binding_mismatch')
    directory = path_in(root, journal.directory/'actions')
    require({p.name for p in directory.iterdir()} == set(ids), 'orphan_action_history_unverified')
    for intent in ids:
        evidence.read(directory/intent/'transactions.jsonl')
    lines = data.splitlines(keepends=True)
    completed_rows = set()
    completed_by_binding_day = {}
    legacy_labels = ('verified_pre_input_abort', 'verified_pre_consumer_abort',
                     'verified_settings_navigation_abort')
    prefix_length = 0
    while (prefix_length+1 < len(history)
           and history[prefix_length+1].get('disposition') in legacy_labels):
        prefix_length += 2
    if prefix_length:
        verify_legacy_readonly(evidence, journal, history, lines, prefix_length, history[0]['device'])
        if prefix_length < len(history):
            successor, previous = history[prefix_length], history[prefix_length-2]
            if successor.get('device') != previous['device']:
                # A fresh invocation on another display cannot spend a legacy
                # retry token. Its closed pair is independently verified below.
                require(not pending and 'retry_of' not in successor, 'legacy_successor_binding')
            else:
                require(successor.get('retry_of') == previous['intent_id']
                        and successor.get('game_day') == previous['game_day'], 'legacy_successor_binding')
    for i in range(prefix_length, len(history), 2):
        if i != prefix_length or not prefix_length:
            require('retry_of' not in history[i], 'navigation_abort_is_not_retry_permission')
    for i in range(prefix_length, len(history)-1, 2):
        start, end = history[i:i+2]
        if end.get('disposition') == DISPOSITION:
            binding = end['reconciliation']
            proof = verify_bundle(evidence, binding['manifest'], binding['manifest_sha256'],
                                  b''.join(lines[:i+2]))
            require(proof['closed'] and proof['parent'] == start, 'abort_pair_binding')
        else:
            require(not end.get('disposition'), 'legacy_prefix_requires_readonly_verification')
            binding = start['device'] if stable else device
            current = settled_history(journal, [start,end], start['game_day'] if stable else game_day, binding,
                prefix_intents=set(ids)-{start['intent_id']}, readonly=True)
            if stable:
                import json
                key = (json.dumps(binding, sort_keys=True), start['game_day'])
                require(completed_by_binding_day.get(key, set()) <= current, 'same_day_completed_row_regressed')
                completed_by_binding_day[key] = current
                if binding == device and start['game_day'] == game_day:
                    completed_rows.update(current)
            else:
                require(not completed_rows-current if start['game_day'] == game_day else True,
                        'same_day_completed_row_regressed')
                completed_rows.update(current)
    evidence.recheck()
    require({p.name for p in directory.iterdir()} == set(ids), 'orphan_action_history_unverified')
    return completed_rows


def verify_legacy_readonly(evidence, journal, history, lines, length, device, *, all_intents=None):
    """Frozen incident bodies, bound to read-only I/O in a private namespace.

    The old validators call Journal.locked internally. Rebinding those three
    function bodies avoids global monkeypatches, writes or locks in dry-run;
    none of their incident predicates or native validators are changed.
    """
    from contextlib import nullcontext
    from types import FunctionType, SimpleNamespace
    from . import onekey_reconcile as legacy, schedule_onekey
    from .preset_execution import Journal
    labels = [e.get('disposition') for e in history[1:length:2]]
    allowed = [legacy.DISPOSITION, legacy.CONSUMER_DISPOSITION, legacy.SETTINGS_DISPOSITION]
    require(labels == allowed[:len(labels)] and 1 <= len(labels) <= len(allowed), 'invalid_legacy_prefix')
    prefix_ids = {e['intent_id'] for e in history[:length:2]}
    all_ids = {e['intent_id'] for e in history[::2]}
    if all_intents is not None:
        require(all_ids <= all_intents, 'invalid_legacy_inventory')
        all_ids = all_intents
    class PrefixDirectory(type(Path())):
        def iterdir(self):
            actual = set(super().iterdir())
            require({p.name for p in actual} == all_ids, 'orphan_action_history_unverified')
            return iter(p for p in actual if p.name in prefix_ids)
    class ReadOnlyJournal(Journal):
        def locked(self):
            return nullcontext(self)

        def append(self, event):
            raise ValueError('readonly_legacy_proof')
    def bounded_read(root, path, limit=64*1024*1024):
        require(root == evidence.root, 'wrong_legacy_root')
        data = evidence.read(path)
        if Path(path) == journal.path:
            data = b''.join(lines[:length])
        require(len(data) <= limit, 'evidence_too_large')
        return data
    namespace = vars(legacy).copy()
    namespace.update(Journal=ReadOnlyJournal, read=bounded_read)
    names = ('validate_locked','validate_no_consumer_locked','validate_settings_abort_locked')
    for name in names:
        original = getattr(legacy, name)
        fn = FunctionType(original.__code__, namespace, name, original.__defaults__, original.__closure__)
        fn.__kwdefaults__ = original.__kwdefaults__
        namespace[name] = fn
    start, end = history[length-2:length]
    day = start['game_day']
    require(all(e['game_day'] == day for e in history[:length])
            and all(e.get('device') == device for e in history[:length:2]), 'history_binding_mismatch')
    binding = end['reconciliation']
    ledger = SimpleNamespace(directory=PrefixDirectory(journal.directory), path=journal.path)
    actions = ReadOnlyJournal(journal.directory/'actions'/start['intent_id'])
    token = schedule_onekey._HISTORY_DAY.set(day)
    try:
        proof = namespace[names[len(labels)-1]](evidence.root, ledger, actions,
            binding['run_id'], day, binding['evidence_sha256'])
        require(proof['root_closed'] and proof['action_closed'], 'legacy_prefix_unverified')
    finally:
        schedule_onekey._HISTORY_DAY.reset(token)
