"""Offline reconciliation of one rejected start, using its complete native call."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time

SCOPE = 'guide_trial_native_not_started'


def require(value, reason):
    if not value:
        raise ValueError('guide_start_evidence_' + reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def event(line):
    match = re.search(r'\[msg=([^\]]+)\] \[details=(\{.*\})\]\s*$', line)
    require(match is not None, 'damaged_event')
    return match[1], json.loads(match[2])


def audit_not_started(log, receipt_bytes, ready_bytes, intent):
    """Only this native log format is supported; missing/contradictory proof fails closed."""
    receipt, ready = json.loads(receipt_bytes), json.loads(ready_bytes)
    tid = receipt.get('task_id')
    require(type(tid) is int and receipt.get('native_ok') is False
        and receipt.get('tasker_inited') is True and receipt.get('nodes')
        and all(n.get('action') is None and n.get('completed') is False for n in receipt['nodes']), 'failed_receipt')
    require(ready.get('native_ok') is True and len(ready.get('nodes', [])) == 1
        and ready['nodes'][0].get('name') == 'GTReadTeam', 'ready_receipt')
    formation = ready['nodes'][0]['recognition']['best']['detail']
    require(formation == intent['team_evidence']['portrait_evidence'], 'formation_binding')
    lines = log.decode('utf-8').splitlines()
    starts = [i for i,line in enumerate(lines) if '::PipelineTask::run]' in line
        and f'[entry_=GTBattleStart] [task_id_={tid}] | enter' in line]
    require(len(starts) == 1, 'ambiguous_dispatch')
    start = starts[0]
    identity = re.search(r'\[Px(\d+)\]\[Tx(\d+)\]', lines[start]).group(0)
    call = [i for i in range(max(0,start-5), start) if identity in lines[i]
        and '::handle_context_run_task]' in lines[i] and '"entry":"GTBattleStart"' in lines[i]
        and '| enter' in lines[i]]
    require(len(call) == 1, 'missing_call_start')
    call = call[0]
    ends = [i for i in range(start+1,len(lines)) if identity in lines[i]
        and '::handle_context_run_task]' in lines[i] and '| leave,' in lines[i]]
    require(ends, 'missing_call_end')
    end = ends[0]
    window = lines[call:end+1]
    require(not any('\x00' in line for line in window), 'damaged_call')
    require(sum('::handle_context_run_task]' in line and '| enter' in line for line in window) == 1,
        'nested_or_repeated_dispatch')
    stamp = lambda line: datetime.strptime(line[1:24], '%Y-%m-%d %H:%M:%S.%f').replace(
        tzinfo=timezone(timedelta(hours=8))).timestamp()
    begin_at, end_at = stamp(lines[call]), stamp(lines[end])
    require(0 <= begin_at-intent['at'] <= 2 and 0 < end_at-begin_at <= 90, 'intent_time_binding')
    all_events = [(i,*event(line)) for i,line in enumerate(lines) if '!!!OnEventNotify!!!' in line]
    require(not any(body.get('task_id') == tid and msg.startswith('Node.Action.')
        for _,msg,body in all_events), 'action_seen')
    ready_events = [(i,body) for i,msg,body in all_events if i < call
        and msg == 'Node.PipelineNode.Succeeded' and body.get('name') == 'GTReadTeam'
        and body.get('task_id') == ready.get('task_id')]
    require(len(ready_events) == 1 and ready_events[0][1]['reco_details']['detail']['best']['detail'] == formation,
        'native_formation_binding')
    require(0 <= begin_at-stamp(lines[ready_events[0][0]]) <= 2, 'stale_ready')
    pending, rejected, pipeline = {}, [], []
    for i,msg,body in all_events:
        if not call <= i <= end:
            continue
        require(not msg.startswith('Node.Action.'), 'action_seen')
        if msg.startswith('Node.Recognition.'):
            key = (body['task_id'], body['reco_id'])
            if msg.endswith('.Starting'):
                require(key not in pending, 'duplicate_recognition')
                pending[key] = body['name']
            else:
                require(pending.pop(key,None) == body['name'], 'unpaired_recognition')
                if body['task_id'] == tid:
                    require(body['name'] == 'GTBattleStart' and msg.endswith('.Failed'), 'guard_not_rejected')
                    rejected.append(body['reco_id'])
        if msg.startswith('Node.PipelineNode.'):
            require(body['task_id'] == tid and body['name'] == 'GTBattleStart', 'other_pipeline')
            pipeline.append((msg,body))
    require(not pending and rejected and len(set(rejected)) == len(rejected), 'incomplete_recognitions')
    require(len(pipeline) == 2 and pipeline[0][0] == 'Node.PipelineNode.Starting'
        and pipeline[1][0] == 'Node.PipelineNode.Failed' and pipeline[0][1] == pipeline[1][1], 'incomplete_pipeline')
    node_id = pipeline[0][1]['node_id']
    bad = [json.loads(re.search(r'\[result=(\{.*\})\]',line)[1]) for line in window
        if 'PipelineTask bad next' in line and f'[task_id_={tid}]' in line]
    require(bad == [dict(action_id=0, completed=False, name='', node_id=node_id, reco_id=0)], 'action_id_not_zero')
    for line in window:
        if 'ctrl_event_sink]' in line and '[message=Controller.Action.' in line:
            body = json.loads(re.search(r'\[details_json=(\{.*\})\] \[trans_arg=',line)[1])
            require(body.get('action') == 'screencap', 'controller_input_seen')
        if '[ChildPipeIOStream.cpp]' in line and '[args_=' in line:
            args = json.loads(re.search(r'\[args_=(\[.*\])\] \[child_',line)[1])
            require(len(args) == 4 and args[0] == '-s' and args[2] == 'exec-out'
                and args[3].startswith('screencap '), 'device_command_seen')
    return dict(entry='GTBattleStart', task_id=tid, node_id=node_id, action_id=0,
        recognition_failures=len(rejected), action_started=False, complete=True,
        start_line=call+1, end_line=end+1, started_at=begin_at, finished_at=end_at)


def read_proof(evidence, intent):
    require(evidence.get('source') == 'preserved_native_log', 'source')
    data = {}
    for name in ('log','receipt','ready'):
        spec = evidence[name]
        path = Path(spec['path'])
        require(path.is_file() and path.stat().st_size <= 64*1024*1024, 'file')
        data[name] = path.read_bytes()
        require(digest(data[name]) == spec['sha256'], 'hash_changed')
    return audit_not_started(data['log'], data['receipt'], data['ready'], intent)


def is_not_started(event, intent, *, prefix=None):
    if event.get('recovery_scope') != SCOPE:
        return False
    try:
        require(intent.get('state') == 'in_flight' and intent.get('operation') == 'guide_trial', 'intent')
        require(all(event.get(k) == v for k,v in intent.items() if k != 'state'), 'intent_changed')
        require(event.get('state') == 'settled' and event.get('outcome') == 'not_started'
            and event.get('input_execution') == 'not_sent' and event.get('consumption_sent') is False
            and event.get('result_verified') is False and event.get('settlement_verified') is False
            and event.get('replay_forbidden') is True, 'disposition')
        if prefix is not None:
            require(digest(prefix) == event['evidence']['ledger_prefix_sha256'], 'ledger_prefix_changed')
        proof = read_proof(event['evidence'], intent)
        require(proof == event['evidence']['proof'] and event['resolved_at'] >= proof['finished_at'], 'proof')
        return True
    except (ValueError, KeyError, TypeError, OSError, AttributeError, IndexError):
        return False


def record_not_started(journal, *, intent_id, ledger_sha256, log_path, receipt_path, ready_path):
    """Explicit offline maintenance; append once, retaining the original attempt and bytes."""
    with journal.locked():
        before = journal.path.read_bytes()
        require(digest(before) == ledger_sha256, 'ledger_changed')
        events = journal.events()
        require(len(events)%2 == 1 and events[-1].get('intent_id') == intent_id, 'not_pending')
        intent = events[-1]
        evidence = dict(source='preserved_native_log', ledger_prefix_sha256=ledger_sha256)
        for name,path in (('log',log_path),('receipt',receipt_path),('ready',ready_path)):
            path = Path(path).resolve()
            evidence[name] = dict(path=str(path), sha256=digest(path.read_bytes()))
        evidence['proof'] = read_proof(evidence,intent)
        result = dict(deepcopy(intent), state='settled', recovery_scope=SCOPE, outcome='not_started',
            input_execution='not_sent', consumption_sent=False, result_verified=False,
            settlement_verified=False, replay_forbidden=True, resolved_at=time.time(), evidence=evidence)
        require(is_not_started(result,intent,prefix=before) and journal.path.read_bytes() == before, 'concurrent_change')
        journal.append(result)
        require(journal.path.read_bytes().startswith(before), 'prefix_changed')
        return result
