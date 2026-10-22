"""Synthetic execution through the existing trial/port/callback/journal boundaries."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace as NS
import pytest
from pcr_maa.guide_identity import read_source, require_source_team, SOURCE_HASHES
from pcr_maa.guide_trial import execute_trial, validate_plan, planned_sets, team_matches
from pcr_maa.guide_trial_native import NativeGuideTrial
from pcr_maa.guide_trial_battle import ensure_running_settings
from pcr_maa.preset_execution import Journal


def candidate(mask=29, stage='红焰深域 8-3'):
    source=read_source(stage,allow_uncertain=True)
    ids=[m['UnitId'] for m in source['Members']]
    plan=dict(version=3,server='cn-bilibili',stage=stage,account_id='123456789',unit_ids=ids,
        stars=[6 if uid in (101001,104701,107101) else 5 for uid in ids],auto=True,max_attempts=3,source_hash='a'*64,
        source_image_sha256=SOURCE_HASHES[stage],source_material_sha256='b'*64,
        source_confirmations=[],set_by_unit={str(uid):bool(mask&(1<<i)) for i,uid in enumerate(ids)})
    for m in source['Members']:
        if 'ImportedSource' in source or m['Score']<.82 or m['Margin']<.08:
            text='|'.join(str(v) for v in (source['SourceUrl'],source['Server'],stage,source['ImageSha256'],m['UnitId'],m['IconUnitId'],plan['source_material_sha256']))
            if 'ImportedSource' in source:
                text += '|' + json.dumps(source['ImportedSource'],ensure_ascii=False,separators=(',',':'))
            plan['source_confirmations'].append(dict(unit_id=m['UnitId'],icon_unit_id=m['IconUnitId'],binding=hashlib.sha256(text.encode()).hexdigest()))
    return plan


class Resource:
    def __init__(self): self.nodes={};self.callbacks={}
    def override_pipeline(self,nodes):
        for name,value in nodes.items():self.nodes.setdefault(name,{}).update(deepcopy(value))
        return True
    def register_custom_recognition(self,name,callback):
        if name in self.callbacks:return False
        self.callbacks[name]=callback;return True


class SimulatedMaa(NativeGuideTrial):
    def __init__(self,directory,order=(4,1,3,0,2),auto=False,initial=None,fault=None,result='GTWinObserved'):
        resource=Resource();super().__init__(NS(resource=resource,_resource_holder=resource,stopping=False),directory)
        self.order=order;self.auto=auto;self.initial=initial;self.fault=fault;self.result=result;self.calls=[];self.returned=False
    def checkpoint(self): pass
    def prepare(self,plan):
        self.configure(plan);self.account=plan['account_id']
        ids=[plan['unit_ids'][i] for i in self.order]
        self.state=dict(auto=self.auto,sets=list(self.initial or [False]*5),set_button=False,unit_ids=ids)
        self.characters.read_controls=lambda _:deepcopy(self.state)
    def read_team(self):
        return dict(unit_ids=self.state['unit_ids'],stars=[dict(zip(self.plan['unit_ids'],self.plan['stars']))[uid] for uid in self.state['unit_ids']])
    def read_battle_settings(self,receipt=None):return deepcopy(self.state)
    def run(self,node,*,click=False):
        self.calls.append((node,click))
        if node in ('GTBattleEnableAuto','GTBattleEnableAllSet','GTBattleToggleSet'):
            params=self.tasker.resource.nodes[node]['custom_recognition_param']
            context=NS(tasker=self.tasker,run_recognition=lambda name,image:NS(hit=name!='GTPauseTitle'))
            args=NS(image=None,custom_recognition_param=json.dumps(params))
            before=self.characters.analyze(context,args)
            if before is None:raise RuntimeError('synthetic_maa_precondition_rejected')
            if self.fault=='no_effect':return {}
            if node=='GTBattleEnableAuto':self.state['auto']=True
            elif node=='GTBattleEnableAllSet':self.state['sets']=[True]*5;self.state['set_button']=True
            else:
                i=params['slot'];assert before.box==[280+160*i,560,75,65]
                self.state['sets'][i]=not self.state['sets'][i]
            if self.fault=='auto_lost':self.state['auto']=False
            if self.fault=='identity_changed':self.state['unit_ids']=list(reversed(self.state['unit_ids']))
        return dict(node=node,synthetic=True)
    def wait(self,names,**kwargs):assert self.result in names;return self.result
    def return_to_stage(self):self.returned=True
    def prepare_retry(self,plan):raise AssertionError('automatic_retry_is_not_allowed')


@pytest.mark.parametrize('mask',range(32))
@pytest.mark.parametrize('order',[(0,1,2,3,4),(4,1,3,0,2)])
def test_all_combinations_through_native_port_callback_and_original_journal(tmp_path,mask,order):
    plan=candidate(mask);validate_plan(plan)
    port=SimulatedMaa(tmp_path,order);journal=Journal(tmp_path/'ledger')
    report=execute_trial(port,journal,plan)
    assert report['outcome']=='victory' and report['attempts_used']==1 and port.returned
    battle=report['battles'][0]
    assert battle['auto'] is True and battle['set_by_unit']==plan['set_by_unit'] and battle['all_set'] is (mask==31)
    assert battle['settings_evidence']['unit_ids']==[plan['unit_ids'][i] for i in order]
    actions=[n for n,click in port.calls if click]
    assert actions[0]=='GTBattleStart' and actions[1]=='GTBattleEnableAuto'
    if mask==31:assert actions[2:]==['GTBattleEnableAllSet']
    else:assert actions[2:]==['GTBattleToggleSet']*mask.bit_count()
    events=journal.events();assert len(events)==2 and events[1]['result_evidence']['set_by_unit']==plan['set_by_unit']
    assert events[0]['team_evidence']['settings_plan']==dict(auto=True,set_by_unit=plan['set_by_unit'])
    assert execute_trial(SimulatedMaa(tmp_path),journal,plan)['outcome']=='already_cleared'


@pytest.mark.parametrize('fault',['no_effect','auto_lost','identity_changed'])
def test_failed_readback_preserves_pending_and_never_restarts(tmp_path,fault):
    plan=candidate(0);port=SimulatedMaa(tmp_path,auto=True,initial=[True]*5,fault=fault);journal=Journal(tmp_path/'ledger')
    report=execute_trial(port,journal,plan)
    assert report['error']=='guide_trial_settings_readback_unknown'
    assert len(journal.events())==1 and sum(n=='GTBattleToggleSet' for n,_ in port.calls)==1
    changed=candidate(31)
    assert execute_trial(SimulatedMaa(tmp_path),journal,changed)['error']=='guide_trial_previous_result_unknown'
    assert len(journal.events())==1


def test_switch_off_only_mismatch_preserves_auto(tmp_path):
    plan=candidate(29);port=SimulatedMaa(tmp_path,auto=True,initial=[True]*5);report=execute_trial(port,Journal(tmp_path/'ledger'),plan)
    assert report['outcome']=='victory'
    assert [n for n,c in port.calls if c]==['GTBattleStart','GTBattleToggleSet']
    assert port.state['auto'] is True and not report['battles'][0]['all_set']


def test_new_team_uses_same_native_form_and_original_limit(tmp_path):
    plan=candidate(31,'红焰深域 8-4');journal=Journal(tmp_path/'ledger')
    for attempt in range(1,4):
        port=SimulatedMaa(tmp_path,result='GTDefeat');report=execute_trial(port,journal,plan)
        assert report['attempts_used']==attempt and report['outcome']=='defeat' and port.returned
        assert sum(n=='GTBattleStart' for n,_ in port.calls)==1
    plan['set_by_unit']={uid:False for uid in plan['set_by_unit']};plan['source_hash']='c'*64
    assert execute_trial(SimulatedMaa(tmp_path),journal,plan)['outcome']=='attempt_limit'
    assert len(journal.events())==6


@pytest.mark.parametrize('change',[
    lambda p:p.update(source_confirmations=[]),
    lambda p:p.update(source_material_sha256='c'*64),
    lambda p:p['source_confirmations'][0].update(unit_id=122601),
    lambda p:p['unit_ids'].__setitem__(2,122601),
])
def test_source_manual_binding_is_not_a_bypass(change):
    plan=candidate();change(plan)
    with pytest.raises(ValueError):require_source_team(plan)


@pytest.mark.parametrize('change',[
    lambda p:p['set_by_unit'].update({'101001':None}),
    lambda p:p['set_by_unit'].update({'999999':True}),
    lambda p:p.update(auto=False),
    lambda p:p.update(max_attempts=4),
    lambda p:p['stars'].__setitem__(0,True),
])
def test_bad_plan_rejected(change):
    plan=candidate();change(plan)
    with pytest.raises(ValueError):validate_plan(plan)


def test_unknown_hud_identity_never_clicks(tmp_path):
    plan=candidate();port=SimulatedMaa(tmp_path);port.prepare(plan);port.state['unit_ids'][1]=None
    with pytest.raises(RuntimeError,match='identity_unknown'):ensure_running_settings(port,set_by_unit=planned_sets(plan))
    assert not port.calls


def test_star_values_follow_ids_not_source_positions():
    plan=candidate();reading=dict(unit_ids=list(reversed(plan['unit_ids'])),stars=list(reversed(plan['stars'])))
    assert team_matches(reading,plan)
    reading['stars']=plan['stars'];assert not team_matches(reading,plan)


@pytest.mark.parametrize('visible_after,identity_changed,expected',[(16,False,True),(40,False,False),(0,True,False)])
def test_formation_observes_star_cycle_without_clicks(tmp_path,monkeypatch,visible_after,identity_changed,expected):
    plan=candidate();port=SimulatedMaa(tmp_path);port.prepare(plan)
    clock=[0.0];reads=[]
    monkeypatch.setattr('pcr_maa.guide_trial_native.time.monotonic',lambda:clock[0])
    monkeypatch.setattr('pcr_maa.guide_trial_native.time.sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def read():
        reads.append(clock[0]);clock[0]+=1
        ids=plan['unit_ids'].copy()
        if identity_changed:ids[0]=122601
        return dict(unit_ids=ids,stars=plan['stars'] if clock[0]>=visible_after else [None]*5)
    port.read_team=read
    if expected:
        assert port.verify_team(plan)['stars']==plan['stars']
        assert clock[0]>=16
    else:
        with pytest.raises(RuntimeError,match='team_or_account_mismatch'):port.verify_team(plan)
    assert not port.calls and clock[0]<33
    if identity_changed:assert len(reads)==1
