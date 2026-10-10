"""Independent START artwork observation from two raised blue/white pillars.

Source semantics come from the caller's confirmed invitation-to-initial-map
pixels and visible gate artwork. No protocol ID, route, new-run configuration
or leftmost-position assumption supplies the type. Runtime memory only.
"""
from hashlib import sha256
from threading import RLock

import numpy as np
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum
from .world_marker import _box, _image, _matches


class StartGateFailure(ValueError):
    pass


_ROI = (0,80,1280,530)
_THRESHOLD = .70  # Raw NCC acceptance only, never confidence/probability.
_PAIR_TOLERANCE = 3  # Pixel registration tolerance, not a logical position prior.


class StartGateTemplate:
    """Two caller-verified initial-map pillar crops, held for one runtime owner."""
    def __init__(self, source_image, *, left_crop, right_crop, source_note):
        image = _image(source_image)
        self.left_crop, self.right_crop = _box(left_crop), _box(right_crop)
        if type(source_note) is not str or not source_note.strip() or len(source_note)>500:
            raise StartGateFailure('start_source_annotation_required')
        dx = self.right_crop[0]-self.left_crop[0]
        dy = self.right_crop[1]-self.left_crop[1]
        if not 50<=dx<=250 or not -150<=dy<0:
            raise StartGateFailure('start_pillar_geometry_invalid')
        self.offset = (dx,dy)
        self.source_note = source_note
        self.source_frame_sha256 = sha256(image.tobytes()).hexdigest()
        self._pixels = {}
        for name,(x,y,w,h) in (('left',self.left_crop),('right',self.right_crop)):
            pixels = image[y:y+h,x:x+w].copy();pixels.setflags(write=False)
            self._pixels[name] = pixels
        self.pixel_hashes = {name:sha256(p.tobytes()).hexdigest() for name,p in self._pixels.items()}
        prefix = 'start_gate_'+sha256(self._pixels['left'].tobytes()+self._pixels['right'].tobytes()).hexdigest()[:16]
        self.image_names = {name:prefix+'_'+name+'.png' for name in ('left','right')}
        self._lock,self._resource,self._closed = RLock(),None,False

    def provenance(self):
        return dict(source_frame_pixel_sha256=self.source_frame_sha256,
                    left_crop=list(self.left_crop),right_crop=list(self.right_crop),
                    source_pixel_relative_offset=list(self.offset),pixel_sha256=self.pixel_hashes,
                    source_note=self.source_note,storage='run-local official override_image',
                    semantic_origin='caller confirmed initial invitation-to-map stage and START gate artwork',
                    source_qualification='caller responsibility')

    def close(self):
        """Erase memory templates after the owning Tasker stops; no reinstall."""
        with self._lock:
            if self._closed:return
            self._closed = True
            resource,self._resource = self._resource,None
            try:
                if resource is not None:
                    for name in self.image_names.values():
                        resource.override_image(name,np.zeros((1,1,3),dtype=np.uint8))
            finally:self._pixels.clear()


def start_gate_pipeline(roi=_ROI):
    roi = _box(roi)
    return {'LabyStartGateObserved':dict(recognition='Custom',custom_recognition='LabyStartGateObserved',
        roi=list(roi),action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)}


class MaaStartGateRecognition(CustomRecognition):
    """Same-frame stable-map guard and uniquely paired native pillar matches.

    Only the two independently matched pillar rectangles form gate_box. It is
    neither a full platform footprint nor a current-character marker. A type1
    pixel observation supplies no account/new-opening/freshness/navigation gate.
    """
    def __init__(self, template, roi=_ROI):
        super().__init__()
        if type(template) is not StartGateTemplate:raise StartGateFailure('start_template_required')
        self.template,self.roi = template,_box(roi)

    def install(self,resource):
        template = self.template
        with template._lock:
            if template._closed:raise StartGateFailure('start_template_closed')
            if template._resource is not None:raise StartGateFailure('start_template_already_installed')
            template._resource = resource
            try:
                nodes = start_gate_pipeline(self.roi)
                for name in ('left','right'):
                    if not resource.override_image(template.image_names[name],template._pixels[name]):
                        raise StartGateFailure('start_template_install_failed')
                    nodes['LabyStartPillar_'+name] = dict(recognition='TemplateMatch',
                        template=template.image_names[name],method=5,threshold=_THRESHOLD,
                        roi=list(self.roi),action='DoNothing',next=[],pre_delay=0,post_delay=0)
                if (not resource.override_pipeline(nodes)
                        or not resource.register_custom_recognition('LabyStartGateObserved',self)):
                    raise StartGateFailure('start_template_install_failed')
            except Exception:
                template.close();raise StartGateFailure('start_template_install_failed') from None
        return self

    def analyze(self,context,argv):
        report = dict(status='unqualified',complete=False,safe_to_navigate=False,handoff_qualified=False,
                      new_opening_verified=False,account_verified=False,freshness_verified=False,
                      qualification_pending=True)
        try:
            with self.template._lock:
                image = _image(argv.image)
                report.update(source='maa_pixels',scope='start_gate_artwork',roi=list(self.roi),
                    frame_pixel_sha256=sha256(image.tobytes()).hexdigest(),template=self.template.provenance(),
                    minimum_raw_ncc=_THRESHOLD,pair_pixel_tolerance=_PAIR_TOLERANCE)
                if self.template._closed or self.template._resource is None:
                    return self.AnalyzeResult(None,dict(report,reason='start_template_closed'))
                if tuple(argv.roi)!=self.roi:raise StartGateFailure('start_roi_mismatch')
                guard = context.run_recognition('LabyMapStableObserved',image)
                if guard is None or guard.algorithm!=AlgorithmEnum.And or guard.hit is not True:
                    return self.AnalyzeResult(None,dict(report,reason='map_page_unknown'))
                report['page_guard_reco_id'] = guard.reco_id
                matches = {}
                for name,crop in (('left',self.template.left_crop),('right',self.template.right_crop)):
                    detail = context.run_recognition('LabyStartPillar_'+name,image)
                    matches[name],report[name+'_raw'] = _matches(detail,crop[2:],self.roi)
                dx,dy = self.template.offset
                pairs = [(left,right) for left in matches['left'] for right in matches['right']
                         if abs(right['box'][0]-left['box'][0]-dx)<=_PAIR_TOLERANCE
                         and abs(right['box'][1]-left['box'][1]-dy)<=_PAIR_TOLERANCE]
                if len(pairs)!=1:
                    return self.AnalyzeResult(None,dict(report,reason='start_gate_missing' if not pairs else 'start_gate_ambiguous'))
                left,right = pairs[0]
                x=min(left['box'][0],right['box'][0]);y=min(left['box'][1],right['box'][1])
                far_x=max(left['box'][0]+left['box'][2],right['box'][0]+right['box'][2])
                far_y=max(left['box'][1]+left['box'][3],right['box'][1]+right['box'][3])
                gate = [x,y,far_x-x,far_y-y]
                report.update(status='start_gate_observed',block_type=1,gate_box=gate,
                    left_pillar_box=left['box'],right_pillar_box=right['box'],
                    left_raw_ncc=left['score'],right_raw_ncc=right['score'],
                    provenance=dict(pillar_boxes='same-frame Maa TemplateMatch',
                                    block_type='observed START gate artwork calibrated from initial-map source',
                                    relative_geometry='two actual source pillar crops'))
                return self.AnalyzeResult(gate,report)
        except StartGateFailure as failure:report['reason']=str(failure)
        except Exception:report['reason']='start_collection_unknown'
        return self.AnalyzeResult(None,report)


def start_gate_readback(raw, *, hit=None):
    """Read either direct Tasker receipt or borrowed Context Custom raw_detail."""
    if hit is None:
        if (type(raw) is not dict or raw.get('tasker_inited') is not True
                or type(raw.get('nodes')) is not list or len(raw['nodes'])!=1
                or raw['nodes'][0].get('name')!='LabyStartGateObserved'):
            raise StartGateFailure('start_readback_unknown')
        receipt_ok=raw.get('native_ok');node=raw['nodes'][0];raw=node.get('recognition')
        if type(raw) is not dict or type(raw.get('filtered')) is not list:
            raise StartGateFailure('start_readback_unknown')
        hit=bool(raw['filtered'])
        if hit and (receipt_ok is not True or node.get('completed') is not True):
            raise StartGateFailure('start_readback_unknown')
    if (type(hit) is not bool or type(raw) is not dict or set(raw)!={'all','filtered','best'}
            or type(raw['all']) is not list or len(raw['all'])!=1):
        raise StartGateFailure('start_readback_unknown')
    row=raw['all'][0]
    if (type(row) is not dict or set(row)!={'box','detail'} or type(row['detail']) is not dict
            or raw['filtered']!=([row] if hit else []) or raw['best']!=(row if hit else None)):
        raise StartGateFailure('start_readback_unknown')
    report=row['detail']
    if (report.get('complete') is not False or report.get('safe_to_navigate') is not False
            or report.get('handoff_qualified') is not False):raise StartGateFailure('start_readback_unknown')
    if hit:
        if report.get('status')!='start_gate_observed' or report.get('block_type')!=1 or row['box']!=report.get('gate_box'):
            raise StartGateFailure('start_readback_unknown')
    elif row['box']!=[0,0,0,0] or report.get('status')!='unqualified':
        raise StartGateFailure('start_readback_unknown')
    return report
