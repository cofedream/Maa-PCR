from datetime import datetime, timezone, timedelta
import hashlib
import json
import pytest
from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_reconcile import audit_not_started, record_not_started, is_not_started
from pcr_maa.preset_execution import Journal
from test_guide_opening import candidate, SimulatedMaa


def evidence(tmp_path):
    journal = Journal(tmp_path/'ledger')
    port = SimulatedMaa(tmp_path)
    port.start_and_read = lambda plan: dict(outcome='unknown')
    execute_trial(port,journal,candidate())
    intent = journal.events()[-1]
    intent['at'] -= 5
    journal.path.write_text(json.dumps(intent)+'\n',encoding='utf-8')
    formation = intent['team_evidence']['portrait_evidence']
    ready = dict(native_ok=True,task_id=11,nodes=[dict(name='GTReadTeam',recognition=dict(best=dict(detail=formation)))])
    receipt = dict(native_ok=False,tasker_inited=True,task_id=22,nodes=[dict(name='',completed=False,action=None)])
    stamp = datetime.fromtimestamp(intent['at']+.01,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    line = lambda text: f'[{stamp}][DBG][Px5][Tx7]{text}'
    notify = lambda msg,body: line('[EventDispatcher::notify] !!!OnEventNotify!!! [msg='+msg+'] [details='+json.dumps(body,separators=(',',':'))+']')
    body = dict(name='GTBattleStart',task_id=22,node_id=33)
    reco = dict(name='GTBattleStart',task_id=22,reco_id=44)
    lines = [notify('Node.PipelineNode.Succeeded',dict(name='GTReadTeam',task_id=11,reco_details=dict(detail=dict(best=dict(detail=formation))))),
        line('[AgentClient::handle_context_run_task] [req={"entry":"GTBattleStart"}] | enter'),
        line('[TaskNS::PipelineTask::run] [entry_=GTBattleStart] [task_id_=22] | enter'),
        notify('Node.PipelineNode.Starting',body),
        notify('Node.Recognition.Starting',reco),notify('Node.Recognition.Failed',reco),
        line('[PipelineTask::run_next] PipelineTask bad next [result={"action_id":0,"completed":false,"name":"","node_id":33,"reco_id":0}] [task_id_=22]'),
        notify('Node.PipelineNode.Failed',body),
        line('[AgentClient::handle_context_run_task] | leave, 1000ms')]
    # End is one second after the same-call start, still before the reconciliation time.
    endstamp=datetime.fromtimestamp(intent['at']+1.01,timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    lines[-1]=lines[-1].replace(stamp,endstamp)
    data=dict(log=('\n'.join(lines)+'\n').encode(),receipt=json.dumps(receipt).encode(),ready=json.dumps(ready).encode())
    paths={}
    for key,value in data.items():
        paths[key]=tmp_path/(key+'.txt');paths[key].write_bytes(value)
    args=dict(intent_id=intent['intent_id'],ledger_sha256=hashlib.sha256(journal.path.read_bytes()).hexdigest(),
        **{key+'_path':path for key,path in paths.items()})
    return journal,intent,data,paths,args


def test_append_proven_no_start_preserves_attempt_and_next_limit(tmp_path):
    journal,intent,data,paths,args=evidence(tmp_path)
    old=journal.path.read_bytes()
    event=record_not_started(journal,**args)
    assert journal.path.read_bytes().startswith(old) and journal.events()[0]==intent
    assert is_not_started(event,intent) and event['attempt']==1 and event['result_verified'] is False
    with pytest.raises(ValueError):record_not_started(journal,**args)
    port=SimulatedMaa(tmp_path,result='GTDefeat');plan=candidate();plan['source_hash']='c'*64
    for attempt in (2,3):
        port=SimulatedMaa(tmp_path,result='GTDefeat');report=execute_trial(port,journal,plan)
        assert report['attempts_used']==attempt and report['outcome']=='defeat'
        assert [n for n,c in port.calls if n=='GTBattleStart']==['GTBattleStart']
    assert len(journal.events())==6 and journal.path.read_bytes().startswith(old)


@pytest.mark.parametrize('mutation', [
    lambda b:b.replace(b'Node.Recognition.Failed',b'Node.Recognition.Succeeded'),
    lambda b:b.replace(b'Node.PipelineNode.Failed',b'Node.PipelineNode.Succeeded'),
    lambda b:b.replace(b'"action_id":0',b'"action_id":99'),
    lambda b:b.replace(b'Node.Recognition.Starting',b'Node.Action.Starting'),
    lambda b:b.replace(b'| leave, 1000ms',b'| truncated'),
    lambda b:b.replace(b'[entry_=GTBattleStart]',b'[entry_=OtherStart]'),
    lambda b:b.replace(b'[Tx7]',b'[Tx7]\x00'),
    lambda b:b.replace(b'"task_id":22',b'"task_id":99'),
    lambda b:b+b.splitlines()[2]+b'\n',
])
def test_missing_or_contradictory_native_proof_keeps_pending(tmp_path,mutation):
    journal,intent,data,paths,args=evidence(tmp_path)
    before=journal.path.read_bytes();paths['log'].write_bytes(mutation(data['log']))
    with pytest.raises((ValueError,KeyError,AttributeError)):record_not_started(journal,**args)
    assert journal.path.read_bytes()==before


@pytest.mark.parametrize('field',['log','receipt','ready','intent','disposition'])
def test_reuse_rechecks_evidence_and_binding(tmp_path,field):
    journal,intent,data,paths,args=evidence(tmp_path)
    result=record_not_started(journal,**args)
    if field in paths:paths[field].write_bytes(data[field]+b' ')
    elif field=='intent':result['source_hash']='f'*64
    else:result.update(outcome='victory',result_verified=True)
    if field not in paths:
        journal.path.write_text(json.dumps(intent)+'\n'+json.dumps(result)+'\n',encoding='utf-8')
    port=SimulatedMaa(tmp_path)
    report=execute_trial(port,journal,candidate())
    assert report['error']=='guide_trial_previous_result_unknown' and port.calls==[]
    assert report['attempts_used']==1


def test_rewriting_original_intent_and_terminal_cannot_change_account(tmp_path):
    journal,intent,data,paths,args=evidence(tmp_path)
    result=record_not_started(journal,**args)
    for item in (intent,result):item['account_id']='998877665'
    journal.path.write_text(json.dumps(intent)+'\n'+json.dumps(result)+'\n',encoding='utf-8')
    plan=candidate();plan['account_id']='998877665'
    report=execute_trial(None,journal,plan)
    assert report['error']=='guide_trial_previous_result_unknown'
