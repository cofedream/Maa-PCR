"""CC BY-NC-SA 4.0 port of LabyrinthEventChoicePlanner's utility model.

Recommendations never authorize input by themselves: a stable event identity,
current enabled button, and fresh result are required by the Maa executor.
"""
from collections import OrderedDict
from dataclasses import dataclass
from .strategies import MARKS,relic_focus

ROLE_CLASSES=('掩护者','治疗者','增幅者','增益者','减益者','妨碍者','攻击者','破防者')
ROLE_OPTIONS=dict(zip((11011,11012,11021,11022,11031,11032,11041,11042),ROLE_CLASSES))
BATTLES={11051:(True,200),11052:(False,300),11091:(True,300),11092:(False,450),
         11131:(True,400),11132:(False,600),11171:(True,500),11172:(False,750)}
CONTINUATIONS={2314:(23141,),3314:(33141,),2315:(23152,23151),3315:(33152,33151),
               2316:(23162,23161),3316:(33162,33161)}

@dataclass(frozen=True)
class Choice:
    choice_id: int
    slot: int
    rect: tuple[int,int,int,int]
    enabled_confidence: float
    def __post_init__(self):
        if (type(self.choice_id) is not int or type(self.slot) is not int or self.slot<0
                or len(self.rect)!=4 or any(type(v) is not int or v<0 for v in self.rect)
                or self.rect[2]<=0 or self.rect[3]<=0 or not 0<=self.enabled_confidence<=1):
            raise ValueError('invalid_event_button')

@dataclass(frozen=True)
class Decision:
    choice: Choice
    utility: float
    action_safe: bool
    reason: str

class Planner:
    def __init__(self,optimizer,*,normal_win_rate=95,extreme_win_rate=70,area=None,locked_focus=None,baseline_last_area=3,baseline_target=4,debuff_pivot_minimum=12,debuff_pivot_tolerance=2,priority=()):
        if any(type(v) is not int or not 0<=v<=100 for v in (normal_win_rate,extreme_win_rate)):
            raise ValueError('invalid_event_win_rate')
        self.optimizer=optimizer;self.normal=normal_win_rate/100;self.extreme=extreme_win_rate/100
        self.cache=OrderedDict();self.area=area;self.locked_focus=locked_focus;self.baseline_last_area=baseline_last_area;self.baseline_target=baseline_target
        self.priority=tuple(priority);self.focus_policy=dict(debuff_pivot_minimum=debuff_pivot_minimum,debuff_pivot_tolerance=debuff_pivot_tolerance)

    def relic_utility(self,mark,rarity,stacks):
        stacks={m:stacks.get(m,0) for m in MARKS}
        baseline=all(stacks[m]>=self.baseline_target for m in MARKS[:3]) or self.area is not None and self.area>self.baseline_last_area
        focus,_=relic_focus(stacks,locked_focus=self.locked_focus,priority=self.priority,**self.focus_policy)
        priority_bonus=(len(self.priority)-self.priority.index(mark))*1.2 if mark in self.priority else 0
        current=stacks[mark];value=0
        for _ in range(rarity):
            value+=8 if not baseline and mark in MARKS[:3] and current<self.baseline_target else 2.5 if not baseline else 6 if mark==focus else 3
            if current<15:value+=priority_bonus
            if current<15<=current+1:value+=16 if mark=='弱体' else 10
            current+=1
        return value

    def random_relic(self,rarity,stacks):
        return sum(self.relic_utility(m,rarity,stacks) for m in MARKS)/len(MARKS)
    def coins(self,amount,stacks):
        return amount/1800*max(2.5,max(self.relic_utility(m,1,stacks) for m in MARKS))

    def margins(self,owned,context):
        from copy import deepcopy
        from functools import lru_cache
        from .roles import Profile
        optimizer=deepcopy(self.optimizer)
        # Reuse facts only inside this immutable search; live profiles remain uncached.
        for p in optimizer.profiles.values():
            if isinstance(p,Profile):
                for name in ('f','s','damage','universal','vanguard'):
                    setattr(p,name,lru_cache(maxsize=None)(getattr(p,name)))
        profiles=optimizer.profiles
        pool=tuple(p.id for p in sorted((profiles[x] for x in owned if x in profiles),
                    key=lambda p:(p.rating or 0)+p.damage('PHYSICAL',context.targets)+p.damage('MAGIC',context.targets)+p.f('reliableVanguard')*.5+p.f('physicalDefenseDown')*.3+p.f('magicDefenseDown')*.3,reverse=True)[:12])
        baseline=optimizer.best(pool,context);before=baseline.score if baseline else 0
        values={}
        for uid,p in profiles.items():
            if uid in owned or p.data.get('roleClass') not in ROLE_CLASSES:continue
            forced=optimizer.best(pool+(uid,),context,required=(uid,))
            # Existing size-five candidate teams either keep the baseline or contain uid.
            after=forced.score if forced else 0
            first=max(before,after) if len(pool)>=5 else after
            values[uid]=max(0,first+.35*after-1.35*before)
        return values

    def utilities(self,ids,owned,context,stacks):
        # Compute expensive roster margins only when an event can award roles.
        role_ids=set(ROLE_OPTIONS)|{11072,11081,11122,13151,23152,23151,33152,33151}
        margins=self.margins(owned,context) if set(ids)&role_ids else {}
        pools={role:sorted((v for uid,v in margins.items() if self.optimizer.profiles[uid].data.get('roleClass')==role),reverse=True)
               for role in ROLE_CLASSES}
        all_values=sorted(margins.values(),reverse=True)
        average=lambda xs:sum(xs)/len(xs) if xs else 0
        random_roles=lambda count:count*average([average(v) for v in pools.values() if v])
        class_select=lambda count:average([sum(v[:count]) for v in pools.values() if v])
        coin=lambda n:self.coins(n,stacks)
        relic=lambda n:self.random_relic(n,stacks)
        def continuation(event,visiting):
            if event in visiting:return 0
            return max((utility(x,visiting|{event}) for x in CONTINUATIONS.get(event,())),default=0)
        def utility(uid,visiting=frozenset()):
            if uid in ROLE_OPTIONS:return average(pools[ROLE_OPTIONS[uid]])*2+coin(200)
            if uid in BATTLES:
                normal,reward=BATTLES[uid];return coin(reward)*(self.normal if normal else self.extreme)
            if uid==11073:return coin(1750)
            if uid==11072:return .5*class_select(1)+.5*sum(all_values[:1])
            if uid==11071:return .5*relic(2)+.5*relic(3)
            if uid==11082:return coin(1000)
            if uid==11081:return .5*relic(1)+.5*random_roles(2)
            if uid in (11101,11102,11103):return coin(5000//3)
            if uid in (11111,11121):return coin(2000)
            if uid==11112:return sum(relic(n) for n in (1,2,3))/3
            if uid==11122:
                specific=max((v for x,v in margins.items() if self.optimizer.profiles[x].data.get('displayName')=='纯'),default=0)
                return specific+average(all_values)*3
            if uid==13141:return .5*(relic(1)+continuation(2314,visiting))+.5*coin(500)
            if uid==23141:return .25*(relic(2)+continuation(3314,visiting))+.75*coin(500)
            if uid==33141:return .1*relic(3)+.9*coin(500)
            if uid==13151:return .5*continuation(2315,visiting)+.5*coin(500)
            if uid==23152:return random_roles(2)
            if uid==23151:return .5*continuation(3315,visiting)+.5*coin(500)
            if uid==33152:return class_select(2)
            if uid==33151:return .5*sum(all_values[:2])+.5*coin(500)
            if uid==13161:return .5*(coin(2000)+continuation(2316,visiting))+.5*coin(500)
            if uid in (23162,33162):return 0
            if uid==23161:return .5*(coin(4000)+continuation(3316,visiting))+.5*coin(500)
            if uid==33161:return .5*coin(8000)+.5*coin(500)
            return 0
        return {x:utility(x) for x in ids}

    def decide(self,event_id,choices,owned,context,stacks,*,trusted=False,rejected=frozenset()):
        if not choices or len({c.choice_id for c in choices})!=len(choices):return None
        if set(stacks)-set(MARKS) or any(type(v) is not int or v<0 for v in stacks.values()):
            raise ValueError('invalid_relic_stacks')
        owned=frozenset(owned);ids=tuple(c.choice_id for c in choices)
        key=(event_id,ids,tuple(sorted(owned)),context,tuple(stacks.get(m,0) for m in MARKS))
        if key not in self.cache:
            self.cache[key]=self.utilities(ids,owned,context,stacks)
            if len(self.cache)>12:self.cache.popitem(last=False)
        self.cache.move_to_end(key);values=self.cache[key]
        choices=[c for c in choices if c.choice_id not in rejected]
        if not choices:return None
        ranked=sorted(choices,key=lambda c:(-values[c.choice_id],c.slot))
        choice=next((c for c in ranked if c.enabled_confidence>=.35),ranked[0])
        missing=any(x not in self.optimizer.profiles for x in owned)
        reason=('event_unstable' if trusted is not True else 'button_disabled' if choice.enabled_confidence<.35
                else 'owned_profile_missing' if choice.choice_id in ROLE_OPTIONS and missing else 'ready')
        return Decision(choice,values[choice.choice_id],reason=='ready',reason)
