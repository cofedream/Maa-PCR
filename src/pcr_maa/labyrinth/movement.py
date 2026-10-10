"""Read-only ordinary/LINK movement-page wiring; no navigation input.

Batch retains the accepted area-1 opening session; a qualified area-2 cursor
is still pending. These observations cannot advance that runtime session.
A native task ID is receipt integrity, not a fresh account/current-position proof.
There is intentionally no MaaNodeMovement, move_next, Click or settlement here.
"""
from copy import deepcopy
import re
from .. import native
from .batch import BatchStopped
from .game_session import _root,_box
from .interruptions import date_changed_pipeline,date_changed_readback
from .session import reconnect_pipeline
from .links import link_pipeline,link_offers
from .relics import _words,_normalized


_LABELS={
    'LabyNodeMoveTitle':((490,170,320,55),r'^移动目标确认$'),
    'LabyNodeMoveNormalDescription':((430,365,450,80),r'^将移动到战斗格子[（(]普通[）)][。.]?$'),
    'LabyNodeMoveLinkDescription':((480,375,330,70),r'^将移动到连结印记格子[。.]?$'),
    'LabyNodeMoveConfirmLabel':((670,460,250,70),r'^确认$'),
    'LabyNodeMoveCancelLabel':((375,460,245,70),r'^取消$'),
    'LabyNodeChallengeTitle':((76,52,214,43),r'^战斗格子[（(]普通[）)]$'),
    'LabyNodeChallengeLabel':((945,560,310,110),r'^挑战$'),
    'LabyNodeChallengeMapLabel':((650,560,310,110),r'^查看地图$'),
}
_PAGES={
    (2,'preview'):('LabyNodeMoveNormalWait','LabyNodeMoveNormalObserved','normal_move_confirmation'),
    (4,'preview'):('LabyNodeMoveLinkWait','LabyNodeMoveLinkObserved','link_move_confirmation'),
    (2,'arrival'):('LabyNodeArriveNormalWait','LabyNodeArriveNormalObserved','normal_challenge'),
    (4,'arrival'):('LabyNodeArriveLinkWait','LabyLinkObserved','link_choice'),
}
_INTERRUPTS=('LabyDateChangedObserved','LabyDisconnect6002Observed')


def _scope(block_type,phase):
    if type(block_type) is not int or type(phase) is not str or (block_type,phase) not in _PAGES:
        raise BatchStopped('movement_scope_unqualified')
    return _PAGES[(block_type,phase)]


def movement_pipeline():
    """Specific modal/arrival observations plus stop pages; every node is inert."""
    nodes={name:dict(recognition='OCR',expected=pattern,roi=list(roi),threshold=.85,
        action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)
        for name,(roi,pattern) in _LABELS.items()}
    # A fixed complete title crop avoids batch detector splitting its suffix.
    nodes['LabyNodeChallengeTitle']['only_rec']=True
    def page(name,labels):
        nodes[name]=dict(recognition='And',all_of=labels,action='DoNothing',next=[],
                         timeout=3000,pre_delay=0,post_delay=0)
    for kind,label in (('Normal','LabyNodeMoveNormalDescription'),('Link','LabyNodeMoveLinkDescription')):
        page('LabyNodeMove'+kind+'Observed',['LabyNodeMoveTitle',label,
             'LabyNodeMoveConfirmLabel','LabyNodeMoveCancelLabel'])
    page('LabyNodeArriveNormalObserved',['LabyNodeChallengeTitle','LabyNodeChallengeLabel','LabyNodeChallengeMapLabel'])
    # Only reachable read-only guards are copied; no dormant input node is exported.
    for pipeline,root in ((date_changed_pipeline(),'LabyDateChangedObserved'),
                          (reconnect_pipeline(),'LabyDisconnect6002Observed'),
                          (link_pipeline(),'LabyLinkObserved')):
        nodes.update({name:deepcopy(pipeline[name]) for name in [root]+pipeline[root]['all_of']})
    for entry,observed,_ in _PAGES.values():
        nodes[entry]=dict(recognition='Or',any_of=[*_INTERRUPTS,observed],
            action='DoNothing',next=[],timeout=5000,rate_limit=250,pre_delay=0,post_delay=0)
    return nodes


def _unknown():raise BatchStopped('movement_readback_unknown')


def _labels(raw,names,pipeline):
    if (type(raw) is not list or len(raw)!=len(names)
            or any(type(row) is not dict for row in raw)
            or [row.get('name') for row in raw]!=names):_unknown()
    for row,name in zip(raw,names):
        node=pipeline[name]
        if row.get('algorithm')!='OCR':_unknown()
        try:words=_words(row.get('detail'),node['roi'])
        except BatchStopped:_unknown()
        word=words[0];detail=row['detail']
        if (len(words)!=1 or row.get('box')!=list(word.box)
                or detail.get('best')!=detail['filtered'][0]
                or (node.get('only_rec') is True and list(word.box)!=node['roi'])
                or re.fullmatch(node['expected'],_normalized(word.text)) is None):_unknown()


def _nested(receipt,name,raw):
    return dict(receipt,nodes=[dict(name=name,completed=True,recognition=raw)])


def movement_page_readback(receipt,block_type,*,phase):
    """Parse one native Or task, including its failed earlier alternatives.

    This shape parser does not authenticate a caller-supplied receipt or confer
    freshness, session identity, node identity, action success or settlement.
    Only observe_movement_page actually runs the read-only native task.
    """
    entry,expected,page=_scope(block_type,phase)
    try:root=_root(receipt,entry)
    except BatchStopped:_unknown()
    action=root.get('action')
    if (type(action) is not dict or action.get('name')!=entry
            or action.get('success') is not True or action.get('raw')!={}):_unknown()
    raw=root.get('recognition');names=[*_INTERRUPTS,expected]
    if (type(raw) is not list or not 1<=len(raw)<=len(names)
            or any(type(row) is not dict or row.get('algorithm')!='And' for row in raw)
            or [row.get('name') for row in raw]!=names[:len(raw)]
            or any(row.get('box') is not None for row in raw[:-1])
            or not _box(raw[-1].get('box'))):_unknown()
    selected=raw[-1];name=selected['name'];details=selected.get('detail')
    pipeline=movement_pipeline()
    if name=='LabyDateChangedObserved':
        try:date_changed_readback(_nested(receipt,name,details))
        except BatchStopped:_unknown()
        raise BatchStopped('date_changed')
    if name=='LabyDisconnect6002Observed':
        _labels(details,pipeline[name]['all_of'],pipeline)
        raise BatchStopped('disconnected_6002')
    offers=()
    if name=='LabyLinkObserved':
        try:offers=link_offers(_nested(receipt,name,details))
        except BatchStopped:_unknown()
    else:_labels(details,pipeline[name]['all_of'],pipeline)
    result=dict(scope='node_movement_page',source='native_receipt_shape',block_type=block_type,
        phase=phase,page=page,page_observed=True,input_performed=False,settled=False,
        safe_to_navigate=False,next_target_qualified=False,account_verified=False,
        session_verified=False,freshness_verified=False,ui_map_fingerprint_verified=False,
        run_clear_verified=False,native=deepcopy(receipt))
    if offers:result['link_offers']=offers
    return result


def _checkpoint(tasker,stopped):
    if getattr(tasker,'stopping',False):raise BatchStopped('stopped')
    if stopped is None:return
    if not callable(stopped):raise BatchStopped('movement_stop_unknown')
    try:value=stopped()
    except Exception:raise BatchStopped('movement_stop_unknown') from None
    if type(value) is not bool:raise BatchStopped('movement_stop_unknown')
    if value:raise BatchStopped('stopped')


def observe_movement_page(tasker,block_type,*,phase,stopped=None):
    """Single new native read; no hit reset, input, recovery or business retry."""
    entry,_,_=_scope(block_type,phase)
    _checkpoint(tasker,stopped)
    receipt=native.run(tasker,entry)
    _checkpoint(tasker,stopped)
    result=movement_page_readback(receipt,block_type,phase=phase)
    result['source']='maa_native_readback'
    return result
