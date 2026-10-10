"""Bounded fresh-run batches. Maa adapters supply fresh, verified observations.

The journal is shared by both bot acquisition modes. An unresolved operation
stops subsequent invocations; changing guild/configuration does not reset it.
"""
from dataclasses import dataclass
from time import monotonic
from uuid import uuid4
from .routes import select_route,Route
from .records import RunRecord
from .timing import Timings
from .snapshots import OpeningSnapshot

class BatchStopped(RuntimeError):
    pass

def current_position_basis(observation):
    """Minimum independent local evidence, not a graph or target qualification.

    The consensus matcher still validates the complete observation schema and
    searches every feasible mapping. A marker alone cannot qualify this handoff.
    """
    if type(observation) is not dict: return False
    columns,marker=observation.get('columns'),observation.get('current_marker')
    if (type(columns) is not list or len(columns)<2 or type(marker) is not dict
            or type(marker.get('column')) is not int or type(marker.get('node')) is not int):
        return False
    for i,column in enumerate(columns):
        if type(column) is not dict or type(column.get('nodes')) is not list: return False
        for j,node in enumerate(column['nodes']):
            if type(node) is not dict: return False
            if ((i,j)!=(marker['column'],marker['node'])
                    and type(node.get('block_type')) is int and 1<=node['block_type']<=8):
                return True
    return False

@dataclass(frozen=True)
class Binding:
    account_id: int
    device_id: str
    def __post_init__(self):
        if type(self.account_id) is not int or self.account_id <= 0 or not self.device_id:
            raise ValueError('binding_required')
    def payload(self):
        return dict(account_id=self.account_id, device_id=self.device_id)

@dataclass(frozen=True)
class GameRunSession:
    """Ephemeral accepted opening facts; not a click or area-transition permit."""
    run_id: str
    binding: Binding
    route: Route
    guild_id: int
    difficulty: int
    creation_operation_id: str
    protocol_map_sha256: str
    current_block_id: int
    area: int
    accepted_frame: int

class Batch:
    def __init__(self, settings, port, journal, binding, *, stopped=lambda:False, clock=monotonic, progress=lambda v:None,resume=False):
        self.settings,self.port,self.journal,self.binding=settings,port,journal,binding
        self.stopped,self.clock,self.progress=stopped,clock,progress
        self.last_frame=-1
        self.timings=getattr(port,'timings',None) or Timings()
        self.run_id=uuid4().hex
        self.completed={g.guild_id:0 for g in settings.goals}
        self._game_session=None;self._game_session_owner=None
        self._first_acquire_done=False;self.resume=resume;self.failed={g.guild_id:0 for g in settings.goals}

    def invalidate_game_session(self):
        self._game_session=None;self._game_session_owner=None

    def _game_owner(self):
        game=getattr(self.port,'game',None)
        return self.port,game,getattr(game,'tasker',None),getattr(game,'stamp',None)

    def require_game_session(self,session,route):
        """Accept only this Batch's retained handle, never a caller-built copy.

        The initial position remains area1 until a separate, qualified movement
        or transition implementation reads back and commits a new position.
        Native recognition IDs cannot advance this handle or prove freshness.
        """
        self.checkpoint()
        owner=self._game_owner();previous=self._game_session_owner
        if (type(session) is not GameRunSession or session is not self._game_session
                or previous is None or any(a is not b for a,b in zip(owner[:3],previous[:3]))
                or owner[3]!=previous[3] or session.run_id!=self.run_id
                or session.binding!=self.binding or session.route!=route
                or session.accepted_frame>self.last_frame):
            raise BatchStopped('game_session_unknown')
        return session

    def game_session(self,route):
        return self.require_game_session(self._game_session,route)

    def checkpoint(self):
        try:stopped=self.stopped()
        except BaseException:
            self.invalidate_game_session();raise
        if stopped:
            self.invalidate_game_session();raise BatchStopped('stopped')

    def observation(self, value, **expected):
        self.checkpoint()
        if (type(value) is not dict or value.get('verified') is not True
                or value.get('binding') != self.binding.payload()
                or type(value.get('frame')) is not int or value['frame'] <= self.last_frame
                or any(value.get(k)!=v for k,v in expected.items())):
            self.invalidate_game_session()
            raise BatchStopped('observation_unknown')
        self.last_frame=value['frame']
        return value

    def transaction(self, operation, expected, send,*,operation_id=None):
        # Ledger failures and cancellation revoke the accepted game session too.
        from ..native import NativeInputNotSent
        try:
            self.checkpoint()
            events=self.journal.events()
            if events and events[-1]['state']=='in_flight':
                self.invalidate_game_session();raise BatchStopped('blocked_in_flight')
            operation_id=operation_id or uuid4().hex
            intent=dict(state='in_flight',operation=operation,operation_id=operation_id,
                        binding=self.binding.payload(),**expected)
            self.journal.append(intent)
            # An unresolved or partly written intent is never retried.
            try:result=send(operation_id)
            except NativeInputNotSent as error:
                # Positive native completion evidence only; never reconcile old unknowns.
                self.journal.append(dict(intent,state='settled',result=dict(
                    operation_id=operation_id,binding=self.binding.payload(),
                    input_execution='not_sent',input_action_verified=False,
                    business_result_verified=False,native=error.receipt)))
                raise BatchStopped('native_input_not_sent') from error
            self.observation(result,operation_id=operation_id,**expected)
            self.journal.append(dict(intent,state='settled',result=result))
            return result
        except BaseException as error:
            if not (isinstance(error,BatchStopped) and error.args==('native_input_not_sent',)
                    and isinstance(error.__cause__,NativeInputNotSent)):
                self.invalidate_game_session()
            raise

    def route_for(self, opening, guild_id, enter_id,*,consume=True):
        if consume:self.observation(opening,enter_id=enter_id,guild_id=guild_id,difficulty=self.settings.difficulty,current_block_id=0)
        if opening.get('complete') is not True: raise BatchStopped('incomplete_map')
        try:
            retained=getattr(self.port,'require_opening_snapshot',None)
            if callable(retained):snapshot=retained(opening)
            else:
                snapshot=OpeningSnapshot.read(self.binding.account_id,enter_id,
                    {k:opening[k] for k in ('enter_id','guild_id','difficulty')},
                    dict(guild_id=opening['guild_id'],block_id=opening.get('protocol_current_block_id',opening['current_block_id']),map_list=opening['map_list']))
            if ('protocol_current_block_id' in opening and
                    (type(opening['protocol_current_block_id']) is not type(snapshot.protocol_current_block_id)
                     or opening['protocol_current_block_id']!=snapshot.protocol_current_block_id)):
                raise ValueError('opening_position_mismatch')
            if opening.get('map_sha256')!=snapshot.fingerprint:raise ValueError('opening_fingerprint_mismatch')
            nodes=snapshot.nodes
            with self.timings.measure('strategy'):
                route=select_route(opening['enter_id'],nodes,self.settings.difficulty,self.settings.policy,
                                   source=opening['source'])
        except (KeyError,TypeError,ValueError): raise BatchStopped('map_invalid') from None
        return route

    def acquire(self,guild_id):
        self.invalidate_game_session()
        first=not self._first_acquire_done;self._first_acquire_done=True
        top=self.observation(self.port.top());started=self.clock();reused=None
        if top.get('fresh_start') is not True:
            enter=top.get('enter_id')
            if type(enter) is not int or enter<=0:raise BatchStopped('top_unknown')
            observe=getattr(self.port,'observe_existing_opening',None)
            if first and callable(observe):
                read_id=uuid4().hex
                actual=observe(guild_id,self.settings.difficulty,read_id,policy=self.settings.policy)
                if actual is not None:
                    reused=self.transaction('existing_opening',dict(enter_id=enter,guild_id=guild_id,
                        difficulty=self.settings.difficulty),lambda oid:actual,operation_id=read_id)
            if reused is None:
                self.transaction('retire',dict(enter_id=enter),lambda oid:self.port.retire(enter,oid))
                self.observation(self.port.top(),enter_id=0)
        for attempt in range(1,self.settings.max_attempts+1):
            self.checkpoint()
            if self.clock()-started >= self.settings.max_seconds: raise BatchStopped('reroll_time_limit')
            self.progress(dict(stage='opening',guild_id=guild_id,attempt=attempt,completed=dict(self.completed)))
            if attempt==1 and reused is not None:
                opening=observed=reused
                route=self.route_for(observed,guild_id,opening['enter_id'],consume=False)
            else:
                opening=self.transaction('enter',dict(guild_id=guild_id,difficulty=self.settings.difficulty),
                    lambda oid:self.port.enter(guild_id,self.settings.difficulty,oid))
                observed=self.port.read_opening(opening['enter_id'])
                route=self.route_for(observed,guild_id,opening['enter_id'])
            if route is not None:
                # Keep the complete map in the protocol. Maa independently
                # proves only the fresh opening's current marker plus invitation.
                if self.clock()-started >= self.settings.max_seconds: raise BatchStopped('reroll_time_limit')
                self.progress(dict(stage='handoff',guild_id=guild_id,completed=dict(self.completed)))
                live=self.observation(self.port.handoff(route,self),enter_id=route.enter_id,guild_id=guild_id,
                    difficulty=self.settings.difficulty,game_account_id=self.binding.account_id,
                    protocol_current_block_id=observed.get('protocol_current_block_id',observed['current_block_id']),protocol_map_sha256=observed['map_sha256'],
                    game_area=1,game_current_block_id=route.nodes[0].block_id,
                    creation_operation_id=opening['operation_id'],source='protocol_maa_handoff',
                    identity_source='protocol',scope='fresh_opening_current',
                    current_marker_binding_verified=True,ui_map_fingerprint_verified=False,next_target_qualified=False)
                if any(type(live.get(k)) is not int for k in
                        ('enter_id','guild_id','difficulty','game_account_id','game_area','game_current_block_id')):
                    raise BatchStopped('observation_unknown')
                if live.get('protocol_current_block_id') is not None and type(live.get('protocol_current_block_id')) is not int:
                    raise BatchStopped('observation_unknown')
                if (live.get('current_marker_binding_verified') is not True
                        or live.get('ui_map_fingerprint_verified') is not False
                        or live.get('next_target_qualified') is not False):
                    raise BatchStopped('observation_unknown')
                binding=live.get('current_binding')
                sequence_basis=live.get('position_basis')
                sequence_start=sequence_basis in ('created_opening_invitation_map_start','observed_opening_invitation_map_start')
                if sequence_start:
                    require=getattr(self.port,'require_handoff_receipt',None)
                    if (not callable(require) or require(live) is not live
                            or type(binding) is not dict
                            or binding.get('scope')!=sequence_basis
                            or binding.get('start_source')!=('observed_invitation_map_sequence' if sequence_basis=='observed_opening_invitation_map_start' else 'created_invitation_map_sequence')
                            or live.get('initial_map_area')!=1):
                        raise BatchStopped('handoff_current_binding_unknown')
                elif (type(binding) is not dict or binding.get('scope')!='current_marker_consensus'
                        or binding.get('search_complete') is not True
                        or type(binding.get('candidate_count')) is not int or binding['candidate_count']<1):
                    raise BatchStopped('handoff_current_binding_unknown')
                if (binding.get('current_marker_binding_verified') is not True
                        or type(binding.get('bound_protocol_block_id')) is not int
                        or binding['bound_protocol_block_id']!=live['game_current_block_id']
                        or binding.get('ui_map_fingerprint_verified') is not False
                        or binding.get('next_target_qualified') is not False
                        or binding.get('safe_to_navigate') is not False):
                    raise BatchStopped('handoff_current_binding_unknown')
                # Only the already validated live handoff can create this owner
                # handle. It stores actual accepted current/frame, not settings.
                self._game_session=GameRunSession(self.run_id,self.binding,route,live['guild_id'],
                    live['difficulty'],opening['operation_id'],observed['map_sha256'],
                    live['game_current_block_id'],live['game_area'],live['frame'])
                self._game_session_owner=self._game_owner()
                return route
            if not self.settings.reroll: raise BatchStopped('opening_not_matching')
            if attempt>=self.settings.max_attempts: raise BatchStopped('reroll_attempt_limit')
            if self.clock()-started>=self.settings.max_seconds: raise BatchStopped('reroll_time_limit')
            enter_id=opening['enter_id']
            self.transaction('retire',dict(enter_id=enter_id),lambda oid:self.port.retire(enter_id,oid))
            self.observation(self.port.top(),enter_id=0)
        raise BatchStopped('reroll_attempt_limit')

    def purchase(self,enter_id,item_id,quote,*,readback=None):
        if not self.settings.buy_relics: raise BatchStopped('purchase_disabled')
        self.observation(quote,enter_id=enter_id,item_id=item_id,kind='relic',currency='run_coins')
        price,balance=quote.get('price'),quote.get('balance')
        if (type(price) is not int or price<=0 or type(balance) is not int or balance<price
                or quote.get('purchasable') is not True): raise BatchStopped('purchase_quote_invalid')
        result=self.transaction('purchase',dict(enter_id=enter_id,item_id=item_id,price=price),
            lambda oid:self.port.buy_relic(quote,oid))
        if readback is not None:
            result=self.observation(readback(result),enter_id=enter_id,item_id=item_id,price=price,inventory_added=True)
        if result.get('inventory_added') is not True: raise BatchStopped('purchase_result_unknown')
        return result

    def run(self):
        self.invalidate_game_session()
        try:
            outcome='unknown'
            with self.journal.locked():
                recovered=RunRecord.recover(self.journal.directory,self.binding,self.settings.payload()) if self.resume else None
                if recovered:
                    self.run_id=recovered['run_id'];self.completed={int(k):v for k,v in recovered['completed'].items()}
                    self.failed={int(k):v for k,v in recovered['failed'].items()}
                    self._first_acquire_done=bool(sum(self.completed.values())+sum(self.failed.values()))
                record=RunRecord(self.journal.directory,self.run_id,self.binding,resume=bool(recovered))
                if not recovered:record.append('started',settings=self.settings.payload())
                events=self.journal.events()
                for a,b in zip(events[::2],events[1::2]):
                    if a.get('operation_id')!=b.get('operation_id') or a.get('binding')!=b.get('binding'):
                        raise ValueError('labyrinth_journal_invalid')
                if events and events[-1]['state']=='in_flight':
                    result=dict(outcome='blocked_in_flight',completed=dict(self.completed),batch_complete=False)
                    record.append('finished',**result,timings=self.timings.payload())
                    return dict(result,run_id=self.run_id)
                try:
                    for goal in self.settings.goals:
                        failure_started=None;failures_this_invocation=self.failed[goal.guild_id]
                        while self.completed[goal.guild_id]<goal.clears:
                            record.checkpoint(self.settings.payload(),'ready',self.completed,self.failed)
                            self.checkpoint()
                            record.checkpoint(self.settings.payload(),'acquiring',self.completed,self.failed)
                            route=self.acquire(goal.guild_id)
                            record.checkpoint(self.settings.payload(),'playing',self.completed,self.failed)
                            self.progress(dict(stage='playing',guild_id=goal.guild_id,completed=dict(self.completed)))
                            # Adapter owns all screenshots/recognition/input via Maa. It
                            # may use purchase() but cannot turn a retry into settlement.
                            result=self.observation(self.port.play(route,self),enter_id=route.enter_id,
                                guild_id=goal.guild_id,difficulty=self.settings.difficulty)
                            if result.get('outcome')=='defeated' and result.get('failure_verified') is True:
                                failed=dict(self.failed);failed[goal.guild_id]+=1
                                completed=dict(self.completed)
                                if goal.mode=='attempts':completed[goal.guild_id]+=1
                                record.append('failed',enter_id=route.enter_id,guild_id=goal.guild_id,
                                    difficulty=self.settings.difficulty,frame=result['frame'],outcome='failed_max_retry',
                                    completed=completed,failed=failed)
                                self.completed,self.failed=completed,failed
                                if failure_started is None:failure_started=self.clock()
                                failures_this_invocation+=1
                                if completed[goal.guild_id]<goal.clears and failures_this_invocation>=self.settings.max_attempts:raise BatchStopped('reroll_attempt_limit')
                                if completed[goal.guild_id]<goal.clears and self.clock()-failure_started>=self.settings.max_seconds:raise BatchStopped('reroll_time_limit')
                                self.port.resume_failed_run(route.enter_id)
                                self.transaction('retire',dict(enter_id=route.enter_id),lambda oid:self.port.retire(route.enter_id,oid))
                                self.observation(self.port.top(),enter_id=0)
                                record.checkpoint(self.settings.payload(),'ready',self.completed,self.failed)
                                continue
                            if result.get('outcome')!='cleared' or result.get('clear_verified') is not True:
                                import re
                                reason=result.get('outcome')
                                raise BatchStopped(reason if type(reason) is str and reason!='cleared' and re.fullmatch('[a-z][a-z0-9_]{0,95}',reason) else 'clear_unknown')
                            # Count once only after dedicated victory plus return/top.
                            self.observation(self.port.top(),enter_id=0)
                            completed=dict(self.completed);completed[goal.guild_id]+=1
                            record.append('cleared',enter_id=route.enter_id,guild_id=goal.guild_id,
                                difficulty=self.settings.difficulty,frame=result['frame'],completed=completed)
                            self.completed=completed
                            record.checkpoint(self.settings.payload(),'ready',self.completed,self.failed)
                            self.progress(dict(stage='cleared',guild_id=goal.guild_id,completed=dict(self.completed)))
                    outcome='complete'
                except Exception as error:
                    from pathlib import Path
                    import re
                    from ..native import NativeInputNotSent
                    frames=[];causes=[];cause=error;seen=set()
                    while cause is not None and id(cause) not in seen:
                        seen.add(id(cause));tb=cause.__traceback__
                        while tb is not None:
                            frames.append(dict(file=Path(tb.tb_frame.f_code.co_filename).name,
                                function=tb.tb_frame.f_code.co_name,line=tb.tb_lineno));tb=tb.tb_next
                        item=dict(error_type=type(cause).__name__)
                        reason=str(cause) if isinstance(cause,BatchStopped) else cause.receipt.get('reason') if isinstance(cause,NativeInputNotSent) else None
                        if type(reason) is str and re.fullmatch('[a-z][a-z0-9_]{0,95}',reason):item['reason']=reason
                        causes.append(item);cause=cause.__cause__ or cause.__context__
                    record.append('adapter_error',error_type=type(error).__name__,frames=frames,causes=causes)
                    outcome=str(error) if isinstance(error,BatchStopped) else 'adapter_failed'
                finish_timings=getattr(self.port,'finish_timings',None)
                if callable(finish_timings):finish_timings()
                result=dict(outcome=outcome,completed=dict(self.completed),batch_complete=outcome=='complete')
                record.append('finished',**result,timings=self.timings.payload())
                record.checkpoint(self.settings.payload(),'finished',self.completed,self.failed)
            return dict(result,run_id=self.run_id)
        finally:
            self.invalidate_game_session()
