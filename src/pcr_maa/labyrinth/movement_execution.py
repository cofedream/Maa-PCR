"""Retained-session-bound Maa target preparation; no movement execution.

Current production sessions begin in area1 while the target pixel calibration
supports area2 only. Native area1 preparation therefore remains unqualified.
This component cannot move, start a transaction, advance a cursor or qualify a
caller-built target dictionary. A later input must rerun the callback on its own
fresh click frame and independently commit arrival through the Batch Owner.
"""
from copy import deepcopy
from maa.custom_action import CustomAction
from maa.tasker import Tasker
from maa.define import AlgorithmEnum
from .. import native
from ..context_backend import ContextTasker
from .batch import Batch,BatchStopped
from .routes import Route
from .snapshots import OpeningSnapshot
from .game_session import _root
from .timing import Timings
from .target_observation import (MaaRouteTargetRecognition,route_target_readback,
                                 _pending)

_READ='LabyMovementPreparationRead'


def preparation_pipeline():
    """One inert CustomAction: fresh screenshot and same-image recognitions only."""
    return {_READ:dict(recognition='DirectHit',action='Custom',custom_action=_READ,
        next=[],timeout=3000,pre_delay=0,post_delay=0)}


class _BoundTarget(MaaRouteTargetRecognition):
    def __init__(self,owner,batch,route,session):
        self.owner,self.batch,self.session=owner,batch,session
        self.failure=None
        super().__init__(route,session.current_block_id,stopped=batch.stopped)

    def analyze(self,context,argv):
        try:
            self.owner._require(self.batch,self.route,self.session,context=context)
            result=super().analyze(context,argv)
            self.owner._require(self.batch,self.route,self.session,context=context)
            return result
        except BaseException as error:
            self.failure=error;self.batch.invalidate_game_session()
            reason='stopped' if isinstance(error,BatchStopped) and str(error)=='stopped' else 'target_binding_unknown'
            return self.AnalyzeResult(None,_pending(reason))


class _ReadTarget(CustomAction):
    def __init__(self,owner,batch,route,session,bound):
        super().__init__()
        self.owner,self.batch,self.route,self.session,self.bound=owner,batch,route,session,bound
        self.report=None;self.reco_id=None;self.failure=None
        # Envelope timing is separate from any Owner NativeTimings sink/trace.
        self.timings=Timings()

    def run(self,context,argv):
        try:
            self.owner._require(self.batch,self.route,self.session,context=context)
            with self.timings.measure('screenshot'):
                if not native.wait_job(context.tasker.controller.post_screencap(),seconds=10).succeeded:
                    raise BatchStopped('movement_capture_unknown')
                image=context.tasker.controller.cached_image.copy()
            self.owner._require(self.batch,self.route,self.session,context=context)
            with self.timings.measure('recognition'):
                detail=context.run_recognition('LabyRouteTargetObserved',image)
            self.owner._require(self.batch,self.route,self.session,context=context)
            if (detail is None or detail.algorithm!=AlgorithmEnum.Custom
                    or type(detail.reco_id) is not int or detail.reco_id<=0):
                raise BatchStopped('movement_preparation_unknown')
            self.reco_id=detail.reco_id
            # The native callback already exhausts the geometric matcher. This
            # cheap consumer validation is timed separately, without rematching.
            with self.batch.timings.measure('strategy'):
                self.report=route_target_readback(detail.raw_detail,hit=detail.hit)
                if self.report['status']=='target_observed':
                    self.owner._target(self.route,self.session,self.report)
            self.owner._require(self.batch,self.route,self.session,context=context)
            if self.bound.failure is not None:raise self.bound.failure
        except BaseException as error:self.failure=error
        # Success describes completion of this read callback, never a game action.
        # The consumer must separately inspect failure and actual typed report.
        return True


class MaaMovementPreparation:
    def __init__(self,tasker,stamp):
        if not callable(stamp):raise ValueError('owner_stamp_required')
        self.tasker,self.stamp=tasker,stamp
        self.last_evidence=None

    def _require(self,batch,route,session,*,context=None):
        if type(batch) is not Batch or type(route) is not Route:
            raise BatchStopped('game_session_unknown')
        live=batch.require_game_session(session,route)
        port=batch.port;game=getattr(port,'game',None)
        if (getattr(port,'binding',None)!=batch.binding or game is None
                or getattr(game,'tasker',None) is not self.tasker
                or getattr(game,'stamp',None)!=self.stamp
                or getattr(port,'stamp',None)!=self.stamp):
            raise BatchStopped('movement_owner_unknown')
        if type(self.tasker) not in (Tasker,ContextTasker) or not self.tasker.inited:
            raise BatchStopped('movement_owner_unknown')
        if getattr(self.tasker,'stopping',False):raise BatchStopped('stopped')
        snapshot=OpeningSnapshot(batch.binding.account_id,route.enter_id,live.guild_id,live.difficulty,route.full_map)
        current=[node for node in route.nodes if node.block_id==live.current_block_id]
        if (snapshot.fingerprint!=live.protocol_map_sha256 or len(current)!=1
                or type(live.area) is not int or current[0].area!=live.area
                or type(live.current_block_id) is not int):
            raise BatchStopped('movement_session_map_unknown')
        if type(self.tasker) is ContextTasker:
            _,source_context,_,_=self.tasker.observation_source()
            owner_tasker=source_context.tasker
        else:owner_tasker=self.tasker
        if context is not None:
            # Native SDK Context wraps the same opaque Tasker; Python wrapper
            # identity can differ. Neither handle comes from a supplied receipt.
            if (type(context.tasker) is not Tasker
                    or context.tasker._handle!=owner_tasker._handle):
                raise BatchStopped('movement_owner_unknown')
        return live

    @staticmethod
    def _target(route,session,report):
        current=[i for i,node in enumerate(route.nodes) if node.block_id==session.current_block_id]
        if len(current)!=1 or current[0]+1>=len(route.nodes):
            raise BatchStopped('movement_target_unknown')
        target=report['target'];expected=route.nodes[current[0]+1]
        if (report['current_block_id']!=session.current_block_id
                or target['enter_id']!=route.enter_id or target['block_id']!=expected.block_id
                or target['area']!=session.area or expected.area!=session.area
                or target['block_type']!=expected.block_type or expected.block_type not in (2,4)):
            raise BatchStopped('movement_target_unknown')

    def prepare_next(self,route,batch,*,session=None):
        """Capture and return a readonly envelope; its box is never an input permit.

        The current comes exclusively from Batch's retained object. The shared
        Owner stamp is a separate envelope frame; Maa reco IDs stay inside the
        report. Unknown, Stop or unqualified results destroy the retained handle.
        This function writes no intent, performs no input and advances no cursor.
        """
        try:
            if session is None:session=batch.game_session(route)
            self._require(batch,route,session)
            self.last_evidence=None
            bound=_BoundTarget(self,batch,route,session)
            bound.install(self.tasker.resource)
            reader=_ReadTarget(self,batch,route,session,bound)
            if (not self.tasker.resource.override_pipeline(preparation_pipeline())
                    or not self.tasker.resource.register_custom_action(_READ,reader)):
                raise BatchStopped('movement_preparation_unavailable')
            with batch.timings.measure('page_wait'):receipt=native.run(self.tasker,_READ)
            self.last_evidence=dict(native=deepcopy(receipt),target_report=deepcopy(reader.report),
                                    target_reco_id=reader.reco_id,
                                    preparation_timings=reader.timings.payload(),
                                    measurement_scope=dict(
                                        screenshot='independent explicit controller capture envelope',
                                        recognition='independent whole target callback including collection and geometry search',
                                        strategy='Owner typed parser and next-node assertion only',
                                        geometry_strategy_separately_measured=False,
                                        owner_native_measurements_verified=False))
            self._require(batch,route,session)
            root=_root(receipt,_READ);action=root.get('action')
            if (type(action) is not dict or action.get('name')!=_READ
                    or action.get('success') is not True or reader.report is None):
                if reader.failure is not None:raise reader.failure
                raise BatchStopped('movement_preparation_unknown')
            if reader.failure is not None:raise reader.failure
            report=reader.report
            evidence=self.stamp(scope='movement_preparation',source='maa',enter_id=route.enter_id,
                retained_current_block_id=session.current_block_id,retained_area=session.area,
                retained_accepted_frame=session.accepted_frame,protocol_map_sha256=session.protocol_map_sha256,
                target_report=deepcopy(report),target_reco_id=reader.reco_id,native=deepcopy(receipt),
                preparation_timings=reader.timings.payload(),measurement_scope=deepcopy(self.last_evidence['measurement_scope']),
                input_performed=False,settled=False,safe_to_navigate=False,next_target_qualified=False,
                cursor_advanced=False)
            self.last_evidence=deepcopy(evidence)
            batch.observation(evidence,scope='movement_preparation',source='maa',enter_id=route.enter_id,
                retained_current_block_id=session.current_block_id,retained_area=session.area,
                protocol_map_sha256=session.protocol_map_sha256,input_performed=False,settled=False,
                safe_to_navigate=False,next_target_qualified=False,cursor_advanced=False)
            self._require(batch,route,session)
            if report['status']!='target_observed':raise BatchStopped(report.get('reason','movement_target_unknown'))
            return evidence
        except BaseException:
            if isinstance(batch,Batch):batch.invalidate_game_session()
            raise
