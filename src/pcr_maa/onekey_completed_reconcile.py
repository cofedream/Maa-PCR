"""Bounded completion evidence and one explicitly approved historical closure.

real_dry_run remains diagnostic only. The isolated synthetic append is an audit record,
not an operational settlement or permission to resume schedule-onekey.
The separate parent-assisted entry accepts only the fixed approval and review.
"""
import hashlib
import json
from pathlib import Path
import re

from .onekey_history import path_in, require

SIDECAR_ACTIONS = (('dungeon_confirm',), ('dungeon_result',),
                   ('mana_confirm', 'mana_result'),
                   ('exp_confirm', 'exp_result', 'like_result'), ('item_result',))
POINTS = dict(onekey=[880,640], dungeon_confirm=[785,493], mana_confirm=[785,493],
              exp_confirm=[785,493], dungeon_result=[640,640], mana_result=[640,640],
              exp_result=[640,640], like_result=[640,493], item_result=[640,640])
REQUIRED = ('root', 'child', 'original_report', 'original_log', 'schedule_report',
            'schedule_log', 'schedule_frame', 'home_report', 'home_log', 'home_frame') + tuple(
                f'sidecar{i}_{kind}' for i in range(5) for kind in ('report','intent','dispatch','log'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def decode(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def lines(data):
    require(data.endswith(b'\n'), 'incomplete_jsonl')
    return [decode(line) for line in data.splitlines()]


def load_bundle(root, manifest_path, digest):
    root = Path(root).absolute()
    path = path_in(root, manifest_path)
    data = path.read_bytes()
    require(sha(data) == digest, 'manifest_changed')
    manifest = decode(data)
    require(manifest.get('schema') == 1 and type(manifest.get('synthetic')) is bool, 'manifest_schema')
    pins = manifest['files']
    require(set(REQUIRED) <= set(pins), 'missing_required_input')
    payloads, paths = {}, {}
    for role, pin in pins.items():
        p = path_in(root, pin['path'])
        require(p not in paths.values() and p.stat().st_size <= 64*1024*1024, 'duplicate_or_large_evidence')
        payloads[role] = p.read_bytes()
        require(sha(payloads[role]) == pin['sha256'], 'changed_evidence:' + role)
        paths[role] = p
    return manifest, payloads, paths


def native_clicks(data, receipts, game_day, device):
    """Pair native tasks/actions and compare all Clicks with report receipts.

    This is local consistency evidence, not authentication of the producer.
    The independently pinned manifest remains the review trust boundary.
    """
    text = data.decode('utf-8')
    require(data.endswith(b'\n') and text.count('[Logger] MAA Process Start') == 1
            and text.count('[Logger] Close log') == 1, 'native_log_envelope')
    active = action = None
    task_ids, action_ids, actual = set(), set(), []
    uuid = re.sub('[^a-zA-Z0-9-]', '', device['serial'])
    for line in text.splitlines():
        if '!!!OnEventNotify!!!' not in line:
            continue
        m = re.search(r'\[msg=([^\]]+)\] \[details=(\{.*\})\]\s*$', line)
        require(m is not None and line.startswith('['+game_day+' '), 'native_event_or_day')
        msg, raw = m.groups(); d = decode(raw)
        if msg == 'Tasker.Task.Starting':
            require(active is None and d['task_id'] not in task_ids
                    and d.get('uuid') == uuid and bool(d.get('hash')), 'native_task_binding')
            task_ids.add(d['task_id']); active = d
        elif msg.startswith('Tasker.Task.'):
            require(msg in ('Tasker.Task.Succeeded','Tasker.Task.Failed')
                    and active == d and action is None, 'native_task_unpaired')
            active = None
        elif msg == 'Node.Action.Starting':
            require(active is not None and action is None and d['task_id'] == active['task_id']
                    and d['action_id'] not in action_ids, 'native_action_unpaired')
            action_ids.add(d['action_id']); action = d
        elif msg.startswith('Node.Action.'):
            require(msg == 'Node.Action.Succeeded' and action is not None
                    and all(action[k] == d[k] for k in ('task_id','action_id','name')), 'native_action_unknown')
            proof = d['action_details']
            require(proof.get('success') is True and proof.get('name') == action['name']
                    and proof.get('action_id') == action['action_id'], 'native_action_proof')
            require(proof['action'] in ('Click','DoNothing'), 'unexpected_native_input')
            if proof['action'] == 'Click':
                actual.append((d['task_id'], d['name'], proof['detail']))
            action = None
    require(active is None and action is None and task_ids, 'unfinished_native_log')
    expected = []
    for receipt in receipts:
        for node in receipt['nodes']:
            a = node.get('action')
            if a and a.get('raw'):
                require(receipt['native_ok'] is True and node['completed'] is True
                        and a['success'] is True and a['name'] == node['name'], 'report_input_unknown')
                expected.append((receipt['task_id'],node['name'],a['raw']))
    require(actual == expected, 'native_report_input_mismatch')
    return actual


def verify(root, manifest_path, digest):
    manifest, payloads, paths = load_bundle(root, manifest_path, digest)
    parents, children = lines(payloads['root']), lines(payloads['child'])
    # Deliberately do not reinterpret legacy abort closures or navigation history.
    # A real multi-incident prefix needs independent verification by its owner.
    require(len(parents) == len(children) == 1, 'historical_prefix_not_verified_by_completed_candidate')
    parent, child = parents[0], children[0]
    require(parent.get('state') == child.get('state') == 'in_flight'
            and parent.get('operation') == 'schedule_onekey' and child.get('operation') == 'onekey', 'pending_intent_required')
    for key in ('game_day','device','intent_id','session_id'):
        require(parent.get(key) is not None and parent[key] == child.get(key), 'ledger_binding')
    day, device = parent['game_day'], parent['device']
    require(set(device) == {'serial','input_display','screenshot_display'}, 'device_binding')
    origin = decode(payloads['original_report'])['result']
    context = dict(run_id=parent['intent_id'],session_id=parent['session_id'],
        invocation_id=parent['invocation_id'],game_day=day,device=device)
    require(origin.get('navigation_context') == context and origin.get('device') == device
            and origin.get('game_day') == day and origin.get('outcome') == 'unknown', 'original_run_binding')
    receipts = [r['native'] for r in origin['receipts']]
    clicks = native_clicks(payloads['original_log'],receipts,day,device)
    require(len(clicks) == 1 and clicks[0][1] == 'OneKeyInput_onekey'
            and clicks[0][2] == dict(point=POINTS['onekey'],contact=0,pressure=1), 'original_onekey_exactly_once')

    def bound_frame(value):
        p = path_in(Path(root).absolute(),value)
        require(p in paths.values(), 'unbound_screenshot')
        return sha(payloads[next(k for k,v in paths.items() if v == p)])

    for i, expected_ops in enumerate(SIDECAR_ACTIONS):
        report = decode(payloads[f'sidecar{i}_report'])
        intent = decode(payloads[f'sidecar{i}_intent'])
        require(report.get('intent') == intent and intent.get('origin_run') == parent['invocation_id']
                and intent.get('game_day') == day and intent.get('device') == device, 'sidecar_origin_device_day')
        ledger_pins = intent.get('ledger_before',{})
        require(ledger_pins == {manifest['files'][key]['path']:sha(payloads[key]) for key in ('root','child')}, 'sidecar_ledger_prefix')
        steps = report['steps']
        require([s['action'] for s in steps] == list(expected_ops)
                and report.get('completed_actions') == list(expected_ops), 'sidecar_action_sequence')
        require([s.get('action') for s in lines(payloads[f'sidecar{i}_dispatch'])] == list(expected_ops), 'sidecar_dispatch_sequence')
        actual = native_clicks(payloads[f'sidecar{i}_log'],[s['native'] for s in steps],day,device)
        require(len(actual) == len(expected_ops), 'sidecar_input_count')
        for op, step, (_,name,raw) in zip(expected_ops,steps,actual):
            require(name == 'ParentContinue_'+op and raw == dict(point=POINTS[op],contact=0,pressure=1), 'sidecar_specific_input')
            bound_frame(step['before']); bound_frame(step['after'])
    schedule, home = decode(payloads['schedule_report']), decode(payloads['home_report'])
    for report in (schedule, home):
        require(report.get('device') == device and report.get('game_day') == day, 'final_device_day_unverified')
    count = schedule['delegated']; evidence = count['evidence']
    heading, number = evidence['heading'], evidence['count']
    require(count['completed'] == count['total'] == 9 and heading['text'] == '交给可可萝'
            and number['text'] == '9/9' and heading['score'] >= .9 and number['score'] >= .9
            and abs(heading['box'][1]-number['box'][1]) <= 10
            and number['box'][0] > heading['box'][0]+heading['box'][2], 'same_column_nine_unverified')
    frame_sha = bound_frame(evidence['screenshot'])
    require(any(p.get('screenshot') == evidence['screenshot'] and p.get('image_sha256') == frame_sha
                for p in schedule['pages']), 'schedule_frame_binding')
    reading = home['reading']
    require(reading.get('state') == 'home' and bound_frame(reading['screenshot']) == reading['image_sha256'], 'home_unverified')
    # Only synthetic fixtures currently have the supported, fully explicit shape.
    # Never present a schema-normalized historical receipt as native completion.
    require(manifest['synthetic'] is True and device['serial'] == 'synthetic-offline', 'real_completion_requires_parent_review')
    for role,path in paths.items():
        require(path.read_bytes() == payloads[role], 'evidence_changed_during_read')
    return dict(outcome='candidate_verified',source='parent-assisted',synthetic=True,
        original_outcome=origin['outcome'],original_context=context,manifest_sha256=digest,
        root_prefix_sha256=sha(payloads['root']),child_prefix_sha256=sha(payloads['child']),
        actions=['onekey']+[op for ops in SIDECAR_ACTIONS for op in ops],
        execution_permitted=False,daily_complete=False)


def dry_run(root, manifest_path, digest):
    try:
        return verify(root,manifest_path,digest)
    except (ValueError,KeyError,TypeError,OSError,IndexError) as exc:
        return dict(outcome='rejected',reason=str(exc),source='parent-assisted',
                    execution_permitted=False,daily_complete=False)


def real_native(data, receipts, day, device):
    """Read v5.13 log bytes, pairing physical dispatch with local task/action.

    A log digest scopes task IDs. The native resource hash is not a Python
    session ID and is never inherited across independently launched processes.
    """
    from .onekey_reconcile import navigation_detail
    text = data.decode('utf-8')
    require(data.endswith(b'\n') and text.count('[Logger] MAA Process Start') >= 1
            and text.count('[Logger] Close log') == 1, 'native_log_envelope')
    require(text.rindex('MAA Process Start') < text.index('!!!OnEventNotify!!!')
            < text.rindex('!!!OnEventNotify!!!') < text.index('[Logger] Close log'), 'native_log_envelope')
    active = action = pid = size = session = None
    controllers = 0
    tasks, ids, actual, physical, failed, all_actions = {}, set(), [], [], [], []
    uuid = re.sub('[^a-zA-Z0-9-]', '', device['serial'])
    def expected_detail(name):
        if name.startswith('OneKeyInput_') and name != 'OneKeyInput_onekey':
            return navigation_detail(name)
        op = name.removeprefix('ParentContinue_')
        op = {'OneKeyInput_onekey':'onekey', 'ParentUserDungeonConfirmOnce':'dungeon_confirm'}.get(op, op)
        if name == 'ScheduleCloseClick':
            return dict(point=[389,645], contact=0, pressure=1)
        require(op in POINTS, 'unknown_native_input')
        return dict(point=POINTS[op], contact=0, pressure=1)
    for lineno, line in enumerate(text.splitlines(), 1):
        process = re.search(r'\[Px([^\]]+)\]', line)
        if process:
            pid = pid or process[1]
            require(process[1] == pid, 'multiple_native_processes')
        if '[MaaAdbControllerCreate]' in line and '| enter' in line:
            controllers += 1
            require('[address='+device['serial']+']' in line and '[input_methods=1]' in line
                    and '[screencap_methods=2]' in line, 'native_connection_binding')
        if 'Resolution changed [raw.cols=' in line:
            match = re.search(r'\[raw.cols=(\d+)\] \[raw.rows=(\d+)\]', line)
            require(match is not None, 'raw_resolution_missing')
            current = tuple(map(int, match.groups()))
            require(current[0]*720 == current[1]*1280 and (size is None or size == current), 'raw_resolution_changed')
            size = current
        if '[MaaNS::ChildPipeIOStream::ChildPipeIOStream]' in line:
            match = re.search(r'\[args_=(\[.*\])\] \[child_', line)
            require(match is not None, 'malformed_native_dispatch')
            args = decode(match[1])
            require(args[:2] == ['-s', device['serial']], 'dispatch_serial_mismatch')
            command = args[2:]
            if command not in (['get-serialno'], ['get-state'], ['shell','cat'],
                               ['exec-out','screencap -p -d '+device['screenshot_display']]):
                require(action is not None and not action['physical'] and size, 'unbound_physical_input')
                detail = expected_detail(action['start']['name'])
                def scaled(point):
                    return [int(point[0]*size[0]/1280+.5), int(point[1]*size[1]/720+.5)]
                words = (['tap', *scaled(detail['point'])] if 'point' in detail else
                         ['swipe', *scaled(detail['begin']), *scaled(detail['end'][0]), 1000])
                require(command == ['shell','input -d '+str(device['input_display'])+' '+' '.join(map(str,words))],
                        'dispatch_target_or_display_mismatch')
                action['physical'].append(lineno)
        if '!!!OnEventNotify!!!' not in line:
            require('[msg=' not in line, 'damaged_native_event')
            continue
        match = re.search(r'\[msg=([^\]]+)\] \[details=(\{.*\})\]\s*$', line)
        require(match is not None and line.startswith('['+day+' '), 'native_event_or_day')
        msg, raw = match.groups(); d = decode(raw)
        if msg == 'Tasker.Task.Starting':
            require(active is None and d['task_id'] not in tasks and d.get('uuid') == uuid
                    and bool(d.get('hash')), 'native_task_binding')
            session = session or d['hash']
            require(session == d['hash'], 'native_session_changed')
            active = d; tasks[d['task_id']] = {'start':lineno, 'inputs':[]}
        elif msg.startswith('Tasker.Task.'):
            require(msg in ('Tasker.Task.Succeeded','Tasker.Task.Failed') and active == d
                    and action is None, 'native_task_unpaired')
            task = tasks[d['task_id']]; task['end'] = lineno; task['status'] = msg.rsplit('.',1)[1]
            if msg.endswith('Failed'):
                require(not task['inputs'], 'failed_task_has_input')
                failed.append(d['task_id'])
            active = None
        elif msg == 'Node.Action.Starting':
            require(active is not None and action is None and d['task_id'] == active['task_id']
                    and d['action_id'] not in ids, 'native_action_unpaired')
            ids.add(d['action_id']); action = dict(start=d, physical=[])
        elif msg.startswith('Node.Action.'):
            require(msg == 'Node.Action.Succeeded' and action is not None
                    and all(action['start'][k] == d[k] for k in ('task_id','action_id','name')), 'native_action_unknown')
            proof = d['action_details']; kind = proof['action']
            require(proof.get('success') is True and proof.get('name') == d['name']
                    and proof.get('action_id') == d['action_id'] and kind in ('Click','Swipe','DoNothing'), 'native_action_proof')
            all_actions.append((d['task_id'],d['name'],proof['detail']))
            if kind == 'DoNothing':
                require(not action['physical'] and proof['detail'] == {}, 'hidden_physical_input')
            else:
                require(len(action['physical']) == 1 and proof['detail'] == expected_detail(d['name']), 'native_physical_mismatch')
                actual.append((d['task_id'],d['name'],proof['detail']))
                event = dict(task_id=d['task_id'], node=d['name'], action_id=d['action_id'],
                             physical_line=action['physical'][0], success_line=lineno, kind=kind)
                physical.append(event); tasks[d['task_id']]['inputs'].append(event)
            action = None
    require(active is None and action is None and tasks and controllers == 1 and size and pid, 'unfinished_native_log')
    expected, reported_actions = [], []
    require(len({r['task_id'] for r in receipts}) == len(receipts), 'duplicate_report_task')
    for receipt in receipts:
        task = tasks[receipt['task_id']]
        require(receipt['native_ok'] is (task['status'] == 'Succeeded'), 'native_report_status_mismatch')
        for node in receipt['nodes']:
            a = node.get('action')
            if a:
                require(node['completed'] is True and a['success'] is True and a['name'] == node['name'], 'report_action_unknown')
                reported_actions.append((receipt['task_id'],node['name'],a['raw']))
            if a and a.get('raw'):
                require(node['completed'] is True and a['success'] is True and a['name'] == node['name'], 'report_input_unknown')
                expected.append((receipt['task_id'],node['name'],a['raw']))
    require(actual == expected, 'native_report_input_mismatch')
    require(all_actions == reported_actions, 'native_report_action_mismatch')
    return dict(physical_inputs=len(physical), inputs=physical, failed_zero_input_tasks=failed,
                task_identity_scope='this log SHA + process + local task/node', process=pid,
                native_resource_hash=session, original_session_inherited=False)


def real_dry_run(root, manifest_path, digest, *, _evidence=None, _history_intents=None):
    """Bounded real-artifact diagnostics only; no locks, settlement or permission.

    Each independent check survives another check's refusal. Unpinned historical
    reads are allowed only through the existing anchored history verifier and
    are recorded separately; their new hashes are observations, not old attestations.
    """
    from types import SimpleNamespace
    from . import onekey_history
    from .navigation_abort import Evidence, verify_bundle
    root = Path(root).absolute()
    evidence = _evidence if _evidence is not None else Evidence(root)
    checks, payloads, paths = [], {}, {}
    def check(name, fn, sources=(), status='proven'):
        item = dict(check=name, sources=list(sources))
        before = set(evidence.files)
        try:
            value = fn(); item.update(status=status, detail=value)
        except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
            item.update(status='blocked', reason=str(exc)); value = None
        item['new_source_paths'] = [p.relative_to(root).as_posix() for p in evidence.files if p not in before]
        checks.append(item)
        return value
    def ensure(ok, reason, detail=None):
        require(ok, reason); return detail
    def manifest_read():
        data = evidence.read(manifest_path)
        require(sha(data) == digest, 'manifest_changed')
        value = decode(data)
        require(value.get('schema') == 1 and value.get('synthetic') is False, 'real_manifest_required')
        require(isinstance(value.get('files'), dict) and len(value['files']) <= 256, 'invalid_file_inventory')
        return value
    manifest = check('manifest', manifest_read, [str(manifest_path)])
    if manifest is not None:
        for role in sorted(set(REQUIRED) | set(manifest['files'])):
            def read_role(role=role):
                pin = manifest['files'][role]; p = path_in(root, pin['path'])
                require(p not in paths.values(), 'duplicate_evidence_path')
                data = evidence.ref(pin); paths[role] = p; payloads[role] = data
                return dict(path=p.relative_to(root).as_posix(), sha256=sha(data))
            check('input:'+role, read_role, [role])
    def obj(role): return decode(payloads[role])
    def frame(value, expected_sha=None):
        p = path_in(root, value)
        require(p in paths.values() and p.suffix == '.png', 'unbound_screenshot')
        h = sha(evidence.files[p])
        require(expected_sha is None or h == expected_sha, 'screenshot_sha_mismatch')
        return dict(path=p.relative_to(root).as_posix(), sha256=h)
    def ledger():
        parents, children = lines(payloads['root']), lines(payloads['child'])
        parent, child = parents[-1], children[-1]
        require(parent['state'] == child['state'] == 'in_flight' and parent['operation'] == 'schedule_onekey'
                and child['operation'] == 'onekey', 'pending_intent_required')
        require(all(parent[k] == child[k] for k in ('game_day','device','intent_id','session_id')), 'ledger_binding')
        return parents, children
    ledgers = check('ledger_binding', ledger, ['root','child'])
    if ledgers:
        parents, children = ledgers; parent = parents[-1]; day, device = parent['game_day'], parent['device']
        journal = SimpleNamespace(directory=paths['root'].parent, path=paths['root'])
        check('history', lambda: sorted(onekey_history.navigation_history(journal, payloads['root'], day,
              device, pending=True, evidence=evidence)), ['root'])
        # A separate prefix check cannot override the pending display refusal.
        check('legacy_prefix', lambda: onekey_history.verify_legacy_readonly(evidence, journal, parents,
              payloads['root'].splitlines(keepends=True), 6, parents[0]['device'],
              all_intents=_history_intents), ['root'])
        def navigation_prefix():
            end = parents[7]; binding = end['reconciliation']
            proof = verify_bundle(evidence,binding['manifest'],binding['manifest_sha256'],
                                  b''.join(payloads['root'].splitlines(keepends=True)[:8]))
            require(proof['closed'] and proof['parent'] == parents[6], 'navigation_prefix_unverified')
            return dict(closed=True, grants_execution_permission=False)
        check('navigation_abort_prefix', navigation_prefix, ['root'])
        def original():
            value = obj('original_report')['result']
            context = dict(run_id=parent['intent_id'],session_id=parent['session_id'],
                           invocation_id=parent['invocation_id'],game_day=day,device=device)
            require(value['navigation_context'] == context and value['device'] == device and value['game_day'] == day
                    and value['authorizations'] == parent['authorizations'] and value['outcome'] == 'unknown', 'original_run_binding')
            return value
        origin = check('original_binding', original, ['root','child','original_report'])
        if origin:
            native = check('original_native', lambda: real_native(payloads['original_log'],
                           [r['native'] for r in origin['receipts']],day,device), ['original_report','original_log'])
            if native:
                check('original_seven_inputs', lambda: ensure(origin['sent'] == ['enter']+['down:'+str(i) for i in range(5)]+['onekey']
                      and [v['node'] for v in native['inputs']] == ['OneKeyInput_enter']+['OneKeyInput_down']*5+['OneKeyInput_onekey'],
                      'original_input_sequence'), ['original_report','original_log'])
            def child_prefix():
                require(len(children) == 13, 'child_navigation_length')
                for i, op in enumerate(origin['sent']):
                    a = children[2*i]
                    require(a['state'] == 'in_flight' and a['operation'] == op
                            and all(a[k] == parent[k] for k in ('device','game_day','intent_id','session_id')), 'child_navigation_binding')
                    require(a['image_sha256'] == origin['receipts'][2*i]['reading']['image_sha256'], 'child_before_frame')
                    if i < 6:
                        b = children[2*i+1]
                        require(b['state'] == 'settled' and b['operation'] == op
                                and all(b[k] == a[k] for k in ('device','game_day','intent_id','session_id'))
                                and b['image_sha256'] == origin['receipts'][2*i+2]['reading']['image_sha256'], 'child_after_frame')
                return [frame(r['reading']['screenshot'],r['reading']['image_sha256']) for r in origin['receipts']]
            check('child_navigation_prefix', child_prefix, ['child','original_report'])
        actions = []
        for i, ops in enumerate(SIDECAR_ACTIONS):
            prefix = f'sidecar{i}_'; sources = [prefix+k for k in ('report','intent','dispatch','log')]
            def sidecar(i=i, prefix=prefix):
                report, intent = obj(prefix+'report'), obj(prefix+'intent')
                require(report['intent'] == intent and intent['origin_run'] == parent['invocation_id'], 'sidecar_origin')
                require(intent['device'] == {k:device[k] for k in ('input_display','screenshot_display')}, 'sidecar_display')
                require({path_in(root,p):h for p,h in intent['ledger_before'].items()} ==
                        {paths[k]:sha(payloads[k]) for k in ('root','child')}, 'sidecar_ledger_prefix')
                require('origin_action_intent' not in intent or intent['origin_action_intent'] == parent['intent_id'], 'sidecar_intent')
                return report
            report = check(prefix+'binding', sidecar, sources)
            if report is None: continue
            steps = report.get('steps', [dict(action='dungeon_confirm',native=report.get('native'),
                        input_completed=report.get('input_completed'),after=report.get('after_screenshot'))])
            native = check(prefix+'native', lambda: real_native(payloads[prefix+'log'],
                           [s['native'] for s in steps],day,device), sources)
            def step_check():
                require(native is not None, 'native_proof_missing')
                dispatch = (lines(payloads[prefix+'dispatch']) if i else [obj(prefix+'dispatch')])
                require([d.get('action',d.get('operation')) for d in dispatch] == [s['action'] for s in steps], 'dispatch_sequence')
                completed = [s['action'] for s in steps if s['input_completed'] is True]
                require(completed == list(ops) and (i == 0 or report['completed_actions'] == completed), 'sidecar_actions')
                for s in steps:
                    hits = [v for v in native['inputs'] if v['task_id'] == s['native']['task_id']]
                    name = 'ParentUserDungeonConfirmOnce' if i == 0 else 'ParentContinue_'+s['action']
                    require([v['node'] for v in hits] == ([name] if s['input_completed'] else []), 'sidecar_step_input')
                    if not s['input_completed']:
                        require(s['native']['task_id'] in native['failed_zero_input_tasks'], 'unproven_zero_input')
                    if 'before' in s: frame(s['before'])
                    frame(s['after'])
                actions.extend(completed)
                return completed
            check(prefix+'steps', step_check, sources)
            intent = report['intent']
            if i:
                check(prefix+'previous', lambda: ensure(intent.get('previous_sidecar') ==
                      paths[f'sidecar{i-1}_report'].relative_to(paths[prefix+'report'].parents[1]).as_posix(),
                      'previous_sidecar_skips_preceding_report',dict(declared=intent.get('previous_sidecar'))), sources)
            check(prefix+'session', lambda: ensure(False, 'no_original_session_inheritance_proof'), sources)
            check(prefix+'device_day', lambda: ensure(native is not None, 'native_binding_unverified',
                  dict(basis='origin_run + exact ledger SHA/day + native connection/display + dated local log; not original fields',
                       missing_fields=[k for k in ('session_id','game_day') if k not in intent]+['device.serial'])), sources, 'cross-proven')
            if 'quote_validation' in intent or 'observation' in intent:
                def assistance():
                    claim = intent.get('quote_validation', intent.get('observation'))
                    return dict(declaration=claim, source_frame=frame(paths[prefix+'report'].parents[1]/claim['source']),
                                provenance='parent-assisted declaration, not automatic quote acceptance')
                check(prefix+'parent_assistance', assistance, [prefix+'intent'], 'source-fact')
        check('sidecar_actions_once', lambda: ensure(actions == [op for ops in SIDECAR_ACTIONS for op in ops],
              'missing_or_duplicate_sidecar_actions', actions), ['sidecar'+str(i)+'_report' for i in range(5)])
        def nine():
            report = obj('schedule_report'); page = report['pages'][1]; count = page['sections']['delegated']
            heading, number = count['heading'], count['count']
            require(report['device'] == device and count['completed'] == count['total'] == 9
                    and heading['text'] == '交给可可萝' and number['text'] == '9/9'
                    and heading['score'] >= .9 and number['score'] >= .9
                    and abs(heading['box'][1]-number['box'][1]) <= 10
                    and number['box'][0] > heading['box'][0]+heading['box'][2], 'same_column_nine_unverified')
            require(report['delegated']['evidence'] == dict(count,screenshot=page['screenshot']), 'final_alias_mismatch')
            return frame(page['screenshot'],page['image_sha256'])
        check('final_nine', nine, ['schedule_report','schedule_frame'])
        def home():
            report = obj('home_report')['native']
            require(report['native_ok'] is True and [n['name'] for n in report['nodes'] if n['completed']] ==
                    ['CloseSchedule','ScheduleCloseClick','ScheduleHomeGate','ObserveHome'], 'home_gate_unverified')
            return real_native(payloads['home_log'],[report],day,device)
        check('home_native', home, ['home_report','home_log'])
        check('home_frame', lambda: frame(paths['home_frame']), ['home_frame'], 'source-fact')
        check('home_frame_native_binding', lambda: ensure(False,'home_report_has_no_frame_digest; script/artifact association requires parent review'),
              ['home_report','home_frame'])
        check('game_day_continuity', lambda: ensure(False,'dated_logs_and_origin_day_do_not_prove_no_game_refresh_across_processes'), ['root','schedule_report'])
    if manifest and ('report_alias' in manifest['files'] or 'report_alias2' in manifest['files']):
        check('report_aliases', lambda: ensure(payloads['report_alias'] == payloads['report_alias2'],
              'report_alias_mismatch',dict(independent_runs=1)), ['report_alias','report_alias2'])
    check('recheck', evidence.recheck)
    pins = {p.relative_to(root).as_posix():sha(data) for p,data in evidence.files.items()}
    blocked = [c for c in checks if c['status'] == 'blocked']
    return dict(outcome='diagnostic_blocked' if blocked else 'diagnostic_only', synthetic=False,
                execution_permitted=False,daily_complete=False,checks=checks,blockers=blocked,
                sources_sha256=pins,manifest_sha256=digest,
                source_roles={k:v.relative_to(root).as_posix() for k,v in paths.items()},
                source='real originals; source facts and cross-proven inference are distinct; no settlement')


def synthetic_root(root):
    from .native import ROOT
    base = ROOT/'.runtime/firstpass-closeout-20260926/schedule'
    root = path_in(base,Path(root).absolute())
    require(root.name == 'synthetic', 'isolated_synthetic_directory_required')
    return root


def apply_synthetic(root, manifest_path, digest):
    """One immutable audit append in a lane fixture, never root/child settlement."""
    root = synthetic_root(root)
    manifest,_,_ = load_bundle(root,manifest_path,digest)
    require(manifest['synthetic'] is True, 'synthetic_only')
    report = verify(root,manifest_path,digest)
    with (root/'completed-candidate.jsonl').open('xb') as handle:
        handle.write((json.dumps(report,ensure_ascii=False,sort_keys=True)+'\n').encode())
    return report


def read_synthetic_history(root, manifest_path, digest):
    root = synthetic_root(root)
    report = verify(root,manifest_path,digest)
    history = lines(path_in(root,root/'completed-candidate.jsonl').read_bytes())
    require(history == [report], 'candidate_history_changed')
    return report


PARENT_BASE = '.runtime/firstpass-closeout-20260926/parent-assisted/'
PARENT_APPROVAL_SHA = '3dd30b815efc9c21178935dd16155e7f3b508e4b50d2a32235adad9637cf5370'
PARENT_ASSOCIATION_SHA = '5443c62577fcc7138a0618b64f8e1b5e453a11c42b0b9aa651a32825bc2f7803'
PARENT_MANIFEST = '.runtime/firstpass-closeout-20260926/real-reconcile/real-manifest.json'
PARENT_DISPOSITION = 'verified_parent_assisted_completion_20260926'
PARENT_INTENT = '63477facb23143d7885888e0252ce955'


def _journal_line(event):
    # Exact Journal.append encoding; the planned bytes are also checked on readback.
    return (json.dumps(event, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')


def _parent_assisted_proof(root, **args):
    from .onekey_history import _EVIDENCE_RELOCATION
    root = Path(root).absolute()
    path_in(root, root)
    # Immutable archive paths are rooted here; remap references, not source bytes.
    original = Path(__file__).resolve().parents[2]
    token = _EVIDENCE_RELOCATION.set((original, root))
    try:
        return _parent_assisted_proof_in_view(root, **args)
    finally:
        _EVIDENCE_RELOCATION.reset(token)


def _parent_assisted_proof_in_view(root, *, intent_id, game_day, device,
                                   _history_evidence=None, _history_intents=None):
    """Read only. The fixed review authorizes ten limitations, never new blockers.

    Existing validators see only the SHA-pinned pre-closure ledger prefixes.
    Actual full ledger bytes are separately retained/rechecked, and may contain
    only the exact planned terminal line. No file is rewritten for validation.
    """
    from .navigation_abort import Evidence
    from .schedule_onekey import ITEMS
    root = Path(root).absolute()
    # Only the history reader supplies this read-only prefix view/inventory.
    # Public dry-run/apply always inspect the full, fixed-size ledgers.
    evidence = _history_evidence if _history_evidence is not None else Evidence(root)
    association_path = PARENT_BASE + 'PARENT_ASSOCIATION.json'
    association = decode(evidence.ref(dict(path=association_path, sha256=PARENT_ASSOCIATION_SHA)))
    evidence.ref(dict(path=PARENT_BASE+'APPROVAL.md', sha256=PARENT_APPROVAL_SHA))
    scope = association['scope']
    require(intent_id == scope['intent_id'] == PARENT_INTENT
            and game_day == scope['game_day'] == '2026-09-26'
            and device == scope['device'], 'parent_assisted_scope_mismatch')
    roles = association['source_roles']
    ledger_paths = {role: path_in(root, roles[role]) for role in ('root', 'child')}
    raw = {role: evidence.read(p) for role, p in ledger_paths.items()}
    lengths = dict(root=9, child=13)
    prefixes = {}
    for role, data in raw.items():
        require(data.endswith(b'\n'), 'incomplete_parent_assisted_ledger')
        chunks = data.splitlines(keepends=True)
        require(len(chunks) in (lengths[role], lengths[role]+1), 'extra_parent_assisted_history')
        prefix = b''.join(chunks[:lengths[role]])
        require(sha(prefix) == association[role+'_before_sha256'], 'parent_assisted_prefix_changed')
        prefixes[ledger_paths[role]] = prefix

    class PrefixEvidence(Evidence):
        def read(self, path):
            p = path_in(self.root, path)
            actual = evidence.read(p)
            data = prefixes.get(p, actual)
            require(p not in self.files or self.files[p] == data, 'evidence_changed')
            self.files[p] = data
            return data

        def recheck(self):
            evidence.recheck()

    view = PrefixEvidence(root)
    # These are immutable pins from the fixed parent review, not caller hashes.
    for group in ('sources_sha256', 'additional_sources_sha256'):
        for path, digest in association[group].items():
            view.ref(dict(path=path, sha256=digest))
    fixed_paths = set(view.files)
    manifest_sha = association['sources_sha256'][PARENT_MANIFEST]
    diagnostic = real_dry_run(root, PARENT_MANIFEST, manifest_sha, _evidence=view,
                              _history_intents=_history_intents)
    require(diagnostic['synthetic'] is False and diagnostic['source_roles'] == roles,
            'parent_assisted_diagnostic_binding')
    blockers = [dict(check=c['check'], reason=c['reason']) for c in diagnostic['blockers']]
    require(blockers == association['reviewed_blockers'] and len(blockers) == 10,
            'unreviewed_parent_assisted_blocker')
    reviewed = decode(view.ref({k:association['diagnostic'][k] for k in ('path','sha256')}))
    require([(c['check'], c['status']) for c in diagnostic['checks']] ==
            [(c['check'], c['status']) for c in reviewed['checks']], 'diagnostic_checks_changed')
    require(set(reviewed['sources_sha256']) == set(association['sources_sha256']),
            'diagnostic_source_inventory_changed')
    for p, h in reviewed['sources_sha256'].items():
        require(association['sources_sha256'][p] == h, 'diagnostic_source_pin_changed')
    require(set(view.files) == fixed_paths, 'unreviewed_parent_assisted_source')
    parent = lines(prefixes[ledger_paths['root']])[-1]
    require(all(parent[k] == v for k, v in scope.items()), 'parent_assisted_original_binding')
    ids = {e['intent_id'] for e in lines(prefixes[ledger_paths['root']])[::2]}
    if _history_intents is not None:
        require(ids <= _history_intents, 'invalid_parent_assisted_inventory')
        ids = _history_intents
    actions = path_in(root, ledger_paths['root'].parent/'actions')
    require({p.name for p in actions.iterdir()} == ids, 'orphan_action_history_unverified')
    binding = dict(approval=PARENT_BASE+'APPROVAL.md', approval_sha256=PARENT_APPROVAL_SHA,
                   association=association_path, association_sha256=PARENT_ASSOCIATION_SHA,
                   root_before_sha256=association['root_before_sha256'],
                   child_before_sha256=association['child_before_sha256'])
    common = dict(state='settled', **scope, disposition=PARENT_DISPOSITION,
                  outcome='parent_assisted_completed', reconciliation=binding,
                  completion_source='parent-assisted historical adjudication',
                  original_session_inherited=False, automatic_settlement_verified=False,
                  grants_execution_permission=False, daily_complete=False)
    child = dict(common, operation='onekey')
    parent_end = dict(common, operation='schedule_onekey', rows=dict.fromkeys(ITEMS, 'complete'),
                      child_terminal_sha256=sha(_journal_line(child)))
    plan = dict(child=child, root=parent_end)
    closed = {}
    for role in ('child', 'root'):
        prefix = prefixes[ledger_paths[role]]
        suffix = raw[role][len(prefix):]
        require(suffix in (b'', _journal_line(plan[role])), 'parent_assisted_terminal_changed')
        closed[role] = bool(suffix)
    require(closed['root'] == closed['child'], 'partial_parent_assisted_closure_no_resend')
    view.recheck()
    require({p.name for p in actions.iterdir()} == ids, 'orphan_action_history_unverified')
    report = dict(outcome='already_parent_assisted_completed' if closed['root'] else 'ready_parent_assisted',
                  intent_id=intent_id, game_day=game_day, device=device, append_order=['child', 'root'],
                  append_plan=plan, execution_permitted=False, daily_complete=False,
                  approval_sha256=PARENT_APPROVAL_SHA, association_sha256=PARENT_ASSOCIATION_SHA,
                  reviewed_blockers=blockers,
                  sources_sha256={p.relative_to(root).as_posix():sha(b) for p,b in evidence.files.items()})
    return report, evidence, ledger_paths, raw


def parent_assisted_dry_run(root, *, intent_id, game_day, device):
    """No locks, writes, Tasker or device access; exceptions mean refusal."""
    return _parent_assisted_proof(root, intent_id=intent_id, game_day=game_day, device=device)[0]


def apply_parent_assisted(root, *, intent_id, game_day, device):
    """One authorized child-then-root append. Partial writes require parent review.

    This function is used by Hermes on the original only after independent review;
    Worker calls it solely on a complete isolated copy. No automatic repair/retry.
    """
    import os
    from .preset_execution import Journal
    root = Path(root).absolute()
    args = dict(intent_id=intent_id, game_day=game_day, device=device)
    report, evidence, paths, raw = _parent_assisted_proof(root, **args)
    if report['outcome'] == 'already_parent_assisted_completed':
        return report
    backup = root / (PARENT_BASE+'worker/ledger-backup')
    # Validate each existing ancestor before mkdir/open, including a preexisting lock.
    current = root
    for part in backup.relative_to(root).parts:
        current = current / part
        if current.exists():
            path_in(root, current)
        else:
            current.mkdir()
    backup_data = {role+'-before.jsonl':raw[role] for role in ('root','child')}
    backup_data['metadata.json'] = _journal_line(report)
    for name, data in backup_data.items():
        p = backup / name
        if p.exists():
            require(path_in(root, p).read_bytes() == data, 'parent_assisted_backup_changed')
        else:
            with p.open('xb') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
    journals = {role:Journal(p.parent) for role,p in paths.items()}
    for journal in journals.values():
        path_in(root, journal.directory)
        lock = journal.directory/'transaction.lock'
        if lock.exists(): path_in(root, lock)
    with journals['root'].locked(), journals['child'].locked():
        evidence.recheck()
        locked_report, locked_evidence, _, _ = _parent_assisted_proof(root, **args)
        require(locked_report == report, 'parent_assisted_plan_changed_under_lock')
        locked_evidence.recheck()
        journals['child'].append(report['append_plan']['child'])
        require(paths['child'].read_bytes() == raw['child'] + _journal_line(report['append_plan']['child']),
                'parent_assisted_child_readback_failed')
        # Recheck every original source between writes, expecting only our child append.
        locked_evidence.files[paths['child']] = paths['child'].read_bytes()
        locked_evidence.recheck()
        journals['root'].append(report['append_plan']['root'])
        final, _, _, _ = _parent_assisted_proof(root, **args)
        require(final['outcome'] == 'already_parent_assisted_completed', 'parent_assisted_readback_failed')
    return dict(final, outcome='parent_assisted_completed')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fixed 2026-09-26 parent-assisted closure; no game input')
    parser.add_argument('mode', choices=('dry-run', 'apply'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--intent-id', required=True)
    parser.add_argument('--game-day', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--input-display', type=int, required=True)
    parser.add_argument('--screenshot-display', required=True)
    args = parser.parse_args()
    function = apply_parent_assisted if args.mode == 'apply' else parent_assisted_dry_run
    try:
        result = function(args.root, intent_id=args.intent_id, game_day=args.game_day,
                          device=dict(serial=args.serial, input_display=args.input_display,
                                      screenshot_display=args.screenshot_display))
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps(dict(outcome='refused', reason=str(exc), execution_permitted=False)))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
