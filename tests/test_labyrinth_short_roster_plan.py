"""A forced short roster must never be filtered into a terminal dead end.

The labyrinth opening grants exactly three roles, so the first battle has one
possible composition.  The front-line gate can only choose between alternatives;
applying it to that forced roster produced ``team_strategy_unknown`` and ended the
whole batch (2026-10-23 22:10 live run).  The no-anchor penalty already lowers the
score, so a forced roster is ranked and fought instead of being rejected.

A stronger pool can hit the same dead end: it contains a gate-reaching front role,
yet every five-member combination still fails because a fragile or unmodelled role
takes the front slot.  That pool also fights its best available team; an invested
middle/back line can clear the fight (2026-11-02 live run, six-role pool whose only
gate-valid team needed an unidentifiable card).
"""
from types import SimpleNamespace
import pytest
from pcr_maa.labyrinth import character_recognition as cr
from pcr_maa.labyrinth.roles import Context,Optimizer,Profile,load_optimizer
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


def synthetic_profile(uid,position,*,tank=False,damage='PHYSICAL',front=0):
    magic=damage=='MAGIC'
    return Profile(dict(characterId=uid,displayName=uid,roleClass='掩护者' if tank else '攻击者',
        attribute='FIRE',damageType=damage,position=position,userScore=60,modelStatus='database-v2',
        physicalTeamUplift=0,magicTeamUplift=0,physicalDamage1Target=0 if magic else 60,
        magicDamage1Target=60 if magic else 0,physicalDamagePotential=0 if magic else 60,
        magicDamagePotential=60 if magic else 0,dataConfidence=dict(support='present'),
        support=dict(universalSurvival=40 if tank else 0),
        functions=dict(reliableVanguard=90 if tank else front,selfSustain=80 if tank else front,
            healing=0,singleTargetDamage=60,physicalDefenseDown=40,magicDefenseDown=40)))


# Every five-member combination here keeps a fragile role in the front slot, so
# the gate rejects all of them even though one tank reaches the threshold.
DEAD_END_ROLES=[synthetic_profile('glass',100),synthetic_profile('frail',200),
    synthetic_profile('tank',300,tank=True),synthetic_profile('dps0',400),
    synthetic_profile('dps1',500),synthetic_profile('dps2',600)]


def dead_end_optimizer(roles=DEAD_END_ROLES):
    return Optimizer({p.id:p for p in roles},dict(load_optimizer().config))


def test_gate_dead_end_still_ranks_the_best_available_team():
    optimizer=dead_end_optimizer()
    plan=optimizer.best(tuple(p.id for p in DEAD_END_ROLES),Context(defense=0),battle=True)
    assert plan is not None and len(plan.members)==5
    assert optimizer.ranked(tuple(p.id for p in DEAD_END_ROLES),Context(defense=0),battle=True)


def test_gate_dead_end_still_prefers_a_gate_valid_alternative():
    roles=[*DEAD_END_ROLES,synthetic_profile('anchor',700,tank=True)]
    plan=dead_end_optimizer(roles).best(tuple(p.id for p in roles),Context(defense=0),battle=True)
    assert plan is not None and 'anchor' in plan.members


def test_gate_dead_end_prepares_and_fights_instead_of_stopping(monkeypatch):
    roster=tuple(p.id for p in DEAD_END_ROLES);chars=Characters(roster)
    class Navigation:
        def __init__(self,characters,stamp,*,roster):
            assert characters is chars and tuple(roster)==chars.roster
        def seek(self,batch,enter,page,observation,targets,**kw):
            return chars.view(),[dict(character_id=role,trusted=True) for role in targets]
    monkeypatch.setattr(cr,'CharacterNavigation',Navigation)
    team=MaaTeam(SimpleNamespace(),lambda **v:v,roster,optimizer=dead_end_optimizer(),characters=chars)
    result=team.prepare(Batch(),113,30203,Context(defense=0))
    assert len(result['team'])==5 and set(result['team'])<=set(roster)
    assert team.prepared[(113,30203)]==sorted(result['team']) and team.pages[(113,30203)]=='battle'
    assert len(chars.inputs)==5


# prefer_single must not buy a pure damage system with the front line.  The only
# gate-reaching role here is the magic front, so every gate-valid team is mixed;
# the relaxed pure-system pass answered first and returned frontline 0 (a team
# that fails the very gate the relaxation dropped).  2026-11-04 review.
PREFER_SINGLE_ROLES=[synthetic_profile(f'dps{i}',2+i) for i in range(5)]+[
    synthetic_profile('magic_front',1,damage='MAGIC',front=100)]


def test_prefer_single_keeps_the_reachable_front_instead_of_a_pure_system():
    roles=PREFER_SINGLE_ROLES;optimizer=dead_end_optimizer(roles);context=Context(prefer_single=True)
    plan=optimizer.best(tuple(p.id for p in roles),context,battle=True)
    assert plan is not None and 'magic_front' in plan.members
    assert optimizer.eligible(tuple(optimizer.profiles[uid] for uid in plan.members),context)


def test_prefer_single_still_fights_a_pool_where_no_team_passes_the_gate():
    # The same relaxation must keep working where nothing can pass the gate: with
    # prefer_single the mixed search now owns it, so the batch still fights.
    roles=[*DEAD_END_ROLES,synthetic_profile('magic_front',700,damage='MAGIC',front=100)]
    optimizer=dead_end_optimizer(roles);context=Context(prefer_single=True)
    plan=optimizer.best(tuple(p.id for p in roles),context,battle=True)
    assert plan is not None and len(plan.members)==5
