"""Bounded disjoint Boss roster allocation adapted from the upstream planner.

Safe leading teams are preferred by count, then total score. Remaining full
slots may be damage supplements; they are never labelled safe. Known failed formations are excluded before a bounded retry search.
"""
from dataclasses import dataclass
from itertools import combinations

@dataclass(frozen=True)
class Plan:
    teams: tuple
    safe_count: int
    reason: str
    def __post_init__(self):
        ids=[uid for team in self.teams for uid in team.members]
        if len(ids)!=len(set(ids)) or not 0<=self.safe_count<=len(self.teams)<=3:
            raise ValueError('team_plan_invalid')

def boss_plan(optimizer,owned,context,*,requested=3,supplements=True,beam_width=40,excluded=()):
    if type(requested) is not int or not 1<=requested<=3 or type(beam_width) is not int or not 1<=beam_width<=40:
        raise ValueError('invalid_team_search')
    owned=tuple(dict.fromkeys(owned))
    if not owned:return Plan((),0,'insufficient_roles')
    if len(owned)<5:
        team=optimizer.best(owned,context,battle=True,excluded=excluded)
        return Plan((team,),1,'safe') if team is not None else Plan((),0,'no_valid_team')
    if any(uid not in optimizer.profiles for uid in owned):
        return Plan((),0,'role_identity_or_data_unknown')
    profiles=optimizer.profiles;gate=optimizer.cfg('relaxedMinimumReliableVanguard' if context.relaxed else 'minimumReliableVanguard')
    vanguards=[uid for uid in owned if profiles[uid].position is not None and profiles[uid].vanguard(context)>=gate]
    capacity=min(requested,len(vanguards),len(owned)//5)
    ranked=optimizer.ranked(owned,context,battle=True,limit=beam_width,excluded=excluded)
    def disjoint(teams):
        ids=[uid for team in teams for uid in team.members];return len(ids)==len(set(ids))
    safe=()
    for count in range(capacity,1,-1):
        valid=(combo for combo in combinations(ranked,count) if disjoint(combo))
        best=max(valid,key=lambda combo:sum(team.score for team in combo),default=None)
        if best is None:
            # When every beam entry shares a damage core, search residual pools
            # from a bounded set of first teams with distinct actual frontliners.
            seeds=[];fronts=set()
            for team in ranked:
                front=min(team.members,key=lambda uid:(profiles[uid].position,uid))
                if front in fronts:continue
                fronts.add(front);seeds.append(team)
                if len(seeds)==8:break
            # A high-score first team can consume all available frontliners.
            # Add bounded seeds that reserve other tanks for the required slots.
            for front in sorted(vanguards,key=lambda uid:optimizer.priority(profiles[uid],context),reverse=True)[:8]:
                pool=tuple(uid for uid in owned if uid not in vanguards or uid==front)
                candidate=optimizer.best(pool,context,battle=True,required=(front,),excluded=excluded)
                if candidate and candidate not in seeds:seeds.append(candidate)
            possibilities=[]
            for first in seeds:
                teams=[first];remaining=tuple(uid for uid in owned if uid not in first.members)
                for _ in range(count-1):
                    teams_left=count-len(teams)
                    if teams_left>1:
                        options=[]
                        remaining_fronts=[uid for uid in remaining if uid in vanguards]
                        for front in sorted(remaining_fronts,key=lambda uid:optimizer.priority(profiles[uid],context),reverse=True)[:8]:
                            pool=tuple(uid for uid in remaining if uid not in vanguards or uid==front)
                            candidate=optimizer.best(pool,context,battle=True,required=(front,),excluded=excluded)
                            if candidate:options.append(candidate)
                        team=max(options,key=lambda t:t.score,default=None)
                    else:team=optimizer.best(remaining,context,battle=True,excluded=excluded)
                    if team is None or len(team.members)!=5:break
                    teams.append(team);remaining=tuple(uid for uid in remaining if uid not in team.members)
                if len(teams)==count:possibilities.append(tuple(teams))
            best=max(possibilities,key=lambda combo:sum(team.score for team in combo),default=None)
        if best is not None:safe=tuple(sorted(best,key=lambda team:team.score,reverse=True));break
    if not safe and ranked:safe=(ranked[0],)
    teams=list(safe);remaining=tuple(uid for uid in owned if all(uid not in t.members for t in teams))
    if supplements:
        while len(teams)<requested and remaining:
            known=tuple(uid for uid in remaining if profiles[uid].position is not None)
            if not known:break
            ranked_fill=optimizer.ranked(known,context,limit=1,cohesive=context.prefer_single,excluded=excluded)
            if not ranked_fill:ranked_fill=optimizer.ranked(known,context,limit=1,excluded=excluded)
            if not ranked_fill:break
            team=ranked_fill[0]
            if any(profiles[uid].position is None for uid in team.members):break
            teams.append(team);remaining=tuple(uid for uid in remaining if uid not in team.members)
    return Plan(tuple(teams),len(safe),'safe_with_supplements' if len(teams)>len(safe) else 'safe' if safe else 'no_valid_team')
