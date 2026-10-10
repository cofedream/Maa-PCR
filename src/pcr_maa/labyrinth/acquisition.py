"""Fixed protocol acquisition plugged into Batch; Maa owns game handoff/play.

The authenticated client and game adapter come from the same Owner session.
Explicit SDK phase restoration never retries or replays a labyrinth operation.
A concrete game adapter must qualify restore_opening before GUI execution.
"""
import asyncio
from dataclasses import dataclass
from .routes import Node
from .batch import BatchStopped,current_position_basis
from .snapshots import OpeningSnapshot,canonical_map
from .handoff import match_current_marker_consensus,VisibleBindingFailure
from .portrait_cache import joined_portrait_readback

@dataclass(frozen=True)
class _CreationAck:
    client:object
    generation:object
    account_id:int
    enter_id:int
    guild_id:int
    difficulty:int
    operation_id:str
    nodes:tuple
    creation:dict

@dataclass(frozen=True)
class _ObservedOpening:
    client:object
    generation:object
    account_id:int
    snapshot:OpeningSnapshot
    operation_id:str
    source_record:dict

class ProtocolAcquisitionPort:
    def __init__(self,client,game,binding,*,stopped=lambda:False):
        self.client,self.game,self.binding=client,game,binding
        self.stopped=stopped
        self.runner=asyncio.Runner()
        self.sequence=0;self.game_frame=-1;self.openings={};self.creations={};self.closed=False
        self._game_enter_id=None
        self._creation_acks={};self._opening_receipts={};self._created_snapshots={};self._observed_openings={}
        self._play_started=False;self._play_finished=False
        self._resume_after_clear=False;self._resume_after_defeat=False
        self._handoff_receipt=None

    def _fail(self,code):
        self.close()
        raise BatchStopped(code)

    def _checkpoint(self):
        if self.closed:raise BatchStopped('protocol_binding_unknown')
        if self.stopped():self._fail('stopped')

    def _protocol(self):
        self._checkpoint()
        if (self.client.phase!='protocol' or not self.client.logged_in
                or type(self.client.auth.viewer_id) is not int
                or self.client.auth.viewer_id!=self.binding.account_id):
            self._fail('protocol_binding_unknown')

    def _game_phase(self):
        self._checkpoint()
        if self.client.phase!='game' or self.client.logged_in:
            self._fail('game_phase_unknown')

    def _run(self,method,*args):
        self._checkpoint()
        try:value=self.runner.run(method(*args))
        except asyncio.CancelledError:self._fail('stopped')
        except Exception:
            self.close();raise
        self._checkpoint()
        return value

    def _call(self,method,*args):
        self._protocol()
        value=self._run(method,*args)
        self._protocol()
        return value

    def stamp(self,**fields):
        self.sequence+=1
        return dict(verified=True,binding=self.binding.payload(),frame=self.sequence,**fields)

    def top(self):
        self._checkpoint()
        if self.client.phase=='game':
            # Permission is minted only by this port's fresh completed play
            # result. Clear it before the one explicit SDK login attempt.
            if not self._resume_after_clear:self._fail('protocol_resume_not_allowed')
            self._resume_after_clear=False
            try:resumed=self._run(self.client.resume_protocol)
            except BatchStopped:raise
            except Exception:self._fail('protocol_resume_failed')
            if resumed is not True:self._fail('protocol_resume_failed')
            self._protocol()
            self.openings.pop(self._game_enter_id,None)
            self.creations.pop(self._game_enter_id,None)
            self._creation_acks.pop(self._game_enter_id,None);self._opening_receipts.pop(self._game_enter_id,None);self._created_snapshots.pop(self._game_enter_id,None);self._observed_openings.pop(self._game_enter_id,None)
            self._game_enter_id=None
        value=self._call(self.client.top)
        enter=value.get('enter_id')
        if enter is None:enter=0
        if type(enter) is not int or enter<0:self._fail('top_unknown')
        return self.stamp(enter_id=enter,fresh_start=enter==0,**{k:value[k] for k in ('guild_id','difficulty') if k in value})

    def enter(self,guild,difficulty,oid):
        if type(oid) is not str or not oid:self._fail("enter_identity_unknown")
        generation=getattr(self.client,"session_generation",None)
        value=self._call(self.client.enter,guild,difficulty)
        # ENTER proves creation; read its actual active identity before settling.
        # Configuration is only the expectation, never observed guild/difficulty.
        top=self._call(self.client.top)
        if (type(value) is not dict or type(value.get('enter_id')) is not int
                or value['enter_id']<=0 or type(top) is not dict
                or any(type(top.get(k)) is not int for k in ('enter_id','guild_id','difficulty'))
                or top['enter_id']!=value['enter_id']
                or top['guild_id']!=guild or top['difficulty']!=difficulty):
            self._fail('enter_identity_unknown')
        if type(oid) is not str or not oid:self._fail('enter_identity_unknown')
        created=self.stamp(enter_id=top['enter_id'],guild_id=top['guild_id'],
            difficulty=top['difficulty'],operation_id=oid,source='protocol')
        self.creations[top['enter_id']]=dict(created)
        if hasattr(self.client,'session_generation'):
            if generation is None or generation is not self.client.session_generation:self._fail('opening_creation_unknown')
            try:nodes=tuple(Node.parse(n) for n in value['map_list']);canonical_map([n.payload() for n in nodes])
            except (KeyError,TypeError,ValueError):self._fail('opening_ack_unknown')
            self._creation_acks[top['enter_id']]=_CreationAck(self.client,generation,self.binding.account_id,
                top['enter_id'],top['guild_id'],top['difficulty'],oid,nodes,self.creations[top['enter_id']])
        return created

    def observe_existing_opening(self,guild,difficulty,oid,*,policy=None):
        """Read a real untouched opening; no ENTER receipt or mid-run state is invented."""
        if type(oid) is not str or not oid:self._fail('opening_identity_required')
        generation=getattr(self.client,'session_generation',None)
        before=self._call(self.client.top)
        if type(before) is not dict:self._fail('top_unknown')
        enter=before.get('enter_id')
        if enter is None or type(enter) is int and enter==0:return None
        if type(enter) is not int or enter<=0:self._fail('top_unknown')
        keys=('enter_id','guild_id','difficulty')
        if any(type(before.get(k)) is not int for k in keys):self._fail('opening_identity_unknown')
        if before['guild_id']!=guild or before['difficulty']!=difficulty:return None
        resumed=self._call(self.client.resume,enter)
        after=self._call(self.client.top)
        if (type(after) is not dict or any(type(after.get(k)) is not int or after[k]!=before[k] for k in keys)
                or hasattr(self.client,'session_generation') and generation is not self.client.session_generation):
            self._fail('opening_changed')
        if type(resumed) is not dict:self._fail('opening_identity_unknown')
        block=resumed.get('block_id')
        if type(block) is int and block>0:return None
        try:snapshot=OpeningSnapshot.read(self.binding.account_id,enter,after,resumed)
        except ValueError as error:self._fail(str(error))
        if policy is not None:
            from .routes import select_route
            if select_route(enter,snapshot.nodes,difficulty,policy,source='protocol') is None:return None
        receipt=self.stamp(**dict(snapshot.payload(),operation_id=oid,source='protocol',opening_origin='observed_existing'))
        source_record=dict(receipt)
        self.openings[enter]=snapshot;self.creations[enter]=source_record
        self._opening_receipts[enter]=receipt
        self._observed_openings[enter]=_ObservedOpening(self.client,generation,self.binding.account_id,snapshot,oid,source_record)
        return receipt

    def _require_ack(self,enter_id):
        self._checkpoint()
        if self.client.phase=='protocol':self._protocol()
        elif self.client.phase=='game':self._game_phase()
        else:self._fail('opening_creation_unknown')
        ack=self._creation_acks.get(enter_id)
        public=self.creations.get(enter_id)
        if (type(ack) is not _CreationAck or ack.client is not self.client
                or ack.generation is None or ack.generation is not getattr(self.client,'session_generation',None)
                or ack.account_id!=self.binding.account_id or public is not ack.creation
                or any(public.get(k)!=v for k,v in dict(enter_id=ack.enter_id,guild_id=ack.guild_id,
                    difficulty=ack.difficulty,operation_id=ack.operation_id).items())):
            self._fail('opening_creation_unknown')
        return ack

    def require_created_opening(self,snapshot,*,batch=None):
        """An equal/constructed snapshot or public receipt cannot mint this source."""
        self._checkpoint()
        if (type(snapshot) is not OpeningSnapshot or self.openings.get(snapshot.enter_id) is not snapshot
                or self._created_snapshots.get(snapshot.enter_id) is not snapshot and self._observed_openings.get(snapshot.enter_id) is None):
            self._fail('opening_creation_unknown')
        observed=self._observed_openings.get(snapshot.enter_id)
        if observed is not None:
            if self.client.phase=='protocol':self._protocol()
            elif self.client.phase=='game':self._game_phase()
            else:self._fail('opening_creation_unknown')
            if (type(observed) is not _ObservedOpening or observed.client is not self.client
                    or observed.generation is not getattr(self.client,'session_generation',None)
                    or observed.account_id!=self.binding.account_id or observed.snapshot is not snapshot
                    or snapshot.protocol_current_block_id not in (None,0)
                    or self.creations.get(snapshot.enter_id) is not observed.source_record
                    or any(observed.source_record.get(k)!=v for k,v in dict(source='protocol',opening_origin='observed_existing',
                        operation_id=observed.operation_id,enter_id=snapshot.enter_id,guild_id=snapshot.guild_id,
                        difficulty=snapshot.difficulty,binding=self.binding.payload()).items())):
                self._fail('opening_creation_unknown')
            if batch is not None:
                if (batch.port is not self or batch.binding!=self.binding
                        or not any(event.get('state')=='settled' and event.get('operation')=='existing_opening'
                            and event.get('operation_id')==observed.operation_id and event.get('binding')==self.binding.payload()
                            and type(event.get('result')) is dict and event['result'].get('enter_id')==snapshot.enter_id
                            and event['result'].get('source')=='protocol' and event['result'].get('opening_origin')=='observed_existing'
                            for event in batch.journal.events())):
                    self._fail('opening_creation_unknown')
            return snapshot
        ack=self._require_ack(snapshot.enter_id)
        if batch is not None:
            try:events=batch.journal.events()
            except Exception:self._fail('opening_creation_unknown')
            if (batch.port is not self or batch.binding!=self.binding
                    or type(ack.creation.get('frame')) is not int or not 0<ack.creation['frame']<=batch.last_frame
                    or not any(event.get('state')=='settled' and event.get('operation')=='enter'
                        and event.get('operation_id')==ack.operation_id and event.get('binding')==self.binding.payload()
                        and type(event.get('result')) is dict and event['result'].get('enter_id')==snapshot.enter_id
                        for event in events)):
                self._fail('opening_creation_unknown')
        return snapshot

    def require_opening_snapshot(self,receipt):
        self._protocol()
        enter=receipt.get('enter_id') if type(receipt) is dict else None
        if self._opening_receipts.get(enter) is not receipt:self._fail('opening_creation_unknown')
        snapshot=self.openings.get(enter)
        if type(snapshot) is not OpeningSnapshot:self._fail('opening_creation_unknown')
        try:
            if (canonical_map(receipt.get('map_list'))!=canonical_map([n.payload() for n in snapshot.nodes])
                    or receipt.get('map_sha256')!=snapshot.fingerprint):raise ValueError()
        except ValueError:self._fail('opening_changed')
        if hasattr(self.client,'session_generation'):self.require_created_opening(snapshot)
        return snapshot

    def read_opening(self,enter_id):
        self._protocol()
        ack=self._require_ack(enter_id) if hasattr(self.client,'session_generation') else None
        before=self._call(self.client.top)
        if ack:self._require_ack(enter_id)
        resumed=self._call(self.client.resume,enter_id)
        if ack:self._require_ack(enter_id)
        after=self._call(self.client.top)
        if ack:self._require_ack(enter_id)
        keys=('enter_id','guild_id','difficulty')
        if (type(before) is not dict or type(after) is not dict
                or any(type(before.get(k)) is not int or before[k]!=after.get(k) for k in keys)):
            self._fail('opening_changed')
        try:
            if ack:
                if any(after.get(k)!=v for k,v in dict(enter_id=ack.enter_id,guild_id=ack.guild_id,difficulty=ack.difficulty).items()):
                    raise ValueError('opening_changed')
                snapshot=OpeningSnapshot.read_created(self.binding.account_id,enter_id,after,resumed,
                    enter_ack=dict(enter_id=ack.enter_id,map_list=[n.payload() for n in ack.nodes]))
            else:snapshot=OpeningSnapshot.read(self.binding.account_id,enter_id,after,resumed)
        except ValueError as error:self._fail(str(error))
        self.openings[enter_id]=snapshot
        if ack:self._created_snapshots[enter_id]=snapshot
        receipt=self.stamp(**snapshot.payload());self._opening_receipts[enter_id]=receipt
        return receipt

    def retire(self,enter_id,oid):
        if self._call(self.client.retire,enter_id) is not True:self._fail('retire_result_unknown')
        self.openings.pop(enter_id,None)
        self.creations.pop(enter_id,None)
        self._creation_acks.pop(enter_id,None);self._opening_receipts.pop(enter_id,None);self._created_snapshots.pop(enter_id,None);self._observed_openings.pop(enter_id,None)
        return self.stamp(enter_id=enter_id,operation_id=oid)

    def _game(self,value,**expected):
        self._game_phase()
        if (type(value) is not dict or value.get('verified') is not True or value.get('binding')!=self.binding.payload()
                or type(value.get('frame')) is not int or value['frame']<=self.game_frame
                or any(value.get(k)!=v for k,v in expected.items())):
            self._fail('game_handoff_unknown')
        self.game_frame=value['frame']
        # The adapter uses this port's stamp for native subtransactions too.
        # Never restamp behind a frame already consumed by Batch.
        self.sequence=max(self.sequence,self.game_frame)
        fields={k:v for k,v in value.items() if k not in ('verified','binding','frame')}
        return self.stamp(**fields,game_frame=self.game_frame)

    def _invoke_game(self,method,*args,**kwargs):
        self._game_phase()
        try:value=method(*args,**kwargs)
        except asyncio.CancelledError:self._fail('stopped')
        except Exception:
            self.close();raise
        self._game_phase()
        return value

    def _invitation(self,value,snapshot,local_frame,created_frame):
        # This is a receipt chain, not an invented screenshot freshness token.
        # The concrete adapter owns fresh Maa captures and the initial inputs.
        if (type(value) is not dict or value.get('source')!='maa'
                or type(value.get('enter_id')) is not int or value['enter_id']!=snapshot.enter_id):
            self._fail('handoff_invitation_unknown')
        previous=created_frame
        for key,nodes in (('empty',('LabyOpeningEmptyObserved',)),
                         ('joined',('LabyOpeningJoinedObserved','LabyOpeningAnyJoinedObserved')),
                         ('closed',('LabyMapObserved',))):
            step=value.get(key)
            if (type(step) is not dict or step.get('verified') is not True
                    or step.get('binding')!=self.binding.payload()
                    or step.get('source')!='maa' or type(step.get('enter_id')) is not int
                    or step['enter_id']!=snapshot.enter_id
                    or type(step.get('frame')) is not int or not previous<step['frame']<local_frame):
                self._fail('handoff_invitation_unknown')
            native=step.get('native')
            if (type(native) is not dict or native.get('native_ok') is not True
                    or native.get('tasker_inited') is not True
                    or type(native.get('task_id')) is not int or native['task_id']<=0
                    or type(native.get('nodes')) is not list or len(native['nodes'])!=1
                    or type(native['nodes'][0]) is not dict
                    or native['nodes'][0].get('name') not in nodes
                    or native['nodes'][0].get('completed') is not True):
                self._fail('handoff_invitation_unknown')
            previous=step['frame']
        try:layout,characters=joined_portrait_readback(value['joined']['native'])
        except BatchStopped:self._fail('handoff_invitation_unknown')
        if layout!='opening3' or len(characters)!=3:self._fail('handoff_invitation_unknown')
        # These identities come from actual joined-page OCR, never configured IDs.
        return [dict(character_id=item.character_id,raw_name=item.raw_text,
                     name_score=item.score) for item in characters]

    def handoff(self,route,batch):
        self._protocol()
        snapshot=self.openings.get(route.enter_id)
        if snapshot is None or canonical_map([n.payload() for n in snapshot.nodes])!=canonical_map([n.payload() for n in route.full_map]):
            self._fail('opening_changed')
        if hasattr(self.client,'session_generation'):self.require_created_opening(snapshot,batch=batch)
        elif snapshot.protocol_current_block_id not in (None,0):self._fail('opening_creation_unknown')
        created=self.creations.get(route.enter_id)
        if (type(created) is not dict or created.get('verified') is not True
                or type(created.get('frame')) is not int or not 0<created['frame']<=batch.last_frame
                or created.get('source')!='protocol'
                or created.get('binding')!=self.binding.payload()
                or created.get('enter_id')!=snapshot.enter_id
                or created.get('guild_id')!=snapshot.guild_id
                or created.get('difficulty')!=snapshot.difficulty
                or type(created.get('operation_id')) is not str or not created['operation_id']
                or batch.binding!=self.binding or batch.port is not self):
            self._fail('opening_creation_unknown')
        # Invalidate the protocol SID before the actual game-session restoration.
        if self._run(self.client.suspend_for_game) is not True:self._fail('game_phase_unknown')
        self._game_phase()
        self._resume_after_clear=False
        self._game_enter_id=None
        self._play_started=self._play_finished=False
        raw=self._invoke_game(self.game.restore_opening,snapshot,batch,creation=dict(created))
        basis='observed_opening_invitation_map_start' if created.get('opening_origin')=='observed_existing' else 'created_opening_invitation_map_start'
        start_source='observed_invitation_map_sequence' if created.get('opening_origin')=='observed_existing' else 'created_invitation_map_sequence'
        sequence_start=raw.get('position_basis')==basis if type(raw) is dict else False
        if sequence_start:
            verify=getattr(self.game,'verify_start_handoff',None)
            if (not callable(verify) or verify(snapshot,raw,batch) is not True
                    or raw.get('start_source')!=start_source
                    or raw.get('initial_map_area')!=1):
                self._fail('handoff_start_mismatch')
        if (type(raw) is not dict or type(raw.get('frame')) is not int
                or raw['frame']<batch.last_frame or raw['frame']>self.sequence
                or any(type(raw.get(k)) is not int for k in
                    ('enter_id','game_account_id','game_area','game_current_block_id'))
                or raw.get('protocol_current_block_id') is not None and type(raw.get('protocol_current_block_id')) is not int):
            self._fail('game_handoff_unknown')
        # Legacy synthetic adapters echo a route sentinel; never expose it as a
        # protocol null-to-zero conversion. Actual generation-bound adapters must
        # echo the exact retained RESUME value.
        if (not hasattr(self.client,'session_generation') and snapshot.protocol_current_block_id is None
                and type(raw.get('protocol_current_block_id')) is int and raw['protocol_current_block_id']==0):
            raw=dict(raw,protocol_current_block_id=None)
        result=self._game(raw,
            enter_id=route.enter_id,game_account_id=self.binding.account_id,
            protocol_map_sha256=snapshot.fingerprint,protocol_current_block_id=snapshot.protocol_current_block_id,game_area=1)
        if sequence_start:
            # A new ENTER ACK plus the real initial invitation/map sequence is
            # the upstream's start basis. It is not a visual full-map match.
            local_frame=result.get('initial_map_frame')
            map_native=result.get('initial_map_native')
            if (type(local_frame) is not int or not created['frame']<local_frame<=result['game_frame']
                    or type(map_native) is not dict or map_native.get('native_ok') is not True):
                self._fail('handoff_local_observation_unknown')
            joined=self._invitation(result.get('invitation'),snapshot,local_frame,created['frame'])
            first_column=min(n.column for n in snapshot.nodes if n.area==1)
            starts=[n for n in snapshot.nodes if n.area==1 and n.column==first_column and n.block_type==1]
            if len(starts)!=1:self._fail('handoff_start_mismatch')
            current=starts[0].block_id
            matched=dict(scope=basis,
                current_marker_binding_verified=True,bound_protocol_block_id=current,
                start_source=start_source,ui_map_fingerprint_verified=False,
                next_target_qualified=False,safe_to_navigate=False)
        else:
            local=result.get('local_observation')
            if (type(local) is not dict or type(local.get('frame')) is not int
                    or local['frame']>result['game_frame'] or local.get('area')!=result['game_area']):
                self._fail('handoff_local_observation_unknown')
            joined=self._invitation(result.get('invitation'),snapshot,local['frame'],created['frame'])
            if not current_position_basis(local):self._fail('handoff_current_basis_unknown')
            try:
                matched=match_current_marker_consensus([n.payload() for n in snapshot.nodes],local,stopped=self.stopped)
            except VisibleBindingFailure as error:
                if str(error)=='observed_subgraph_stopped':self._fail('stopped')
                self._fail(str(error))
            current=matched['bound_protocol_block_id']
        first=min(n.column for n in snapshot.nodes if n.area==1)
        bound=next(n for n in snapshot.nodes if n.block_id==current)
        if (type(result.get('game_current_block_id')) is not int
                or result['game_current_block_id']!=current or bound.area!=1 or bound.column!=first
                or not route.nodes or route.nodes[0].block_id!=current
                or snapshot.protocol_current_block_id not in (None,0,current)):
            self._fail('handoff_start_mismatch')
        # Protocol identity and full map remain protocol facts; the game proves
        # account, invitation lifecycle and consensus over the current marker.
        # Neighbor mappings and the next click remain independently unqualified.
        # Only this narrow contract crosses the adapter boundary. Unrecognized
        # game flags, next-target coordinates and neighbor-ID mappings cannot
        # leak into a current-only receipt through an old adapter's extra fields.
        fields=('verified','binding','frame','game_frame','enter_id','game_account_id',
                'account_source','protocol_map_sha256','protocol_current_block_id',
                'game_area','game_current_block_id','invitation','local_observation',
                'local_native','session_native','account_native','initial_map_native',
                'initial_map_area','initial_map_frame','start_source','position_basis')
        result={key:result[key] for key in fields if key in result}
        result.update(source='protocol_maa_handoff',identity_source='protocol',
            guild_id=snapshot.guild_id,difficulty=snapshot.difficulty,
            creation_operation_id=created['operation_id'],scope='fresh_opening_current',
            current_binding=matched,current_marker_binding_verified=True,
            ui_map_fingerprint_verified=False,next_target_qualified=False,joined_characters=joined)
        self._game_enter_id=route.enter_id
        self._handoff_receipt=result
        return result

    def require_handoff_receipt(self,receipt):
        self._game_phase()
        if receipt is not self._handoff_receipt:self._fail('game_handoff_unknown')
        return receipt

    def play(self,route,batch):
        self._game_phase()
        snapshot=self.openings.get(route.enter_id)
        if self._game_enter_id!=route.enter_id or snapshot is None or self._play_started:
            self._fail('game_handoff_unknown')
        self._play_started=True
        result=self._game(self._invoke_game(self.game.play,route,batch),enter_id=route.enter_id,
            guild_id=snapshot.guild_id,difficulty=snapshot.difficulty)
        self._play_finished=True
        self._resume_after_clear=result.get('clear_verified') is True and result.get('outcome')=='cleared'
        self._resume_after_defeat=result.get('outcome')=='defeated' and result.get('failure_verified') is True
        if not self._resume_after_clear and not self._resume_after_defeat:
            # Partial or unknown can be reported to Batch, but can never
            # grant a reconnect or another opening from this invocation.
            self.close()
        return result

    def resume_failed_run(self,enter_id):
        self._game_phase()
        if not self._resume_after_defeat or self._game_enter_id!=enter_id:self._fail('protocol_resume_not_allowed')
        self._resume_after_defeat=False
        if self._run(self.client.resume_protocol) is not True:self._fail('protocol_resume_failed')
        self._protocol();self._game_enter_id=None

    def buy_relic(self,quote,oid):
        self._game_phase()
        if self._play_finished or quote.get('enter_id')!=self._game_enter_id:
            self._fail('game_handoff_unknown')
        return self._game(self._invoke_game(self.game.buy_relic,quote,oid),operation_id=oid)

    def close(self):
        if not self.closed:
            self.closed=True
            self.openings.clear()
            self.creations.clear()
            self._creation_acks.clear();self._opening_receipts.clear();self._created_snapshots.clear();self._observed_openings.clear()
            self._resume_after_clear=False
            self._handoff_receipt=None
            self._game_enter_id=None
            try:self.client.close()
            finally:self.runner.close()
