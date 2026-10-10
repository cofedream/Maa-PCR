"""Adapted from wbero/limingjie-assistant RouteSearch.kt (CC BY-NC-SA 4.0).
Pinned provenance is recorded in docs/LABYRINTH_SOURCES.json.
"""
from dataclasses import dataclass
from functools import lru_cache

AREA_REQUIREMENTS = {
 1: (1,2,4,2,4,6), 2: (1,4,2,6,3,4,6),
 3: (1,2,6,4,3,7,8), 4: (1,4,3,5,3,2,4,7), 5: (1,2,6,3,6,3,7,8)}
BOSSES = {3: (301206,303306,319604,312505,306604),
          5: (302501,301701,310103,315004,319401)}

@dataclass(frozen=True)
class Node:
    block_id: int
    area: int
    column: int
    row: int
    block_type: int
    next_ids: tuple[int,...]
    quest_id: int | None = None

    @classmethod
    def parse(cls, value):
        keys=('block_id','area','column','row','block_type')
        if type(value) is not dict: raise ValueError('invalid_map_node')
        if any(type(value.get(k)) is not int or value[k] < 1 for k in keys):
            raise ValueError('invalid_map_node')
        links=value.get('next_block_id_list', [])
        if type(links) is not list or any(type(x) is not int or x < 1 for x in links) or len(set(links))!=len(links):
            raise ValueError('invalid_map_links')
        quest=value.get('quest_id')
        if quest==0 and type(quest) is int: quest=None
        if quest is not None and (type(quest) is not int or quest<=0): raise ValueError('invalid_quest')
        return cls(*(value[k] for k in keys),tuple(links),quest)

    def payload(self):
        return dict(block_id=self.block_id,area=self.area,column=self.column,row=self.row,
                    block_type=self.block_type,next_block_id_list=list(self.next_ids),quest_id=self.quest_id)

@dataclass(frozen=True)
class Policy:
    mode: str = 'value'
    perfect_start: bool = True
    allowance: int = 1
    third_choice: str = 'relic'
    area3_bosses: tuple[int,...] = (312505,319604)
    area5_bosses: tuple[int,...] = (310103,)

    def __post_init__(self):
        if type(self.perfect_start) is not bool: raise ValueError('invalid_route_flag')
        if self.mode not in ('value','template') or self.third_choice not in ('relic','event','either'):
            raise ValueError('invalid_route_policy')
        if type(self.allowance) is not int or not 0 <= self.allowance <= 3:
            raise ValueError('invalid_route_allowance')
        for area,ids in ((3,self.area3_bosses),(5,self.area5_bosses)):
            if type(ids) is not tuple or len(set(ids))!=len(ids) or any(type(x) is not int or x not in BOSSES[area] for x in ids):
                raise ValueError('invalid_boss_filter')

    def boss_matches(self, area, path):
        ids=self.area3_bosses if area==3 else self.area5_bosses if area==5 else ()
        if not ids: return True
        boss=next((n for n in reversed(path) if n.block_type==8),None)
        if boss is None or boss.quest_id is None: return False
        prefix=7703 if area==3 else 7705
        ordinal=boss.quest_id//100%100
        return boss.quest_id//100000==prefix and 1<=ordinal<=20 and BOSSES[area][(ordinal-1)%5] in ids

    def types(self, area, column):
        if area in (3,5) and column==3:
            return {'relic':{6},'event':{5},'either':{5,6}}[self.third_choice]
        return {AREA_REQUIREMENTS[area][column-1]}

    def accepts(self, area, path):
        if not self.boss_matches(area,path): return False
        return not self.perfect_start or (set(n.column for n in path)==set(range(1,len(AREA_REQUIREMENTS[area])+1))
             and all(n.block_type in self.types(area,n.column) for n in path))

@dataclass(frozen=True)
class Route:
    enter_id: int
    nodes: tuple[Node,...]
    full_map: tuple[Node,...]
    source: str

    def payload(self):
        return dict(enter_id=self.enter_id,nodes=[n.payload() for n in self.nodes],
                    map_list=[n.payload() for n in self.full_map],source=self.source,complete=True)

def select_route(enter_id, nodes, difficulty, policy=Policy(), *, source='protocol', current_block_id=None,
                 enforce_allowance=True,require_terminal_boss=False):
    if type(enforce_allowance) is not bool or type(require_terminal_boss) is not bool:raise ValueError('invalid_route_flag')
    if type(enter_id) is not int or enter_id<=0 or type(difficulty) is not int or not 1<=difficulty<=5:
        raise ValueError('invalid_opening')
    if current_block_id not in (None,0): raise ValueError('mid_run_not_supported')
    if source not in ('protocol','visual'): raise ValueError('invalid_map_source')
    # Real sampling exposed area 1 only. A caller's complete=True is not proof
    # that unvisited regions, their edges and both Boss identities were visible.
    if source=='visual': raise ValueError('visual_full_map_not_qualified')
    nodes=tuple(nodes)
    if not nodes or len(nodes)>1000: raise ValueError('invalid_map_size')
    by_id={n.block_id:n for n in nodes}
    if len(by_id)!=len(nodes): raise ValueError('duplicate_map_node')
    areas=(1,2,3) if difficulty==1 else (1,2,3,4,5)
    if set(n.area for n in nodes)!=set(areas): raise ValueError('incomplete_map')
    for n in nodes:
        if n.block_type not in range(1,9): raise ValueError('unknown_node_type')
        for child_id in n.next_ids:
            child=by_id.get(child_id)
            if child is None: raise ValueError('missing_map_link')
            if child.area==n.area and child.column<=n.column or child.area<n.area:
                raise ValueError('map_cycle_or_regression')
    selected=[]
    for area in areas:
        area_nodes=sorted((n for n in nodes if n.area==area),key=lambda n:(n.column,n.row,n.block_id))
        last=max(n.column for n in area_nodes) if policy.mode=='value' else len(AREA_REQUIREMENTS[area])
        if set(n.column for n in area_nodes)!=set(range(1,last+1)):
            raise ValueError('incomplete_map')
        valuable={}
        for col in range(1,last+1):
            present={n.block_type for n in area_nodes if n.column==col}
            kinds=present & {3,4,6,7}
            if {4,6}<=present: kinds.discard(6); kinds.add(4)
            if {6,7}<=present: kinds.discard(7); kinds.add(6)
            if area in (3,5) and {5,6}<=present:
                if policy.third_choice=='relic': kinds.discard(5); kinds.add(6)
                elif policy.third_choice=='event': kinds.discard(6); kinds.add(5)
                else: kinds.update((5,6))
            valuable[col]=kinds
        upper=sum(bool(kinds) for kinds in valuable.values())
        @lru_cache(None)
        def walk(block_id):
            n=by_id[block_id]
            if policy.mode=='template' and policy.perfect_start and n.block_type not in policy.types(area,n.column):
                return None
            gain=int(n.block_type in valuable[n.column])
            preference=int(area in (3,5) and policy.third_choice=='either' and n.block_type==6)
            if n.column==last:
                if require_terminal_boss and area in (3,5) and (n.block_type!=8 or type(n.quest_id) is not int or n.quest_id<=0):
                    return None
                return (gain,preference,(n,)) if policy.boss_matches(area,(n,)) else None
            candidates=[]
            for c in sorted((by_id[x] for x in n.next_ids if by_id[x].area==area),key=lambda c:(c.column,c.row,c.block_id)):
                suffix=walk(c.block_id)
                if suffix: candidates.append((gain+suffix[0],preference+suffix[1],(n,)+suffix[2]))
            if not candidates: return None
            if policy.mode=='template': return candidates[0]
            return max(candidates,key=lambda c:c[:2])
        choices=[walk(n.block_id) for n in area_nodes if n.column==1]
        choices=[p for p in choices if p and (policy.mode!='template' or policy.accepts(area,p[2]))]
        if not choices: return None
        best=choices[0] if policy.mode=='template' else max(choices,key=lambda p:p[:2])
        if enforce_allowance and policy.mode=='value' and upper-best[0]>policy.allowance: return None
        selected.extend(best[2])
    return Route(enter_id,tuple(selected),nodes,source)
