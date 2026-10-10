"""Independent, data-only configuration; credentials are never persisted."""
from dataclasses import dataclass, field
from types import MappingProxyType
import math,re
from .routes import Policy
from .strategies import MARKS, ALIASES

@dataclass(frozen=True)
class Goal:
    guild_id: int
    clears: int
    mode: str = 'clears'

@dataclass(frozen=True)
class Strategy:
    boss_mode: str = 'multi'
    deployment_weights: tuple[float,...] = (1.0, .35, .2)
    scoring_overrides: object = field(default_factory=lambda:MappingProxyType({}))
    personal_scores: object = field(default_factory=lambda:MappingProxyType({}))

    player_weight: int = 70
    attribute_pairing_bonus_enabled: bool = True
    duplicate_role_penalty: int = 8
    mixed_damage_penalty: int = 15
    effective_character_system_bonus: int = 8
    prefer_pure_boss_damage_system: bool = True
    single_boss_fallback_to_multi_after_three_failures: bool = True
    single_boss_retry_count_before_multi: int = 2
    reroll_after_three_battle_failures: bool = True
    baseline_last_area: int = 3
    baseline_target: int = 4
    debuff_pivot_minimum: int = 12
    debuff_pivot_tolerance: int = 2
    relic_mark_priority: tuple[str,...] = ()
    refresh_from_area: int = 5
    normal_win_rate: int = 95
    extreme_win_rate: int = 70
    opening_rosters: object = field(default_factory=lambda:MappingProxyType({}))

    def payload(self):
        values={key:getattr(self,key) for key in self.__dataclass_fields__}
        values.update(deployment_weights=list(self.deployment_weights),
            scoring_overrides=dict(self.scoring_overrides),personal_scores=dict(self.personal_scores),
            relic_mark_priority=list(self.relic_mark_priority),
            opening_rosters={key:[list(slot) for slot in slots] for key,slots in self.opening_rosters.items()})
        return values

    @classmethod
    def parse(cls,value):
        allowed=set(cls.__dataclass_fields__)
        if type(value) is not dict or set(value)-allowed:raise ValueError('invalid_strategy')
        mode=value.get('boss_mode','multi')
        if mode not in ('multi','single'):raise ValueError('invalid_boss_mode')
        weights=value.get('deployment_weights',[1.0,.35,.2])
        def number(x,lo,hi):
            return type(x) in (int,float) and math.isfinite(x) and lo<=x<=hi
        if (type(weights) is not list or len(weights)!=3
                or any(not number(x,0,10) for x in weights) or weights[0]<=0):
            raise ValueError('invalid_deployment_weights')
        defaults=dict(systemDamageWeight=.5,systemSurvivalWeight=.23,systemFunctionWeight=.13,
                      systemFormationWeight=.04,systemCohesionWeight=.1)
        scores=value.get('scoring_overrides',{})
        if (type(scores) is not dict or set(scores)-set(defaults)
                or any(not number(x,0,1) for x in scores.values())
                or abs(sum(dict(defaults,**scores).values())-1)>1e-6):
            raise ValueError('invalid_scoring_weights')
        personal=value.get('personal_scores',{})
        if (type(personal) is not dict or len(personal)>1000
                or any(type(k) is not str or re.fullmatch(r'[1-9][0-9]{3,5}',k) is None
                       or not number(x,0,100) for k,x in personal.items())):
            raise ValueError('invalid_personal_scores')
        defaults=cls();extra={}
        for key,lo,hi in (('player_weight',0,100),('duplicate_role_penalty',0,30),
                ('mixed_damage_penalty',0,30),('effective_character_system_bonus',0,30),
                ('single_boss_retry_count_before_multi',0,10),('baseline_last_area',0,5),
                ('baseline_target',1,4),('debuff_pivot_minimum',5,15),('debuff_pivot_tolerance',0,5),
                ('refresh_from_area',1,5),('normal_win_rate',0,100),('extreme_win_rate',0,100)):
            item=value.get(key,getattr(defaults,key))
            if type(item) is not int or not lo<=item<=hi:raise ValueError('invalid_'+key)
            extra[key]=item
        for key in ('attribute_pairing_bonus_enabled','prefer_pure_boss_damage_system',
                'single_boss_fallback_to_multi_after_three_failures','reroll_after_three_battle_failures'):
            item=value.get(key,getattr(defaults,key))
            if type(item) is not bool:raise ValueError('invalid_'+key)
            extra[key]=item
        priority=value.get('relic_mark_priority',[])
        if type(priority) is not list or any(type(mark) is not str for mark in priority):raise ValueError('invalid_relic_mark_priority')
        priority=tuple(ALIASES.get(mark,mark) for mark in priority)
        if len(set(priority))!=len(priority) or set(priority)-set(MARKS):raise ValueError('invalid_relic_mark_priority')
        rosters=value.get('opening_rosters',{})
        if type(rosters) is not dict or set(rosters)-set('12345'):raise ValueError('invalid_opening_rosters')
        frozen={}
        for guild,slots in rosters.items():
            if type(slots) is not list or len(slots)!=3:raise ValueError('invalid_opening_rosters')
            if any(type(slot) is not list or not slot or any(type(role) is not str or re.fullmatch(r'[0-9]{4,6}',role) is None for role in slot) for slot in slots):raise ValueError('invalid_opening_rosters')
            roles=[role for slot in slots for role in slot]
            if len(roles)!=len(set(roles)):raise ValueError('invalid_opening_rosters')
            frozen[guild]=tuple(tuple(slot) for slot in slots)
        return cls(mode,tuple(float(x) for x in weights),MappingProxyType(dict(scores)),MappingProxyType(dict(personal)),
            **extra,relic_mark_priority=priority,opening_rosters=MappingProxyType(frozen))

@dataclass(frozen=True)
class Settings:
    goals: tuple[Goal,...]
    difficulty: int = 1
    reroll: bool = False
    max_attempts: int = 100
    max_seconds: int = 1800
    buy_relics: bool = True
    refresh_shop: bool = True
    policy: Policy = Policy()
    strategy: Strategy = field(default_factory=Strategy)

    def payload(self):
        return dict(version=1,goals=[dict(guild_id=g.guild_id,clears=g.clears,**({'mode':g.mode} if g.mode!='clears' else {})) for g in self.goals],
            difficulty=self.difficulty,reroll=self.reroll,max_attempts=self.max_attempts,max_seconds=self.max_seconds,
            buy_relics=self.buy_relics,refresh_shop=self.refresh_shop,strategy=self.strategy.payload(),route=dict(mode=self.policy.mode,
                perfect_start=self.policy.perfect_start,allowance=self.policy.allowance,third_choice=self.policy.third_choice,
                area3_bosses=list(self.policy.area3_bosses),area5_bosses=list(self.policy.area5_bosses)))

    @classmethod
    def parse(cls, v):
        if type(v) is not dict or type(v.get('version')) is not int or v.get('version')!=1: raise ValueError('invalid_bot_config')
        allowed={'version','goals','difficulty','reroll','max_attempts','max_seconds','buy_relics','refresh_shop','route','strategy'}
        if set(v)-allowed: raise ValueError('unknown_bot_setting')
        goals=v.get('goals')
        if type(goals) is not list or not goals or len(goals)>5: raise ValueError('invalid_goals')
        result=[]
        for g in goals:
            if type(g) is not dict or not {'guild_id','clears'}<=set(g) or set(g)-{'guild_id','clears','mode'}: raise ValueError('invalid_goal')
            if type(g['guild_id']) is not int or not 1<=g['guild_id']<=5 or type(g['clears']) is not int or not 1<=g['clears']<=999:
                raise ValueError('invalid_goal')
            if g.get('mode','clears') not in ('clears','attempts'):raise ValueError('invalid_goal_mode')
            result.append(Goal(g['guild_id'],g['clears'],g.get('mode','clears')))
        if len({g.guild_id for g in result})!=len(result): raise ValueError('duplicate_guild_goal')
        nums={k:v.get(k,d) for k,d in [('difficulty',1),('max_attempts',100),('max_seconds',1800)]}
        for k,(lo,hi) in {'difficulty':(1,5),'max_attempts':(1,100),'max_seconds':(1,1800)}.items():
            if type(nums[k]) is not int or not lo<=nums[k]<=hi: raise ValueError('invalid_'+k)
        flags={k:v.get(k,d) for k,d in [('reroll',False),('buy_relics',True),('refresh_shop',True)]}
        if any(type(x) is not bool for x in flags.values()): raise ValueError('invalid_flag')
        r=v.get('route',{})
        if type(r) is not dict or set(r)-{'mode','perfect_start','allowance','third_choice','area3_bosses','area5_bosses'}:
            raise ValueError('invalid_route_policy')
        if any(type(x) is not list for k,x in r.items() if k.endswith('_bosses')): raise ValueError('invalid_boss_filter')
        strategy=Strategy.parse(v.get('strategy',{}))
        return cls(tuple(result),**nums,**flags,strategy=strategy,policy=Policy(**{k:tuple(x) if k.endswith('_bosses') else x for k,x in r.items()}))
