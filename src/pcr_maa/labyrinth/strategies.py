"""Ported opening and relic strategies (upstream CC BY-NC-SA 4.0).

Only recognized identities enter decisions. Selection and purchase confirmation
remain the responsibility of the Maa adapter, using a new frame after input.
"""
from dataclasses import dataclass

OPENING = {
 1: (('1075',),('1351',),('1059',)),
 2: (('1089',),('1088',),('1003','1801','1225')),
 3: (('1025',),('1308',),('1145',)),
 4: (('1242',),('1339',),('1136','1115','1236','1238')),
 5: (('1091',),('1171',),('1011',))}
GRANTED = {5: ('1068',)}
MARKS = ('加速','会心','守备','强化','弱体')
ALIASES = {'暴击':'会心','削弱':'弱体','崩弱':'弱体'}

def opening_roster(guild_id,available):
    if guild_id not in OPENING: raise ValueError('invalid_guild')
    result=tuple(next((c for c in slot if c in available),None) for slot in OPENING[guild_id])
    if None in result: return None
    if len(set(result)) != 3: raise ValueError('duplicate_opening_role')
    return result

def next_opening_role(guild_id,available,selected):
    for slot in OPENING[guild_id]:
        if not set(slot)&set(selected):
            candidate=next((c for c in slot if c in available and c not in selected),None)
            if candidate: return candidate
    return None

@dataclass(frozen=True)
class Relic:
    relic_id: str
    mark: str
    bonus: int
    recognized: bool = True
    def __post_init__(self):
        if not self.relic_id or type(self.bonus) is not int or self.bonus<=0 or type(self.recognized) is not bool:
            raise ValueError('invalid_relic')
        object.__setattr__(self,'mark',ALIASES.get(self.mark,self.mark))

@dataclass(frozen=True)
class RelicChoice:
    choice: Relic
    focus: str
    phase: str

def relic_focus(stacks,*,locked_focus=None,priority=(),debuff_pivot_minimum=12,debuff_pivot_tolerance=2):
    stacks={m:stacks.get(m,0) for m in MARKS}
    unfinished=[m for m in MARKS if stacks[m]<15]
    highest=max(unfinished,key=lambda m:stacks[m],default=None)
    pivot=stacks['弱体']>=debuff_pivot_minimum and (highest is None or stacks['弱体']+debuff_pivot_tolerance>=stacks[highest])
    retained=locked_focus if locked_focus in unfinished else None
    preferred=next((m for m in priority if m in unfinished),None)
    return ('弱体' if pivot else retained or preferred or highest or '弱体'),pivot

def choose_relic(stacks,candidates,*,locked_focus=None,area=None,priority=(),baseline_last_area=3,baseline_target=4,debuff_pivot_minimum=12,debuff_pivot_tolerance=2):
    if any(type(v) is not int or v<0 for v in stacks.values()) or set(stacks)-set(MARKS):
        raise ValueError('invalid_relic_stacks')
    if len(set(priority))!=len(priority) or set(priority)-set(MARKS): raise ValueError('invalid_relic_priority')
    stacks={m:stacks.get(m,0) for m in MARKS}
    recognized=[c for c in candidates if c.recognized and c.mark in MARKS]
    if not recognized: return None
    deficits={m:max(0,baseline_target-stacks[m]) for m in MARKS[:3]}
    focus,pivot=relic_focus(stacks,locked_focus=locked_focus,priority=priority,debuff_pivot_minimum=debuff_pivot_minimum,debuff_pivot_tolerance=debuff_pivot_tolerance)
    capstones=[c for c in recognized if stacks[c.mark]<15 and stacks[c.mark]+c.bonus>=15]
    immediate=max(capstones,key=lambda c:(c.mark=='弱体',stacks[c.mark],c.bonus),default=None)
    early=area is not None and 1<=area<=baseline_last_area
    def score(c):
        before=stacks[c.mark]; useful=min(c.bonus,max(0,15-before)); deficit=deficits.get(c.mark,0)
        finishes=deficit>0 and c.bonus>=deficit
        priority_bonus=(len(priority)-priority.index(c.mark))*1.2 if c.mark in priority else 0
        return (useful*2+min(c.bonus,deficit)*(2.5 if early else .25)
                +((4 if early else 2) if finishes else 0)
                +(min(before,15)*.5 if useful else 0)+(3 if c.mark==focus and useful else 0)
                +(1 if c.mark=='弱体' and pivot and useful else 0)+(priority_bonus if useful else 0))
    choice=immediate or max(recognized,key=lambda c:(score(c),c.bonus))
    effective=immediate.mark if immediate else choice.mark if choice.mark!=focus and any(c.mark==focus for c in recognized) else focus
    phase='baseline' if early and immediate is None and deficits.get(choice.mark,0)>0 else 'debuff15' if effective=='弱体' and (pivot or immediate and immediate.mark=='弱体') else 'capstone15'
    return RelicChoice(choice,effective,phase)

def shop_relic(stacks,items,*,balance,buy=True,area=None,locked_focus=None,priority=(),**policy):
    if type(balance) is not int or balance<0: return None
    if not buy or area==4: return None
    eligible=[i for i in items if i.get('verified') is True and i.get('kind')=='relic'
              and i.get('currency')=='run_coins' and type(i.get('price')) is int
              and 0<i['price']<=balance and i.get('purchasable') is True
              and isinstance(i.get('relic'),Relic) and i['relic'].recognized]
    decision=choose_relic(stacks,[i['relic'] for i in eligible],area=area,locked_focus=locked_focus,priority=priority,**policy)
    if decision is None: return None
    return next(i for i in eligible if i['relic'].relic_id==decision.choice.relic_id)
