import unittest
from dataclasses import replace
from pcr_maa.labyrinth.roles import Profile,Context,Optimizer,load_optimizer

def profile(uid,position,damage='PHYSICAL',*,rating=70,attribute='FIRE',tank=False):
    return Profile(dict(characterId=uid,displayName=uid,roleClass='掩护者' if tank else '攻击者',attribute=attribute,damageType=damage,
        position=position,userScore=rating,modelStatus='database-v2',physicalTeamUplift=0,magicTeamUplift=0,
        physicalDamage1Target=60 if damage=='PHYSICAL' else 0,magicDamage1Target=60 if damage=='MAGIC' else 0,
        physicalDamagePotential=60 if damage=='PHYSICAL' else 0,magicDamagePotential=60 if damage=='MAGIC' else 0,
        dataConfidence=dict(support='present'),support=dict(universalSurvival=40 if tank else 0),
        functions=dict(reliableVanguard=90 if tank else 0,selfSustain=80 if tank else 0,healing=0,singleTargetDamage=60,physicalDefenseDown=40,magicDefenseDown=40)))

class RoleTests(unittest.TestCase):
 def optimizer(self,roles): return Optimizer({p.id:p for p in roles},dict(load_optimizer().config))
 def test_static_data_cached_unknown_not_decision_ready(self):
  a=load_optimizer();self.assertIs(a,load_optimizer());self.assertFalse(a.profiles['1000'].ready)
 def test_damage_system_is_member_fact(self):
  a=self.optimizer([profile('p',1),profile('m',2,'MAGIC')]);e=a.score(('p','m'),Context(defense=4))
  self.assertEqual(e.damage_type,'MIXED')
 def test_front_position_gate_and_equal_position_conservatism(self):
  roles=[profile('tank',200,tank=True),profile('glass',100)]+[profile(str(i),300+i) for i in range(4)]
  a=self.optimizer(roles);best=a.best(tuple(p.id for p in roles),Context(),battle=True)
  self.assertIsNotNone(best);self.assertNotIn('glass',best.members)
  roles[1].position=200;best=self.optimizer(roles).best(tuple(p.id for p in roles),Context(),battle=True)
  self.assertNotIn('glass',best.members)
 def test_attribute_damage_group_limited_to_two(self):
  roles=[profile('a',1,attribute='FIRE'),profile('b',2,attribute='FIRE'),profile('c',3,attribute='WATER'),profile('d',4,attribute='WATER'),profile('e',5,attribute='WIND')]
  e=self.optimizer(roles).score(tuple(p.id for p in roles),Context(defense=4))
  self.assertEqual(dict(e.active_bonuses),{'FIRE':.08,'WATER':.08})
 def test_unknown_rating_does_not_become_zero_player_vote(self):
  roles=[profile('a',1,rating=None,tank=True),profile('b',2,rating=0)]
  e=self.optimizer(roles).score(('a','b'),Context(defense=4))
  self.assertEqual(e.player_coverage,.5)
  all_rated=self.optimizer([profile('a',1,rating=0,tank=True),profile('b',2,rating=0)]).score(('a','b'),Context(defense=4))
  self.assertGreater(e.score,all_rated.score)
 def test_choose_role_refuses_missing_profiles(self):
  roles=[profile('a',1,tank=True),profile('b',2),profile('c',3)]
  a=self.optimizer(roles)
  self.assertIsNone(a.choose_role(('b','missing'),('a',),Context()))
  rankings=a.choose_role(('b','c'),('a',),Context())
  self.assertEqual(rankings[0]['character_id'],'b')
 def test_bounded_search_keeps_valid_low_rating_tank(self):
  roles=[profile('tank',100,rating=1,tank=True)]+[profile(str(i),200+i,rating=100) for i in range(9)]
  a=self.optimizer(roles);a.config['exhaustiveRosterLimit']=5
  result=a.best(tuple(p.id for p in roles),Context(defense=4),battle=True)
  self.assertIsNotNone(result);self.assertIn('tank',result.members)

if __name__=='__main__':unittest.main()


import pytest
from pcr_maa.labyrinth.roles import configured_optimizer,Optimizer
from pcr_maa.labyrinth.events import ROLE_CLASSES,Planner

@pytest.mark.parametrize('size,defense,player_weight',[(0,0,70),(1,1,0),(4,3,100),(5,4,70),(12,5,70)])
def test_role_margins_matches_original_exhaustive_scores(size,defense,player_weight):
    base=configured_optimizer(dict(player_weight=player_weight))
    profiles=dict(list((uid,p) for uid,p in base.profiles.items() if p.ready and p.data.get('roleClass') in ROLE_CLASSES)[:14])
    optimizer=Optimizer(profiles,base.config);planner=Planner(optimizer);context=Context(defense=defense,prefer_single=True)
    owned=tuple(profiles)[:size]
    pool=tuple(p.id for p in sorted((profiles[x] for x in owned),key=lambda p:(p.rating or 0)+p.damage('PHYSICAL',context.targets)+p.damage('MAGIC',context.targets)+p.f('reliableVanguard')*.5+p.f('physicalDefenseDown')*.3+p.f('magicDefenseDown')*.3,reverse=True)[:12])
    baseline=optimizer.best(pool,context);before=baseline.score if baseline else 0;expected={}
    for uid in profiles:
        if uid in owned:continue
        candidate=pool+(uid,);first=optimizer.best(candidate,context);forced=optimizer.best(candidate,context,required=(uid,))
        expected[uid]=max(0,(first.score if first else 0)+.35*(forced.score if forced else 0)-1.35*before)
    assert planner.margins(owned,context)==expected


def test_role_margins_reuses_baseline_without_repeating_unforced_search():
    from types import SimpleNamespace as NS
    profile=NS(id='new',rating=0,data={'roleClass':'攻击者'},damage=lambda *a:0,f=lambda *a:0)
    calls=[]
    def best(ids,context,**kw):
        calls.append((ids,kw.get('required',())))
        return NS(score=10) if ids else None
    planner=Planner(NS(profiles={'new':profile},best=best))
    assert planner.margins((),Context())=={'new':13.5}
    assert calls==[((),()),(('new',),('new',))]


def test_event_margin_fact_cache_is_local_and_preserves_live_profile_reads():
    base=configured_optimizer()
    profiles=dict(list((uid,p) for uid,p in base.profiles.items() if p.ready and p.data.get('roleClass') in ROLE_CLASSES)[:7])
    optimizer=Optimizer(profiles,base.config);planner=Planner(optimizer)
    owned=tuple(profiles)[:5];context=Context(defense=4)
    planner.margins(owned,context)
    assert all(not any(name in p.__dict__ for name in ('f','s','damage','universal','vanguard')) for p in profiles.values())
    p=profiles[owned[0]];p.data['functions']['physicalDefenseDown']=91
    assert p.f('physicalDefenseDown')==91
    # A new calculation sees changed source facts, without retaining the temporary caches.
    optimizer.score.cache_clear()
    actual=planner.margins(owned,context)
    assert actual==Planner(Optimizer({uid:Profile(dict(p.data)) for uid,p in profiles.items()},dict(base.config))).margins(owned,context)


def test_event_margin_reads_each_static_function_once_per_snapshot(monkeypatch):
    from collections import Counter
    roles=[profile(str(i),100+i,tank=i==0) for i in range(14)]
    optimizer=Optimizer({p.id:p for p in roles},dict(load_optimizer().config))
    calls=Counter();original=Profile.f
    def counted(self,key):
        calls[(self.id,key)]+=1
        return original(self,key)
    monkeypatch.setattr(Profile,'f',counted)
    Planner(optimizer).margins(tuple(p.id for p in roles[:12]),Context(defense=4))
    assert calls and max(calls.values())==1
