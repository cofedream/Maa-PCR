"""Native node battle waiting; interruption stops and victories are not clears."""
from .. import native
from .batch import BatchStopped
from .interruptions import date_changed_pipeline
from .timing import Timings


def read_node_result(tasker,enter_id,block_id,*,timings=None):
    if type(enter_id) is not int or enter_id<=0 or type(block_id) is not int or block_id<=0:raise BatchStopped('battle_identity_required')
    timings=timings or Timings()
    resource=tasker.resource;node='LabyNodeBattleOutcomeObserved'
    original=resource.get_node_data(node)
    if not original:raise BatchStopped('battle_wait_unavailable')
    readonly=date_changed_pipeline();readonly.pop('LabyDateChangedConfirmOnce')
    readonly[node]=dict(any_of=['LabyNormalBattleWinObserved','LabyNodeBattleDefeatedObserved','LabyDateChangedObserved'])
    if not resource.override_pipeline(readonly):raise BatchStopped('battle_wait_unavailable')
    try:
        # Preserve the caller's timeout/rate limit and restore the original wait.
        # No interruption input callback or diagnostic confirmation is registered.
        with timings.measure('page_wait'):receipt=native.run(tasker,node)
    finally:
        if not resource.override_pipeline({node:original}):raise BatchStopped('battle_wait_restore_failed')
    if not receipt['native_ok']:raise BatchStopped('battle_result_unknown')
    raw=next((n['recognition'] for n in receipt['nodes'] if n['name']==node and n['completed']),None)
    expected=['LabyNormalBattleWinObserved','LabyNodeBattleDefeatedObserved','LabyDateChangedObserved']
    # Maa Or reports failed prior alternatives as box=None before the first hit.
    if (type(raw) is not list or not 1<=len(raw)<=len(expected)
            or any(type(row) is not dict for row in raw)
            or [row.get('name') for row in raw]!=expected[:len(raw)]
            or any(row.get('algorithm')!='And' for row in raw)
            or any(row.get('box') is not None for row in raw[:-1])):
        raise BatchStopped('battle_result_unknown')
    box=raw[-1].get('box')
    if (type(box) is not list or len(box)!=4 or any(type(v) is not int for v in box)
            or box[0]<0 or box[1]<0 or box[2]<=0 or box[3]<=0
            or box[0]+box[2]>1280 or box[1]+box[3]>720):
        raise BatchStopped('battle_result_unknown')
    name=raw[-1]['name']
    if name=='LabyDateChangedObserved':raise BatchStopped('date_changed')
    outcome='won' if name=='LabyNormalBattleWinObserved' else 'defeated' if name=='LabyNodeBattleDefeatedObserved' else None
    if outcome is None:raise BatchStopped('battle_result_unknown')
    return dict(verified=True,enter_id=enter_id,block_id=block_id,outcome=outcome,node_victory_verified=outcome=='won',run_clear_verified=False,native=receipt)
