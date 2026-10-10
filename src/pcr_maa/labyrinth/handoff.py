"""Pure current-region alignment; never qualifies a protocol/game handoff.

Protocol owns full-map IDs and structure. Maa owns partial pixels, node types,
boxes, edges and marker position. This matcher accepts no route or expected
column. Account/guild/difficulty/fresh-opening proof remains pending.
"""
import math
from .routes import Node
from .navigation import visual_column
from .snapshots import canonical_map

class VisibleBindingFailure(ValueError):pass


def _integer(value,minimum=0,maximum=2**63-1):
    return type(value) is int and minimum<=value<=maximum


def _confidence(value):
    return type(value) in (int,float) and .9<=value<=1 and math.isfinite(value)


def _box(value):
    if type(value) is not list or len(value)!=4 or any(type(n) is not int for n in value):
        raise VisibleBindingFailure('visible_box_invalid')
    x,y,w,h=value
    if x<0 or y<80 or w<=0 or h<=0 or x+w>1280 or y+h>610:
        raise VisibleBindingFailure('visible_box_invalid')
    return tuple(value)


def _center(box):
    x,y,w,h=box;return x+w/2,y+h/2


def _overlap(a,b):
    return max(a[0],b[0])<min(a[0]+a[2],b[0]+b[2]) and max(a[1],b[1])<min(a[1]+a[3],b[1]+b[3])


def _protocol_columns(protocol_map,area):
    try:
        if type(protocol_map) is not list or not 1<=len(protocol_map)<=1000:raise ValueError()
        canonical_map(protocol_map)
        nodes=[Node.parse(value) for value in protocol_map]
        if any(not (_integer(n.block_id,1) and _integer(n.area,1,5) and _integer(n.column,1,99)
            and _integer(n.row,1) and _integer(n.block_type,1,8)
            and (n.quest_id is None or _integer(n.quest_id,1))) for n in nodes):raise ValueError()
        by_id={n.block_id:n for n in nodes}
        last_columns={}
        for node in nodes:last_columns[node.area]=max(last_columns.get(node.area,0),node.column)
        for node in nodes:
            for target_id in node.next_ids:
                target=by_id.get(target_id)
                if target is None:raise ValueError()
                if target.area==node.area:
                    if target.column!=node.column+1:raise ValueError()
                elif target.area!=node.area+1 or target.column!=1 or node.column!=last_columns[node.area]:raise ValueError()
        columns={}
        for node in nodes:
            if node.area==area:columns.setdefault(node.column,[]).append(node)
        if len(columns)<2 or sorted(columns)!=list(range(1,max(columns)+1)):raise ValueError()
        columns={column:tuple(visual_column(values)) for column,values in columns.items()}
        # Each nonterminal current-region node must have a forward link. Empty
        # or missing graph segments cannot make an incomplete edge list match.
        last=max(columns)
        for column,values in columns.items():
            if column<last and any(not any(by_id[i].area==area and by_id[i].column==column+1 for i in n.next_ids) for n in values):
                raise ValueError()
        return columns
    except (ValueError,TypeError,KeyError):raise VisibleBindingFailure('protocol_map_invalid') from None


def _observation(value):
    keys={'source','complete','area','frame','columns','edges','edges_complete','start_marker'}
    if (type(value) is not dict or set(value)!=keys or value.get('source')!='maa'
            or value.get('complete') is not False or not _integer(value.get('area'),1,5)
            or not _integer(value.get('frame'),1)):
        raise VisibleBindingFailure('visible_observation_invalid')
    raw_columns=value['columns']
    if type(raw_columns) is not list or not 2<=len(raw_columns)<=16:
        raise VisibleBindingFailure('visible_observation_invalid')
    columns=[];all_boxes=[];previous_right=-1
    for column in raw_columns:
        if type(column) is not dict or set(column)!={'complete','nodes'}:
            raise VisibleBindingFailure('visible_observation_invalid')
        if column['complete'] is not True:raise VisibleBindingFailure('visible_column_incomplete')
        if type(column['nodes']) is not list or not 1<=len(column['nodes'])<=16:
            raise VisibleBindingFailure('visible_observation_invalid')
        values=[]
        for node in column['nodes']:
            if (type(node) is not dict or set(node)!={'box','block_type','confidence','active'}
                    or not _integer(node['block_type'],1,8) or not _confidence(node['confidence'])
                    or type(node['active']) is not bool):
                raise VisibleBindingFailure('visible_node_invalid')
            box=_box(node['box'])
            if any(_overlap(box,other) for other in all_boxes):raise VisibleBindingFailure('visible_box_overlap')
            all_boxes.append(box)
            values.append(dict(box=box,block_type=node['block_type'],confidence=node['confidence'],active=node['active']))
        centers=[_center(node['box']) for node in values]
        if any(b[1]<=a[1] for a,b in zip(centers,centers[1:])) or max(x for x,_ in centers)-min(x for x,_ in centers)>90:
            raise VisibleBindingFailure('visible_column_order_invalid')
        left=min(node['box'][0] for node in values);right=max(node['box'][0]+node['box'][2] for node in values)
        if left<previous_right:raise VisibleBindingFailure('visible_column_order_invalid')
        previous_right=right
        columns.append(tuple(values))
    if value['edges_complete'] is not True:raise VisibleBindingFailure('visible_edges_incomplete')
    raw_edges=value['edges']
    if type(raw_edges) is not list or not raw_edges or len(raw_edges)>4096:raise VisibleBindingFailure('visible_edges_incomplete')
    edges=set();bands=set()
    for edge in raw_edges:
        if type(edge) is not dict or set(edge)!={'from','to','confidence'} or not _confidence(edge['confidence']):
            raise VisibleBindingFailure('visible_edge_invalid')
        endpoints=[]
        for name in ('from','to'):
            endpoint=edge[name]
            if (type(endpoint) is not list or len(endpoint)!=2
                    or not _integer(endpoint[0],0,len(columns)-1)
                    or not _integer(endpoint[1],0,len(columns[endpoint[0]])-1)):
                raise VisibleBindingFailure('visible_edge_invalid')
            endpoints.append(tuple(endpoint))
        source,target=endpoints
        if target[0]!=source[0]+1 or (source,target) in edges:raise VisibleBindingFailure('visible_edge_invalid')
        edges.add((source,target));bands.add(source[0])
    if bands!=set(range(len(columns)-1)):raise VisibleBindingFailure('visible_edges_incomplete')
    marker=value['start_marker']
    if (type(marker) is not dict or set(marker)!={'column','node','box','confidence'}
            or not _integer(marker['column'],0,len(columns)-1)
            or not _integer(marker['node'],0,len(columns[marker['column']])-1)
            or not _confidence(marker['confidence'])):
        raise VisibleBindingFailure('visible_marker_invalid')
    marker_box=_box(marker['box']);x,y=_center(marker_box)
    node_box=columns[marker['column']][marker['node']]['box']
    if not node_box[0]<=x<node_box[0]+node_box[2] or not node_box[1]<=y<node_box[1]+node_box[3]:
        raise VisibleBindingFailure('visible_marker_invalid')
    return tuple(columns),edges,marker_box


def match_visible_map(protocol_map,observation):
    """Return a unique structural alignment, with qualification still pending.

    Columns and node arrays must be physically left-to-right/top-to-bottom.
    Edge endpoints are zero-based [observed_column, observed_node] indices.
    edges_complete means every connection between the full observed adjacent
    columns was independently captured, including non-route connections.
    start_marker is a position observation, not proof of an untouched opening.
    """
    visible,edges,marker_box=_observation(observation)
    columns=_protocol_columns(protocol_map,observation['area'])
    matches=[]
    for start in sorted(columns):
        candidate=[columns.get(start+i,()) for i in range(len(visible))]
        if any(len(nodes)!=len(group) or any(node.block_type!=v['block_type'] for node,v in zip(nodes,group))
                for nodes,group in zip(candidate,visible)):
            continue
        indices={node.block_id:(i,j) for i,nodes in enumerate(candidate) for j,node in enumerate(nodes)}
        expected={(indices[node.block_id],indices[target]) for i,nodes in enumerate(candidate[:-1])
            for node in nodes for target in node.next_ids if target in indices and indices[target][0]==i+1}
        if expected==edges:matches.append((start,candidate))
    if not matches:raise VisibleBindingFailure('visible_map_unmatched')
    if len(matches)!=1:raise VisibleBindingFailure('visible_map_ambiguous')
    start,candidate=matches[0]
    marker=observation['start_marker']
    marker_id=candidate[marker['column']][marker['node']].block_id
    return dict(alignment_verified=True,qualification_pending=True,area=observation['area'],frame=observation['frame'],
        visible_complete=False,protocol_columns=list(range(start,start+len(visible))),
        nodes=[[dict(block_id=node.block_id,box=list(v['box']),block_type=v['block_type'],active=v['active'])
            for node,v in zip(nodes,group)] for nodes,group in zip(candidate,visible)],
        edges=[dict(from_block_id=candidate[a[0]][a[1]].block_id,to_block_id=candidate[b[0]][b[1]].block_id)
            for a,b in sorted(edges)],
        start_marker=dict(box=list(marker_box),bound_block_id=marker_id,untouched_opening_verified=False),
        provenance=dict(full_map='protocol',block_ids='protocol',column_numbers='protocol',node_types='maa',
            node_boxes='maa',visible_edges='maa',marker_position='maa'),
        pending=['fresh_account','guild','difficulty','fresh_opening','native_capture_freshness','untouched_start'])


# This additive API deliberately does not relax match_visible_map or its callers.
_OBSERVED_SUBGRAPH_SEARCH_LIMIT = 100000


def _observed_subgraph(value):
    keys = {'source', 'scope', 'area', 'frame', 'columns',
            'positive_edges', 'current_marker'}
    if (type(value) is not dict or set(value) != keys
            or value['source'] != 'maa' or value['scope'] != 'observed_subgraph'
            or not _integer(value['area'], 1, 5) or not _integer(value['frame'], 1)):
        raise VisibleBindingFailure('observed_subgraph_invalid')
    raw_columns = value['columns']
    if type(raw_columns) is not list or not 1 <= len(raw_columns) <= 16:
        raise VisibleBindingFailure('observed_subgraph_invalid')
    columns, all_boxes, previous_right = [], [], -1
    for column in raw_columns:
        if (type(column) is not dict or set(column) != {'nodes'}
                or type(column['nodes']) is not list
                or not 1 <= len(column['nodes']) <= 16):
            raise VisibleBindingFailure('observed_subgraph_invalid')
        values = []
        for node in column['nodes']:
            if (type(node) is not dict or set(node) != {'box', 'block_type'}
                    or (node['block_type'] is not None
                        and not _integer(node['block_type'], 1, 8))):
                raise VisibleBindingFailure('observed_node_invalid')
            box = _box(node['box'])
            if any(_overlap(box, other) for other in all_boxes):
                raise VisibleBindingFailure('visible_box_overlap')
            all_boxes.append(box)
            values.append(dict(box=box, block_type=node['block_type']))
        centers = [_center(node['box']) for node in values]
        if (any(b[1] <= a[1] for a, b in zip(centers, centers[1:]))
                or max(x for x, _ in centers) - min(x for x, _ in centers) > 90):
            raise VisibleBindingFailure('visible_column_order_invalid')
        left = min(node['box'][0] for node in values)
        right = max(node['box'][0] + node['box'][2] for node in values)
        if left < previous_right:
            raise VisibleBindingFailure('visible_column_order_invalid')
        previous_right = right
        columns.append(tuple(values))
    raw_edges = value['positive_edges']
    if type(raw_edges) is not list or len(raw_edges) > 4096:
        raise VisibleBindingFailure('observed_edge_invalid')
    edges = set()
    for edge in raw_edges:
        if type(edge) is not dict or set(edge) != {'from', 'to'}:
            raise VisibleBindingFailure('observed_edge_invalid')
        endpoints = []
        for name in ('from', 'to'):
            endpoint = edge[name]
            if (type(endpoint) is not list or len(endpoint) != 2
                    or not _integer(endpoint[0], 0, len(columns) - 1)
                    or not _integer(endpoint[1], 0, len(columns[endpoint[0]]) - 1)):
                raise VisibleBindingFailure('observed_edge_invalid')
            endpoints.append(tuple(endpoint))
        source, target = endpoints
        if source[0] >= target[0] or (source, target) in edges:
            raise VisibleBindingFailure('observed_edge_invalid')
        edges.add((source, target))
    marker = value['current_marker']
    if (type(marker) is not dict or set(marker) != {'column', 'node', 'box'}
            or not _integer(marker['column'], 0, len(columns) - 1)
            or not _integer(marker['node'], 0, len(columns[marker['column']]) - 1)):
        raise VisibleBindingFailure('observed_marker_invalid')
    marker_box = _box(marker['box'])
    x, y = _center(marker_box)
    node_box = columns[marker['column']][marker['node']]['box']
    if (not node_box[0] <= x < node_box[0] + node_box[2]
            or not node_box[1] <= y < node_box[1] + node_box[3]):
        raise VisibleBindingFailure('observed_marker_invalid')
    return tuple(columns), edges, marker_box


def _subgraph_search(protocol_map, observation, stopped):
    """One bounded unbiased candidate iterator shared by both projections."""
    if stopped is not None and not callable(stopped):
        raise VisibleBindingFailure('observed_stop_invalid')

    def check_stopped():
        if stopped is None:
            return
        try:
            result = stopped()
        except Exception:
            raise VisibleBindingFailure('observed_stop_failed') from None
        if type(result) is not bool:
            raise VisibleBindingFailure('observed_stop_invalid')
        if result:
            raise VisibleBindingFailure('observed_subgraph_stopped')

    check_stopped()
    visible, edges, marker_box = _observed_subgraph(observation)
    columns = _protocol_columns(protocol_map, observation['area'])
    check_stopped()
    column_numbers = sorted(columns)
    selected, states = [], 0

    def step():
        nonlocal states
        check_stopped()
        states += 1
        if states > _OBSERVED_SUBGRAPH_SEARCH_LIMIT:
            raise VisibleBindingFailure('observed_subgraph_search_limit')

    def node_choices(nodes, observed, index=0, start=0, chosen=()):
        step()
        if index == len(observed):
            yield chosen
            return
        end = len(nodes) - (len(observed) - index) + 1
        for position in range(start, end):
            kind = observed[index]['block_type']
            if kind is None or nodes[position].block_type == kind:
                yield from node_choices(nodes, observed, index + 1,
                                        position + 1, chosen + (nodes[position],))

    def search(index=0, previous=0):
        step()
        if index == len(visible):
            yield tuple(selected)
            return
        for ordinal, number in enumerate(column_numbers):
            if (number <= previous
                    or len(column_numbers) - ordinal < len(visible) - index):
                continue
            for nodes in node_choices(columns[number], visible[index]):
                selected.append((number, nodes))
                try:
                    valid = all(selected[b[0]][1][b[1]].block_id
                                in selected[a[0]][1][a[1]].next_ids
                                for a, b in edges if b[0] == index)
                    if valid:
                        yield from search(index + 1, number)
                finally:
                    selected.pop()

    return visible, edges, marker_box, search(), check_stopped


def match_observed_subgraph(protocol_map, observation, *, stopped=None):
    """Bind actual local observations uniquely, without proving a handoff.

    The complete protocol map is supplied and qualified by the caller. Maa
    columns/nodes are ordered left-to-right/top-to-bottom but may omit whole
    columns or nodes. None is an unknown observed type, never a protocol echo.
    Only independently observed positive edges constrain the search; omitted
    edges do not mean absent edges. All increasing column/node subsequences in
    the area are considered, without a route, target or expected position.

    current_marker.box describes an independently observed contact-point
    anchor associated with its observed node, not a character sprite body.
    This box-in-node association is a structural input contract, not evidence
    that a real marker collector exists or that an opening is untouched.
    No confidence is invented from a template score. Capture qualification,
    account, freshness, new-run and protocol/game lifecycle belong outside.
    """
    visible, edges, marker_box, search, check_stopped = _subgraph_search(
        protocol_map, observation, stopped)
    candidates = []
    try:
        for candidate in search:
            candidates.append(candidate)
            if len(candidates) > 1:
                break
    finally:
        search.close()
    check_stopped()
    if not candidates:
        raise VisibleBindingFailure('observed_subgraph_unmatched')
    if len(candidates) != 1:
        raise VisibleBindingFailure('observed_subgraph_ambiguous')
    candidate = candidates[0]
    marker = observation['current_marker']
    return dict(
        scope='observed_subgraph', proves_unique_binding=True,
        qualification_pending=True, area=observation['area'], frame=observation['frame'],
        protocol_columns=[number for number, _ in candidate],
        nodes=[[dict(protocol_block_id=node.block_id,
                     protocol_block_type=node.block_type,
                     observed_block_type=value['block_type'],
                     observed_box=list(value['box']))
                for node, value in zip(nodes, group)]
               for (_, nodes), group in zip(candidate, visible)],
        positive_edges=[dict(**{'from': list(a), 'to': list(b)},
                             from_protocol_block_id=candidate[a[0]][1][a[1]].block_id,
                             to_protocol_block_id=candidate[b[0]][1][b[1]].block_id)
                        for a, b in sorted(edges)],
        current_marker=dict(column=marker['column'], node=marker['node'],
                            box=list(marker_box),
                            bound_protocol_block_id=candidate[marker['column']][1][marker['node']].block_id),
        provenance=dict(supplied_full_map='protocol', block_ids='protocol',
                        column_numbers='protocol', protocol_node_types='protocol',
                        observed_node_types='maa', node_boxes='maa',
                        positive_edges='maa', marker_position='maa'),
        ui_full_map_verified=False, complete_columns_verified=False,
        complete_edges_verified=False, freshness_verified=False,
        account_verified=False, new_opening_verified=False,
        pending=['independent_capture_qualification', 'fresh_account', 'guild',
                 'difficulty', 'fresh_opening', 'native_capture_freshness',
                 'protocol_game_lifecycle'])



def match_current_marker_consensus(protocol_map, observation, *, stopped=None):
    """Project all feasible local mappings onto one current marker ID.

    Neighbors may have multiple mappings. The entire unbiased bounded search
    must finish before a same-ID projection succeeds; a different marker ID
    may reject early. Unknown or omitted nodes/columns/edges retain exactly
    the strict matcher's semantics. No route or expected position is accepted.

    This read-only result proves a structural current-position consensus only.
    It supplies no arbitrary neighbor-ID-to-box mapping, UI map fingerprint,
    next target, capture freshness, account, creation, or handoff qualification.
    """
    visible, edges, marker_box, search, check_stopped = _subgraph_search(
        protocol_map, observation, stopped)
    marker = observation['current_marker']
    current, count = None, 0
    try:
        for candidate in search:
            bound = candidate[marker['column']][1][marker['node']].block_id
            if current is not None and current != bound:
                raise VisibleBindingFailure('current_marker_ambiguous')
            current = bound
            count += 1
    finally:
        search.close()
    check_stopped()
    if count == 0:
        raise VisibleBindingFailure('observed_subgraph_unmatched')
    return dict(
        scope='current_marker_consensus', search_complete=True, candidate_count=count,
        current_marker_binding_verified=True, bound_protocol_block_id=current,
        subgraph_mapping_unique=count == 1, qualification_pending=True,
        area=observation['area'], frame=observation['frame'],
        observed_marker=dict(column=marker['column'], node=marker['node'],
                             box=list(marker_box)),
        observed_column_count=len(visible),
        observed_node_count=sum(len(group) for group in visible),
        observed_positive_edge_count=len(edges),
        provenance=dict(supplied_full_map='protocol', bound_protocol_block_id='protocol',
                        protocol_node_types='protocol', observed_node_types='maa',
                        node_boxes='maa', positive_edges='maa', marker_position='maa',
                        current_binding='consensus over every feasible local mapping',
                        candidate_count='exhaustive bounded search'),
        ui_map_fingerprint_verified=False, next_target_qualified=False,
        safe_to_navigate=False, ui_full_map_verified=False,
        complete_columns_verified=False, complete_edges_verified=False,
        freshness_verified=False, account_verified=False, new_opening_verified=False,
        pending=['independent_capture_qualification', 'fresh_account', 'guild',
                 'difficulty', 'fresh_opening', 'native_capture_freshness',
                 'protocol_game_lifecycle', 'independent_next_target'])



def match_target_marker_consensus(protocol_map, observation, target_block_id, *, stopped=None):
    """Project every feasible mapping onto one current ID and observed target.

    The requested target never constrains the shared unbiased search. Every
    complete candidate is visited, including those missing that target. Only
    after exhaustion can identical current/target projections succeed. Unknown
    observed target type is not filled from the protocol. No arbitrary neighbor
    mapping, reachability, active state, route or capture/session proof is given.
    """
    if not _integer(target_block_id, 1):
        raise VisibleBindingFailure('target_block_id_invalid')
    visible, edges, marker_box, search, check_stopped = _subgraph_search(
        protocol_map, observation, stopped)
    marker = observation['current_marker']
    currents, target_positions, count, missing = set(), set(), 0, False
    try:
        for candidate in search:
            count += 1
            currents.add(candidate[marker['column']][1][marker['node']].block_id)
            projected = [(i, j) for i, (_, nodes) in enumerate(candidate)
                         for j, node in enumerate(nodes) if node.block_id == target_block_id]
            if len(projected) != 1:
                missing = True
            else:
                target_positions.add(projected[0])
    finally:
        search.close()
    check_stopped()
    if count == 0:
        raise VisibleBindingFailure('observed_subgraph_unmatched')
    if len(currents) != 1:
        raise VisibleBindingFailure('current_marker_ambiguous')
    if missing:
        raise VisibleBindingFailure('target_not_observed_in_all_mappings')
    if len(target_positions) != 1:
        raise VisibleBindingFailure('target_box_ambiguous')
    column, index = next(iter(target_positions))
    target = visible[column][index]
    if target['block_type'] is None:
        raise VisibleBindingFailure('target_type_unobserved')
    return dict(
        scope='target_marker_consensus', search_complete=True, candidate_count=count,
        current_marker_binding_verified=True, target_binding_verified=True,
        current_block_id=next(iter(currents)), target_block_id=target_block_id,
        # This ID is an observed coordinate, never a remotely read game-node ID.
        observed_target_id=[column, index], observed_target_type=target['block_type'],
        target_box=list(target['box']), subgraph_mapping_unique=count == 1,
        qualification_pending=True, area=observation['area'], frame=observation['frame'],
        observed_marker=dict(column=marker['column'], node=marker['node'], box=list(marker_box)),
        observed_column_count=len(visible), observed_node_count=sum(len(group) for group in visible),
        observed_positive_edge_count=len(edges),
        provenance=dict(supplied_full_map='protocol', current_block_id='protocol',
                        target_block_id='protocol request projected after unbiased search',
                        observed_target_id='maa observed column/node coordinates',
                        observed_target_type='maa', target_box='maa', marker_position='maa',
                        current_binding='consensus over every feasible local mapping',
                        target_binding='present at identical observed node in every feasible local mapping',
                        candidate_count='exhaustive bounded search'),
        ui_map_fingerprint_verified=False, next_target_qualified=False, safe_to_navigate=False,
        active_verified=False, reachable_verified=False, route_verified=False,
        protocol_game_lifecycle_verified=False, ui_full_map_verified=False,
        complete_columns_verified=False, complete_edges_verified=False,
        freshness_verified=False, account_verified=False, new_opening_verified=False,
        pending=['independent_capture_qualification', 'fresh_account', 'guild', 'difficulty',
                 'fresh_opening', 'native_capture_freshness', 'protocol_game_lifecycle',
                 'target_active_state', 'target_reachability', 'route_authorization'])
