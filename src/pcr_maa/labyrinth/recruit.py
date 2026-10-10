"""Maa evidence for the two locally sampled guild recruitment pairs.

Game labels are translated to the existing upstream policy IDs. Each input
rechecks the exact observed pair; mixed or unknown pairs never acquire a choice.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import math
from .batch import BatchStopped

PAIRS = {('坦克型', '治疗型'): (1101, 11011, 11012),
         ('强化型', '增益型'): (1102, 11021, 11022)}

@dataclass(frozen=True)
class RecruitPair:
    labels: tuple[str, str]
    event_id: int
    choice_ids: tuple[int, int]


def read_pair(receipt):
    if type(receipt) is not dict or receipt.get('native_ok') is not True:
        raise BatchStopped('event_unknown')
    nodes = receipt.get('nodes')
    if type(nodes) is not list:
        raise BatchStopped('event_unknown')
    roots = [n for n in nodes if type(n) is dict and n.get('name') == 'LabyRecruitObserved' and n.get('completed') is True]
    if len(roots) != 1 or type(roots[0].get('recognition')) is not list:
        raise BatchStopped('event_unknown')
    raw = roots[0]['recognition']; labels = []
    for name, roi in [('LabyRecruitClass0',(360,477,202,32)),('LabyRecruitClass1',(733,477,202,32))]:
        matches = [r for r in raw if type(r) is dict and r.get('name') == name]
        rows = matches[0].get('detail',{}).get('filtered') if len(matches) == 1 and type(matches[0].get('detail')) is dict else None
        if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
            raise BatchStopped('event_unknown')
        row=rows[0]; text=row.get('text'); score=row.get('score'); box=row.get('box')
        if (type(text) is not str or type(score) not in (int,float) or not math.isfinite(score) or not .85 <= score <= 1
                or type(box) is not list or len(box)!=4 or any(type(v) is not int for v in box)
                or box[2]<=0 or box[3]<=0 or box[0]<roi[0] or box[1]<roi[1]
                or box[0]+box[2]>roi[0]+roi[2] or box[1]+box[3]>roi[1]+roi[3]
                or not text.endswith('职能的')):
            raise BatchStopped('event_unknown')
        labels.append(text[:-3])
    value=PAIRS.get(tuple(labels))
    if value is None: raise BatchStopped('event_pair_unqualified')
    return RecruitPair(tuple(labels),value[0],value[1:])


@contextmanager
def pair_guard(tasker,pair=None):
    if pair is not None and (type(pair) is not RecruitPair or PAIRS.get(pair.labels)!=(pair.event_id,*pair.choice_ids)):
        raise BatchStopped('event_unknown')
    resource=tasker.resource
    names=('LabyRecruitClass0','LabyRecruitClass1')
    original={name:resource.get_node_data(name) for name in names}
    patterns={name:dict(expected='^'+(pair.labels[i] if pair else '(?:坦克型|治疗型|强化型|增益型)')+'职能的$') for i,name in enumerate(names)}
    if not resource.override_pipeline(patterns):raise BatchStopped('event_guard_load_failed')
    try:yield
    finally:
        if not resource.override_pipeline(original):raise BatchStopped('event_guard_restore_failed')
