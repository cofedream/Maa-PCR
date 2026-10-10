"""Read-only same-frame route target observation, region-2 calibration only.

The pure helper calculates from supplied evidence; it does not authenticate its
source. The native callback independently reruns the installed pixel collector
on argv.image, checks its pixel hash and uses the actual Maa recognition ID as
structural frame. A caller must separately stamp/qualify a fresh account/session
and durable navigation intent. This module contains no game input or permission.
"""
from copy import deepcopy
from hashlib import sha256

import numpy as np
from maa.custom_recognition import CustomRecognition
from .navigation import locate_next_observed
from .partial_map import partial_map_readback,stamp_partial_observation,PartialMapFailure
from .handoff import VisibleBindingFailure


class TargetObservationFailure(ValueError):
    pass


_ROI=(0,80,1280,530)
_CLOSED=('safe_to_navigate','account_verified','freshness_verified',
         'session_verified','next_target_qualified','complete','handoff_qualified')
_REASONS=frozenset((
    'stopped','target_stop_invalid','target_stop_failed','target_frame_invalid',
    'target_roi_invalid','target_roi_mismatch','target_partial_observer_unavailable',
    'target_install_failed','target_partial_readback_unknown','target_partial_report_unqualified',
    'target_frame_hash_mismatch','target_partial_reco_id_unknown','target_inventory_disagrees',
    'target_active_unobserved','target_observation_scope_unqualified','route_end_no_target',
    'target_binding_unknown','target_collection_unknown','partial_frame_stamp_invalid',
    'partial_observation_unqualified','route_binding_invalid','current_node_required',
    'current_node_not_on_route','area_transition_requires_readback','route_edge_missing',
    'observed_current_mismatch','observed_subgraph_invalid','observed_node_invalid',
    'observed_edge_invalid','observed_marker_invalid','visible_box_overlap',
    'visible_column_order_invalid','observed_subgraph_unmatched','current_marker_ambiguous',
    'observed_subgraph_search_limit','observed_stop_invalid','observed_stop_failed',
    'target_not_observed_in_all_mappings','target_box_ambiguous','target_type_unobserved',
    'map_page_unknown','actor_missing','actor_ambiguous','marker_unqualified',
    'anchor_platform_geometry_unqualified','current_area_unobserved','current_area_conflicting',
    'platforms_missing','platforms_ambiguous','partial_collection_unknown'))


def _reason(value,fallback='target_binding_unknown'):
    if value=='observed_subgraph_stopped':return 'stopped'
    return value if type(value) is str and value in _REASONS else fallback


def _closed():
    return dict({name:False for name in _CLOSED},qualification_pending=True,
                ui_map_fingerprint_verified=False,protocol_game_lifecycle_verified=False)


def _pending(reason):
    return dict(_closed(),status='unqualified',reason=_reason(reason,'target_collection_unknown'))


def _check_stopped(stopped):
    if stopped is None:return
    if not callable(stopped):raise TargetObservationFailure('target_stop_invalid')
    try:result=stopped()
    except Exception:raise TargetObservationFailure('target_stop_failed') from None
    if type(result) is not bool:raise TargetObservationFailure('target_stop_invalid')
    if result:raise TargetObservationFailure('stopped')


def _sha(value):
    return type(value) is str and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def _roi(value):
    if type(value) not in (list,tuple) or len(value)!=4 or any(type(v) is not int for v in value):
        raise TargetObservationFailure('target_roi_invalid')
    x,y,w,h=value
    if x<0 or y<80 or w<=0 or h<=0 or x+w>1280 or y+h>610:
        raise TargetObservationFailure('target_roi_invalid')
    return tuple(value)


def _inventory_target(report,observation,target):
    """Use the same indexed pixel platform; never replace it with route geometry."""
    rich=report.get('columns');visible=observation['columns'];matches=[]
    if type(rich) is not list or len(rich)!=len(visible):
        raise TargetObservationFailure('target_inventory_disagrees')
    for i,(column,seen) in enumerate(zip(rich,visible)):
        if (type(column) is not dict or type(column.get('nodes')) is not list
                or len(column['nodes'])!=len(seen['nodes'])):
            raise TargetObservationFailure('target_inventory_disagrees')
        for j,(node,pixel) in enumerate(zip(column['nodes'],seen['nodes'])):
            if (type(node) is not dict or node.get('box')!=pixel['box']
                    or node.get('platform_box')!=pixel['box']
                    or type(node.get('block_type')) is not type(pixel['block_type'])
                    or node.get('block_type')!=pixel['block_type']):
                raise TargetObservationFailure('target_inventory_disagrees')
            if pixel['box']==list(target.box):matches.append((i,j,node))
    if len(matches)!=1:raise TargetObservationFailure('target_inventory_disagrees')
    i,j,node=matches[0]
    if node['block_type']!=target.block_type:
        raise TargetObservationFailure('target_inventory_disagrees')
    if node.get('active') is not True:raise TargetObservationFailure('target_active_unobserved')
    return [i,j]


def route_target_from_report(route,current,report,frame,*,stopped=None):
    """Calculate from supplied rich evidence without claiming a Maa producer.

    The frozen calculator exhausts every unbiased local map candidate before
    comparing the requested current, and requires one shared observed target.
    No route/current parameter is ever supplied to the pixel collector.
    """
    _check_stopped(stopped)
    if (type(report) is not dict or report.get('status')!='observed_subgraph'
            or report.get('source')!='maa_pixels' or report.get('scope')!='observed_subgraph'
            or any(report.get(k) is not False for k in ('complete','safe_to_navigate','handoff_qualified'))
            or not _sha(report.get('frame_pixel_sha256'))):
        raise TargetObservationFailure('target_partial_report_unqualified')
    try:observation=stamp_partial_observation(report,frame)
    except PartialMapFailure as error:raise TargetObservationFailure(_reason(str(error))) from None
    if report.get('area')!=observation.get('area'):
        raise TargetObservationFailure('target_inventory_disagrees')
    # Existing activation thresholds are calibrated against actual region-2
    # pixels only. A type observation elsewhere cannot widen that qualification.
    if type(observation.get('area')) is not int or observation['area']!=2:
        raise TargetObservationFailure('target_observation_scope_unqualified')
    try:target=locate_next_observed(route,current,observation,stopped=stopped)
    except (VisibleBindingFailure,ValueError) as error:
        raise TargetObservationFailure(_reason(str(error))) from None
    if target is None:raise TargetObservationFailure('route_end_no_target')
    if target.area!=2 or target.block_type not in (2,4):
        raise TargetObservationFailure('target_observation_scope_unqualified')
    observed_id=_inventory_target(report,observation,target)
    _check_stopped(stopped)
    identity=dict(enter_id=target.enter_id,block_id=target.block_id,area=target.area,
                  column=target.column,row=target.row,block_type=target.block_type,box=list(target.box))
    return dict(_closed(),status='target_observed',scope='observed_route_target',
        source='calculation_from_supplied_report',frame_source='caller_supplied_structural_frame',
        frame=frame,frame_pixel_sha256=report['frame_pixel_sha256'],current_block_id=current,
        target=identity,target_box=list(target.box),observed_target_id=observed_id,
        active_observed=True,activation_scope='existing region-2 pixel calibration; target types 2 or 4',
        local_observation=deepcopy(observation),partial_report=deepcopy(report),
        provenance=dict(target_identity='protocol route projected after unbiased exhaustive mapping',
                        target_box='supplied rich platform observation; producer not authenticated by pure helper',
                        active='supplied rich observation; only literal True accepted'),
        pending=['caller_batch_stamp','fresh_account','fresh_capture','game_session',
                 'protocol_game_lifecycle','durable_navigation_intent','movement_readback'])


def target_observation_pipeline(roi=_ROI):
    roi=_roi(roi)
    return {'LabyRouteTargetObserved':dict(recognition='Custom',custom_recognition='LabyRouteTargetObserved',
        roi=list(roi),action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)}


class MaaRouteTargetRecognition(CustomRecognition):
    """Recollect the installed Maa partial observer on exactly argv.image."""
    def __init__(self,route,current_block_id,*,stopped=None,roi=_ROI):
        super().__init__()
        if stopped is not None and not callable(stopped):raise TargetObservationFailure('target_stop_invalid')
        self.route,self.current,self.stopped,self.roi=route,current_block_id,stopped,_roi(roi)

    def install(self,resource):
        if not resource.get_node_data('LabyPartialMapObserved'):
            raise TargetObservationFailure('target_partial_observer_unavailable')
        if (not resource.override_pipeline(target_observation_pipeline(self.roi))
                or not resource.register_custom_recognition('LabyRouteTargetObserved',self)):
            raise TargetObservationFailure('target_install_failed')
        return self

    def analyze(self,context,argv):
        report=_pending('target_collection_unknown')
        def stopped():
            if getattr(context.tasker,'stopping',False):return True
            return False if self.stopped is None else self.stopped()
        try:
            _check_stopped(stopped)
            image=argv.image
            if type(image) is not np.ndarray or image.shape!=(720,1280,3) or image.dtype!=np.uint8:
                raise TargetObservationFailure('target_frame_invalid')
            digest=sha256(image.tobytes()).hexdigest()
            report.update(source='maa_same_frame',frame_source='maa_reco_id',frame_pixel_sha256=digest)
            if tuple(argv.roi)!=self.roi:raise TargetObservationFailure('target_roi_mismatch')
            detail=context.run_recognition('LabyPartialMapObserved',image)
            _check_stopped(stopped)
            if detail is None or type(detail.reco_id) is not int or not 1<=detail.reco_id<=2**63-1:
                raise TargetObservationFailure('target_partial_reco_id_unknown')
            report['partial_reco_id']=detail.reco_id
            try:partial=partial_map_readback(detail.raw_detail,hit=detail.hit)
            except PartialMapFailure:raise TargetObservationFailure('target_partial_readback_unknown') from None
            report['partial_report']=partial
            if detail.hit is not True:
                raise TargetObservationFailure(_reason(partial.get('reason'),'target_partial_report_unqualified'))
            if partial.get('frame_pixel_sha256')!=digest:
                raise TargetObservationFailure('target_frame_hash_mismatch')
            result=route_target_from_report(self.route,self.current,partial,detail.reco_id,stopped=stopped)
            result.update(source='maa_same_frame',frame_source='maa_reco_id',partial_reco_id=detail.reco_id)
            result['provenance'].update(target_box='same argv.image Maa fixed-template platform observation',
                active='same argv.image existing region-2 pixel calibration',
                structural_frame='actual LabyPartialMapObserved Maa reco_id; caller stamp remains separate',
                pixel_hash='partial frame pixel SHA256 equals argv.image pixel SHA256')
            return self.AnalyzeResult(result['target_box'],result)
        except TargetObservationFailure as error:report['reason']=_reason(str(error),'target_collection_unknown')
        except Exception:report['reason']='target_collection_unknown'
        return self.AnalyzeResult(None,report)


def route_target_readback(raw,*,hit=None):
    """Typed Maa CustomRecognition raw_detail / single native.run receipt parser."""
    if hit is None:
        if (type(raw) is not dict or type(raw.get('native_ok')) is not bool
                or raw.get('tasker_inited') is not True or type(raw.get('nodes')) is not list
                or len(raw['nodes'])!=1 or type(raw['nodes'][0]) is not dict
                or raw['nodes'][0].get('name')!='LabyRouteTargetObserved'):
            raise TargetObservationFailure('target_readback_unknown')
        receipt_ok=raw['native_ok'];node=raw['nodes'][0];raw=node.get('recognition')
        if type(raw) is not dict or type(raw.get('filtered')) is not list:
            raise TargetObservationFailure('target_readback_unknown')
        hit=bool(raw['filtered'])
        if hit and (receipt_ok is not True or node.get('completed') is not True):
            raise TargetObservationFailure('target_readback_unknown')
    if (type(hit) is not bool or type(raw) is not dict or set(raw)!={'all','filtered','best'}
            or type(raw['all']) is not list or len(raw['all'])!=1):
        raise TargetObservationFailure('target_readback_unknown')
    row=raw['all'][0]
    if (type(row) is not dict or set(row)!={'box','detail'} or type(row['detail']) is not dict
            or raw['filtered']!=([row] if hit else []) or raw['best']!=(row if hit else None)):
        raise TargetObservationFailure('target_readback_unknown')
    report=row['detail']
    if any(report.get(k) is not False for k in _CLOSED) or report.get('qualification_pending') is not True:
        raise TargetObservationFailure('target_readback_unknown')
    if not hit:
        if (row['box']!=[0,0,0,0] or report.get('status')!='unqualified'
                or report.get('reason') not in _REASONS
                or any(k in report for k in ('target','target_box','observed_target_id'))):
            raise TargetObservationFailure('target_readback_unknown')
        return report
    target=report.get('target');local=report.get('local_observation');partial=report.get('partial_report')
    if (report.get('status')!='target_observed' or report.get('scope')!='observed_route_target'
            or report.get('source')!='maa_same_frame' or report.get('frame_source')!='maa_reco_id'
            or type(report.get('frame')) is not int or not 1<=report['frame']<=2**63-1
            or type(report.get('partial_reco_id')) is not int or report['partial_reco_id']!=report['frame']
            or not _sha(report.get('frame_pixel_sha256')) or type(target) is not dict
            or set(target)!={'enter_id','block_id','area','column','row','block_type','box'}
            or any(type(target.get(k)) is not int or target[k]<1 for k in ('enter_id','block_id','area','column','row','block_type'))
            or target['area']!=2 or target['block_type'] not in (2,4)
            or target.get('box')!=report.get('target_box') or row['box']!=report['target_box']
            or report.get('active_observed') is not True or type(report.get('current_block_id')) is not int
            or report['current_block_id']<1 or type(local) is not dict or local.get('frame')!=report['frame']
            or type(partial) is not dict or partial.get('frame_pixel_sha256')!=report['frame_pixel_sha256']):
        raise TargetObservationFailure('target_readback_unknown')
    try:
        if local!=stamp_partial_observation(partial,report['frame']):raise ValueError()
        from .navigation import Target
        value=Target(target['enter_id'],target['block_id'],target['area'],target['column'],
                     target['row'],target['block_type'],tuple(target['box']))
        if _inventory_target(partial,local,value)!=report.get('observed_target_id'):raise ValueError()
    except (PartialMapFailure,TargetObservationFailure,ValueError,TypeError,KeyError):
        raise TargetObservationFailure('target_readback_unknown') from None
    return report
