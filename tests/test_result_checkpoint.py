"""Synthetic failure injection, plus separately labelled native frame replay."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
from pcr_maa import result_checkpoint as cp
from pcr_maa.preset_execution import Journal, execute_one
from pcr_maa.game_day import current_game_day
from test_preset_execution import Port, PRESET, reading, quote_for


def consumer(name='ExecConfirm', success=True):
    return dict(tasker_inited=True, native_ok=success, task_id=123, nodes=[
        dict(name=name, completed=True, action=dict(name=name,success=success,raw=dict(type='Click')))])


def anchors():
    return {name:dict(filtered=[dict(text=text,score=.99,box=[1,1,10,10])])
            for name,text in zip(cp.ANCHORS,('扫荡结果','扫荡完成！','确认'))}


def frame(tmp_path):
    from hashlib import sha256
    path=tmp_path/'synthetic.png';path.write_bytes(b'labelled synthetic result frame, not live OCR')
    return dict(path=str(path),sha256=sha256(path.read_bytes()).hexdigest(),kind='result',task_id=124)


class CheckpointPort(Port):
    def __init__(self,tmp_path,fault):
        super().__init__();self.tmp_path=tmp_path;self.fault=fault;self.callback=None
    def bind_result_checkpoint(self, callback): self.callback=callback
    def submit(self,q):
        self.calls.append('submit')
        self.callback(consumer(),frame(self.tmp_path),anchors())
        if self.fault=='stop': raise KeyboardInterrupt()
        if self.fault=='exception': raise RuntimeError('Callback scope blocked; no retry')
        return dict(consumption_dispatched=True,result_verified=True,preset_return_verified=self.fault!='navigation')
    def read(self,slot):
        if 'submit' in self.calls and self.fault=='read': raise RuntimeError('Callback scope blocked; no retry')
        return super().read(slot)


def invoke(port,journal):
    port.journal=journal
    return execute_one(port,PRESET,'gui-current-state',journal,execute=True,game_day=current_game_day())


@pytest.mark.parametrize('fault',['read','navigation','stop','exception'])
def test_known_result_durable_restart_no_consumer_replay(tmp_path,fault):
    journal=Journal(tmp_path/'ledger');port=CheckpointPort(tmp_path,fault)
    first=invoke(port,journal);original=journal.path.read_bytes()
    assert first['outcome']=='result_verified_followup_incomplete',first
    assert first['recovery_available'] and not first['settlement_verified']
    assert port.callback is None and journal.events()[-1]['state']=='in_flight'
    restarted=Port(reading(0));second=invoke(restarted,journal)
    assert second['outcome']=='skipped_goal_met' and second['home_verified'],second
    assert restarted.calls==['read','home']
    end=journal.events()[-1]
    assert end['disposition']==cp.DISPOSITION and not end['settlement_verified'] and 'remaining' not in end
    assert journal.path.read_bytes().startswith(original)
    unchanged=journal.path.read_bytes();assert invoke(Port(reading(0)),journal)['home_verified']
    assert journal.path.read_bytes()==unchanged


def pending(tmp_path,kind='preset',game_day=None):
    before=reading();quote=quote_for(before)
    if kind=='event':
        before.update(event_identity=dict(layout='story_event_h',period='举办时间：10/0112:00~10/0704:59'))
        quote.update(scope='event-hard',event_identity=before['event_identity'])
    if kind=='abyss':
        from test_abyss import reading as ar, quotation
        before,quote=ar(),quotation()
    original=dict(state='in_flight',game_day=game_day or current_game_day(),at=time.time(),quote=quote)
    if kind=='preset':original['slot']=2
    else:original['scope']='abyss-outpost' if kind=='abyss' else 'event-hard'
    journal=Journal(tmp_path/'ledger')
    with journal.locked():journal.append(original)
    checkpoint=cp.ResultCheckpoint(journal,kind=kind,before=before,quote=quote)
    checkpoint.capture(consumer('AbyssTicketCommit' if kind=='abyss' else 'ExecConfirm'),frame(tmp_path),anchors())
    return journal,checkpoint


@pytest.mark.parametrize('kind',['preset','event','abyss'])
def test_all_consuming_families_recover_phase_without_fake_counts(tmp_path,kind):
    journal,checkpoint=pending(tmp_path,kind)
    old=journal.path.read_bytes()
    with journal.locked():end=cp.recover(journal,journal.events(),kind)[-1]
    assert end['consumption_result_verified'] and not end['post_counts_verified']
    assert not end['home_verified'] and not end['retry_permitted'] and 'remaining' not in end
    assert journal.path.read_bytes().startswith(old)


@pytest.mark.parametrize('tamper',['image','quote','consumer','anchor','original','kind','terminal_day'])
def test_corrupt_or_wrong_evidence_never_opens_input(tmp_path,tamper):
    journal,checkpoint=pending(tmp_path)
    if tamper=='image':Path(checkpoint.read()['image']['path']).write_bytes(b'changed')
    elif tamper=='terminal_day':
        with journal.locked():cp.recover(journal,journal.events(),'preset')
        events=journal.events();events[-1]['game_day']='2099-01-01'
        journal.path.write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
    else:
        record=checkpoint.read()
        if tamper=='quote':record['quote']['counts']=[1]
        if tamper=='consumer':record['consumer']['nodes'][0]['action']['success']=False
        if tamper=='anchor':record['anchors']['ExecResultComplete']['filtered'][0]['score']=.5
        if tamper=='original':record['original']['slot']=1
        if tamper=='kind':record['kind']='event'
        checkpoint.path.write_text(json.dumps(record),encoding='utf-8')
    old=journal.path.read_bytes();port=Port(reading(0));result=invoke(port,journal)
    assert result['outcome']=='blocked_or_failed',result
    assert port.calls==[] and journal.path.read_bytes()==old


def test_same_day_nonzero_no_repeat_after_known_result(tmp_path):
    journal,checkpoint=pending(tmp_path)
    port=Port();result=invoke(port,journal)
    assert result['outcome']=='blocked_post_counts_unverified',result
    assert port.calls==['read']


def test_unknown_without_checkpoint_stays_blocked_and_unchanged(tmp_path):
    journal=Journal(tmp_path/'ledger')
    with journal.locked():journal.append(dict(state='in_flight',slot=2,game_day=current_game_day(),at=time.time(),quote={}))
    old=journal.path.read_bytes();port=Port();result=invoke(port,journal)
    assert result['outcome']=='blocked_in_flight' and port.calls==[] and journal.path.read_bytes()==old


def test_checkpoint_survives_abrupt_process_exit(tmp_path):
    script="""from pathlib import Path
import os,sys
sys.path.insert(0, sys.argv[2])
sys.path.insert(0, sys.argv[3])
from test_result_checkpoint import pending
journal,checkpoint=pending(Path(sys.argv[1]))
os._exit(17)
"""
    run=subprocess.run([sys.executable,'-X','utf8','-B','-c',script,str(tmp_path),
        str(Path(__file__).parent),str(Path(__file__).parents[1]/'src')],
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),capture_output=True,text=True,timeout=30)
    assert run.returncode==17,run.stderr
    journal=Journal(tmp_path/'ledger');port=Port(reading(0));result=invoke(port,journal)
    assert result['outcome']=='skipped_goal_met' and 'submit' not in port.calls


@pytest.mark.parametrize('value',[1,3,7])
def test_interrupted_count_fit_restores_only_expected_value(tmp_path,value):
    journal=Journal(tmp_path/'ledger');before=reading();quote=quote_for(before)
    original=dict(state='in_flight',slot=2,game_day=current_game_day(),at=time.time(),quote=quote,
                  count_fit=dict(original=1,target=3,readback=3))
    with journal.locked():journal.append(original)
    checkpoint=cp.ResultCheckpoint(journal,kind='preset',before=before,quote=quote)
    checkpoint.capture(consumer(),frame(tmp_path),anchors())
    class FitPort(Port):
        def __init__(self):super().__init__(reading(0));self.value=value
        def read_count(self,slot):self.calls.append('read_count');return self.value
        def fit_count(self,slot,target):self.calls.append('restore');self.value=target;return dict(verified=True)
    port=FitPort();result=invoke(port,journal)
    if value==7:
        assert result['outcome']=='count_restore_value_changed' and 'restore' not in port.calls
    else:
        assert result['outcome']=='skipped_goal_met' and result['count_fit']['restored']
        assert port.value==1 and 'submit' not in port.calls


def test_recovery_pipeline_cannot_reach_consumers():
    from pcr_maa import native
    nodes={}
    for path in (native.RESOURCE/'pipeline').glob('*.json'):
        nodes.update(json.loads(path.read_text(encoding='utf-8')))
    seen=set();todo=['ExecRecoverResultStart','AbyssRecoverResultStart']
    while todo:
        name=todo.pop()
        if name in seen:continue
        seen.add(name);node=nodes.get(name,{})
        todo.extend(node.get('next',[])+node.get('on_error',[]))
    assert not seen.intersection({'ExecConfirm','ExecChallenge','AbyssTicketCommit','AbyssChallenge','AbyssOpenSweep'})
    assert {'ExecLimitedShopCancel','AbyssLimitedShopCancel'} <= seen


def test_new_cycle_requires_new_observation_and_new_intent(tmp_path):
    journal,checkpoint=pending(tmp_path,game_day='2026-01-01')
    port=Port();result=invoke(port,journal)
    assert result['outcome']=='executed' and port.calls.count('submit')==1,result
    assert len(journal.events())==4 and journal.events()[0]['game_day']=='2026-01-01'
    # A later full settlement no longer selects the old pending recovery.
    again=Port(reading(0));assert invoke(again,journal)['outcome']=='skipped_goal_met'
    assert again.calls==['read','home']


def test_next_cycle_count_fit_restores_old_and_new_adjustments(tmp_path):
    journal=Journal(tmp_path/'ledger');before=reading();quote=quote_for(before)
    old=dict(state='in_flight',slot=2,game_day='2026-01-01',at=time.time(),quote=quote,
             count_fit=dict(original=1,target=3,readback=3))
    with journal.locked():journal.append(old)
    checkpoint=cp.ResultCheckpoint(journal,kind='preset',before=before,quote=quote)
    checkpoint.capture(consumer(),frame(tmp_path),anchors())
    class FitPort(Port):
        def __init__(self):super().__init__();self.value=3;self.targets=[]
        def read_count(self,slot):return self.value
        def fit_count(self,slot,target):self.value=target;self.targets.append(target);return dict(verified=True)
    port=FitPort();port.journal=journal
    result=execute_one(port,PRESET,'gui-current-state',journal,execute=True,fit_remaining=True,game_day=current_game_day())
    assert result['outcome']=='executed',result
    assert port.targets==[1,3,1] and port.value==1 and result['count_fit']['restored']
