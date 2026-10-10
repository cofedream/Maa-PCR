"""Read-only route target calculations using independently observed Maa boxes.

CC BY-NC-SA 4.0; NodePositionMapper.kt is listed in the source manifest.
locate_next requires exactly one complete type-column alignment. The additive
locate_next_observed accepts partial graphs only when exhaustive unbiased
mapping yields the same current and target positions in every candidate.
Neither calculator recognizes pixels, clicks, assumes target active state or
qualifies capture freshness, account/session identity or automatic navigation.
"""
from dataclasses import dataclass
import math
from .routes import Route,Node

@dataclass(frozen=True)
class VisibleNode:
    box:tuple[int,int,int,int]
    block_type:int
    confidence:float
    active:bool
    @property
    def center(self):
        x,y,w,h=self.box;return x+w/2,y+h/2

@dataclass(frozen=True)
class Target:
    enter_id:int
    block_id:int
    area:int
    column:int
    row:int
    block_type:int
    box:tuple[int,int,int,int]


def visual_column(nodes):
    """Protocol AACCNN numbers run bottom-to-top; row is not a visual index."""
    if not nodes:raise ValueError('protocol_column_invalid')
    area,column=nodes[0].area,nodes[0].column
    if any(n.area!=area or n.column!=column or n.block_id//10000!=area
           or n.block_id//100%100!=column for n in nodes):
        raise ValueError('protocol_column_invalid')
    if {n.block_id%100 for n in nodes}!=set(range(1,len(nodes)+1)):
        raise ValueError('protocol_column_invalid')
    return sorted(nodes,key=lambda n:-(n.block_id%100))


def locate_next(route:Route,current_block_id:int,visible:tuple[VisibleNode,...]):
    """Calculate a target from exactly one complete type-column alignment."""
    if type(current_block_id) is not int:raise ValueError('current_node_required')
    current=next((n for n in route.nodes if n.block_id==current_block_id),None)
    if current is None:raise ValueError('current_node_not_on_route')
    index=route.nodes.index(current)
    if index==len(route.nodes)-1:return None
    target=route.nodes[index+1]
    if target.area!=current.area:raise ValueError('area_transition_requires_readback')
    if target.block_id not in current.next_ids:raise ValueError('route_edge_missing')
    if not visible:return None
    for v in visible:
        if (not isinstance(v,VisibleNode) or type(v.box) is not tuple or len(v.box)!=4
                or any(type(x) is not int for x in v.box) or type(v.active) is not bool
                or type(v.block_type) is not int or v.block_type not in range(1,9)
                or type(v.confidence) not in (int,float) or not 0<=v.confidence<=1
                or not math.isfinite(v.confidence)):
            raise ValueError('invalid_visible_node')
        x,y,w,h=v.box
        if w<=0 or h<=0 or x<0 or x+w>1280 or y<80 or y+h>610:
            raise ValueError('node_outside_map_roi')
        if v.confidence<.9:return None
    groups=[]
    for v in sorted(visible,key=lambda v:v.center):
        if not groups or v.center[0]-sum(n.center[0] for n in groups[-1])/len(groups[-1])>90:
            groups.append([v])
        else:groups[-1].append(v)
    for group in groups:
        group.sort(key=lambda v:v.center[1])
        if any(b.center[1]-a.center[1]<40 for a,b in zip(group,group[1:])):
            raise ValueError('duplicate_or_overlapping_nodes')
    columns={}
    for n in route.full_map:
        if n.area==current.area:columns.setdefault(n.column,[]).append(n)
    columns={column:visual_column(nodes) for column,nodes in columns.items()}
    matches=[]
    for start in sorted(columns):
        sequence=[columns.get(start+i,[]) for i in range(len(groups))]
        if any(len(nodes)!=len(group) or any(n.block_type!=v.block_type for n,v in zip(nodes,group))
               for nodes,group in zip(sequence,groups)):
            continue
        matched=None
        for nodes,group in zip(sequence,groups):
            for n,v in zip(nodes,group):
                if n.block_id==target.block_id:matched=v
        # Another complete alignment without the target is also ambiguity;
        # the saved route must not rewrite visual evidence to force a match.
        matches.append(matched)
    if len(matches)!=1:return None
    v=matches[0]
    if v is None or not v.active:return None
    return Target(route.enter_id,target.block_id,target.area,target.column,target.row,target.block_type,v.box)


def locate_next_observed(route:Route,current_block_id:int,observation,*,stopped=None):
    """Calculate only a route box shared by every feasible local mapping.

    The requested route target never filters the matcher search. This result
    is a geometric calculation, not input authorization: the caller must still
    verify the current account/session, a fresh native capture, target active
    state, movement confirmation and durable operation intent. Reaching the
    last route node returns None and does not prove a clear or a stage result.
    """
    from .handoff import match_current_marker_consensus,match_target_marker_consensus
    if (type(route) is not Route or route.source!='protocol'
            or type(route.enter_id) is not int or route.enter_id<=0
            or type(route.nodes) is not tuple or not route.nodes
            or type(route.full_map) is not tuple
            or any(type(n) is not Node for n in route.nodes+route.full_map)):
        raise ValueError('route_binding_invalid')
    full={n.block_id:n for n in route.full_map}
    if (len(full)!=len(route.full_map) or len({n.block_id for n in route.nodes})!=len(route.nodes)
            or any(full.get(n.block_id)!=n for n in route.nodes)):
        raise ValueError('route_binding_invalid')
    if type(current_block_id) is not int:raise ValueError('current_node_required')
    current=next((n for n in route.nodes if n.block_id==current_block_id),None)
    if current is None:raise ValueError('current_node_not_on_route')
    index=route.nodes.index(current)
    protocol_map=[n.payload() for n in route.full_map]
    if index==len(route.nodes)-1:
        binding=match_current_marker_consensus(protocol_map,observation,stopped=stopped)
        if binding['bound_protocol_block_id']!=current_block_id:
            raise ValueError('observed_current_mismatch')
        return None
    target=route.nodes[index+1]
    if target.area!=current.area:raise ValueError('area_transition_requires_readback')
    if target.block_id not in current.next_ids:raise ValueError('route_edge_missing')
    binding=match_target_marker_consensus(protocol_map,observation,target.block_id,stopped=stopped)
    if binding['current_block_id']!=current_block_id:
        raise ValueError('observed_current_mismatch')
    return Target(route.enter_id,target.block_id,target.area,target.column,target.row,
                  target.block_type,tuple(binding['target_box']))
