"""Parent-reviewed append-only proofs of bounded pre-input/pre-consumer aborts.

Hashes bind reviewed bytes, not the authenticity of a maliciously replaced log.
No device/config access. The CLI has fixed evidence/ledger locations and is dry
by default. This module never labels a no-input closure as game settlement.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from contextvars import ContextVar

from .preset_execution import Journal
# Public compatibility alias for ordinary and historical evidence paths.
from .onekey_history import path_in

DISPOSITION = 'verified_pre_input_abort'
RUN = re.compile(r'run-[0-9a-f]{32}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')

# Only the compatibility reader sets this, after validating the entire suffix.
# Frozen incident proof functions still see and validate original prefix bytes.
_HISTORY_VIEW = ContextVar('onekey_verified_history_view', default=None)


class _HistoryDirectory(type(Path())):
    def iterdir(self):
        view = _HISTORY_VIEW.get()
        actual = set(super().iterdir())
        if view is not None and self == view['actions']:
            require(actual == view['all_directories'], 'extra_action_journals')
            return iter(actual & view['prefix_directories'])
        return iter(actual)


def verify_closed_prefix(journal, events, prefix_length, device):
    """Reverify a closed historical prefix; never return a retry authorization.

    The caller already validated every normal suffix pair under the root lock.
    A task-local read view bounds only the root bytes and directory inventory;
    all incident bundles, logs, frames and child journals remain original files.
    """
    from types import SimpleNamespace
    from . import native, schedule_onekey
    require(journal.directory == native.ROOT / '.runtime/schedule-onekey-work/ledger', 'wrong_history_ledger')
    labels = [e.get('disposition') for e in events[1:prefix_length:2]]
    require(labels in ([DISPOSITION], [DISPOSITION, CONSUMER_DISPOSITION],
                       [DISPOSITION, CONSUMER_DISPOSITION, SETTINGS_DISPOSITION]), 'invalid_legacy_prefix')
    data = read(native.ROOT, journal.path)
    lines = data.splitlines(keepends=True)
    require(len(lines) == len(events) and all(line.endswith(b'\n') for line in lines)
            and [decode(line) for line in lines] == events, 'history_snapshot_changed')
    view = dict(path=journal.path, original=data, prefix=b''.join(lines[:prefix_length]),
        actions=journal.directory/'actions',
        all_directories={journal.directory/'actions'/e['intent_id'] for e in events[::2]},
        prefix_directories={journal.directory/'actions'/e['intent_id'] for e in events[:prefix_length:2]})
    prefix = events[:prefix_length]
    parent, end = prefix[-2:]
    day, binding = parent['game_day'], end['reconciliation']
    require(all(e.get('game_day') == day for e in prefix)
            and all(e.get('device') == device for e in prefix[::2]), 'history_binding_mismatch')
    ledger = SimpleNamespace(directory=_HistoryDirectory(journal.directory), path=journal.path)
    actions = Journal(journal.directory/'actions'/parent['intent_id'])
    path_in(native.ROOT, actions.path)
    token = _HISTORY_VIEW.set(view)
    day_token = schedule_onekey._HISTORY_DAY.set(day)
    try:
        validate = {DISPOSITION:validate_locked, CONSUMER_DISPOSITION:validate_no_consumer_locked,
                    SETTINGS_DISPOSITION:validate_settings_abort_locked}[labels[-1]]
        with actions.locked():
            proof = validate(native.ROOT, ledger, actions, binding['run_id'], day, binding['evidence_sha256'])
        require(proof['root_closed'] and proof['action_closed'] and proof['parent']['device'] == device,
                'historical_prefix_unverified')
    finally:
        schedule_onekey._HISTORY_DAY.reset(day_token)
        _HISTORY_VIEW.reset(token)


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def read(root, path, limit=64 * 1024 * 1024):
    path = path_in(root, path)
    view = _HISTORY_VIEW.get()
    projected = view is not None and path == view['path']
    physical_limit = 64 * 1024 * 1024 if projected else limit
    require(path.is_file() and path.stat().st_size <= physical_limit, 'invalid_evidence_file')
    data = path.read_bytes()
    require(len(data) <= physical_limit, 'evidence_too_large')
    if projected:
        require(data == view['original'], 'history_snapshot_changed')
        require(len(view['prefix']) <= limit, 'evidence_too_large')
        return view['prefix']
    return data


def terminal(operation, game_day, intent_id, run_id, pin):
    return dict(state='settled', operation=operation, game_day=game_day,
        disposition=DISPOSITION, settlement_verified=False, actual_input_sent=False,
        reconciliation=dict(version=1, intent_id=intent_id, run_id=run_id, evidence_sha256=pin))


def native_zero_input(log, task_ids, game_day):
    events = []
    recognition = {}
    failed = 0
    active_task = None
    stop_seen = False
    for line in log.decode('utf-8').splitlines():
        if '!!!OnEventNotify!!!' not in line:
            require('[msg=' not in line, 'damaged_native_event_marker')
            continue
        match = re.search(r'\[msg=([^\]]+)\] \[details=(\{.*\})\]\s*$', line)
        require(match is not None, 'malformed_native_event')
        msg, body = match.groups()
        detail = decode(body)
        if msg.startswith(('Node.Action.', 'Tasker.Task.')):
            require(line.startswith('[' + game_day + ' '), 'native_event_day_mismatch')
            events.append((msg, detail))
        if msg == 'Tasker.Task.Starting':
            require(active_task is None, 'overlapping_native_tasks')
            active_task = detail.get('task_id')
        elif msg in ('Tasker.Task.Succeeded', 'Tasker.Task.Failed'):
            require(active_task == detail.get('task_id'), 'native_task_end_without_start')
            active_task = None
        elif msg == 'Node.Action.Starting' and detail.get('name') == 'OneKeyStop':
            require(failed > 0 and all(recognition.values()), 'stop_without_failed_guard')
            stop_seen = True
        if msg.startswith('Node.Recognition.') and detail.get('name', '').startswith('OneKeyInput_'):
            require(detail['name'] == 'OneKeyInput_enter' and detail.get('task_id') == task_ids[1],
                    'unexpected_input_recognition')
            require(active_task == task_ids[1] and not stop_seen, 'guard_outside_enter_task')
            key = detail.get('reco_id')
            require(type(key) is int, 'missing_recognition_id')
            if msg == 'Node.Recognition.Starting':
                require(key not in recognition, 'duplicate_recognition')
                recognition[key] = False
            else:
                require(msg == 'Node.Recognition.Failed' and recognition.get(key) is False,
                        'input_recognition_not_failed')
                recognition[key] = True
                failed += 1
    require(1 <= failed <= 2 and all(recognition.values()), 'missing_or_unfinished_enter_recognition')
    cursor = 0
    action_ids = set()
    identity = None
    for task_id, entry, names in [(task_ids[0], 'OneKeyExecRead', ['OneKeyExecRead','OneKeyExecSnapshot']),
                                  (task_ids[1], 'OneKeyDo_enter', ['OneKeyDo_enter','OneKeyStop'])]:
        msg, start = events[cursor]
        cursor += 1
        require(msg == 'Tasker.Task.Starting' and start.get('task_id') == task_id
                and start.get('entry') == entry and start.get('uuid') == '12700116384'
                and isinstance(start.get('hash'), str) and bool(start['hash']), 'native_task_binding')
        if identity is None: identity = start['hash']
        require(start['hash'] == identity, 'native_session_changed')
        for name in names:
            msg, action = events[cursor]
            end_msg, end = events[cursor + 1]
            cursor += 2
            aid = action.get('action_id')
            require(type(aid) is int and aid not in action_ids, 'duplicate_action_id')
            action_ids.add(aid)
            require(msg == 'Node.Action.Starting' and end_msg == 'Node.Action.Succeeded'
                    and action.get('name') == name and end.get('name') == name
                    and action.get('task_id') == task_id and end.get('task_id') == task_id
                    and end.get('action_id') == aid, 'unexpected_or_incomplete_native_action')
            proof = end.get('action_details', {})
            require(proof.get('action') == ('StopTask' if name == 'OneKeyStop' else 'DoNothing')
                    and proof.get('name') == name and proof.get('action_id') == aid
                    and proof.get('success') is True, 'actual_input_or_missing_action_proof')
        msg, end = events[cursor]
        cursor += 1
        require(msg == 'Tasker.Task.Succeeded' and end == start, 'unfinished_native_task')
    require(cursor == len(events), 'extra_native_task_or_action')


def validate_locked(root, ledger, actions, run_id, game_day, pin, *, successor=None, settings_successor=None):
    """Caller holds root then action locks. Re-read every byte on every use."""
    from .schedule_onekey import check_day, validate_execution
    check_day(game_day)
    require(RUN.fullmatch(run_id) is not None and HEX.fullmatch(pin) is not None, 'invalid_review_binding')
    bundle_path = root / '.runtime/schedule-onekey-work' / ('parent-no-input-evidence-' + game_day.replace('-','') + '.json')
    bundle_bytes = read(root, bundle_path, 1024 * 1024)
    require(sha(bundle_bytes) == pin, 'parent_review_sha_mismatch')
    bundle = decode(bundle_bytes)
    require(bundle.get('run_id') == run_id and bundle.get('game_day') == game_day, 'bundle_binding_mismatch')
    hashes = bundle.get('sha256')
    require(isinstance(hashes, dict) and len(hashes) == 7, 'seven_original_files_required')
    resolved = {}
    for name, digest in hashes.items():
        path = path_in(root, name)
        require(path not in resolved and isinstance(digest, str) and HEX.fullmatch(digest), 'duplicate_or_bad_hash')
        resolved[path] = digest
    root_data, action_data = read(root, ledger.path, 1024 * 1024), read(root, actions.path, 1024 * 1024)
    root_lines, action_lines = root_data.splitlines(keepends=True), action_data.splitlines(keepends=True)
    if settings_successor is not None:
        require(successor is not None and len(root_lines) in (5,6)
                and decode(root_lines[4]) == settings_successor
                and settings_successor.get('retry_of') == successor.get('intent_id'),
                'invalid_settings_successor')
        root_lines = root_lines[:4]
    if successor is not None:
        # Read-only verification of the original two-event prefix. This does
        # not authorize a retry and never changes the original evidence bytes.
        require(len(root_lines) in (3,4) and decode(root_lines[2]) == successor
                and successor.get('retry_of') == decode(root_lines[0]).get('intent_id'),
                'invalid_original_successor')
        root_lines = root_lines[:2]
    require(len(root_lines) in (1,2) and len(action_lines) in (1,2), 'first_attempt_only')
    require(all(line.endswith(b'\n') for line in root_lines + action_lines), 'incomplete_journal_append')
    parent, action = decode(root_lines[0]), decode(action_lines[0])
    require(set(parent) == {'state','operation','game_day','device','authorizations','intent_id','image_sha256'}
            and set(action) == {'state','operation','game_day','image_sha256'}, 'not_original_first_intents')
    intent_id = parent.get('intent_id')
    require(isinstance(intent_id, str) and re.fullmatch('[0-9a-f]{32}', intent_id), 'invalid_intent_id')
    require(actions.directory == ledger.directory / 'actions' / intent_id, 'wrong_action_ledger')
    expected_dirs = {actions.directory}
    if successor is not None:
        successor_id = successor.get('intent_id')
        require(isinstance(successor_id,str) and re.fullmatch('[0-9a-f]{32}',successor_id), 'invalid_successor_id')
        expected_dirs.add(ledger.directory / 'actions' / successor_id)
    if settings_successor is not None:
        third_id = settings_successor.get('intent_id')
        require(isinstance(third_id,str) and re.fullmatch('[0-9a-f]{32}',third_id), 'invalid_successor_id')
        expected_dirs.add(ledger.directory / 'actions' / third_id)
    require(set((ledger.directory / 'actions').iterdir()) == expected_dirs, 'extra_action_journals')
    require(parent.get('state') == action.get('state') == 'in_flight'
            and parent.get('operation') == 'schedule_onekey' and action.get('operation') == 'enter'
            and parent.get('game_day') == action.get('game_day') == game_day
            and parent.get('image_sha256') == action.get('image_sha256'), 'original_intents_mismatch')
    validate_execution(game_day, parent.get('device'), parent.get('authorizations'))
    root_end = terminal('schedule_onekey', game_day, intent_id, run_id, pin)
    action_end = terminal('enter', game_day, intent_id, run_id, pin)
    if len(root_lines) == 2:
        require(len(action_lines) == 2 and decode(root_lines[1]) == root_end, 'unpaired_or_forged_root_closure')
    if len(action_lines) == 2:
        require(decode(action_lines[1]) == action_end, 'forged_action_closure')
    run = root / '.runtime/supported-daily-work' / run_id
    report_path, log_path = run / 'report.json', run / 'maa/maafw.log'
    report_bytes = read(root, report_path, 16 * 1024 * 1024)
    log_bytes = read(root, log_path)
    report = decode(report_bytes)
    require(report.get('run_id') == run_id and report.get('game_day') == game_day
            and report.get('source') == 'live supported chain' and report.get('outcome') == 'stopped'
            and report.get('daily_complete') is False and report.get('supported_chain_complete') is False,
            'report_not_first_abort')
    steps = report['steps']
    require([s.get('name') for s in steps] == ['schedule_onekey','event','deep','rewards','very_hard','hard']
            and steps[0].get('status') == 'stopped'
            and all(s == {'name':s['name'], 'status':'not_run'} for s in steps[1:]), 'later_steps_or_wrong_chain')
    result = steps[0]['result']
    require(result.get('outcome') == 'unknown' and result.get('reason') == 'input_unverified_no_retry:enter'
            and result.get('sent') == ['enter'] and result.get('game_day') == game_day
            and result.get('device') == parent['device'] and result.get('authorizations') == parent['authorizations']
            and all(result.get(k) is False for k in ('one_key_attempted','schedule_executed','daily_complete',
                                                    'home_verified','nine_rows_verified')),
            'component_not_zero_input_abort')
    receipts = result['receipts']
    require([r['entry'] for r in receipts] == ['OneKeyExecRead','OneKeyDo_enter'], 'extra_or_missing_receipt')
    task_ids = []
    readings = []
    for index, receipt in enumerate(receipts):
        native = receipt['native']
        require(native.get('native_ok') is True and type(native.get('task_id')) is int, 'native_receipt_invalid')
        task_ids.append(native['task_id'])
        nodes = native['nodes']
        expected = ['OneKeyExecRead','OneKeyExecSnapshot'] if index == 0 else ['OneKeyDo_enter','','OneKeyStop']
        require([n.get('name') for n in nodes] == expected, 'unexpected_receipt_node')
        for node in nodes:
            if not node['name']:
                require(node.get('completed') is False and node.get('action') is None, 'input_not_failed')
            else:
                require(node.get('completed') is True and node.get('action') == dict(name=node['name'], success=True, raw={}),
                        'receipt_action_mismatch')
        readings.append(receipt['reading'])
    require(len(set(task_ids)) == 2 and readings[0].get('state') == 'home' and readings[1].get('state') == 'unknown'
            and readings[0].get('session_id') == readings[1].get('session_id') and bool(readings[0].get('session_id'))
            and readings[0].get('capture_index') == 1 and type(readings[1].get('capture_index')) is int
            and 1 < readings[1]['capture_index'] <= 3
            and all(r.get('acquisition') == 'current_tasker_capture' for r in readings), 'capture_binding_invalid')
    image_paths = [path_in(root, r['screenshot']) for r in readings]
    require(all(p.parent == run and p.name.startswith('onekey-exec-') and p.suffix == '.png' for p in image_paths),
            'capture_outside_run')
    after = run.parent / ('parent-after-unknown-' + game_day.replace('-','') + '.png')
    require(set(resolved) == {report_path,log_path,ledger.path,actions.path,*image_paths,after}, 'wrong_evidence_paths')
    for path, digest in resolved.items():
        data = (root_lines[0] if path == ledger.path else action_lines[0] if path == actions.path else
                report_bytes if path == report_path else log_bytes if path == log_path else read(root, path))
        require(sha(data) == digest, 'original_file_sha_mismatch')
    require(readings[0]['image_sha256'] == parent['image_sha256']
            and all(r['image_sha256'] == resolved[p] for r,p in zip(readings,image_paths)), 'image_intent_sha_mismatch')
    native_zero_input(log_bytes, task_ids, game_day)
    return dict(parent=parent, root_end=root_end, action_end=action_end,
                root_closed=len(root_lines)==2, action_closed=len(action_lines)==2)


def journals(root):
    ledger = Journal(root / '.runtime/schedule-onekey-work/ledger')
    path_in(root, ledger.directory / 'transaction.lock')
    data = read(root, ledger.path, 1024 * 1024)
    first = decode(data.splitlines()[0])
    intent = first.get('intent_id')
    require(isinstance(intent, str) and re.fullmatch('[0-9a-f]{32}', intent), 'invalid_intent_id')
    actions = Journal(ledger.directory / 'actions' / intent)
    path_in(root, actions.directory / 'transaction.lock')
    path_in(root, actions.path)
    return ledger, actions


def reconcile(*, run_id, game_day, evidence_sha256, apply=False, root=None):
    from . import native
    result = dict(outcome='unknown', daily_complete=False, settlement_verified=False,
                  grants_execution_permission=False)
    try:
        require(type(apply) is bool, 'explicit_boolean_apply_required')
        root = Path(root) if root is not None else native.ROOT
        ledger, actions = journals(root)
        with ledger.locked(), actions.locked():
            verified = validate_locked(root, ledger, actions, run_id, game_day, evidence_sha256)
            if verified['root_closed']:
                result['outcome'] = 'already_reconciled'
            elif not apply:
                result.update(outcome='eligible_no_input_abort', partial=verified['action_closed'])
            else:
                if not verified['action_closed']:
                    actions.append(verified['action_end'])
                # Action fsync first; crash here leaves the original root blocking.
                validate_locked(root, ledger, actions, run_id, game_day, evidence_sha256)
                ledger.append(verified['root_end'])
                result['outcome'] = 'reconciled_no_input_abort'
    except (Exception, KeyboardInterrupt) as exc:
        result['reason'] = str(exc) or 'interrupted'
    return result


def retry_authorized(journal, events, game_day, device):
    """Only called with the normal root lock held; no writes, no device access."""
    from . import native
    try:
        if len(events) != 2 or events[-1].get('disposition') != DISPOSITION:
            return None
        require(journal.directory == native.ROOT / '.runtime/schedule-onekey-work/ledger', 'wrong_retry_ledger')
        ledger, actions = journals(native.ROOT)
        binding = events[-1]['reconciliation']
        with actions.locked():
            proof = validate_locked(native.ROOT, ledger, actions, binding['run_id'], game_day, binding['evidence_sha256'])
            require(proof['root_closed'] and proof['action_closed'] and proof['parent']['device'] == device,
                    'retry_binding_mismatch')
        return proof['parent']['intent_id']
    except Exception:
        return None


CONSUMER_DISPOSITION = 'verified_pre_consumer_abort'
SETTINGS_DISPOSITION = 'verified_settings_navigation_abort'
SETTINGS_ABORT_RUN = 'run-a52dd2c251f941dfa8490fbb29b4f52b'
SETTINGS_ABORT_DAY = '2026-09-25'
SETTINGS_ABORT_REASON = 'unknown_settings_group:黎明界迷宫设定'
NAVIGATION = {'enter': ('home','schedule','Click'),
              'settings_open': ('schedule','settings','Click'),
              'settings_down': ('settings','settings','Swipe'),
              'settings_close': ('settings','schedule','Click'),
              'down': ('schedule','schedule','Swipe'), 'up': ('schedule','schedule','Swipe')}


def navigation_detail(name):
    """Fixed pipeline coordinates in Maa's normalized 1280x720 space."""
    clicks={'enter':[1017,575],'settings_open':[612,640],'settings_close':[640,640]}
    swipes={'settings_down':([60,530],[60,244]),'down':([667,500],[667,287]),'up':([667,287],[667,500])}
    if not name.startswith('OneKeyInput_'):return {}
    op=name[12:]
    if op in clicks:return dict(contact=0,point=clicks[op],pressure=1)
    require(op in swipes,'consumer_or_unknown_input_detail')
    begin,end=swipes[op]
    return dict(begin=begin,contact=0,duration=[1000],end=[end],end_hold=[],only_hover=False,pressure=1,starting=0)


def same_detail(actual,expected):
    return json.dumps(actual,sort_keys=True,allow_nan=False)==json.dumps(expected,sort_keys=True,allow_nan=False)


def native_no_consumer(log, receipts, game_day, *, settings_abort=False):
    """Complete sequential task/action proof, not a search for absent Clicks."""
    tasks=[]; active=None; action=None; action_ids=set(); reco_ids=set(); task_ids=set()
    session=None; guards={}; failed=0; auxiliaries={}; node_ids=set(); resources={}
    action_proofs={}; pipeline_actions=set()
    recognition_nodes={}; inner_recos={}; inner_tasks=set(); inner_count=0
    wait_pending=None; wait_ids=set()
    def identity(d,fields):
        return {key:d[key] for key in fields}
    def recognition_only(d):
        # Recognition callbacks cannot smuggle action evidence into a proof.
        for value in (d,d.get('node_details',{})):
            require(isinstance(value,dict),'invalid_recognition_details')
            require(value.get('action_id') is None or
                    (type(value['action_id']) is int and value['action_id']==0),'recognition_contains_action')
            require(all(value.get(key) is None or same_detail(value[key],{})
                        for key in ('action','action_details')),'recognition_contains_action')
    def reco_proof(proof,rid,name,phase):
        fields={'algorithm','box','detail','name','reco_id'}
        require(isinstance(proof,dict) and set(proof) in (fields,fields|{'hit'})
                and type(proof.get('reco_id')) is int
                and proof['reco_id']==rid and proof.get('name')==name
                and ('hit' not in proof or proof['hit'] is (phase=='Succeeded')),
                'nested_recognition_proof_mismatch')
        recognition_only(proof)
    for line in log.decode('utf-8').splitlines():
        if '!!!OnEventNotify!!!' not in line:
            require('[msg=' not in line, 'damaged_native_event_marker')
            continue
        match=re.search(r'\[msg=([^\]]+)\] \[details=(\{.*\})\]\s*$',line)
        require(match is not None and line.startswith('['+game_day+' '), 'malformed_or_cross_day_event')
        msg,body=match.groups(); d=decode(body)
        require(wait_pending is None or msg.startswith('Node.WaitFreezes.'), 'unfinished_wait_freezes')
        if msg in ('Resource.Loading.Starting','Resource.Loading.Succeeded'):
            require(active is None and not tasks and type(d.get('res_id')) is int,'resource_changed_during_run')
            rid=d['res_id']
            if msg.endswith('Starting'):
                require(rid not in resources,'duplicate_resource_event');resources[rid]=d
            else:
                before=resources.get(rid)
                require(before is not None and before.get('hash')=='' and d.get('hash')
                        and all(before.get(k)==d.get(k) for k in ('path','res_id','type')),'unpaired_resource_event')
                resources[rid]=d
        elif msg.startswith('Node.RecognitionNode.'):
            require(active is not None and action is None,'recognition_node_outside_task')
            nid=d.get('node_id');tid=d.get('task_id');phase=msg.rsplit('.',1)[1]
            fields=('node_id','task_id','name','focus')
            require(type(nid) is int and type(tid) is int and tid not in task_ids
                    and isinstance(d.get('name'),str) and bool(d['name']) and 'focus' in d,
                    'invalid_recognition_node_identity')
            recognition_only(d)
            if phase=='Starting':
                require(nid not in node_ids and len(recognition_nodes)<32 and inner_count<4096,
                        'duplicate_or_excess_recognition_node')
                require(not d.get('node_details') and not d.get('reco_details'),'premature_recognition_proof')
                node_ids.add(nid);inner_tasks.add(tid);inner_count+=1
                recognition_nodes[nid]=dict(start=d,reco=None)
            else:
                node=recognition_nodes.get(nid)
                require(phase in ('Succeeded','Failed') and node is not None
                        and same_detail(identity(d,fields),identity(node['start'],fields)),
                        'unpaired_recognition_node')
                reco=node['reco']
                require(reco is not None and reco['phase']==phase,'unfinished_nested_recognition')
                rid=reco['start']['reco_id'];detail=d.get('node_details',{})
                require(type(detail.get('node_id')) is int and detail['node_id']==nid
                        and detail.get('name')==d['name'] and type(detail.get('reco_id')) is int
                        and type(detail.get('action_id')) is int and detail['action_id']==0
                        and detail['reco_id']==rid and detail.get('completed') is (phase=='Succeeded'),
                        'recognition_node_completion_mismatch')
                reco_proof(d.get('reco_details'),rid,d['name'],phase)
                require(same_detail(d['reco_details'],reco['end']['reco_details']),
                        'nested_recognition_details_changed')
                del inner_recos[rid];del recognition_nodes[nid]
        elif msg.startswith(('Node.PipelineNode.','Node.NextList.')):
            require(active is not None and d.get('task_id')==active['start']['task_id'],'auxiliary_event_outside_task')
            group,phase=msg.rsplit('.',1)
            key=group
            if phase=='Starting':
                require(key not in auxiliaries,'duplicate_auxiliary_event')
                if group=='Node.PipelineNode':
                    nid=d.get('node_id')
                    require(type(nid) is int and nid not in node_ids,'duplicate_pipeline_node_id');node_ids.add(nid)
                auxiliaries[key]=d
            else:
                before=auxiliaries.pop(key,None)
                fields=('node_id','task_id','name') if group=='Node.PipelineNode' else ('task_id','list')
                require(phase in ('Succeeded','Failed') and before is not None
                        and all(before.get(k)==d.get(k) for k in fields),'unpaired_auxiliary_event')
                if group=='Node.PipelineNode' and phase=='Succeeded':
                    proof=d.get('action_details',{});aid=proof.get('action_id')
                    require(aid in active['proof_ids'] and aid not in pipeline_actions
                            and same_detail(proof,action_proofs[aid])
                            and d.get('node_details',{}).get('action_id')==aid,
                            'pipeline_action_proof_mismatch')
                    pipeline_actions.add(aid)
                elif group=='Node.PipelineNode':
                    require(not d.get('action_details'),'failed_pipeline_contains_action')
        elif msg.startswith('Node.WaitFreezes.'):
            phase=msg.rsplit('.',1)[1];wid=d.get('wf_id')
            require(active is not None and d.get('task_id')==active['start']['task_id'],
                    'wait_freezes_outside_task')
            if phase=='Starting':
                require(wait_pending is None and type(wid) is int and wid not in wait_ids
                        and not active['wait_seen'] and action is None
                        and active['start']['entry']=='OneKeyDo_enter'
                        and any(aid not in pipeline_actions
                                and action_proofs[aid].get('name')=='OneKeyInput_enter'
                                for aid in active['proof_ids']), 'unexpected_wait_freezes_start')
                require(set(d)=={'task_id','wf_id','name','phase','focus','roi','param'}
                        and d['name']=='OneKeyInput_enter' and d['phase']=='post'
                        and d['focus'] is None and d['roi']==[240,25,810,650]
                        and d['param']==dict(time=200,threshold=0.98,method=5,
                                             rate_limit=100,timeout=2500),
                        'unexpected_wait_freezes_details')
                wait_pending=d;wait_ids.add(wid);active['wait_seen']=True
            else:
                require(phase=='Succeeded' and wait_pending is not None
                        and set(d)==set(wait_pending)|{'elapsed','reco_ids'}
                        and all(d.get(key)==value for key,value in wait_pending.items())
                        and type(d['elapsed']) is int and d['elapsed']>=0
                        and isinstance(d['reco_ids'],list)
                        and all(type(rid) is int for rid in d['reco_ids']),
                        'unpaired_or_failed_wait_freezes')
                wait_pending=None
        elif msg=='Tasker.Task.Starting':
            tid=d.get('task_id')
            require(active is None and not auxiliaries and not recognition_nodes
                    and all(r.get('hash') for r in resources.values())
                    and type(tid) is int and tid not in task_ids and tid not in inner_tasks, 'duplicate_or_overlapping_task')
            require(d.get('uuid')=='12700116384' and isinstance(d.get('hash'),str) and d['hash'], 'native_device_binding')
            if session is None: session=d['hash']
            require(d['hash']==session, 'native_session_changed')
            task_ids.add(tid); active=dict(start=d,actions=[],guards={},proof_ids=set(),wait_seen=False);guards=active['guards']
        elif msg=='Tasker.Task.Succeeded':
            require(active is not None and d==active['start'] and action is None and wait_pending is None and not auxiliaries
                    and not recognition_nodes and not inner_recos
                    and all(v[1] in ('Succeeded','Failed') for v in guards.values()), 'unfinished_native_task')
            tasks.append(active);active=None
        elif msg.startswith('Node.Recognition.'):
            require(active is not None and action is None,'recognition_outside_task')
            rid=d.get('reco_id');name=d.get('name');phase=msg.rsplit('.',1)[1]
            require(type(rid) is int and isinstance(name,str), 'invalid_recognition_identity')
            if settings_abort:
                recognition_only(d)
                require(name != 'OneKeyInput_onekey', 'unexpected_consumer_guard')
            if d.get('task_id')!=active['start']['task_id']:
                # Inner callbacks belong to one pending RN, never outer guards.
                fields=('task_id','name','focus')
                require(type(d.get('task_id')) is int and 'focus' in d,'invalid_inner_recognition_identity')
                recognition_only(d)
                if phase=='Starting':
                    owners=[nid for nid,node in recognition_nodes.items()
                            if same_detail(identity(d,fields),identity(node['start'],fields))]
                    require(len(owners)==1 and rid not in reco_ids,'unbound_or_duplicate_inner_recognition')
                    node=recognition_nodes[owners[0]]
                    require(node['reco'] is None and not d.get('reco_details'),'extra_inner_recognition')
                    reco_ids.add(rid);inner_recos[rid]=owners[0]
                    node['reco']=dict(start=d,phase='Starting')
                else:
                    node=recognition_nodes.get(inner_recos.get(rid),{})
                    reco=node.get('reco')
                    require(phase in ('Succeeded','Failed') and reco is not None and reco['phase']=='Starting'
                            and same_detail(identity(d,(*fields,'reco_id')),identity(reco['start'],(*fields,'reco_id'))),
                            'unpaired_inner_recognition')
                    reco_proof(d.get('reco_details'),rid,name,phase)
                    reco.update(phase=phase,end=d)
                continue
            if phase=='Starting':
                require(rid not in reco_ids, 'duplicate_recognition_id')
                reco_ids.add(rid);guards[rid]=(name,'Starting')
            else:
                require(phase in ('Succeeded','Failed') and guards.get(rid)==(name,'Starting'), 'unpaired_recognition')
                guards[rid]=(name,phase)
                if name=='OneKeyInput_onekey':
                    require(phase=='Failed' and active['start']['entry']=='OneKeyDo_onekey'
                            and not any(a[0]=='OneKeyStop' for a in active['actions']), 'consumer_guard_not_failed')
                    failed+=1
        elif msg=='Node.Action.Starting':
            require(active is not None and action is None and d.get('task_id')==active['start']['task_id'], 'action_outside_task')
            aid=d.get('action_id');name=d.get('name')
            if settings_abort:
                require(name in {'OneKeyExecRead','OneKeyExecSnapshot',
                    'OneKeyDo_enter','OneKeyInput_enter','OneKeyDo_settings_open','OneKeyInput_settings_open',
                    'OneKeyDo_settings_down','OneKeyInput_settings_down'}, 'unexpected_settings_abort_action')
            require(type(aid) is int and aid not in action_ids, 'duplicate_action_id')
            action_ids.add(aid)
            require(name in ('OneKeyExecRead','OneKeyExecSnapshot','OneKeyStop','OneKeyDo_onekey')
                    or name in {'OneKey'+part+op for part in ('Do_','Input_') for op in NAVIGATION},
                    'consumer_or_unknown_action_started')
            if name=='OneKeyStop':
                require(failed>0 and all(v[1]!='Starting' for v in guards.values()), 'stop_without_complete_failed_guard')
            if name.startswith('OneKeyInput_'):
                require(any(v==(name,'Succeeded') for v in guards.values()), 'input_without_verified_guard')
            action=d
        elif msg=='Node.Action.Succeeded':
            require(active is not None and action is not None
                    and all(d.get(k)==action.get(k) for k in ('name','action_id','task_id')), 'unpaired_action_end')
            proof=d.get('action_details',{});name=action['name']
            expected=('StopTask' if name=='OneKeyStop' else NAVIGATION[name[12:]][2]
                      if name.startswith('OneKeyInput_') else 'DoNothing')
            require(proof.get('name')==name and proof.get('action_id')==action['action_id']
                    and proof.get('success') is True and proof.get('action')==expected
                    and same_detail(proof.get('detail'),navigation_detail(name)), 'native_action_type_or_target_mismatch')
            action_proofs[action['action_id']]=proof
            active['proof_ids'].add(action['action_id'])
            active['actions'].append((name,expected));action=None
        else:
            raise ValueError('unrecognized_or_failed_native_event:'+msg)
    require(active is None and action is None and wait_pending is None and not recognition_nodes and not inner_recos
            and (failed==0 if settings_abort else failed>0) and len(tasks)==len(receipts)
            and all(r.get('hash') for r in resources.values()), 'incomplete_native_task_coverage')
    for task,receipt in zip(tasks,receipts):
        native=receipt['native'];entry=receipt['entry']
        require(type(native.get('task_id')) is int and task['start']['entry']==entry and task['start']['task_id']==native['task_id']
                and native.get('native_ok') is True, 'task_receipt_binding')
        names=(['OneKeyExecRead','OneKeyExecSnapshot'] if entry=='OneKeyExecRead' else
               [entry,'','OneKeyStop'] if entry=='OneKeyDo_onekey' else [entry,entry.replace('Do_','Input_')])
        require([n.get('name') for n in native['nodes']]==names
                and [name for name,_ in task['actions']]==[n for n in names if n], 'unexpected_receipt_or_action_order')
        for node in native['nodes']:
            if not node['name']:
                require(node.get('completed') is False and node.get('action') is None, 'consumer_not_failed')
            else:
                require(node.get('completed') is True and same_detail(node.get('action'),
                        dict(name=node['name'],success=True,raw=navigation_detail(node['name']))),
                        'receipt_action_mismatch')
        for name,phase in task['guards'].values():
            if name.startswith('OneKeyInput_'):
                expected=entry.replace('Do_','Input_')
                require(name==expected and phase==('Failed' if entry=='OneKeyDo_onekey' else 'Succeeded'),
                        'unexpected_input_guard')


def consumer_terminal(parent,run_id,pin,*,action=False):
    return dict(state='settled',operation='onekey' if action else 'schedule_onekey',game_day=parent['game_day'],
        intent_id=parent['intent_id'],device=parent['device'],disposition=CONSUMER_DISPOSITION,
        actual_input_sent=not action,consumer_input_sent=False,settlement_verified=False,
        grants_execution_permission=False,reconciliation=dict(version=1,run_id=run_id,
            intent_id=parent['intent_id'],evidence_sha256=pin))


def validate_no_consumer_locked(root,ledger,actions,run_id,game_day,pin,*,settings_successor=None):
    """Only first reviewed abort followed by one navigation-only attempt."""
    from .schedule_onekey import validate_execution, ITEMS, SETTINGS, OneKeyPage
    from datetime import date
    require(RUN.fullmatch(run_id) is not None and HEX.fullmatch(pin) is not None, 'invalid_review_binding')
    bundle_path=root/'.runtime/schedule-onekey-work'/('parent-no-consumer-evidence-'+game_day.replace('-','')+'.json')
    bundle_bytes=read(root,bundle_path,1024*1024)
    require(sha(bundle_bytes)==pin,'parent_review_sha_mismatch');bundle=decode(bundle_bytes)
    require(bundle.get('version')==1 and bundle.get('run_id')==run_id and bundle.get('game_day')==game_day
            and bundle.get('root_prefix_lines')==3, 'invalid_consumer_bundle')
    root_lines=read(root,ledger.path,1024*1024).splitlines(keepends=True)
    if settings_successor is not None:
        require(len(root_lines) in (5,6) and decode(root_lines[4])==settings_successor
                and settings_successor.get('retry_of')==decode(root_lines[2]).get('intent_id'),
                'invalid_settings_successor')
        root_lines=root_lines[:4]
    lines=read(root,actions.path,1024*1024).splitlines(keepends=True)
    n=bundle.get('action_prefix_lines')
    require(type(n) is int and 7<=n<=105 and n%2==1 and len(lines) in (n,n+1)
            and len(root_lines) in (3,4) and all(l.endswith(b'\n') for l in root_lines+lines), 'torn_or_extra_journal_events')
    parent=decode(root_lines[2]);intent=parent.get('intent_id')
    require(set(parent)=={'state','operation','game_day','device','authorizations','intent_id','retry_of'}
            and parent['state']=='in_flight' and parent['operation']=='schedule_onekey' and parent['game_day']==game_day
            and isinstance(intent,str) and re.fullmatch('[0-9a-f]{32}',intent)
            and bundle.get('intent_id')==intent and actions.directory==ledger.directory/'actions'/intent,
            'second_root_binding_invalid')
    validate_execution(game_day,parent['device'],parent['authorizations'])
    require(set(parent['authorizations'])==set(ITEMS),'pending_scope_not_authorized')
    first=decode(root_lines[0]);closed=decode(root_lines[1]);binding=closed['reconciliation']
    first_actions=Journal(ledger.directory/'actions'/first['intent_id'])
    path_in(root,first_actions.directory/'transaction.lock')
    with first_actions.locked():
        proof=validate_locked(root,ledger,first_actions,binding['run_id'],game_day,binding['evidence_sha256'],
                              successor=parent,settings_successor=settings_successor)
        require(proof['root_closed'] and proof['action_closed'] and first['device']==parent['device'], 'first_abort_not_verified')
    events=[decode(line) for line in lines[:n]]
    require(all(e.get('state')==('in_flight' if i%2==0 else 'settled') and e.get('game_day')==game_day
                for i,e in enumerate(events)), 'invalid_action_sequence')
    require(all(set(e)=={'state','operation','game_day','image_sha256'} for e in events[:-1])
            and set(events[-1])=={'state','operation','game_day','image_sha256','settings_audit'},
            'unexpected_original_action_fields')
    ops=[e.get('operation') for e in events[::2]]
    require(ops[:2]==['enter','settings_open'] and ops[-1]=='onekey' and ops.count('settings_close')==1
            and len(ops)==len(set(ops)), 'unexpected_navigation_sequence')
    closing=ops.index('settings_close')
    require(ops[2:closing]==['settings_down:'+str(i) for i in range(closing-2)] and closing-2<=25,
            'settings_scroll_scope')
    list_ops=ops[closing+1:-1]
    require(len(list_ops)<=24 and all(op in ('down:'+str(i),'up:'+str(i)) for i,op in enumerate(list_ops)),
            'list_scroll_scope')
    root_end=consumer_terminal(parent,run_id,pin);action_end=consumer_terminal(parent,run_id,pin,action=True)
    if len(lines)==n+1:
        actual=decode(lines[-1])
        require(actual==action_end and all(actual.get(k) is False for k in
            ('actual_input_sent','consumer_input_sent','settlement_verified','grants_execution_permission')),
            'forged_consumer_action_closure')
    if len(root_lines)==4:
        actual=decode(root_lines[-1])
        require(len(lines)==n+1 and actual==root_end and actual.get('actual_input_sent') is True
                and all(actual.get(k) is False for k in ('consumer_input_sent','settlement_verified','grants_execution_permission')),
                'unpaired_consumer_root_closure')
    run=root/'.runtime/supported-daily-work'/run_id;report_path=run/'report.json';log_path=run/'maa/maafw.log'
    report_bytes=read(root,report_path,16*1024*1024);log_bytes=read(root,log_path)
    report=decode(report_bytes)
    require(report.get('run_id')==run_id and report.get('game_day')==game_day and report.get('outcome')=='stopped'
            and report.get('source')=='live supported chain' and report.get('daily_complete') is False
            and report.get('supported_chain_complete') is False,'report_binding_invalid')
    steps=report['steps']
    require([s.get('name') for s in steps]==['schedule_onekey','event','deep','rewards','very_hard','hard']
            and steps[0].get('status')=='stopped' and all(s==dict(name=s['name'],status='not_run') for s in steps[1:]),
            'later_steps_or_wrong_chain')
    result=steps[0]['result']
    require(result.get('outcome')=='unknown' and result.get('reason')=='input_unverified_no_retry:onekey'
            and result.get('game_day')==game_day and result.get('device')==parent['device']
            and result.get('authorizations')==parent['authorizations'] and result.get('sent')==ops
            and result.get('one_key_attempted') is True
            and result.get('settlement_verified') is not True and result.get('consumer_input_sent') is not True
            and all(result.get(k) is False for k in ('schedule_executed','daily_complete','home_verified','nine_rows_verified'))
            and result.get('before_rows')==dict.fromkeys(ITEMS,'pending'), 'component_not_pre_consumer_abort')
    receipts=result['receipts'];require(isinstance(receipts,list) and 8<=len(receipts)<=210,'receipt_budget_invalid')
    require(receipts[0]['entry']=='OneKeyExecRead' and receipts[-1]['entry']=='OneKeyDo_onekey','receipt_boundary_invalid')
    hashes=bundle['sha256'];require(isinstance(hashes,dict),'invalid_hash_manifest')
    resolved={}
    for name,digest in hashes.items():
        p=path_in(root,name)
        require(p not in resolved and isinstance(digest,str) and HEX.fullmatch(digest), 'duplicate_or_bad_hash')
        resolved[p]=digest
    expected_paths={report_path,log_path,ledger.path,actions.path};readings=[];session=None;index=0
    for receipt in receipts:
        r=receipt['reading'];p=path_in(root,r['screenshot']);expected_paths.add(p)
        require(p.parent==run and p.name.startswith('onekey-exec-') and p.suffix=='.png'
                and r.get('acquisition')=='current_tasker_capture' and isinstance(r.get('session_id'),str)
                and r['session_id'] and type(r.get('capture_index')) is int and r['capture_index']>index,
                'reading_binding_invalid')
        day=date.fromisoformat(game_day)
        require(isinstance(r.get('captured_at'),str) and r['captured_at'].startswith(game_day+'T')
                and r.get('date') in (None,[day.month,day.day]), 'reading_date_changed')
        if session is None:session=r['session_id']
        require(r['session_id']==session and r.get('image_sha256')==resolved.get(p),'reading_sha_or_session_mismatch')
        index=r['capture_index'];readings.append(r)
    require(set(resolved)==expected_paths,'wrong_consumer_evidence_paths')
    for p,digest in resolved.items():
        data=(b''.join(root_lines[:3]) if p==ledger.path else b''.join(lines[:n]) if p==actions.path else
              report_bytes if p==report_path else log_bytes if p==log_path else read(root,p))
        require(sha(data)==digest,'original_file_sha_mismatch')
    final=readings[-1]
    final_schedule=final['state']=='schedule' and final.get('reason') is None
    require(readings[0]['state']=='home' and (final_schedule or
            (final['state']=='unknown' and final.get('reason')=='schedule_date_unreadable')),
            'reading_boundary_invalid')
    # A failed native guard can leave a verified page. This is evidence about
    # the failed input, never permission to dispatch it again. Both boundary
    # forms still require an independently saved capture and no risk text.
    require(path_in(root,final['screenshot']) not in
            {path_in(root,r['screenshot']) for r in readings[:-1]},'stale_final_capture')
    require(isinstance(final.get('words'),list),'final_words_missing')
    require(not any(re.search(
        '确认|确定|取消|购买|充值|重置|宝石|钻石|付费|有偿|额外战斗|战斗开始|日期.*变更|连接|通信|重试|重新登录|计划整理|(?:扫荡券|券).*(?:不足|不够)|(?:不足|不够).*(?:扫荡券|券)',
        re.sub(r'\s+','',word['text'])) for word in final['words']),'final_risk_text')
    audit=result['settings_audit']
    require(audit==events[-1].get('settings_audit') and audit.get('verified') is True
            and audit.get('session_id')==session and audit.get('game_day')==game_day and audit.get('device')==parent['device']
            and audit.get('rows')=={k:v[1] for k,v in SETTINGS.items()}, 'settings_proof_invalid')
    settings_frames=[];catalog={};cursor=1;before=readings[0];opened=None;anchored=False
    for j,op in enumerate(ops):
        base=op.split(':')[0];entry='OneKeyDo_'+base
        require(cursor<len(receipts) and receipts[cursor]['entry']==entry
                and events[2*j]['image_sha256']==before['image_sha256'],'action_intent_receipt_mismatch')
        if base=='onekey':
            require(cursor==len(receipts)-1 and anchored and list(catalog)==list(ITEMS)
                    and catalog==dict.fromkeys(ITEMS,'pending'),'pending_inventory_unverified')
            if final_schedule:
                require(OneKeyPage.signature(final)==OneKeyPage.signature(before),'final_schedule_changed')
            break
        source,target,_=NAVIGATION[base]
        require(before['state']==source and OneKeyPage.signature(readings[cursor])==OneKeyPage.signature(before),
                'navigation_guard_changed')
        cursor+=1;observed=[]
        while cursor<len(receipts) and receipts[cursor]['entry']=='OneKeyExecRead':
            observed.append(readings[cursor]);cursor+=1
        # Mirror the existing bounded read-only transition evidence. Every
        # intermediate capture is accounted for; only the final verified page
        # can settle the navigation, and no additional input is inferred.
        transient={'unverified:ScheduleGuard','unverified:OneKeyLabel','unverified:OneKeyPanelBright',
                   'unverified:OneKeySettingsTitle','unverified:OneKeySettingsClose',
                   'unverified:OneKeySettingsBright','schedule_date_unreadable','unreadable_home_resource'}
        require(1<=len(observed)<=4 and observed[-1]['state']==target,'unverified_navigation_result')
        require(len({r['image_sha256'] for r in [before,*observed]})==len(observed)+1,'stale_navigation_capture')
        for intermediate in observed[:-1]:
            require(intermediate['state']=='unknown' and intermediate.get('reason') in transient
                    and isinstance(intermediate.get('words'),list),'unverified_transition_frame')
            require(not any(re.search('确认|购买|充值|重置|钻石|付费|有偿|额外战斗|战斗开始|日期.*变更|连接|通信|重试|重新登录',
                        word['text']) for word in intermediate['words']),'transition_risk_text')
        after=observed[-1]
        require(events[2*j+1].get('operation')==op and events[2*j+1].get('image_sha256')==after['image_sha256'],
                'navigation_settlement_mismatch')
        if base=='settings_open':opened=before;require(after.get('settings_top') is True,'settings_not_top')
        if target=='settings':settings_frames.append(after)
        if base=='settings_close':
            require(OneKeyPage.signature(after)==OneKeyPage.signature(opened),'settings_return_changed')
            catalog={};anchored=False
        if target=='schedule' and base!='enter':
            if 'delegated' in after['sections']:
                require(after['sections']['delegated']==dict(completed=0,total=9),'delegated_count_changed')
                anchored=True
            if anchored:
                for k,row in sorted(after['rows'].items(),key=lambda item:item[1]['y']):
                    require(k in ITEMS and row.get('state')=='pending','row_changed_or_unknown');catalog[k]='pending'
        before=after
    require(before.get('bottom') is True,'list_bottom_not_verified')
    expected_frames=[{k:r[k] for k in ('screenshot','image_sha256','session_id','capture_index','captured_at')} for r in settings_frames]
    require(audit['frames']==expected_frames,'settings_frame_binding')
    collected={}
    for r in settings_frames:
        for k,value in r['settings'].items():
            require(k in SETTINGS and value==SETTINGS[k][1],'unsafe_setting');collected[k]=value
    require(collected==audit['rows'],'settings_coverage_incomplete')
    native_no_consumer(log_bytes,receipts,game_day)
    return dict(parent=parent,root_end=root_end,action_end=action_end,
                root_closed=len(root_lines)==4,action_closed=len(lines)==n+1)


def settings_terminal(parent,run_id,pin,*,action=False):
    result=consumer_terminal(parent,run_id,pin,action=action)
    result.update(disposition=SETTINGS_DISPOSITION,actual_input_sent=True,
                  operation='settings_down:21' if action else 'schedule_onekey',
                  final_page_verified=False,abort_reason=SETTINGS_ABORT_REASON)
    return result


def validate_settings_abort_locked(root,ledger,actions,run_id,game_day,pin):
    """One fixed third attempt, not a general unknown-result reconciliation."""
    from datetime import date
    from .schedule_onekey import validate_execution, ITEMS, SETTINGS, OneKeyPage
    require(run_id==SETTINGS_ABORT_RUN and game_day==SETTINGS_ABORT_DAY
            and isinstance(pin,str) and HEX.fullmatch(pin), 'wrong_settings_abort_binding')
    bundle_path=root/'.runtime/schedule-onekey-work'/('parent-settings-abort-evidence-'+game_day.replace('-','')+'.json')
    bundle_bytes=read(root,bundle_path,1024*1024)
    require(sha(bundle_bytes)==pin,'parent_review_sha_mismatch')
    bundle=decode(bundle_bytes)
    require(bundle.get('version')==1 and bundle.get('run_id')==run_id and bundle.get('game_day')==game_day
            and bundle.get('root_prefix_lines')==5 and bundle.get('action_prefix_lines')==47,
            'invalid_settings_abort_bundle')
    root_lines=read(root,ledger.path,1024*1024).splitlines(keepends=True)
    lines=read(root,actions.path,1024*1024).splitlines(keepends=True)
    require(len(root_lines) in (5,6) and len(lines) in (47,48)
            and all(line.endswith(b'\n') for line in root_lines+lines),'torn_or_extra_journal_events')
    parent=decode(root_lines[4]);intent=parent.get('intent_id')
    require(set(parent)=={'state','operation','game_day','device','authorizations','intent_id','retry_of'}
            and parent.get('state')=='in_flight' and parent.get('operation')=='schedule_onekey'
            and parent.get('game_day')==game_day and isinstance(intent,str) and re.fullmatch('[0-9a-f]{32}',intent)
            and intent==bundle.get('intent_id') and actions.directory==ledger.directory/'actions'/intent,
            'third_root_binding_invalid')
    validate_execution(game_day,parent['device'],parent['authorizations'])
    require(set(parent['authorizations'])==set(ITEMS),'pending_scope_not_authorized')
    previous=decode(root_lines[2]);binding=decode(root_lines[3])['reconciliation']
    require(parent['retry_of']==previous.get('intent_id') and parent['device']==previous.get('device')
            and len({decode(root_lines[0]).get('intent_id'),previous.get('intent_id'),intent})==3,
            'third_retry_chain_invalid')
    previous_actions=Journal(ledger.directory/'actions'/parent['retry_of'])
    path_in(root,previous_actions.directory/'transaction.lock')
    with previous_actions.locked():
        prior=validate_no_consumer_locked(root,ledger,previous_actions,binding['run_id'],game_day,
                                          binding['evidence_sha256'],settings_successor=parent)
        require(prior['root_closed'] and prior['action_closed'],'previous_abort_not_closed')
    ops=['enter','settings_open']+['settings_down:'+str(i) for i in range(22)]
    events=[decode(line) for line in lines[:47]]
    for i,event in enumerate(events):
        require(set(event)=={'state','operation','game_day','image_sha256'}
                and event['state']==('in_flight' if i%2==0 else 'settled')
                and event['operation']==ops[i//2] and event['game_day']==game_day,
                'unexpected_settings_action_sequence')
    root_end=settings_terminal(parent,run_id,pin);action_end=settings_terminal(parent,run_id,pin,action=True)
    if len(lines)==48:
        require(same_detail(decode(lines[-1]),action_end),'forged_settings_action_closure')
    if len(root_lines)==6:
        require(len(lines)==48 and same_detail(decode(root_lines[-1]),root_end),'unpaired_settings_root_closure')
    run=root/'.runtime/supported-daily-work'/run_id
    report_path=run/'report.json';log_path=run/'maa/maafw.log'
    report_bytes=read(root,report_path,16*1024*1024);log_bytes=read(root,log_path)
    report=decode(report_bytes)
    require(report.get('run_id')==run_id and report.get('game_day')==game_day
            and report.get('source')=='live supported chain' and report.get('outcome')=='stopped'
            and report.get('daily_complete') is False and report.get('supported_chain_complete') is False,
            'report_binding_invalid')
    steps=report['steps']
    require([s.get('name') for s in steps]==['schedule_onekey','event','deep','rewards','very_hard','hard']
            and steps[0].get('status')=='stopped'
            and all(s==dict(name=s['name'],status='not_run') for s in steps[1:]),'later_steps_or_wrong_chain')
    result=steps[0]['result']
    require(result.get('outcome')=='unknown' and result.get('reason')==SETTINGS_ABORT_REASON
            and result.get('game_day')==game_day and result.get('device')==parent['device']
            and result.get('authorizations')==parent['authorizations'] and result.get('sent')==ops
            and all(result.get(k) is False for k in ('one_key_attempted','schedule_executed','daily_complete',
                                                    'home_verified','nine_rows_verified'))
            and 'before_rows' not in result and not result.get('quotes') and not result.get('quote')
            and all(k not in result or result[k] is False for k in
                    ('consumer_input_sent','settlement_verified','consumption_sent')),
            'component_not_settings_abort')
    receipts=result['receipts']
    expected_entries=['OneKeyExecRead']
    for op in ops:expected_entries.extend(['OneKeyDo_'+op.split(':')[0],'OneKeyExecRead'])
    require(isinstance(receipts,list) and [r['entry'] for r in receipts]==expected_entries,
            'settings_receipt_sequence_invalid')
    hashes=bundle['sha256'];require(isinstance(hashes,dict),'invalid_hash_manifest')
    resolved={}
    for name,digest in hashes.items():
        p=path_in(root,name)
        require(p not in resolved and isinstance(digest,str) and HEX.fullmatch(digest),'duplicate_or_bad_hash')
        resolved[p]=digest
    expected_paths={report_path,log_path,ledger.path,actions.path}
    readings=[];session=None;index=0;captures=set()
    day=date.fromisoformat(game_day)
    for receipt in receipts:
        r=receipt['reading'];p=path_in(root,r['screenshot'])
        require(p.parent==run and p.name.startswith('onekey-exec-') and p.suffix=='.png' and p not in captures
                and r.get('acquisition')=='current_tasker_capture' and isinstance(r.get('session_id'),str)
                and bool(r['session_id']) and type(r.get('capture_index')) is int and r['capture_index']>index
                and isinstance(r.get('captured_at'),str) and r['captured_at'].startswith(game_day+'T')
                and r.get('date') in (None,[day.month,day.day]) and not r.get('quote'),
                'settings_reading_binding_invalid')
        if session is None:session=r['session_id']
        require(r['session_id']==session and r.get('image_sha256')==resolved.get(p),'reading_sha_or_session_mismatch')
        index=r['capture_index'];captures.add(p);expected_paths.add(p);readings.append(r)
    require(set(resolved)==expected_paths,'wrong_settings_evidence_paths')
    for p,digest in resolved.items():
        data=(b''.join(root_lines[:5]) if p==ledger.path else b''.join(lines[:47]) if p==actions.path else
              report_bytes if p==report_path else log_bytes if p==log_path else read(root,p))
        require(sha(data)==digest,'original_file_sha_mismatch')
    require(readings[0]['state']=='home' and readings[0].get('reason') is None,'settings_start_not_home')
    for j,op in enumerate(ops):
        before,guard,after=readings[2*j:2*j+3]
        base=op.split(':')[0];source,target,_=NAVIGATION[base]
        require(before['state']==source and before.get('reason') is None and guard.get('reason') is None
                and OneKeyPage.signature(guard)==OneKeyPage.signature(before)
                and events[2*j]['image_sha256']==before['image_sha256']
                and before['image_sha256']!=after['image_sha256'],'settings_navigation_guard_changed')
        if j<23:
            require(after['state']==target and after.get('reason') is None
                    and events[2*j+1]['image_sha256']==after['image_sha256'],'settings_navigation_result_invalid')
            if base=='settings_open':require(after.get('settings_top') is True,'settings_not_top')
            if target=='settings':
                require(isinstance(after.get('settings'),dict)
                        and all(k in SETTINGS and v==SETTINGS[k][1] for k,v in after['settings'].items()),
                        'unsafe_setting')
    audit=result.get('settings_audit')
    require(isinstance(audit,dict)
            and set(audit)=={'session_id','game_day','device','rows','frames','verified'}
            and audit['verified'] is False and audit['session_id']==session
            and audit['game_day']==report['game_day'] and audit['device']==parent['device'],
            'settings_partial_audit_binding_invalid')
    settings_frames=readings[4:47:2]
    projection=('screenshot','image_sha256','session_id','capture_index','captured_at')
    require(len(settings_frames)==22 and audit['frames']==[
        {key:frame[key] for key in projection} for frame in settings_frames],
        'settings_partial_audit_frames_invalid')
    collected={}
    for frame in settings_frames:
        for key,value in frame['settings'].items():
            require(key in SETTINGS and value==SETTINGS[key][1]
                    and (key not in collected or collected[key]==value),
                    'settings_partial_audit_row_conflict')
            collected[key]=value
    require(len(collected)==13 and audit['rows']==collected
            and set(collected)!=set(SETTINGS), 'settings_partial_audit_rows_invalid')
    final=readings[-1]
    require(final['state']=='unknown' and final.get('reason')==SETTINGS_ABORT_REASON
            and isinstance(final.get('words'),list),'settings_abort_boundary_unknown')
    # An unknown reader result is not a verified page. Require the narrow
    # reported settings boundary as well as the complete native input proof.
    words=final['words']
    def anchor(label,roi):
        x,y,w,h=roi
        matches=[word for word in words if re.sub(r'\s+','',word['text'])==label]
        if len(matches)!=1:return False
        word=matches[0];box=word['box']
        return (word['score']>=.9 and len(box)==4 and box[2]>0 and box[3]>0
                and x<=box[0] and y<=box[1] and box[0]+box[2]<=x+w and box[1]+box[3]<=y+h)
    require(anchor('计划整理',(0,0,1280,96)) and anchor('关闭',(480,590,320,120))
            and anchor('黎明界迷宫设定',(70,96,500,496)), 'settings_abort_boundary_unproven')
    known_labels=set(SETTINGS)|{option for options,_ in SETTINGS.values() for option in options}
    for word in words:
        value=re.sub(r'\s+','',word['text'])
        require(value in known_labels or not re.search(
            '确认|确定|取消|购买|充值|重置|宝石|钻石|付费|有偿|额外战斗|战斗开始|日期.*变更|连接|通信|重试|重新登录|不足|不够',value),
            'settings_abort_risk_text')
    native_no_consumer(log_bytes,receipts,game_day,settings_abort=True)
    return dict(parent=parent,root_end=root_end,action_end=action_end,
                root_closed=len(root_lines)==6,action_closed=len(lines)==48)


def consumer_journals(root,*,settings_abort=False):
    ledger=Journal(root/'.runtime/schedule-onekey-work/ledger')
    path_in(root,ledger.directory/'transaction.lock')
    lines=read(root,ledger.path,1024*1024).splitlines()
    require(len(lines) in ((5,6) if settings_abort else (3,4)),'unexpected_abort_history_length')
    intent=decode(lines[4 if settings_abort else 2]).get('intent_id')
    require(isinstance(intent,str) and re.fullmatch('[0-9a-f]{32}',intent),'invalid_intent_id')
    actions=Journal(ledger.directory/'actions'/intent)
    path_in(root,actions.directory/'transaction.lock');path_in(root,actions.path)
    return ledger,actions


def reconcile_no_consumer(*,run_id,game_day,evidence_sha256,apply=False,root=None):
    return _reconcile_consumer(run_id=run_id,game_day=game_day,evidence_sha256=evidence_sha256,apply=apply,root=root)


def reconcile_settings_abort(*,run_id,game_day,evidence_sha256,apply=False,root=None):
    return _reconcile_consumer(run_id=run_id,game_day=game_day,evidence_sha256=evidence_sha256,
                               apply=apply,root=root,settings_abort=True)


def _reconcile_consumer(*,run_id,game_day,evidence_sha256,apply=False,root=None,settings_abort=False):
    from . import native
    result=dict(outcome='unknown',daily_complete=False,settlement_verified=False,grants_execution_permission=False)
    try:
        require(type(apply) is bool,'explicit_boolean_apply_required')
        root=Path(root) if root is not None else native.ROOT
        ledger,actions=consumer_journals(root,settings_abort=settings_abort)
        validate=validate_settings_abort_locked if settings_abort else validate_no_consumer_locked
        suffix='settings_abort' if settings_abort else 'no_consumer_abort'
        with ledger.locked(),actions.locked():
            proof=validate(root,ledger,actions,run_id,game_day,evidence_sha256)
            if proof['root_closed']:result['outcome']='already_reconciled'
            elif not apply:result.update(outcome='eligible_'+suffix,partial=proof['action_closed'])
            else:
                if not proof['action_closed']:actions.append(proof['action_end'])
                validate(root,ledger,actions,run_id,game_day,evidence_sha256)
                ledger.append(proof['root_end']);result['outcome']='reconciled_'+suffix
            result.update(actual_input_sent=True,consumer_input_sent=False)
            if settings_abort:result.update(final_page_verified=False,abort_reason=SETTINGS_ABORT_REASON)
    except (Exception,KeyboardInterrupt) as exc:
        result['reason']=str(exc) or 'interrupted'
    return result


def consumer_retry_authorized(journal,events,game_day,device):
    """Root lock held. Reverify bounded historical aborts; never dispatch."""
    from . import native
    try:
        settings_abort=len(events)==6 and events[-1].get('disposition')==SETTINGS_DISPOSITION
        require(journal.directory==native.ROOT/'.runtime/schedule-onekey-work/ledger'
                and (settings_abort or (len(events)==4 and events[-1].get('disposition')==CONSUMER_DISPOSITION))
                and events==journal.events(),
                'wrong_consumer_retry_history')
        ledger,actions=consumer_journals(native.ROOT,settings_abort=settings_abort)
        binding=events[-1]['reconciliation']
        with actions.locked():
            validate=validate_settings_abort_locked if settings_abort else validate_no_consumer_locked
            proof=validate(native.ROOT,ledger,actions,binding['run_id'],game_day,binding['evidence_sha256'])
            require(proof['root_closed'] and proof['action_closed'] and proof['parent']['device']==device,
                    'consumer_retry_binding_mismatch')
        return proof['parent']['intent_id']
    except Exception:
        return None


def recovery_message(result):
    """Player wording only; this text never carries retry authority."""
    names = {'onekey': '日程一键', 'dungeon_confirm': '地下城确认',
             'dungeon_result': '地下城结果收尾', 'mana_confirm': '玛那确认',
             'exp_confirm': '经验确认', 'enter': '进入日程'}
    operation = result.get('last_operation', 'unknown')
    step = names.get(operation, operation)
    original = result.get('original_run_id', '待只读检查定位')
    when = result.get('stopped_at_cn') or result.get('started_at_cn') or result.get('original_game_day', '时间待核')
    state = result.get('original_input_state')
    sent = ('已发送，结果未完整确认' if state == 'sent_result_unknown' else
            '此前已有输入，最后一步是否发送仍待核' if state == 'some_input_sent_last_unknown' else
            '原证据已确认未发送' if state == 'verified_not_sent' else
            '已有完整执行证据待复核' if state == 'complete_evidence' else '是否发送仍待核')
    current = '本次未发送日程输入。' if result.get('current_input_state') == 'not_sent' else ''
    return (f'{current}上次 {when}（{original}）停在「{step}」：{sent}。'
            f'原因：{result.get("original_reason") or result.get("reason", "unknown")}。'
            '未决记录用于防止重复消费，禁止直接重跑；重开软件或换日不会解锁。'
            '可交维护端运行只读恢复检查，核对原报告、动作账和缺失证据；不要删账或勾选声明绕过。'
            '需要当前页面观察或专属非消费收尾时，由维护端另核设备与当次授权；本检查不操作游戏。')


def _readonly_abort_proof(evidence, ledger, actions, run_id, game_day, pin, kind):
    """Reuse the frozen validators with the history reader's read-only technique."""
    from contextlib import nullcontext
    from types import FunctionType
    class ReadOnlyJournal(Journal):
        def locked(self):
            return nullcontext(self)

        def append(self, event):
            raise ValueError('readonly_recovery')
    def snapshot_read(root, path, limit=64 * 1024 * 1024):
        require(root == evidence.root, 'wrong_evidence_root')
        data = evidence.read(path)
        require(len(data) <= limit, 'evidence_too_large')
        return data
    namespace = dict(globals(), Journal=ReadOnlyJournal, read=snapshot_read)
    names = {'pre-input': 'validate_locked', 'pre-consumer': 'validate_no_consumer_locked',
             'settings-abort': 'validate_settings_abort_locked'}
    for name in names.values():
        original = globals()[name]
        fn = FunctionType(original.__code__, namespace, name, original.__defaults__, original.__closure__)
        fn.__kwdefaults__ = original.__kwdefaults__
        namespace[name] = fn
    return namespace[names[kind]](evidence.root, ledger, actions, run_id, game_day, pin)


def diagnose(*, run_id, root=None, game_day=None, evidence_sha256=None, kind='pre-input'):
    """Selected run + exact pending intent. Zero locks, writes, native setup or inputs.

    A byte snapshot is rechecked before returning. Digests record consistency, not
    producer authenticity. No diagnostic result is accepted by execution history.
    """
    from datetime import datetime, timezone, timedelta
    from . import native
    from .navigation_abort import Evidence, events
    from .onekey_history import verify_execution
    result = dict(outcome='unknown', recovery_state='evidence_insufficient',
        reason='unsettled_intent_any_day_no_resend', selected_run_id=run_id,
        execution_permitted=False, grants_execution_permission=False, ledger_written=False,
        settlement_verified=False, daily_complete=False, supports=[], gaps=[], evidence_sha256={})
    evidence = None
    try:
        root = Path(root) if root is not None else native.ROOT
        evidence = Evidence(root.absolute())
        require(isinstance(run_id, str) and RUN.fullmatch(run_id), 'invalid_run_id')
        ledger = Journal(evidence.root / '.runtime/schedule-onekey-work/ledger')
        history = events(evidence.read(ledger.path))
        require(all(e.get('operation') == 'schedule_onekey' for e in history), 'foreign_journal')
        # No scan of unrelated reports and no selection by computer date.
        parent = history[-1] if history[-1]['state'] == 'in_flight' else history[-2]
        intent = parent.get('intent_id')
        require(isinstance(intent, str) and re.fullmatch('[0-9a-f]{32}', intent), 'invalid_intent_id')
        actions = Journal(ledger.directory / 'actions' / intent)
        action_events = events(evidence.read(actions.path))
        def load_report(selected):
            require(isinstance(selected, str) and RUN.fullmatch(selected), 'original_run_binding_missing')
            report = decode(evidence.read(evidence.root / '.runtime/supported-daily-work' / selected / 'report.json'))
            require(report.get('run_id') == selected, 'report_run_mismatch')
            steps = [s for s in report['steps'] if s.get('name') == 'schedule_onekey']
            require(len(steps) == 1, 'schedule_step_missing_or_duplicate')
            return report, steps[0]['result']
        selected, selected_result = load_report(run_id)
        origin_id = parent.get('invocation_id', run_id)
        original, component = load_report(origin_id)
        require(original.get('game_day') == component.get('game_day') == parent.get('game_day')
                and component.get('device') == parent.get('device')
                and component.get('authorizations') == parent.get('authorizations'), 'report_target_mismatch')
        if game_day is not None:
            require(game_day == parent['game_day'], 'recovery_game_day_mismatch_no_unlock')
        context = component.get('navigation_context')
        if context is not None:
            require(context == dict(run_id=intent, session_id=parent.get('session_id'),
                invocation_id=origin_id, game_day=parent['game_day'], device=parent['device']),
                'report_context_mismatch')
        elif evidence_sha256 is None:
            result['gaps'].append('original_context_missing_requires_legacy_proof')
        if origin_id != run_id:
            require(selected_result.get('reason') == 'unsettled_intent_any_day_no_resend'
                    and selected_result.get('sent') == [] and selected_result.get('one_key_attempted') is False
                    and selected_result.get('device') == parent['device'], 'blocked_report_binding_mismatch')
            result['current_input_state'] = 'not_sent'
            if selected.get('game_day') != parent['game_day']:
                result['gaps'].append('different_game_day_does_not_resolve_pending')
        else:
            result['current_input_state'] = 'see_original'
        for action in action_events:
            require(action.get('game_day') == parent['game_day'], 'action_game_day_mismatch')
            for key in ('device', 'intent_id', 'session_id'):
                if key in action or key == 'session_id' and key in parent:
                    require(action.get(key) == parent.get(key), 'action_target_mismatch')
        result.update(original_run_id=origin_id, intent_id=intent, original_game_day=parent['game_day'],
            original_reason=component.get('reason'), root_state=history[-1]['state'],
            action_state=action_events[-1]['state'], last_operation=action_events[-1]['operation'],
            reported_sent=component.get('sent', []), original_input_state='unknown')
        for field, target in (('started_at', 'started_at_cn'), ('finished_at', 'stopped_at_cn')):
            timestamp = original.get(field)
            if type(timestamp) in (int, float):
                result[target] = datetime.fromtimestamp(timestamp, timezone(timedelta(hours=8))).isoformat()
        from .schedule_house import completed
        confirmed = []
        for receipt in component.get('receipts', []):
            entry = receipt.get('entry', '')
            if entry.startswith('OneKeyDo_') and completed(receipt['native'], entry.replace('OneKeyDo_', 'OneKeyInput_')):
                confirmed.append(entry[len('OneKeyDo_'):])
        result['native_receipt_confirmed_inputs'] = confirmed
        last_base = result['last_operation'].split(':')[0]
        result['last_operation_input_state'] = 'sent' if last_base in confirmed else 'unknown'
        if confirmed:
            result['original_input_state'] = ('sent_result_unknown' if last_base in confirmed
                                              else 'some_input_sent_last_unknown')
            result['supports'].append('native_receipts_confirm_input_not_settlement')
        result['supports'].append('original_report_and_action_ledger_located')
        result['gaps'] += ['nine_rows_and_home_settlement_missing',
                          'current_page_and_display_not_observed', 'history_not_revalidated_for_execution']
        result['next_steps'] = [
            '维护端先只读核当前设备/display、游戏页面及是否刷新或重新登录；旧截图不提供当前资格。',
            '若仍在地下城结果：仅在新鲜同帧专属识别及另行授权后使用既有 dungeon_finish / dungeon_result 收尾；不得发送 onekey 或 dungeon_confirm。',
            '若出现玛那/经验等消费确认：停机另核原发送记录、目标、次数、报价与当次许可，不自动续跑。',
            '需补齐与原 intent/会话关联的九行完成、同栏9/9和主页宝石/AP读回；地下城1/1不能代替完整日程结算。']
        proof = component.get('journal_evidence')
        if proof:
            require(proof['start'] == parent and proof['actions'] == action_events, 'execution_journal_mismatch')
            if history[-1]['state'] == 'settled':
                require(proof['end'] == history[-1], 'execution_terminal_mismatch')
            verify_execution(component, parent, proof['end'], action_events, invocation_id=origin_id)
            for receipt in component['receipts']:
                page = receipt['reading']
                require(sha(evidence.read(page['screenshot'])) == page['image_sha256'], 'execution_image_mismatch')
            result.update(outcome='evidence_verified', recovery_state='complete_evidence_review_required',
                          original_input_state='complete_evidence')
            result['supports'].append('existing_verify_execution_contract_and_images_verified')
            result['gaps'].remove('nine_rows_and_home_settlement_missing')
        if evidence_sha256 is not None:
            require(kind in ('pre-input', 'pre-consumer', 'settings-abort'), 'invalid_proof_kind')
            verified = _readonly_abort_proof(evidence, ledger, actions, origin_id, parent['game_day'], evidence_sha256, kind)
            require(verified['parent'] == parent, 'proof_target_mismatch')
            result.update(outcome='evidence_verified', recovery_state='already_reconciled' if verified['root_closed']
                else {'pre-input':'eligible_no_input_abort', 'pre-consumer':'eligible_no_consumer_abort',
                      'settings-abort':'eligible_settings_abort'}[kind])
            if kind == 'pre-input':
                result['original_input_state'] = 'verified_not_sent'
            result['supports'].append('existing_abort_evidence_contract_verified')
            result['gaps'] = ['abort_is_not_game_settlement_or_execution_permission']
        evidence.recheck()
    except (Exception, KeyboardInterrupt) as exc:
        result.update(outcome='unknown', recovery_state='evidence_insufficient')
        if result.get('original_input_state') in ('complete_evidence', 'verified_not_sent'):
            result['original_input_state'] = 'unknown'
        result['gaps'].append(str(exc) or 'interrupted')
    if evidence is not None:
        result['evidence_sha256'] = {str(p.relative_to(evidence.root)): sha(data) for p, data in evidence.files.items()}
    result['message'] = recovery_message(result)
    return result


def blocked_recovery(journal):
    """Enrich an already blocked start, never broaden or replace its gate."""
    events = journal.events()
    parent = events[-1] if events else {}
    root = journal.directory.parents[2]
    require(journal.directory == root / '.runtime/schedule-onekey-work/ledger', 'wrong_recovery_ledger')
    result = diagnose(root=root, run_id=parent.get('invocation_id'))
    result['current_input_state'] = 'not_sent'
    result['message'] = recovery_message(result)
    return result


def interrupted_recovery(report, invocation_id):
    """Immediate failure context before the enclosing report has been saved."""
    result = dict(outcome='unknown', recovery_state='evidence_insufficient',
        execution_permitted=False, ledger_written=False, daily_complete=False,
        current_input_state='see_original', original_run_id=invocation_id,
        original_game_day=report.get('game_day'), original_reason=report.get('reason'),
        last_operation=report['sent'][-1] if report.get('sent') else 'unknown',
        original_input_state=('sent_result_unknown' if report.get('native_input_state') == 'completed'
                              else 'unknown'),
        gaps=['result_or_cleanup_unverified', 'readonly_original_evidence_check_required'])
    result['message'] = recovery_message(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='父端显式输入证据对账；默认只读，不运行游戏')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--game-day')
    parser.add_argument('--evidence-sha256', help='父端独立审查后钉住的对应证据包 SHA-256')
    parser.add_argument('--diagnose', action='store_true', help='只读定位未决记录和证据缺口；零设备、零输入、零账本写入')
    parser.add_argument('--apply', action='store_true', help='仅追加经核证的意图终止事件，不执行、不授予许可')
    parser.add_argument('--kind', choices=['pre-input','pre-consumer','settings-abort'], default='pre-input',
                        help='pre-consumer 核第二次；settings-abort 仅核指定第三次设置页中止')
    args = parser.parse_args(argv)
    if args.diagnose:
        if args.apply:
            parser.error('--diagnose 不接受 --apply；只读诊断不结案')
        result = diagnose(run_id=args.run_id, game_day=args.game_day,
                          evidence_sha256=args.evidence_sha256, kind=args.kind)
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result['outcome'] == 'unknown' else 0
    if not args.game_day or not args.evidence_sha256:
        parser.error('原对账入口需要 --game-day 和 --evidence-sha256；只读定位请加 --diagnose')
    operation={'pre-input':reconcile,'pre-consumer':reconcile_no_consumer,'settings-abort':reconcile_settings_abort}[args.kind]
    result = operation(run_id=args.run_id, game_day=args.game_day,
                       evidence_sha256=args.evidence_sha256, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result['outcome'] == 'unknown' else 0


if __name__ == '__main__':
    raise SystemExit(main())
