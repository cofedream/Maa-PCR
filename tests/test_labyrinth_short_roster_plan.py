"""A forced short roster must never be filtered into a terminal dead end.

The labyrinth opening grants exactly three roles, so the first battle has one
possible composition.  The front-line gate can only choose between alternatives;
applying it to that forced roster produced ``team_strategy_unknown`` and ended the
whole batch (2026-10-23 22:10 live run).  The no-anchor penalty already lowers the
score, so a forced roster is ranked and fought instead of being rejected.
"""
from types import SimpleNamespace
import pytest
from pcr_maa.labyrinth import character_recognition as cr
from pcr_maa.labyrinth.roles import Context,load_optimizer
from pcr_maa.labyrinth.team_execution import MaaTeam

OPENING=('1103','1335','1308')      # 增幅者 / 攻击者 / 减益者: no reliable front
ANCHOR='1145'                        # 掩护者, vanguard 65.2


def test_three_role_opening_plans_instead_of_vanishing():
    plan=load_optimizer().best(OPENING,Context(defense=0),battle=True)
    assert plan is not None and set(plan.members)==set(OPENING)


@pytest.mark.parametrize('defense',[0,2,4])
def test_forced_short_roster_plan_is_stable_across_defense_stacks(defense):
    plan=load_optimizer().best(OPENING,Context(defense=defense),battle=True)
    assert plan is not None and set(plan.members)==set(OPENING)


def test_gate_still_filters_when_a_member_can_win_the_front_slot():
    opt=load_optimizer();pool=OPENING+('1025','1060')
    # Nobody in this pool reaches the gate: there is nothing to choose, so it fights.
    assert opt.best(pool,Context(defense=0),battle=True) is not None
    plan=opt.best(pool+(ANCHOR,),Context(defense=0),battle=True)
    assert plan is not None and ANCHOR in plan.members


def test_gate_never_ranks_an_anchorless_team_when_an_anchor_exists():
    opt=load_optimizer();pool=OPENING+('1025','1060',ANCHOR)
    teams=opt.ranked(pool,Context(defense=0),battle=True,limit=8)
    assert teams and all(ANCHOR in team.members for team in teams)


def test_failed_forced_roster_is_still_excluded_on_retry():
    opt=load_optimizer()
    assert opt.retry_best(OPENING,Context(defense=0),failed_teams=(tuple(sorted(OPENING)),),retry_number=1) is None


class Batch:
    settings=SimpleNamespace(strategy=None)
    def checkpoint(self):pass
    def transaction(self,kind,fields,send):return send('actual-test-input')
    def observation(self,value,**fields):return value


class Characters:
    def __init__(self,roster):
        self.roster=roster;self.tab=1;self.tabs={1:[],2:[],3:[]};self.inputs=[]
    def view(self):
        ids=sorted(self.tabs[self.tab])
        return dict(selected=ids,members=[dict(character_id=r,trusted=True) for r in ids],boss_tab=None)
    def read(self,batch,page,*,roster):
        assert tuple(roster)==self.roster
        return self.view(),{'native_ok':True}
    def wait_for(self,batch,page,predicate,*,roster,members_only=False):
        value,receipt=self.read(batch,page,roster=roster)
        assert predicate(value)
        return value,receipt
    def run(self,suffix,params,*,input):
        assert input and suffix=='ToggleOnce' and params['expected_selected']==sorted(self.tabs[self.tab])
        role=params['role_id'];selected=self.tabs[self.tab]
        if role in selected:selected.remove(role)
        else:selected.append(role)
        self.inputs.append((role,role in selected))
        return None,{'native_ok':True}


def test_forced_short_roster_prepares_the_team_instead_of_stopping(monkeypatch):
    chars=Characters(OPENING)
    class Navigation:
        def __init__(self,characters,stamp,*,roster):
            assert characters is chars and tuple(roster)==chars.roster
        def seek(self,batch,enter,page,observation,targets,**kw):
            return chars.view(),[dict(character_id=role,trusted=True) for role in targets]
    monkeypatch.setattr(cr,'CharacterNavigation',Navigation)
    team=MaaTeam(SimpleNamespace(),lambda **v:v,OPENING,optimizer=load_optimizer(),characters=chars)
    result=team.prepare(Batch(),113,30203,Context(defense=0))
    assert result['team']==sorted(OPENING)
    assert team.prepared[(113,30203)]==sorted(OPENING) and team.pages[(113,30203)]=='battle'
    assert {role for role,_ in chars.inputs}==set(OPENING)
