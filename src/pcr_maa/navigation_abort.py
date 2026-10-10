"""Append-only, parent-pinned navigation abort proof; never execution permission."""
import argparse
import json
from pathlib import Path
import re

from .onekey_history import path_in, require
from .onekey_reconcile import read, decode, sha, same_detail, navigation_detail, NAVIGATION
from .preset_execution import Journal

DISPOSITION = 'verified_navigation_abort_v1'
LEDGER = Path('.runtime/schedule-onekey-work/ledger')


def verify_native(log, receipts, game_day, native_uuid):
    """Complete sequential task/action proof, not a search for absent Clicks."""
    # v5.13 text OnEventNotify stream, with process boundaries, not filtered events.
    text = log.decode('utf-8')
    require(log.endswith(b'\n') and text.count('[Logger] MAA Process Start') >= 1
            and text.count('[Logger] Close log') == 1, 'incomplete_log_envelope')
    require(text.rindex('MAA Process Start') < text.index('!!!OnEventNotify!!!')
            < text.rindex('!!!OnEventNotify!!!') < text.index('[Logger] Close log'),
            'incomplete_log_envelope')
    tasks=[]; active=None; action=None; action_ids=set(); reco_ids=set(); task_ids=set()
    session=None; guards={}; auxiliaries={}; node_ids=set(); resources={}
    action_proofs={}; pipeline_actions=set()
    recognition_nodes={}; inner_recos={}; inner_tasks=set(); inner_count=0
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
        elif msg=='Tasker.Task.Starting':
            tid=d.get('task_id')
            require(active is None and not auxiliaries and not recognition_nodes
                    and all(r.get('hash') for r in resources.values())
                    and type(tid) is int and tid not in task_ids and tid not in inner_tasks, 'duplicate_or_overlapping_task')
            require(d.get('uuid')==native_uuid and isinstance(d.get('hash'),str) and d['hash'], 'native_device_binding')
            if session is None: session=d['hash']
            require(d['hash']==session, 'native_session_changed')
            task_ids.add(tid); active=dict(start=d,actions=[],guards={},proof_ids=set());guards=active['guards']
        elif msg=='Tasker.Task.Succeeded':
            require(active is not None and d==active['start'] and action is None and not auxiliaries
                    and not recognition_nodes and not inner_recos
                    and all(v[1] in ('Succeeded','Failed') for v in guards.values()), 'unfinished_native_task')
            tasks.append(active);active=None
        elif msg.startswith('Node.Recognition.'):
            require(active is not None and action is None,'recognition_outside_task')
            rid=d.get('reco_id');name=d.get('name');phase=msg.rsplit('.',1)[1]
            require(type(rid) is int and isinstance(name,str), 'invalid_recognition_identity')
            recognition_only(d)
            if name.startswith('OneKeyInput_'):
                require(name[12:] in NAVIGATION, 'consumer_or_unknown_guard')
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
        elif msg=='Node.Action.Starting':
            require(active is not None and action is None and d.get('task_id')==active['start']['task_id'], 'action_outside_task')
            aid=d.get('action_id');name=d.get('name')
            require(type(aid) is int and aid not in action_ids, 'duplicate_action_id')
            action_ids.add(aid)
            require(name in ('OneKeyExecRead','OneKeyExecSnapshot','OneKeyStop')
                    or name in {'OneKey'+part+op for part in ('Do_','Input_') for op in NAVIGATION},
                    'consumer_or_unknown_action_started')
            if name=='OneKeyStop':
                require(any(n.startswith('OneKeyInput_') and p=='Failed' for n,p in guards.values()) and all(v[1]!='Starting' for v in guards.values()), 'stop_without_complete_failed_guard')
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
    require(active is None and action is None and not recognition_nodes and not inner_recos
            and tasks and resources and len(tasks)==len(receipts)
            and all(r.get('hash') for r in resources.values()), 'incomplete_native_task_coverage')
    states = []
    for task, receipt in zip(tasks, receipts):
        native, entry = receipt['native'], receipt['entry']
        require(type(native.get('task_id')) is int and task['start']['entry'] == entry
                and task['start']['task_id'] == native['task_id']
                and native.get('native_ok') is True, 'task_receipt_binding')
        actual = [name for name, _ in task['actions']]
        if entry == 'OneKeyExecRead':
            names = ['OneKeyExecRead', 'OneKeyExecSnapshot']
        else:
            require(entry.startswith('OneKeyDo_') and entry[9:] in NAVIGATION,
                    'consumer_or_unknown_task')
            target = entry.replace('Do_', 'Input_')
            sent = target in actual
            names = [entry, target] if sent else [entry, '', 'OneKeyStop']
            states.append('completed' if sent else 'not_started')
            guards = [(n,p) for n,p in task['guards'].values() if n.startswith('OneKeyInput_')]
            require(guards and all(n == target and p == ('Succeeded' if sent else 'Failed')
                    for n,p in guards) and (not sent or len(guards) == 1), 'guard_boundary_unproven')
        require([n.get('name') for n in native['nodes']] == names
                and actual == [n for n in names if n], 'receipt_action_order')
        for node in native['nodes']:
            if not node['name']:
                require(node.get('completed') is False and node.get('action') is None,
                        'failed_guard_receipt_invalid')
            else:
                require(node.get('completed') is True and same_detail(node.get('action'),
                    dict(name=node['name'], success=True, raw=navigation_detail(node['name']))),
                    'receipt_action_mismatch')
    return states


def events(data):
    lines = data.splitlines(keepends=True)
    require(lines and all(line.endswith(b'\n') for line in lines), 'torn_or_empty_journal')
    result = [decode(line) for line in lines]
    require(all(isinstance(e, dict) and (e.get('state') ==
                ('in_flight' if i % 2 == 0 else 'settled')
                or i % 2 and e.get('state') == 'historical_unknown'
                and e.get('operation') == 'schedule_onekey') for i,e in enumerate(result)),
            'invalid_journal_sequence')
    return result


def verify_dispatches(log, device):
    """v5.13 ADB command backend only; every process launch is accounted for.

    Other backends are unsupported, not treated as zero input. Coordinates use
    Maa's positive half-up scaling from normalized 1280x720 to logged raw size.
    """
    active, launched, size, pid = None, False, None, None
    controllers = 0
    inputs = 0
    for line in log.decode('utf-8').splitlines():
        if not line:
            continue
        process = re.search(r'\[Px([^\]]+)\]', line)
        if process:
            pid = pid or process[1]
            require(process[1] == pid, 'multiple_native_processes')
        if '[MaaAdbControllerCreate]' in line and '| enter' in line:
            controllers += 1
            require('[address='+device['serial']+']' in line and '[input_methods=1]' in line
                    and '[screencap_methods=2]' in line, 'unsupported_native_backend')
        if 'Resolution changed [raw.cols=' in line:
            match = re.search(r'\[raw.cols=(\d+)\] \[raw.rows=(\d+)\]', line)
            require(match is not None, 'raw_resolution_missing')
            current = tuple(map(int, match.groups()))
            require(current[0]*720 == current[1]*1280 and (size is None or size == current),
                    'raw_resolution_changed_or_unsupported')
            size = current
        if '!!!OnEventNotify!!!' in line:
            match = re.search(r'\[msg=([^\]]+)\] \[details=(\{.*\})\]\s*$', line)
            msg, body = match.groups()
            d = decode(body)
            if msg == 'Node.Action.Starting':
                active, launched = d['name'], False
            elif msg == 'Node.Action.Succeeded':
                require(not active.startswith('OneKeyInput_') or launched, 'missing_input_dispatch')
                active = None
        if '[MaaNS::ChildPipeIOStream::ChildPipeIOStream]' not in line:
            continue
        match = re.search(r'\[args_=(\[.*\])\] \[child_', line)
        require(match is not None, 'malformed_native_dispatch')
        args = decode(match[1])
        require(args[:2] == ['-s', device['serial']], 'dispatch_serial_mismatch')
        command = args[2:]
        if command in (['get-serialno'], ['get-state'], ['shell','cat']):
            continue
        if command == ['exec-out', 'screencap -p -d '+device['screenshot_display']]:
            continue
        require(active is not None and active.startswith('OneKeyInput_') and not launched and size,
                'unknown_or_unbound_dispatch')
        detail = navigation_detail(active)
        def scaled(point):
            return [int(point[0]*size[0]/1280+.5), int(point[1]*size[1]/720+.5)]
        if 'point' in detail:
            words = ['tap', *scaled(detail['point'])]
        else:
            words = ['swipe', *scaled(detail['begin']), *scaled(detail['end'][0]), 1000]
        expected = 'input -d '+str(device['input_display'])+' '+' '.join(map(str, words))
        require(command == ['shell', expected], 'dispatch_target_or_display_mismatch')
        inputs += 1
        launched = True
    require(size is not None and pid is not None and controllers == 1, 'native_backend_unverified')
    return inputs


class Evidence:
    """Read-only byte snapshot; recheck all inspected files before returning/appending."""
    def __init__(self, root):
        require('..' not in root.parts and root.resolve() == root, 'linked_or_noncanonical_project')
        path_in(root, root)
        self.root, self.files = root, {}

    def read(self, path):
        path = path_in(self.root, path)
        data = read(self.root, path)
        require(path not in self.files or self.files[path] == data, 'evidence_changed')
        self.files[path] = data
        return data

    def ref(self, ref):
        require(isinstance(ref, dict) and set(ref) == {'path','sha256'}, 'invalid_file_reference')
        data = self.read(ref['path'])
        require(sha(data) == ref['sha256'], 'evidence_sha_mismatch')
        return data

    def recheck(self):
        for path, data in self.files.items():
            require(read(self.root, path) == data, 'evidence_changed')


def terminal(parent, operation, manifest_path, pin):
    return dict(state='settled', operation=operation, intent_id=parent['intent_id'],
        game_day=parent['game_day'], device=parent['device'], disposition=DISPOSITION,
        outcome='aborted_before_consumer', settlement_verified=False, consumer_input_sent=False,
        grants_execution_permission=False,
        reconciliation=dict(manifest=manifest_path, manifest_sha256=pin))


def verify_bundle(evidence, manifest_path, pin, root_data):
    root = evidence.root
    manifest_path = str(path_in(root, manifest_path).relative_to(root))
    bundle_bytes = evidence.read(manifest_path)
    require(sha(bundle_bytes) == pin, 'manifest_sha_mismatch')
    bundle = decode(bundle_bytes)
    require(bundle.get('version') == 1 and bundle.get('kind') == 'navigation_abort', 'manifest_format')
    approval = bundle['approval']
    require(approval.get('authority') == 'parent' and approval.get('approved') is True
            and approval.get('scope') == 'append_navigation_abort_only', 'parent_approval_required')
    require(bool(evidence.ref(approval['evidence'])), 'empty_parent_approval')
    binding = bundle['binding']
    intent = binding['intent_id']
    require(isinstance(intent, str) and re.fullmatch('[0-9a-f]{32}', intent), 'invalid_intent')
    action_path = root/LEDGER/'actions'/intent/'transactions.jsonl'
    action_data = evidence.read(action_path)
    prefixes = []
    for key, data in (('root_prefix', root_data), ('action_prefix', action_data)):
        spec = bundle[key]
        size = spec['size']
        require(type(size) is int and 0 < size <= len(data)
                and sha(data[:size]) == spec['sha256'], 'prefix_sha_mismatch')
        prefix = events(data[:size])
        require(len(prefix) % 2 == 1, 'prefix_not_in_flight')
        prefixes.append(prefix)
    roots, actions = prefixes
    parent = roots[-1]
    require(parent.get('operation') == 'schedule_onekey' and parent.get('intent_id') == intent
            and parent.get('device') == binding['device'] and parent.get('game_day') == binding['game_day'],
            'intent_device_day_binding')
    device = binding['device']
    require(isinstance(device, dict) and set(device) == {'serial','input_display','screenshot_display'}
            and isinstance(device['serial'], str) and device['serial']
            and type(device['input_display']) is int and device['input_display'] >= 0
            and isinstance(device['screenshot_display'], str) and device['screenshot_display'],
            'invalid_device_binding')
    # Maa's legacy ADB UUID removes punctuation from the serial. Physical display
    # is bound by the pinned parent report, never inferred from this lossy UUID.
    require(binding['native_uuid'] == re.sub('[^a-zA-Z0-9]', '', device['serial']), 'native_uuid_binding')
    report_path = path_in(root, bundle['report']['path'])
    log_path = path_in(root, bundle['native_log']['path'])
    require(report_path.parent.name == binding['run_id'] and log_path.is_relative_to(report_path.parent),
            'run_binding')
    report = decode(evidence.ref(bundle['report']))
    require(report.get('device') == device and report.get('game_day') == binding['game_day']
            and report.get('authorizations') == parent.get('authorizations')
            and report.get('outcome') == 'unknown'
            and all(report.get(k) is False for k in ('one_key_attempted','schedule_executed','daily_complete'))
            and all(k not in report or report[k] is False for k in
                    ('consumer_input_sent','consumption_sent','settlement_verified')),
            'report_binding_or_consumer')
    if 'navigation_context' in report:
        context = report['navigation_context']
        require(context.get('run_id') == intent and context.get('device') == device
                and context.get('game_day') == binding['game_day']
                and context.get('session_id') == binding['session_id'], 'report_context_binding')
    receipts = report['receipts']
    require(isinstance(receipts, list) and receipts and receipts[0]['entry'] == 'OneKeyExecRead',
            'missing_initial_read')
    log = evidence.ref(bundle['native_log'])
    states = verify_native(log, receipts, binding['game_day'], binding['native_uuid'])
    require(verify_dispatches(log, device) == states.count('completed'), 'dispatch_coverage_mismatch')
    frames = {}
    for ref in bundle['frames']:
        p = path_in(root, ref['path'])
        require(p not in frames, 'duplicate_frame')
        frames[p] = sha(evidence.ref(ref))
    used, capture = set(), 0
    for receipt in receipts:
        reading = receipt['reading']
        p = path_in(root, reading['screenshot'])
        require(p.is_relative_to(report_path.parent) and p in frames
                and frames[p] == reading.get('image_sha256')
                and reading.get('session_id') == binding['session_id']
                and reading.get('acquisition') == 'current_tasker_capture'
                and type(reading.get('capture_index')) is int and reading['capture_index'] > capture
                and str(reading.get('captured_at')).startswith(binding['game_day']+'T'), 'frame_binding')
        capture = reading['capture_index']
        used.add(p)
    require(used == set(frames), 'frame_inventory_mismatch')
    def check_frame_refs(value):
        if isinstance(value, dict):
            if 'screenshot' in value:
                p = path_in(root, value['screenshot'])
                require(p in frames and frames[p] == value.get('image_sha256'), 'nested_frame_unverified')
            for child in value.values():
                check_frame_refs(child)
        elif isinstance(value, list):
            for child in value:
                check_frame_refs(child)
    check_frame_refs(report)
    for line in log.decode('utf-8').splitlines():
        if '!!!OnEventNotify!!!' in line:
            match = re.search(r'\[details=(\{.*\})\]\s*$', line)
            check_frame_refs(decode(match[1]))
    ops = [e['operation'] for e in actions[::2]]
    require(report.get('sent') == ops and len(ops) == len(states) and len(set(ops)) == len(ops),
            'action_receipt_coverage')
    cursor = 1
    for i, op in enumerate(ops):
        base = op.split(':')[0]
        require(base in NAVIGATION and re.fullmatch(r'[a-z_]+(?::[0-9]+)?', op), 'non_navigation_intent')
        before = receipts[cursor-1]['reading']
        require(cursor < len(receipts) and receipts[cursor]['entry'] == 'OneKeyDo_'+base
                and actions[2*i].get('image_sha256') == before['image_sha256'], 'action_intent_receipt_binding')
        source, target, _ = NAVIGATION[base]
        require(before.get('state') == source and before.get('reason') is None, 'navigation_source_unknown')
        if states[i] == 'completed':
            guard = receipts[cursor]['reading']
            require(guard.get('state') == source and guard.get('reason') is None,
                    'navigation_guard_page_unknown')
        for event in actions[2*i:2*i+2]:
            require(event.get('game_day') == binding['game_day'] and event.get('operation') == op
                    and not event.get('disposition'), 'action_binding')
            for key in ('device','intent_id','session_id'):
                require(key not in event or event[key] == binding[key], 'action_binding')
        cursor += 1
        if i < len(ops)-1:
            require(states[i] == 'completed' and cursor < len(receipts)
                    and receipts[cursor]['entry'] == 'OneKeyExecRead'
                    and actions[2*i+1].get('image_sha256') == receipts[cursor]['reading']['image_sha256']
                    and receipts[cursor]['reading'].get('state') == target
                    and receipts[cursor]['reading'].get('reason') is None,
                    'navigation_completion_missing')
            cursor += 1
    require(cursor == len(receipts), 'extra_receipt')
    require(report.get('reason') == 'input_unverified_no_retry:'+ops[-1], 'abort_boundary_unsupported')
    root_end = terminal(parent, 'schedule_onekey', manifest_path, pin)
    action_end = terminal(parent, ops[-1], manifest_path, pin)
    root_suffix = root_data[bundle['root_prefix']['size']:]
    action_suffix = action_data[bundle['action_prefix']['size']:]
    root_closed, action_closed = bool(root_suffix), bool(action_suffix)
    require(not root_closed or same_detail(events_terminal(root_suffix), root_end), 'invalid_root_terminal')
    require(not action_closed or same_detail(events_terminal(action_suffix), action_end), 'invalid_action_terminal')
    require(root_closed == action_closed, 'half_written_abort_no_resend')
    return dict(bundle=bundle, parent=parent, root_end=root_end, action_end=action_end,
                closed=root_closed, action_path=action_path, states=states)


def events_terminal(data):
    require(data.endswith(b'\n') and len(data.splitlines()) == 1, 'extra_or_torn_terminal')
    return decode(data)


def reconcile(*, root, manifest, expected_manifest_sha256, expected_root_sha256,
              expected_action_sha256, apply=False):
    result = dict(outcome='rejected', settlement_verified=False, consumer_input_sent=None,
                  grants_execution_permission=False, daily_complete=False)
    try:
        require(type(apply) is bool, 'explicit_boolean_apply_required')
        root = Path(root).absolute()
        ledger = Journal(root/LEDGER)
        def validate():
            evidence = Evidence(root)
            data = evidence.read(ledger.path)
            proof = verify_bundle(evidence, manifest, expected_manifest_sha256, data)
            require(proof['bundle']['root_prefix']['sha256'] == expected_root_sha256
                    and proof['bundle']['action_prefix']['sha256'] == expected_action_sha256,
                    'explicit_prefix_sha_required')
            from .onekey_history import navigation_history
            navigation_history(ledger, data, proof['parent']['game_day'], proof['parent']['device'],
                               evidence=evidence, pending=not proof['closed'])
            evidence.recheck()
            return evidence, proof
        evidence, proof = validate()
        if proof['closed']:
            result['outcome'] = 'already_aborted'
        elif not apply:
            result['outcome'] = 'eligible_navigation_abort'
        else:
            actions = Journal(proof['action_path'].parent)
            # Only apply may create locks. Existing paths must still be safe.
            for journal in (ledger, actions):
                lock = journal.directory/'transaction.lock'
                if lock.exists() or lock.is_symlink():
                    path_in(root, lock)
            with ledger.locked(), actions.locked():
                evidence, proof = validate()
                require(not proof['closed'], 'concurrent_abort')
                actions.append(proof['action_end'])
                # Recheck all original evidence except the intentionally appended child.
                evidence.files[actions.path] += encode(proof['action_end'])
                evidence.recheck()
                ledger.append(proof['root_end'])
                validate()
            result['outcome'] = 'aborted_before_consumer'
        result['navigation_input_states'] = proof['states']
        result['consumer_input_sent'] = False
    except (Exception, KeyboardInterrupt) as exc:
        result['reason'] = str(exc) or type(exc).__name__
    return result


def encode(event):
    return (json.dumps(event, ensure_ascii=False, separators=(',', ':'))+'\n').encode('utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--expected-root-sha256', required=True)
    parser.add_argument('--expected-action-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    result = reconcile(**vars(parser.parse_args(argv)))
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result['outcome'] == 'rejected' else 0


if __name__ == '__main__':
    raise SystemExit(main())
