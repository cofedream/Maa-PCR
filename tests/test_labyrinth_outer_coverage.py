"""Bounded outer-batch contract checks; no device calls or real run facts."""
import json
from dataclasses import replace
from pathlib import Path
from pcr_maa.labyrinth.batch import Batch
from pcr_maa.labyrinth.config import Settings,Goal
from pcr_maa.labyrinth.records import RunRecord
from pcr_maa.preset_execution import Journal
from test_labyrinth import Port

class TerminalPort(Port):
    def __init__(self,failures=1):super().__init__();self.failures=failures;self.reconnected=[]
    def play(self,route,batch):
        failed=self.enters<=self.failures
        return self.observe(enter_id=route.enter_id,guild_id=self.guild,difficulty=self.difficulty,
            outcome='defeated' if failed else 'cleared',clear_verified=not failed,failure_verified=failed)
    def resume_failed_run(self,enter):self.reconnected.append(enter)

def test_actual_failure_retires_then_continues_without_clear_count(tmp_path):
    port=TerminalPort();journal=Journal(tmp_path)
    result=Batch(Settings((Goal(1,1),)),port,journal,port.binding).run()
    assert result['batch_complete'] and result['completed']=={1:1}
    assert port.enters==2 and port.retires==1 and port.reconnected==[1]
    rows=[json.loads(v) for v in (tmp_path/'runs'/result['run_id']/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [r['stage'] for r in rows]==['started','failed','cleared','finished']
    assert rows[1]['failed']=={'1':1} and rows[1]['completed']=={'1':0}

def test_attempt_goal_counts_only_verified_terminal_failure(tmp_path):
    port=TerminalPort(10);result=Batch(Settings((Goal(1,2,'attempts'),)),port,Journal(tmp_path),port.binding).run()
    assert result['batch_complete'] and result['completed']=={1:2}
    assert port.enters==2 and port.retires==2
    port=Port(defeated=True);result=Batch(Settings((Goal(1,1,'attempts'),)),port,Journal(tmp_path/'unknown'),port.binding).run()
    assert result['outcome']=='defeated' and result['completed']=={1:0}

def test_failure_cycle_preserves_attempt_cap(tmp_path):
    port=TerminalPort(10);result=Batch(Settings((Goal(1,1),),max_attempts=2),port,Journal(tmp_path),port.binding).run()
    assert result['outcome']=='reroll_attempt_limit' and port.enters==2 and port.retires==1

def test_start_existing_run_retires_actual_id_once(tmp_path):
    class Active(Port):
        def top(self):return self.observe(enter_id=42 if not self.retires else 0,fresh_start=bool(self.retires))
        def retire(self,enter,oid):assert enter==42;return super().retire(enter,oid)
    port=Active();result=Batch(Settings((Goal(1,1),)),port,Journal(tmp_path),port.binding).run()
    assert result['batch_complete'] and port.retires==1 and port.enters==1

def test_safe_checkpoint_reuses_real_record_counts(tmp_path):
    port=Port();settings=Settings((Goal(1,2),));record=RunRecord(tmp_path,'a'*32,port.binding)
    record.append('started',settings=settings.payload())
    record.append('cleared',enter_id=50,guild_id=1,difficulty=1,frame=100,completed={1:1})
    record.checkpoint(settings.payload(),'ready',{1:1},{1:0})
    result=Batch(settings,port,Journal(tmp_path),port.binding,resume=True).run()
    assert result['run_id']=='a'*32 and result['completed']=={1:2} and port.enters==1
    rows=[json.loads(v) for v in record.path.read_text(encoding='utf-8').splitlines()]
    assert [r['stage'] for r in rows]==['started','cleared','cleared','finished']

def test_mid_run_checkpoint_never_supplies_roles_relics_or_counts(tmp_path):
    port=Port();settings=Settings((Goal(1,2),));record=RunRecord(tmp_path,'b'*32,port.binding)
    record.append('started',settings=settings.payload());record.checkpoint(settings.payload(),'playing',{1:1},{1:0})
    assert RunRecord.recover(tmp_path,port.binding,settings.payload()) is None
    result=Batch(settings,port,Journal(tmp_path),port.binding,resume=True).run()
    assert result['run_id']!='b'*32 and result['completed']=={1:2} and port.enters==2

def test_checkpoint_cannot_invent_terminal_counts(tmp_path):
    import pytest
    port=Port();settings=Settings((Goal(1,2),));record=RunRecord(tmp_path,'c'*32,port.binding)
    record.append('started',settings=settings.payload());record.checkpoint(settings.payload(),'ready',{1:1},{1:0})
    with pytest.raises(ValueError,match='batch_resume_record_invalid'):RunRecord.recover(tmp_path,port.binding,settings.payload())

def test_final_attempt_at_attempt_cap_finishes_without_another_enter(tmp_path):
    port=TerminalPort(10)
    result=Batch(Settings((Goal(1,1,'attempts'),),max_attempts=1),port,Journal(tmp_path),port.binding).run()
    assert result['batch_complete'] and result['completed']=={1:1} and port.enters==1 and port.retires==1

def test_recovery_rejects_path_injection_before_reading_file(tmp_path):
    import pytest
    port=Port();settings=Settings((Goal(1,1),));record=RunRecord(tmp_path,'d'*32,port.binding)
    record.append('started',settings=settings.payload());record.checkpoint(settings.payload(),'ready',{1:0},{1:0})
    p=tmp_path/'batch-checkpoint.json';value=json.loads(p.read_text(encoding='utf-8'));value['run_id']='../elsewhere'
    p.write_text(json.dumps(value),encoding='utf-8')
    with pytest.raises(ValueError,match='invalid_run_id'):RunRecord.recover(tmp_path,port.binding,settings.payload())



def test_ready_attempt_checkpoint_retains_mixed_real_terminal_counts(tmp_path):
    port=TerminalPort(0);settings=Settings((Goal(1,3,'attempts'),));run_id='a'*32
    record=RunRecord(tmp_path,run_id,port.binding);record.append('started',settings=settings.payload())
    record.append('failed',enter_id=101,guild_id=1,difficulty=1,frame=1,outcome='failed_max_retry',completed={1:1},failed={1:1})
    record.append('cleared',enter_id=102,guild_id=1,difficulty=1,frame=2,completed={1:2})
    record.checkpoint(settings.payload(),'ready',{1:2},{1:1})
    result=Batch(settings,port,Journal(tmp_path),port.binding,resume=True).run()
    assert result['run_id']==run_id and result['completed']=={1:3} and port.enters==1
    rows=[json.loads(x) for x in record.path.read_text(encoding='utf-8').splitlines()]
    assert [r['stage'] for r in rows]==['started','failed','cleared','cleared','finished']
