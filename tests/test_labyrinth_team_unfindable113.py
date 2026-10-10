"""Completed UI search can replan; unknown input/readback cannot."""
from types import SimpleNamespace
import pytest
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth import character_recognition as cr,teams
from pcr_maa.labyrinth.roles import Context
from pcr_maa.labyrinth.team_execution import MaaTeam

class Batch:
    settings=SimpleNamespace(strategy=None)
    def checkpoint(self):pass
    def transaction(self,kind,fields,send):return send('actual-test-input')
    def observation(self,value,**fields):return value

class Characters:
    def __init__(self,roster,*,boss=False):
        self.roster=roster;self.tab=1;self.boss=boss
        self.tabs={1:[],2:[],3:[]};self.inputs=[];self.read_rosters=[]
    def view(self):
        ids=sorted(self.tabs[self.tab])
        return dict(selected=ids,members=[dict(character_id=r,trusted=True) for r in ids],boss_tab=self.tab if self.boss else None)
    def read(self,batch,page,*,roster):
        self.read_rosters.append(tuple(roster));return self.view(),{'native_ok':True}
    def wait_for(self,batch,page,predicate,*,roster,members_only=False):
        value,receipt=self.read(batch,page,roster=roster)
        assert predicate(value)
        return value,receipt
    def run(self,suffix,params,*,input):
        assert input and suffix=='ToggleOnce' and params['expected_selected']==sorted(self.tabs[self.tab])
        role=params['role_id'];selected=self.tabs[self.tab]
        if role in selected:selected.remove(role)
        else:
            assert not any(role in group for tab,group in self.tabs.items() if tab!=self.tab)
            selected.append(role)
        self.inputs.append((self.tab,role,role in selected))
        return None,{'native_ok':True}

class Optimizer:
    def __init__(self,roster):
        self.profiles={r:SimpleNamespace(position=100,data={'attribute':'DARK'}) for r in roster};self.calls=[]
    def best(self,pool,context,**kw):
        self.calls.append((tuple(pool),context,kw));return SimpleNamespace(members=tuple(pool[:5])) if pool else None
    def retry_best(self,pool,context,**kw):return self.best(pool,context,**kw)

def setup(monkeypatch,*,boss=False,error=None,retry=0):
    roster=tuple(str(1000+i) for i in range(1,12 if boss else 7))
    chars=Characters(roster,boss=boss);opt=Optimizer(roster);attempts=[]
    class Navigation:
        def __init__(self,characters,stamp,*,roster):assert characters is chars and tuple(roster)==chars.roster
        def move(self,batch,enter,page,observation,*,tab,**kw):chars.tab=tab;return chars.view()
        def seek(self,batch,enter,page,observation,targets,**kw):
            attempts.append(tuple(targets))
            if '1003' in targets:
                if error is not None:raise error
                raise cr.CharacterNotFound(targets,chars.view(),('DARK','ALL'))
            return chars.view(),[dict(character_id=targets[0],trusted=True)]
    monkeypatch.setattr(cr,'CharacterNavigation',Navigation)
    team=MaaTeam(SimpleNamespace(),lambda **v:v,roster,optimizer=opt,characters=chars,
        failed_teams=(('old-failed',),),retry_number=retry,last_failed_team=('old-failed',))
    return team,chars,opt,attempts

@pytest.mark.parametrize('retry',[0,1])
def test_normal_complete_search_replans_without_changing_inventory_or_defeat_history(monkeypatch,retry):
    team,chars,opt,attempts=setup(monkeypatch,retry=retry);owned=team.roster
    context=Context(effective_ids=('1003',))
    result=team.prepare(Batch(),113,30203,context)
    assert result['team']==['1001','1002','1004','1005','1006']
    assert len(opt.calls)==2 and '1003' not in opt.calls[1][0]
    assert all(call[1] is context for call in opt.calls) and context.effective_ids==('1003',)
    if retry:assert all(call[2]['failed_teams']==(('old-failed',),) and call[2]['retry_number']==1 for call in opt.calls)
    assert team.roster==owned and set(chars.read_rosters)=={owned}
    assert team.failed_teams==(('old-failed',),) and team.retry_number==retry and not team.started
    assert attempts.count(('1003',))==1 and not any(role=='1003' for _,role,_ in chars.inputs)


def test_boss_replan_releases_cross_tab_members_before_adding_and_clears_unused_tab(monkeypatch):
    team,chars,opt,attempts=setup(monkeypatch,boss=True);owned=team.roster;calls=[]
    chars.tabs={1:['1007'],2:['1001','1006'],3:['1011']}
    def plan(optimizer,pool,context,**kw):
        calls.append((tuple(pool),kw))
        groups=tuple(SimpleNamespace(members=tuple(pool[i:i+5])) for i in range(0,len(pool),5))
        return SimpleNamespace(teams=groups,safe_count=1,reason='test_actual_replan')
    monkeypatch.setattr(teams,'boss_plan',plan)
    result=team.prepare_boss(Batch(),113,30701,Context(boss=True))
    assert len(calls)==2 and calls[1][0]==tuple(r for r in owned if r!='1003')
    assert all(call[1]['excluded']==(('old-failed',),) for call in calls)
    assert chars.tabs=={1:['1001','1002','1004','1005','1006'],2:['1007','1008','1009','1010','1011'],3:[]}
    assert result['teams']==team.boss_teams[113,30701] and team.boss_all_teams[113,30701][2]==[]
    assert team.roster==owned and team.failed_teams==(('old-failed',),) and not team.started
    assert attempts.count(('1003',))==1

@pytest.mark.parametrize('boss',[False,True])
@pytest.mark.parametrize('reason',['character_list_edge_unknown','character_feedback_unknown','autoplay_input_unknown','native_input_not_sent'])
def test_unknown_or_not_sent_never_becomes_unfindable_replan(monkeypatch,boss,reason):
    error=BatchStopped(reason);team,chars,opt,attempts=setup(monkeypatch,boss=boss,error=error);calls=[]
    if boss:
        def plan(optimizer,pool,context,**kw):
            calls.append(tuple(pool));return SimpleNamespace(teams=(SimpleNamespace(members=tuple(pool[:5])),),safe_count=1,reason='test')
        monkeypatch.setattr(teams,'boss_plan',plan)
    with pytest.raises(BatchStopped,match='^'+reason+'$') as caught:
        (team.prepare_boss if boss else team.prepare)(Batch(),113,30203)
    assert caught.value is error and len(calls if boss else opt.calls)==1
    assert attempts.count(('1003',))==1 and not team.prepared and not team.started


@pytest.mark.parametrize('hold,retry,count,expected_calls',[(True,0,5,1),(False,0,5,2),(True,1,5,2),(True,0,4,2)])
def test_only_requested_ordinary_first_attempt_keeps_full_real_members(monkeypatch,hold,retry,count,expected_calls):
    team,chars,opt,attempts=setup(monkeypatch,retry=retry)
    chars.tabs[1]=['1001','1002','1004','1005','1006'][:count]
    result=team.prepare(Batch(),113,30203,hold_current=hold)
    assert result['team']==['1001','1002','1004','1005','1006'] and len(opt.calls)==expected_calls
    if expected_calls==1:assert not chars.inputs and not attempts
    else:assert ('1003',) in attempts


def test_full_but_unidentified_members_never_use_ordinary_hold(monkeypatch):
    team,chars,opt,_=setup(monkeypatch);chars.tabs[1]=['1001','1002','1004','1005','1006']
    original=chars.view
    def view():
        value=original();value['members'][0]['trusted']=False;return value
    chars.view=view
    with pytest.raises(BatchStopped,match='team_identity_unknown'):team.prepare(Batch(),113,30203,hold_current=True)
    assert not chars.inputs and not team.prepared


@pytest.mark.parametrize('invalid',['outside_owned','no_plan','changed_final'])
def test_ordinary_hold_keeps_inventory_plan_and_final_readback_contract(monkeypatch,invalid):
    team,chars,opt,_=setup(monkeypatch);chars.tabs[1]=['1001','1002','1004','1005','1006']
    if invalid=='outside_owned':
        chars.tabs[1][-1]='1999'
        def edit(batch,enter,block,page,desired,observation):
            assert desired==['1001','1002','1003','1004','1005']
            raise BatchStopped('test_normal_plan_chosen')
        team._edit=edit;reason='test_normal_plan_chosen'
    elif invalid=='no_plan':
        opt.best=lambda *args,**kw:None;reason='team_strategy_unknown'
    else:
        original=chars.read;reads=[]
        def read(*args,**kw):
            reads.append(1)
            if len(reads)==2:chars.tabs[1].remove('1006')
            return original(*args,**kw)
        chars.read=read;reason='team_readback_unknown'
    with pytest.raises(BatchStopped,match='^'+reason+'$'):team.prepare(Batch(),113,30203,hold_current=True)
    assert not chars.inputs and not team.prepared and not team.started
