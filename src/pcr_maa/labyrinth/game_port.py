"""Maa restoration, invitation and the complete single-run autoplay adapter.

Protocol creation/identity remain protocol facts. The independent game proves
Title identity, the empty-to-joined invitation and a fresh area-1 map. A logical
START comes from that new-run sequence, never from invented pixel evidence.
"""
from copy import deepcopy
from .. import native
from .batch import BatchStopped
from .game_session import MaaGameSession, game_session_pipeline, _root, _box
from .opening import MaaOpening
from .session import resume_home_pipeline
from .interruptions import date_changed_pipeline
from .snapshots import OpeningSnapshot


def game_port_pipeline():
    """Dedicated single inputs, with separate fresh waiting tasks."""
    nodes=resume_home_pipeline()
    nodes['LabyClientResumeHomeDepartOnce']['next']=[]
    readonly=date_changed_pipeline();readonly.pop('LabyDateChangedConfirmOnce')
    nodes.update(readonly)
    from .portrait_cache import joined_portrait_pipeline
    for count in (1,2,3):nodes.update(joined_portrait_pipeline(count))
    joined=[f'LabyPortraitJoined{count}Observed' for count in (1,2,3)]
    nodes['LabyGamePortOpeningObserved']=dict(recognition='Or',
        any_of=['LabyDateChangedObserved','LabyDepartureRewardObserved']+joined+['LabyOpeningEmptyObserved'],
        action='DoNothing',next=[],timeout=5000,rate_limit=250,pre_delay=0,post_delay=0)
    nodes['LabyGamePortRosterObserved']=dict(recognition='Or',
        any_of=['LabyDateChangedObserved']+joined+['LabyOpeningEmptyObserved'],
        action='DoNothing',next=[],timeout=5000,rate_limit=250,pre_delay=0,post_delay=0)
    return nodes


class MaaGamePort:
    def __init__(self,tasker,stamp):
        if not callable(stamp):raise ValueError('owner_stamp_required')
        self.tasker,self.stamp=tasker,stamp
        self.session=None;self.opening=None;self.used=set();self.restarted=set();self.blocked=False
        self.runtime=None;self.runner=None
        self._restore_result=self._restore_snapshot=self._restore_batch=self._restore_facts=None

    def _checkpoint(self,batch):
        batch.checkpoint()
        if self.blocked:raise BatchStopped('game_port_blocked')
        if getattr(self.tasker,'stopping',False):raise BatchStopped('stopped')

    def _wait(self,batch,node,names):
        self._checkpoint(batch)
        with batch.timings.measure('page_wait'):receipt=native.run(self.tasker,node)
        self._checkpoint(batch);raw=_root(receipt,node).get('recognition')
        if (type(raw) is not list or not 1<=len(raw)<=len(names)
                or any(type(row) is not dict or row.get('algorithm')!='And' for row in raw)
                or [row.get('name') for row in raw]!=names[:len(raw)]
                or any(row.get('box') is not None for row in raw[:-1])
                or not _box(raw[-1].get('box'))):
            raise BatchStopped('game_port_navigation_unknown')
        if raw[-1]['name']=='LabyDateChangedObserved':raise BatchStopped('date_changed')
        return raw[-1]['name'],receipt

    def _input(self,batch,node):
        self._checkpoint(batch)
        # Only after a new Batch intent and the same-enter/unknown gates. This
        # resets this one scoped navigation node, never a business retry budget.
        with batch.timings.measure('page_wait'):receipt=native.run(self.tasker,node,reset_hits=(node,))
        self._checkpoint(batch);action=_root(receipt,node).get('action')
        if type(action) is not dict or action.get('success') is not True:
            raise BatchStopped('game_port_input_unknown')
        return receipt

    def _initial_roster(self,batch,snapshot):
        pipeline=game_port_pipeline()
        # Protocol supplies the actual guild. This popup is the common guild
        # selection-imprint reward; only its actual title/body/close are used.
        pipeline['LabyDepartureGuildReward']=dict(expected=r'.+选择印记')
        if not self.tasker.resource.override_pipeline(pipeline):
            raise BatchStopped('game_port_pipeline_unavailable')
        fields=dict(enter_id=snapshot.enter_id,source='maa')
        joined_names=[f'LabyPortraitJoined{count}Observed' for count in (1,2,3)]
        opening_names=['LabyDateChangedObserved','LabyDepartureRewardObserved']+joined_names+['LabyOpeningEmptyObserved']
        roster_names=['LabyDateChangedObserved']+joined_names+['LabyOpeningEmptyObserved']
        def depart(oid):
            clicked=self._input(batch,'LabyClientResumeHomeDepartOnce')
            return self.stamp(operation_id=oid,**fields,native=clicked,input_native=clicked,input_action_verified=True)
        clicked=batch.transaction('opening_depart',fields,depart)
        page,seen=self._wait(batch,'LabyGamePortOpeningObserved',opening_names)
        arrived=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page=page,native=seen,input_native=clicked['input_native']),**fields)
        if arrived['page']=='LabyDepartureRewardObserved':
            def close(oid):
                clicked=self._input(batch,'LabyDepartureRewardCloseOnce')
                return self.stamp(operation_id=oid,**fields,native=clicked,input_native=clicked,input_action_verified=True)
            clicked=batch.transaction('opening_departure_reward_close',fields,close)
            page,seen=self._wait(batch,'LabyGamePortRosterObserved',roster_names)
            arrived=batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page=page,native=seen,input_native=clicked['input_native']),**fields)
        if arrived['page'] in joined_names:
            return self.opening.consume_granted(batch,snapshot.enter_id,guild_id=snapshot.guild_id,
                creation_operation_id=self._active_creation_oid)
        return None

    def _restart_client(self,batch,snapshot):
        """Invalidate the cached UI once after protocol creation, via Maa only.

        Package is the independently observed B-server foreground package.
        Stop/Start are two durable navigation intents. No recovery loop or Back
        is used. Each action ACK settles once; fresh Title/Home readiness is
        a separate readonly follow-up and cannot mint a game handoff.
        """
        self._checkpoint(batch)
        key=(snapshot.enter_id,self._active_creation_oid)
        if key in self.restarted:raise BatchStopped('client_restart_already_attempted')
        self.restarted.add(key)
        nodes=game_session_pipeline(snapshot.account_id)
        nodes.update({
            'LabyGamePortStopClientOnce':dict(recognition='DirectHit',action='StopApp',
                package='com.bilibili.priconne',max_hit=1,next=[],pre_delay=0,post_delay=0),
            'LabyGamePortStartClientOnce':dict(recognition='DirectHit',action='StartApp',
                package='com.bilibili.priconne',max_hit=1,next=[],pre_delay=0,post_delay=0),
        })
        if not self.tasker.resource.override_pipeline(nodes):
            raise BatchStopped('game_port_restart_unavailable')
        fields=dict(source='maa',protocol_enter_id=snapshot.enter_id,
            creation_operation_id=self._active_creation_oid,package='com.bilibili.priconne')
        def stop(oid):
            self._checkpoint(batch)
            with batch.timings.measure('page_wait'):
                receipt=native.run(self.tasker,'LabyGamePortStopClientOnce',reset_hits=('LabyGamePortStopClientOnce',))
            action=_root(receipt,'LabyGamePortStopClientOnce').get('action')
            if (type(action) is not dict or action.get('name')!='LabyGamePortStopClientOnce'
                    or action.get('success') is not True or type(action.get('raw')) is not dict
                    or action['raw'].get('package')!=fields['package']):
                raise BatchStopped('game_port_restart_input_unknown')
            self._checkpoint(batch)
            return self.stamp(operation_id=oid,**fields,stop_app_action_verified=True,
                client_ready_verified=False,native=receipt,input_native=receipt)
        batch.transaction('client_stop_after_protocol',fields,stop)
        def start(oid):
            self._checkpoint(batch)
            with batch.timings.measure('page_wait'):
                sent=native.run(self.tasker,'LabyGamePortStartClientOnce',reset_hits=('LabyGamePortStartClientOnce',))
            action=_root(sent,'LabyGamePortStartClientOnce').get('action')
            if (type(action) is not dict or action.get('name')!='LabyGamePortStartClientOnce'
                    or action.get('success') is not True or type(action.get('raw')) is not dict
                    or action['raw'].get('package')!=fields['package']):
                raise BatchStopped('game_port_restart_input_unknown')
            self._checkpoint(batch)
            return self.stamp(operation_id=oid,**fields,start_app_action_verified=True,
                client_ready_verified=False,native=sent,input_native=sent)
        launched=batch.transaction('client_start_after_protocol',fields,start)
        # The known input ACK is already durable. Waiting is readonly: Stop or
        # an unknown page cannot turn that ACK into an unresolved launch input.
        self._checkpoint(batch)
        page,observed=self.session._entry(batch,recovery=True)
        self._checkpoint(batch)
        if page not in ('title','client_home'):raise BatchStopped('game_port_restart_unknown')
        ready=self.stamp(**fields,launch_operation_id=launched['operation_id'],
            start_app_action_verified=True,client_ready_verified=True,
            session_page=page,native=observed,input_native=launched['input_native'])
        return batch.observation(ready,**fields,client_ready_verified=True)

    def restore_opening(self,snapshot,batch,*,creation):
        self._checkpoint(batch)
        if (type(snapshot) is not OpeningSnapshot or snapshot.difficulty!=batch.settings.difficulty
                or type(snapshot.guild_id) is not int or not 1<=snapshot.guild_id<=5):
            raise BatchStopped('game_port_opening_scope_unqualified')
        if (snapshot.account_id!=batch.binding.account_id or type(creation) is not dict
                or creation.get('verified') is not True or creation.get('source')!='protocol'
                or creation.get('binding')!=batch.binding.payload()
                or type(creation.get('frame')) is not int or not 0<creation['frame']<=batch.last_frame
                or type(creation.get('operation_id')) is not str or not creation['operation_id']
                or any(creation.get(k)!=getattr(snapshot,k) for k in ('enter_id','guild_id','difficulty'))):
            raise BatchStopped('game_port_creation_unknown')
        require=getattr(batch.port,'require_created_opening',None)
        if callable(require):require(snapshot,batch=batch)
        elif snapshot.protocol_current_block_id not in (None,0):raise BatchStopped('game_port_creation_unknown')
        events=batch.journal.events()
        if events and events[-1]['state']=='in_flight':raise BatchStopped('blocked_in_flight')
        key=(snapshot.enter_id,creation['operation_id'])
        if key in self.used:
            raise BatchStopped('game_port_opening_already_attempted')
        self.used.add(key)
        if self.session is None:self.session=MaaGameSession(self.tasker,self.stamp,timings=batch.timings)
        if self.opening is None:self.opening=MaaOpening(self.tasker,self.stamp,timings=batch.timings)
        self.session.timings=self.opening.timings=batch.timings
        try:
            self._active_creation_oid=creation['operation_id']
            self._restart_client(batch,snapshot)
            session=self.session.restore_new_opening(batch,snapshot,
                current_block_id=snapshot.protocol_current_block_id,creation_operation_id=creation['operation_id'])
            if session.page!='labyrinth_home' or session.title_player_id!=snapshot.account_id:
                raise BatchStopped('game_port_session_unknown')
            pregranted=self._initial_roster(batch,snapshot)
            joined=self.opening.invite(batch,snapshot.enter_id,guild_id=snapshot.guild_id,
                slots=batch.settings.strategy.opening_rosters.get(str(snapshot.guild_id)),
                creation_operation_id=creation['operation_id'])
            self._checkpoint(batch)
            from .scene import MaaSceneRuntime
            self.runtime=MaaSceneRuntime(self.tasker,self.stamp,timings=batch.timings,stopped=batch.stopped)
            scene=self.runtime.wait_for(batch,{'map'},source='joined',timeout=15,
                reason='game_port_initial_map_unknown')
            if scene.kind!='map' or scene.payload.get('area')!=1:
                raise BatchStopped('game_port_initial_map_unknown')
            starts=[n for n in snapshot.nodes if n.area==1 and n.column==1 and n.block_type==1]
            if len(starts)!=1 or snapshot.protocol_current_block_id not in (None,0,starts[0].block_id):
                raise BatchStopped('game_port_start_unknown')
            invitation=joined.get('invitation')
            if (type(invitation) is not dict or type(invitation.get('closed')) is not dict
                    or type(invitation['closed'].get('frame')) is not int):
                raise BatchStopped('game_port_invitation_unknown')
            map_stamp=self.stamp(source='maa',enter_id=snapshot.enter_id,native=scene.native)
            if map_stamp['frame']<=invitation['closed']['frame']:
                raise BatchStopped('game_port_initial_map_unknown')
            existing=creation.get('opening_origin')=='observed_existing'
            result=self.stamp(scope='fresh_opening_current',source='maa',
                position_basis='observed_opening_invitation_map_start' if existing else 'created_opening_invitation_map_start',
                start_source='observed_invitation_map_sequence' if existing else 'created_invitation_map_sequence',
                current_marker_binding_verified=True,ui_map_fingerprint_verified=False,
                next_target_qualified=False,safe_to_navigate=False,
                enter_id=snapshot.enter_id,guild_id=snapshot.guild_id,difficulty=snapshot.difficulty,
                game_account_id=session.title_player_id,account_source=session.account_source,
                protocol_map_sha256=snapshot.fingerprint,
                protocol_current_block_id=snapshot.protocol_current_block_id,
                game_area=1,game_current_block_id=starts[0].block_id,
                creation_operation_id=creation['operation_id'],invitation=invitation,
                roles_joined=sorted(set(joined['roles_joined'])|set(pregranted['roles_joined'] if pregranted else ())),
                initial_granted=pregranted,
                initial_map_native=scene.native,initial_map_area=1,initial_map_frame=map_stamp['frame'],
                session_native=session.native,account_native=session.account_native)
            self._restore_result=result;self._restore_facts=deepcopy(result)
            self._restore_snapshot=snapshot;self._restore_batch=batch
            return result
        except BaseException:
            self.blocked=True
            raise

    def verify_start_handoff(self,snapshot,raw,batch):
        """Only this adapter's actual completed restore can assert this basis."""
        try:
            self._checkpoint(batch)
            if (raw is not self._restore_result or snapshot is not self._restore_snapshot
                    or batch is not self._restore_batch or raw!=self._restore_facts):
                return False
            require=getattr(batch.port,'require_created_opening',None)
            if callable(require):require(snapshot,batch=batch)
            starts=[n for n in snapshot.nodes if n.area==1 and n.column==1 and n.block_type==1]
            invitation=raw['invitation']
            return (len(starts)==1 and raw['game_current_block_id']==starts[0].block_id
                and raw['game_account_id']==snapshot.account_id
                and raw['initial_map_area']==raw['game_area']==1
                and invitation['closed']['frame']<raw['initial_map_frame']<=raw['frame']
                and (raw['position_basis'],raw['start_source']) in (
                    ('created_opening_invitation_map_start','created_invitation_map_sequence'),
                    ('observed_opening_invitation_map_start','observed_invitation_map_sequence')))
        except (KeyError,TypeError,ValueError,BatchStopped):
            return False

    def play(self,route,batch):
        self._checkpoint(batch)
        if (self._restore_batch is not batch or self._restore_result is None
                or self._restore_result['enter_id']!=route.enter_id):
            raise BatchStopped('game_port_restore_unknown')
        from .autoplay import AutoplayRunner
        if self.runner is not None and self.runner.started:
            # Each new accepted opening receives its own single-use controller.
            if getattr(self.runner,'creation_operation_id',None)==self._restore_result['creation_operation_id']:
                raise BatchStopped('autoplay_already_attempted')
        self.runner=AutoplayRunner(self.tasker,self.stamp,runtime=self.runtime)
        self.runner.enter_id=route.enter_id
        self.runner.creation_operation_id=self._restore_result['creation_operation_id']
        try:
            return self.runner.play(route,batch,opening=self.opening,roles=self._restore_result['roles_joined'])
        except BaseException:
            self.blocked=True
            raise

    def buy_relic(self,quote,operation_id):
        if self.blocked or self.runner is None or not self.runner.started:
            raise BatchStopped('game_port_purchase_without_run')
        adapter=getattr(self.runner.actions,'shop_adapter',None)
        method=getattr(adapter,'purchase_once',None)
        if not callable(method):raise BatchStopped('shop_quote_not_prepared')
        return method(quote,operation_id)
