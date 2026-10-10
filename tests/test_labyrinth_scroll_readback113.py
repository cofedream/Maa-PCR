"""Offline semantic replay of actual run113 views; no SDK/device or live proof.

Actual read/input-guard observations are reused as pure samples to exercise the
shared ACK/readback contract. This does not claim the original run did those reads.
"""
import copy,json,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from pcr_maa import native
from pcr_maa.labyrinth.character_recognition import CharacterNavigation,CharacterNotFound,MaaCharacters
from pcr_maa.labyrinth.batch import Batch,Binding,BatchStopped
from pcr_maa.labyrinth.config import Settings,Goal
from pcr_maa.preset_execution import Journal

BASE=Path(__file__).resolve().parents[2]
OBS=BASE/'evidence/resume-20261006/upstream-complete-20261007/isolation-after109/RUN113_CHARACTER_OBSERVATIONS.json'

class Clock:
    def __init__(self):self.value=100.
    def now(self):return self.value

class Reads:
    wait_for=MaaCharacters.wait_for
    def __init__(self,observations,*,error=None,clock=None,cost=0):
        self.observations=iter(observations);self.error=error;self.clock=clock;self.cost=cost
        self.inputs=[];self.reads=[];self.deadlines=[]
    def run(self,suffix,params,*,input=False):
        self.inputs.append((suffix,params,input))
        if self.error is not None:raise self.error
        return None,dict(native_ok=True,task_id=1)
    def read(self,batch,page,*,roster=None,stable=True):
        batch.checkpoint();self.deadlines.append(native.RUN_DEADLINE.get())
        observation=copy.deepcopy(next(self.observations))
        self.reads.append(observation)
        if self.clock is not None:self.clock.value+=self.cost
        return observation,dict(native_ok=True,task_id=len(self.reads)+1)

class FakeBatch:
    def __init__(self):self.operations=[];self.observations=[]
    def checkpoint(self):pass
    def transaction(self,operation,fields,send):
        self.operations.append((operation,fields));return send('current-operation')
    def observation(self,value,**fields):self.observations.append(value);return value

def view(position=.5,*,filter='ALL',page='battle',tab=None,can_scroll=True,signature='same',role=None,trusted=False):
    return dict(page=page,filter=filter,boss_tab=tab,stable=True,count=0,selected=[],members=[],
        cards=[dict(slot_id='1',character_id=role,trusted=trusted,selected=False,box=[60,160,120,120])],
        scrollbar=dict(visible=True,can_scroll=can_scroll,position=position),signature=signature)

def actual(*numbers):
    if not OBS.is_file():pytest.skip('private actual run113 observation receipt is not distributed')
    rows=json.loads(OBS.read_text(encoding='utf-8'))
    assert len(rows)==122
    return [copy.deepcopy(rows[number-1]['observation']) for number in numbers]

def test_actual113_dark_stretch_does_not_return_before_rebound():
    source,stretch,rebound,later=actual(97,98,99,103)
    assert source['filter']==stretch['filter']==rebound['filter']=='DARK'
    assert abs(stretch['scrollbar']['position']-rebound['scrollbar']['position'])>.02
    assert any(c['trusted'] and c['character_id']=='1806' for c in rebound['cards'])
    chars=Reads([stretch,rebound,later]);batch=FakeBatch()
    result=CharacterNavigation(chars,lambda **v:v).move(batch,113,'battle',source,direction='down',block_id=30203)
    assert len(chars.reads)==3 and result==later and result!=stretch
    assert len(chars.inputs)==1 and chars.inputs[0][0]=='ScrollOnce'
    assert batch.observations[0]['client_page_verified'] is True


def test_actual113_all_top_stretch_is_not_a_finished_viewport():
    source,stretch,rebound,later=actual(107,108,109,119)
    assert abs(stretch['scrollbar']['position']-rebound['scrollbar']['position'])>.02
    chars=Reads([stretch,rebound,later]);batch=FakeBatch()
    result=CharacterNavigation(chars,lambda **v:v).move(batch,113,'battle',source,direction='up',block_id=30203)
    assert len(chars.reads)==3 and result['scrollbar']['position']==.075
    assert len(chars.inputs)==1


def test_stationary_real_scrollbar_ignores_unrelated_portrait_trust_fluctuation():
    first=view(.5,signature='moving-unknown',role=None,trusted=False)
    second=view(.515,signature='different-boxes',role='1001',trusted=True)
    second['cards'][0]['box']=[62,170,122,121]
    assert not CharacterNavigation.unchanged(first,second)
    chars=Reads([first,second]);batch=FakeBatch()
    result=CharacterNavigation(chars,lambda **v:v).move(batch,113,'battle',view(.2),direction='down')
    assert result==second and len(chars.reads)==2 and len(chars.inputs)==1


def test_absent_scroll_measurement_uses_business_probe_without_pixel_or_box_equality():
    first=view(None,can_scroll=False,signature='unknown-texture-1')
    second=view(None,can_scroll=False,signature='unknown-texture-2')
    second['cards'][0]['box']=[60,161,120,120]
    chars=Reads([first,second]);batch=FakeBatch()
    assert CharacterNavigation(chars,lambda **v:v).move(batch,113,'battle',first,direction='up')==second
    assert len(chars.reads)==2 and len(chars.inputs)==1


@pytest.mark.parametrize('mode',['filter','tab'])
def test_filter_and_tab_keep_original_first_stable_read_contract(mode):
    before=view(page='boss' if mode=='tab' else 'battle',tab=1 if mode=='tab' else None)
    after=view(filter='DARK' if mode=='filter' else 'ALL',page=before['page'],tab=2 if mode=='tab' else None)
    chars=Reads([after]);batch=FakeBatch();options=dict(filter='DARK') if mode=='filter' else dict(tab=2)
    assert CharacterNavigation(chars,lambda **v:v).move(batch,113,before['page'],before,**options)==after
    assert len(chars.reads)==1 and len(chars.inputs)==1


@pytest.mark.parametrize('kind',['not_sent','unknown_input'])
def test_no_input_ack_never_arms_readback_or_retries_old_unknown(kind,tmp_path):
    error=(native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True))
        if kind=='not_sent' else RuntimeError('actual input result unknown'))
    chars=Reads([view()],error=error);journal=Journal(tmp_path/'ledger')
    batch=Batch(Settings((Goal(1,1),)),None,journal,Binding(123,'offline-scroll113'))
    nav=CharacterNavigation(chars,lambda **v:v)
    with journal.locked():
        with pytest.raises(BatchStopped if kind=='not_sent' else RuntimeError) as caught:
            nav.move(batch,113,'battle',view(),direction='up')
        assert not isinstance(caught.value,CharacterNotFound)
        assert not chars.reads and len(chars.inputs)==1
        events=journal.events()
        if kind=='not_sent':
            assert len(events)==2 and events[-1]['result']['input_execution']=='not_sent'
        else:
            assert len(events)==1 and events[-1]['state']=='in_flight'
            prior=journal.path.read_bytes()
            with pytest.raises(BatchStopped,match='blocked_in_flight'):
                nav.move(batch,113,'battle',view(),direction='up')
            assert journal.path.read_bytes()==prior and len(chars.inputs)==1


def test_motion_readback_keeps_original_shared_twelve_second_deadline(monkeypatch):
    clock=Clock();monkeypatch.setattr(time,'monotonic',clock.now)
    chars=Reads([view(.1),view(.8),view(.1),view(.8)],clock=clock,cost=3.1)
    batch=FakeBatch();token=native.RUN_DEADLINE.set(500.)
    try:
        with pytest.raises(BatchStopped,match='character_feedback_unknown'):
            CharacterNavigation(chars,lambda **v:v).move(batch,113,'battle',view(.4),direction='down')
        assert chars.deadlines==[112.]*4 and len(chars.inputs)==1
        assert native.RUN_DEADLINE.get()==500. and not batch.observations
    finally:native.RUN_DEADLINE.reset(token)


def test_only_complete_attribute_then_all_search_returns_typed_not_found(monkeypatch):
    nav=CharacterNavigation(None,None);calls=[]
    def move(_batch,_enter,_page,current,**options):
        calls.append(options)
        return view(filter=options.get('filter',current['filter']))
    monkeypatch.setattr(nav,'move',move)
    with pytest.raises(CharacterNotFound) as caught:
        nav.seek(None,113,'battle',view(filter='DARK'),['1806'],filters=['DARK','DARK'],block_id=30203)
    result=caught.value
    assert str(result)=='character_not_visible' and result.targets==('1806',)
    assert result.exhausted_filters==('DARK','ALL') and result.observation['filter']=='ALL'
    assert len(calls)==9 and sum(c.get('direction')=='up' for c in calls)==4
    assert sum(c.get('direction')=='down' for c in calls)==4


@pytest.mark.parametrize('kind',['edge_limit','stop'])
def test_incomplete_search_keeps_original_stop_and_twenty_four_limit(kind,monkeypatch):
    nav=CharacterNavigation(None,None);calls=[]
    def move(*args,**options):
        calls.append(options)
        if kind=='stop':raise BatchStopped('stopped')
        return view(signature=str(len(calls)),role=str(1000+len(calls)),trusted=True)
    monkeypatch.setattr(nav,'move',move)
    with pytest.raises(BatchStopped,match='character_list_edge_unknown' if kind=='edge_limit' else 'stopped') as caught:
        nav.seek(None,113,'battle',view(),['1806'])
    assert not isinstance(caught.value,CharacterNotFound)
    assert len(calls)==(24 if kind=='edge_limit' else 1)


def test_seek_move_discards_actual113_stretch_only_trusted_target():
    # Actual ALL views; repeating the rebound as pure reads is the controlled
    # offline counterexample, not a claim that run113 made these extra reads.
    source,stretch,rebound=actual(107,122,121)
    target=lambda v:any(c['trusted'] and c['character_id']=='1806' for c in v['cards'])
    assert not target(source) and target(stretch) and not target(rebound)
    chars=Reads([stretch,rebound,rebound]+[rebound]*8);batch=FakeBatch()
    nav=CharacterNavigation(chars,lambda **v:v)
    with pytest.raises(CharacterNotFound) as caught:
        nav.seek(batch,113,'battle',source,('1806',),block_id=30203)
    assert caught.value.targets==('1806',) and caught.value.exhausted_filters==('ALL',)
    assert caught.value.observation==rebound and not target(caught.value.observation)
    assert len(chars.reads)==11 and len(chars.inputs)==5
    assert all(suffix=='ScrollOnce' for suffix,_,_ in chars.inputs)
    assert [params['direction'] for _,params,_ in chars.inputs]==['up','up','up','down','down']


def test_seek_move_uses_settled_dark_trusted_target_without_all_fallback():
    # Synthesized top rebound: actual113 DARK card observations 99/103 are
    # placed at the actual top-rebound position from observation91. This tests
    # the shared calling contract, not a recorded uninterrupted native sequence.
    source,stretch,rebound,later,top=actual(89,90,99,103,91)
    for value in (rebound,later):value['scrollbar']['position']=top['scrollbar']['position']
    assert source['filter']==stretch['filter']==rebound['filter']==later['filter']=='DARK'
    assert not any(c['trusted'] and c['character_id']=='1806' for c in source['cards'])
    assert any(c['trusted'] and c['character_id']=='1806' for c in later['cards'])
    chars=Reads([stretch,rebound,later]+[later]*4);batch=FakeBatch()
    result,matches=CharacterNavigation(chars,lambda **v:v).seek(
        batch,113,'battle',source,('1806',),filters=('DARK',),block_id=30203)
    assert result==later and [c['character_id'] for c in matches]==['1806']
    assert len(chars.reads)==7 and len(chars.inputs)==3
    assert all(suffix=='ScrollOnce' and params['direction']=='up' for suffix,params,_ in chars.inputs)
    assert result['filter']=='DARK' and not any(params.get('filter')=='ALL' for _,params,_ in chars.inputs)
