"""Port of upstream role scoring, bounded search and three-card decisions.

The generic scorer preserves measured damage, synergy, survivability and
subjective rating weights. Encounter-specific rules require a validated guide
adapter; no unknown capability is invented. Static data is cached per process.
"""
from dataclasses import dataclass,replace
from functools import lru_cache
from itertools import combinations
from math import prod,isfinite
from pathlib import Path
import json
import heapq

DATA=Path(__file__).resolve().parents[3]/'assets/labyrinth'
def clamp(x,lo=0,hi=100): return max(lo,min(hi,x))
def z(v): return 0 if v is None else v

@dataclass(frozen=True)
class Context:
    defense: int = 0
    targets: int = 1
    enemy: str = 'UNKNOWN'
    boss: bool = False
    prefer_single: bool = False
    recovery: bool = False
    relaxed: bool = False
    effective_ids: tuple[str,...] = ()
    encounter: object = None
    def __post_init__(self):
        if type(self.defense) is not int or self.defense<0 or type(self.targets) is not int or self.targets<1 or self.enemy not in ('PHYSICAL','MAGIC','MIXED','UNKNOWN'):
            raise ValueError('invalid_role_context')

class Profile:
    def __init__(self,v):
        self.data=v
        self.id=v['characterId']
        self.position=v.get('position')
    def val(self,k): return z(self.data.get(k))
    def f(self,k): return z(self.data.get('functions',{}).get(k))
    def s(self,k): return z(self.data.get('support',{}).get(k))
    @property
    def rating(self):
        return next((self.data[k] for k in ('personalScore','userScore','baseQuality') if self.data.get(k) is not None),None)
    @property
    def ready(self):
        v=self.data
        return all(v.get(k) is not None for k in ('roleClass','attribute','damageType','position')) and v.get('dataConfidence',{}).get('support')!='missing' and (v.get('modelStatus','').startswith('database-v') or v.get('physicalDamagePotential') is not None and v.get('magicDamagePotential') is not None)
    def damage(self,system,targets):
        prefix='physical' if system=='PHYSICAL' else 'magic'
        v=self.data.get(prefix+'Damage'+str(min(targets,3))+'Target')
        return self.val(prefix+'DamagePotential') if v is None else v
    def universal(self):
        role=self.data.get('roleClass');physical=self.val('physicalTeamUplift');magic=self.val('magicTeamUplift')
        offense=min(physical,magic)>=.08 or self.s('universalOffense')>=35
        survival=self.s('universalSurvival')>=35
        return (role=='治疗者' and max(self.f('healing'),self.f('regeneration'))>=25
                or role=='掩护者' and (self.f('reliableVanguard')>=45 or survival)
                or role=='增益者' and offense)
    def vanguard(self,c):
        p=self.data.get('vanguardProfile')
        if p is None:
            modeled=self.f('reliableVanguard')*.75+self.f('selfSustain')*.25
            fallback=min(100,45+self.f('selfSustain')*.25+self.s('universalSurvival')*.2) if self.data.get('roleClass')=='掩护者' else 0
            return max(modeled,fallback)
        if p.get('requiresAllyInFront'): return 0
        physical=z(p.get('physicalDurability'));magic=z(p.get('magicDurability'))
        panel=physical if c.enemy=='PHYSICAL' else magic if c.enemy=='MAGIC' else (physical+magic)/2 if c.enemy=='MIXED' else min(physical,magic)
        conventional=clamp(panel*.68+self.f('reliableVanguard')*.17+self.f('selfSustain')*.1+self.s('universalSurvival')*.05)
        evasion=z(p.get('physicalEvasion')) if c.enemy=='PHYSICAL' else z(p.get('magicEvasion')) if c.enemy=='MAGIC' else min(z(p.get('physicalEvasion')),z(p.get('magicEvasion'))) if c.enemy=='MIXED' else 0
        return clamp(conventional+max(z(p.get('untargetable'))*.2,z(p.get('invulnerability'))*.2,evasion*.35,z(p.get('revive'))*.12))

@dataclass(frozen=True)
class Evaluation:
    members: tuple[str,...]
    score: float
    system_score: float
    damage_type: str
    frontline: float
    sustain: float
    player_coverage: float
    active_bonuses: tuple[tuple[str,float],...]

class Optimizer:
    def __init__(self,profiles,config):
        self.profiles=profiles;self.config=config
        if set(config['attributeDamageBonus'])!=set(str(i) for i in range(1,6)): raise ValueError('attribute_bonuses_required')
        if abs(sum(config[k] for k in ('systemDamageWeight','systemSurvivalWeight','systemFunctionWeight','systemFormationWeight','systemCohesionWeight'))-1)>1e-6: raise ValueError('role_weights_invalid')
    def cfg(self,k): return self.config[k]
    def uplift(self,p,system):
        prefix='physical' if system=='PHYSICAL' else 'magic';value=p.data.get(prefix+'TeamUplift')
        if p.data.get('modelStatus','').startswith('database-v') and value is not None:
            return clamp(value,self.cfg('modeledRoleNegativeUpliftFloor'),self.cfg('modeledRolePositiveUpliftCap'))
        return clamp((p.s(prefix+'Offense')+p.s('universalOffense'))/100*self.cfg('supportMultiplierAt100'),0,self.cfg('legacyRoleUpliftCap'))
    @staticmethod
    def damage_type(team):
        kinds={p.data.get('damageType') for p in team if not p.universal()} & {'PHYSICAL','MAGIC'}
        return 'MIXED' if len(kinds)==2 else next(iter(kinds),'NONE')
    @lru_cache(maxsize=32768)
    def score(self,ids,c):
        if not ids or len(ids)>5 or len(set(ids))!=len(ids): raise ValueError('invalid_team')
        team=[self.profiles[x] for x in ids];cfg=self.cfg;t=min(c.targets,3)
        counts={};group={}
        for p in team:
            a=p.data.get('attribute')
            if a is None: continue
            counts[a]=counts.get(a,0)+1;group[a]=group.get(a,0)+p.damage('PHYSICAL',t)+p.damage('MAGIC',t)
        bonuses={a:self.config['attributeDamageBonus'][str(n)] for a,n in counts.items() if self.config['attributeDamageBonus'][str(n)]>0}
        bonuses=dict(sorted(bonuses.items(),key=lambda item:group[item[0]]*item[1],reverse=True)[:cfg('maximumActiveAttributeBonuses')])
        def output(system):
            base=sum(p.damage(system,t)*(1+bonuses.get(p.data.get('attribute'),0)) for p in team)
            uplift=clamp(prod(1+self.uplift(p,system) for p in team)-1,cfg('combinedTeamUpliftFloor'),cfg('combinedTeamUpliftCap'))
            return base*(1+uplift)
        physical,magic=output('PHYSICAL'),output('MAGIC');total=physical+magic;ratio=physical/total if total>0 else 0
        dtype=self.damage_type(team)
        front=min(team,key=lambda p:p.position if p.position is not None else 2**31-1)
        frontline=front.vanguard(c)
        front_for_team=cfg('minimumReliableVanguard')+(frontline-cfg('minimumReliableVanguard'))*cfg('bossExcessVanguardSurvivalRetention') if c.boss and frontline>cfg('minimumReliableVanguard') else frontline
        sustain=max(max(p.f('healing'),p.f('regeneration'),p.s('universalSurvival')*.85) for p in team)
        has_front=frontline>=cfg('minimumReliableVanguard');has_sustain=sustain>=cfg('minimumEffectiveSustain')
        maxf=lambda k:max(p.f(k) for p in team)
        pr,mr=maxf('physicalAttackReduction'),maxf('magicAttackReduction')
        reduction=pr if c.enemy=='PHYSICAL' else mr if c.enemy=='MAGIC' else (pr+mr)/2 if c.enemy=='MIXED' else max(pr,mr)
        survival_support=(sum(p.s('universalSurvival') for p in team)/len(team)+max(max(p.f('healing'),p.f('regeneration')) for p in team)+reduction)/3
        survival=front_for_team*.7+survival_support*.3
        scenario_n=3 if t>=3 else 2
        multi=max(p.data.get('functions',{}).get('threeTargetDamage' if t>=3 else 'twoTargetDamage') if p.data.get('functions',{}).get('threeTargetDamage' if t>=3 else 'twoTargetDamage') is not None else p.damage('PHYSICAL',scenario_n)+p.damage('MAGIC',scenario_n) for p in team)
        pd,md=maxf('physicalDefenseDown'),maxf('magicDefenseDown')
        defense_down=pd if dtype=='PHYSICAL' else md if dtype=='MAGIC' else (pd+md)/2 if dtype=='MIXED' else 0
        scenario=maxf('singleTargetDamage') if t==1 else max(maxf('aoeDamage'),multi)
        function=(scenario+defense_down+maxf('bossMechanismValue'))/3
        missing=sum((not has_front,c.defense<cfg('defenseBaselineStacks') and not has_sustain,t>=2 and multi<cfg('minimumAoe'),t==1 and maxf('singleTargetDamage')<cfg('minimumSingleTarget'),defense_down<cfg('minimumDefenseDown')))
        ratings=[p.rating for p in team if p.rating is not None];coverage=len(ratings)/len(team);player=sum(ratings)/len(ratings) if ratings else None
        pure_penalty=sum(cfg('postFourDefensePureRolePenalty') for p in team if (p.f('pureTank')>=cfg('pureRoleThreshold') or p.f('pureHealer')>=cfg('pureRoleThreshold')) and max(p.f('bossMechanismValue'),p.f('selfSustain'))<cfg('bossSpecialistThreshold')) if c.defense>=cfg('defenseBaselineStacks') else 0
        tanks=healers=0
        for p in team:
            threshold=cfg('compositeOffenseThreshold')
            composite=(p.damage('PHYSICAL',t)+p.damage('MAGIC',t)>=threshold or p.s('universalOffense')>=threshold
                or dtype in ('PHYSICAL','MIXED') and max(p.s('physicalOffense'),p.f('physicalDefenseDown'))>=threshold
                or dtype in ('MAGIC','MIXED') and max(p.s('magicOffense'),p.f('magicDefenseDown'))>=threshold)
            if composite: continue
            tank,healer=p.f('pureTank'),p.f('pureHealer')
            if tank>=cfg('pureRoleThreshold') and tank>=healer: tanks+=1
            elif healer>=cfg('pureRoleThreshold'): healers+=1
        duplicate_factor=1 if c.defense>=cfg('defenseBaselineStacks') else cfg('lowestDefenseDuplicateMultiplier') if c.defense<=1 else cfg('lowDefenseDuplicateMultiplier')
        if c.recovery or not has_front or c.defense<cfg('defenseBaselineStacks') and not has_sustain: duplicate_factor*=cfg('survivalRecoveryDuplicateMultiplier')
        duplicate=(max(0,tanks-1)+max(0,healers-1))*cfg('duplicatePureRolePenalty')*duplicate_factor
        multiplier=1;gate_penalty=0
        if c.defense<=3:
            prefix='lowestDefense' if c.defense<=1 else 'lowDefense'
            if not has_front and not has_sustain: multiplier=cfg(prefix+'NoAnchorMultiplier');gate_penalty=cfg(prefix+'GatePenalty')
            elif not has_front or not has_sustain: multiplier=cfg(prefix+'OneAnchorMultiplier')
        damage=clamp(total/len(team)/cfg('damageSoftCeiling'))
        formation=clamp(100-gate_penalty*cfg('formationSurvivalGatePenaltyMultiplier')-missing*cfg('formationMissingFunctionPenalty')-pure_penalty)
        cohesion=clamp(max(ratio,1-ratio)*100,50,100) if total>0 else 100
        effective=sum(p.id in c.effective_ids for p in team)*cfg('encounterEffectiveCharacterBonus')/len(team)
        from .encounters import contribution
        guide=contribution(team,c.encounter)*cfg('encounterPreferredCapabilityBonus')
        raw=clamp(damage*cfg('systemDamageWeight')+clamp(survival)*cfg('systemSurvivalWeight')+clamp(function)*cfg('systemFunctionWeight')+formation*cfg('systemFormationWeight')+cohesion*cfg('systemCohesionWeight')+effective+guide)
        mixed=cfg('battleMixedDamagePenalty') if c.prefer_single and dtype=='MIXED' else 0
        system=max(0,raw-duplicate-mixed)
        weight=cfg('playerWeight')*coverage if player is not None else 0
        pre=z(player)*weight+raw*(1-weight)
        score=max(0,max(0,pre*multiplier-gate_penalty*cfg('riskGatePenaltyWeight'))-duplicate-mixed)
        return Evaluation(ids,score,system,dtype,frontline,sustain,coverage,tuple(bonuses.items()))
    def priority(self,p,c):
        scenario=p.f('singleTargetDamage') if c.targets==1 else max(p.f('aoeDamage'),p.f('threeTargetDamage' if c.targets>=3 else 'twoTargetDamage'))
        system=(max(p.damage('PHYSICAL',c.targets),p.damage('MAGIC',c.targets))+p.f('reliableVanguard')*.5+clamp(max(p.val('physicalTeamUplift'),p.val('magicTeamUplift'))*100)*.45+max(p.f('physicalDefenseDown'),p.f('magicDefenseDown'))*.3+scenario*.2)/2.45
        if p.id in c.effective_ids: system+=self.cfg('encounterEffectiveCharacterBonus')
        if c.encounter is not None:
            from .encounters import capability,contribution
            system+=(sum((capability(p,kind) or 0)>=50 for kind,_ in c.encounter.preferred)
                +contribution((p,),replace(c.encounter,preferred=(),fallback=())))*self.cfg('encounterPreferredCapabilityBonus')
        weight=self.cfg('playerWeight') if p.rating is not None else 0
        return z(p.rating)*weight+clamp(system)*(1-weight)
    def eligible(self,team,c):
        if any(p.position is None for p in team): return False
        pos=min(p.position for p in team);gate=self.cfg('relaxedMinimumReliableVanguard' if c.relaxed else 'minimumReliableVanguard')
        return all(p.vanguard(c)>=gate for p in team if p.position==pos)
    def ranked(self,ids,c,*,required=(),battle=False,system_only=False,limit=1,cohesive=False,excluded=()):
        ids=tuple(dict.fromkeys(ids))
        if not ids or any(x not in self.profiles for x in ids): return ()
        if not set(required)<=set(ids): return ()
        team=[self.profiles[x] for x in ids]
        if battle:team=[p for p in team if p.position is not None]
        if not team or not set(required)<={p.id for p in team}:return ()
        size=min(5,len(team));bound=self.cfg('exhaustiveRosterLimit')
        if len(team)>bound:
            ordered=sorted(team,key=lambda p:self.priority(p,c),reverse=True)
            forced=[]
            if battle:
                gate=self.cfg('relaxedMinimumReliableVanguard' if c.relaxed else 'minimumReliableVanguard')
                eligible=[p for p in team if p.position is not None and p.vanguard(c)>=gate]
                if not eligible:return ()
                front=min(eligible,key=lambda p:(p.position,p.id))
                compatible=[p for p in ordered if p!=front and p.position is not None and (p.vanguard(c)>=gate or p.position>front.position)][:size-1]
                if len(compatible)!=size-1:return ()
                forced=[front]+compatible
            forced.extend(self.profiles[x] for x in required)
            team=list(dict.fromkeys(forced+ordered))[:bound]
        excluded={tuple(sorted(group)) for group in excluded}
        ranked=[]
        for ordinal,combo in enumerate(combinations(team,size)):
            keys=tuple(p.id for p in combo)
            if tuple(sorted(keys)) in excluded or not set(required)<=set(keys) or battle and not self.eligible(combo,c):continue
            if cohesive and self.damage_type(combo)=='MIXED':continue
            evaluation=self.score(keys,c)
            item=(evaluation.system_score if system_only else evaluation.score,-ordinal,evaluation)
            if len(ranked)<limit: heapq.heappush(ranked,item)
            elif item[:2]>ranked[0][:2]: heapq.heapreplace(ranked,item)
        ranked.sort(key=lambda v:v[:2],reverse=True)
        return tuple(v[2] for v in ranked)
    def best(self,ids,c,*,required=(),battle=False,system_only=False,excluded=()):
        ids=tuple(dict.fromkeys(ids))
        if not ids or any(uid not in self.profiles for uid in ids):return None
        if battle and c.encounter is not None and not required and not excluded:
            from .encounters import guide_core
            required=guide_core(self,tuple(dict.fromkeys(ids)),c)
        if battle and c.prefer_single:
            found=self.ranked(ids,c,required=required,battle=True,system_only=system_only,cohesive=True,excluded=excluded)
            if found:return found[0]
        found=self.ranked(ids,c,required=required,battle=battle,system_only=system_only,excluded=excluded)
        return found[0] if found else None

    def retry_best(self,ids,c,*,failed_teams=(),last_failed_team=None,retry_number=1):
        """Known defeat selects a distinct team; EX second failure may swap in healing."""
        ids=tuple(dict.fromkeys(ids));excluded={tuple(sorted(team)) for team in failed_teams}
        recovery=replace(c,recovery=True)
        if c.encounter is not None and retry_number>=2 and last_failed_team and len(last_failed_team)==5:
            if set(last_failed_team)<=set(ids) and all(uid in self.profiles for uid in ids):
                failed=[self.profiles[uid] for uid in last_failed_team]
                gate=self.cfg('relaxedMinimumReliableVanguard' if c.relaxed else 'minimumReliableVanguard')
                removable=sorted((p for p in failed if p.vanguard(c)<gate),
                    key=lambda p:(p.id in c.effective_ids,p.rating if p.rating is not None else float('-inf'),p.id))
                strength=lambda p:max(p.f('healing'),p.f('regeneration'),p.f('pureHealer'))
                healers=sorted((self.profiles[uid] for uid in ids if uid not in last_failed_team and
                    (self.profiles[uid].data.get('roleClass')=='治疗者' or strength(self.profiles[uid])>=50)),
                    key=lambda p:(p.data.get('roleClass')=='治疗者',strength(p),p.rating or 0,p.id),reverse=True)
                for removed in removable:
                    for healer in healers:
                        candidate=tuple(p.id for p in failed if p!=removed)+(healer.id,)
                        plan=self.best(candidate,recovery,battle=True,excluded=excluded)
                        if plan is not None and len(plan.members)==5:return plan
        return self.best(ids,recovery,battle=True,excluded=excluded)

    def choose_role(self,candidates,acquired,c,weights=(1,.35,.2)):
        if not candidates or len(set(candidates))!=len(candidates):return None
        owned=tuple(x for x in acquired if x not in candidates)
        if any(x not in self.profiles for x in set(owned)|set(candidates)):return None
        rankings=[]
        for candidate in candidates:
            pool=tuple(dict.fromkeys(owned+(candidate,)));plans=[]
            for placement in range(len(weights)):
                if len(pool)-1<placement*5:continue
                remaining=pool;teams=[]
                for layer in range(len(weights)):
                    if layer>0 and len(remaining)<5:continue
                    team_pool=tuple(x for x in remaining if x!=candidate) if layer<placement else remaining
                    if layer<placement and len(team_pool)<5:break
                    required=(candidate,) if layer==placement else ()
                    team=self.best(team_pool,c,required=required,system_only=True)
                    if team is None:break
                    teams.append(team);remaining=tuple(x for x in remaining if x not in team.members)
                if len(teams)<=placement or candidate not in teams[placement].members:continue
                system=sum(team.system_score*weights[i] for i,team in enumerate(teams))/sum(weights[:len(teams)])
                plans.append((system,-placement,tuple(teams)))
            if not plans:return None
            best=max(plans,key=lambda v:v[:2]);p=self.profiles[candidate];weight=self.cfg('playerWeight') if p.rating is not None else 0
            rankings.append(dict(character_id=candidate,score=z(p.rating)*weight+best[0]*(1-weight),system_score=best[0],placement=-best[1],teams=best[2]))
        rankings.sort(key=lambda v:(v['score'],z(self.profiles[v['character_id']].rating)),reverse=True)
        return tuple(rankings)

@lru_cache(maxsize=1)
def load_optimizer():
    raw=json.loads((DATA/'labyrinth-role-decision.json').read_text(encoding='utf-8'))
    defaults=json.loads((DATA/'scoring-defaults.json').read_text(encoding='utf-8'));defaults.update(raw['scoring'])
    profiles={r['characterId']:Profile(r) for r in raw['characters']}
    # Upstream withKnownRoleIdentities: a catalog identity can participate with
    # only existing facts. It never acquires a synthetic attribute/position/T.
    catalog=DATA/'character-pack/characters.landosolroster.json'
    if catalog.is_file():
        for person in json.loads(catalog.read_text(encoding='utf-8'))['characters']:
            key=person['id']
            if key not in profiles and key!='1000':
                profiles[key]=Profile(dict(characterId=key,displayName=person.get('officialName') or person['name']))
    return Optimizer(profiles,defaults)


def strategy_value(strategy,key,default=None):
    return strategy.get(key,default) if isinstance(strategy,dict) else getattr(strategy,key,default)


def configured_optimizer(strategy=None):
    """Copy configuration/personal votes without changing shared catalog facts."""
    import copy
    base=load_optimizer();config=copy.deepcopy(base.config)
    overrides=strategy_value(strategy,'scoring_overrides',{})
    scores=strategy_value(strategy,'personal_scores',{})
    try:overrides,scores=dict(overrides),dict(scores)
    except (TypeError,ValueError):raise ValueError('role_strategy_invalid') from None
    for key,value in overrides.items():
        if key not in ('systemDamageWeight','systemSurvivalWeight','systemFunctionWeight','systemFormationWeight','systemCohesionWeight') or type(value) not in (int,float) or not isfinite(value) or not 0<=value<=1:raise ValueError('role_strategy_invalid')
        config[key]=value
    settings=(('player_weight','playerWeight',70,100),('duplicate_role_penalty','duplicatePureRolePenalty',8,1),
        ('mixed_damage_penalty','battleMixedDamagePenalty',15,1),('effective_character_system_bonus','encounterEffectiveCharacterBonus',8,1))
    for field,key,default,divisor in settings:
        value=strategy_value(strategy,field,default)
        if type(value) not in (int,float) or not isfinite(value) or not 0<=value<=(100 if divisor==100 else 30):raise ValueError('role_strategy_invalid')
        config[key]=value/divisor
    enabled=strategy_value(strategy,'attribute_pairing_bonus_enabled',True)
    if type(enabled) is not bool:raise ValueError('role_strategy_invalid')
    if not enabled:config['attributeDamageBonus']={key:0.0 for key in config['attributeDamageBonus']}
    profiles={key:Profile(copy.deepcopy(p.data)) for key,p in base.profiles.items()}
    for key,value in scores.items():
        if key not in profiles or type(value) not in (int,float) or not isfinite(value) or not 0<=value<=100:raise ValueError('role_personal_score_invalid')
        profiles[key].data['personalScore']=value
    return Optimizer(profiles,config)
