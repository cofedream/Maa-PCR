"""Same-frame local pixel subgraph, with omissions and unknown types explicit.

This collector has no protocol/route/expected-position inputs. A pixel hit is
not a complete map, fresh account/new opening, or production handoff proof.
"""
from copy import deepcopy
from hashlib import sha256

import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum

from .map_collection import (TemplateMapping, _detect, _bind, _active,
                             _inside, _overlap, _center)
from .world_marker import world_marker_readback
from .start_gate import StartGateTemplate, MaaStartGateRecognition, start_gate_readback
from .area import (area_pipeline, _rows as _area_rows, _stable_map,
                   _word, _total, LABEL_ROI, CURRENT_ROI)


class PartialMapFailure(ValueError):
    pass


_ROI = (0, 80, 1280, 530)


def _roi(value):
    if type(value) not in (list, tuple) or len(value) != 4 or any(type(x) is not int for x in value):
        raise PartialMapFailure('partial_roi_invalid')
    x, y, w, h = value
    if x < 0 or y < 80 or w <= 0 or h <= 0 or x+w > 1280 or y+h > 610:
        raise PartialMapFailure('partial_roi_invalid')
    return tuple(value)


def collect_partial_inventory(image, roi, templates):
    """Retain every unambiguous full observed platform and its known/None type.

    platform_box is the actual fixed-template match rectangle, not an invented
    footprint. Partially ROI-clipped detections are omitted, never called absent.
    The existing template crops and NCC thresholds are reused unchanged.
    """
    if type(image) is not np.ndarray or image.shape != (720, 1280, 3) or image.dtype != np.uint8:
        raise PartialMapFailure('partial_frame_invalid')
    if type(templates) is not TemplateMapping:
        raise PartialMapFailure('partial_templates_required')
    roi = _roi(roi)
    report = dict(scope='observed_subgraph', source='maa_pixels', roi=list(roi),
                  frame_pixel_sha256=sha256(image.tobytes()).hexdigest(), columns=[],
                  positive_edges=[], edges_observed=False, complete=False,
                  complete_columns=False, complete_edges=False, safe_to_navigate=False,
                  handoff_qualified=False, qualification_pending=True,
                  template_provenance=templates.provenance())
    edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 50, 100)
    detected = {role: _detect(edges, template) for role, template in templates.values.items()}
    platforms = detected['active_platform'] + detected['inactive_platform']
    icons = detected['event_icon'] + detected['link_icon'] + detected['normal_icon']
    selected = [platform for platform in platforms if _inside(platform['box'], roi)]
    report['raw_detected'] = detected
    report['omitted_clipped_platforms'] = [p for p in platforms
        if _overlap(p['box'], roi) and not _inside(p['box'], roi)]
    if not selected:
        return dict(report, status='unqualified', reason='platforms_missing')
    if any(_overlap(a['box'], b['box']) for i, a in enumerate(selected) for b in selected[i+1:]):
        return dict(report, status='unqualified', reason='platforms_ambiguous')
    nodes = []
    for platform in selected:
        icon, reason = _bind(platform, icons)
        active, cyan_ratio = _active(image, platform['box'])
        nodes.append(dict(box=list(platform['box']), platform_box=list(platform['box']),
                          block_type=icon['block_type'] if icon else None,
                          glyph_box=list(icon['box']) if icon else None,
                          type_gradient_ncc=icon['gradient_ncc'] if icon else None,
                          type_reason=reason, active=active, cyan_ratio=cyan_ratio,
                          platform_gradient_ncc=platform['gradient_ncc'],
                          platform_template_role=platform['role']))
    groups = []
    for node in sorted(nodes, key=lambda n: _center(n['box'])):
        if not groups or _center(node['box'])[0] - np.mean([_center(n['box'])[0] for n in groups[-1]]) > 90:
            groups.append([node])
        else:
            groups[-1].append(node)
    for group in groups:
        group.sort(key=lambda n: _center(n['box'])[1])
    report['columns'] = [dict(nodes=group) for group in groups]
    return dict(report, status='inventory_observed')


def _apply_start_gate_type(report, gate):
    """Associate observed artwork with one actual platform; never logical position.

    Gate artwork extends above its platform. Its complete measured horizontal
    span and its bottom-edge center must naturally be inside the same actual
    platform rectangle. No rectangle is expanded or replaced by gate_box.
    """
    if (gate.get('status') != 'start_gate_observed' or gate.get('block_type') != 1
            or gate.get('frame_pixel_sha256') != report.get('frame_pixel_sha256')):
        raise PartialMapFailure('start_frame_or_readback_unknown')
    gx, gy, gw, gh = _roi(gate.get('gate_box'))
    bottom_center = [gx+gw/2, gy+gh]
    possible = []
    for i, column in enumerate(report['columns']):
        for j, node in enumerate(column['nodes']):
            px, py, pw, ph = _roi(node['platform_box'])
            if px <= gx and gx+gw <= px+pw and py <= bottom_center[1] < py+ph:
                possible.append((i, j, node))
    report['start_geometry'] = dict(gate_artwork_box=gate['gate_box'],
        artwork_bottom_center=bottom_center, candidate_count=len(possible),
        association='whole observed pillar horizontal span and artwork bottom center in unique actual platform box')
    if len(possible) != 1:
        raise PartialMapFailure('start_platform_geometry_unqualified')
    column, index, node = possible[0]
    report['start_geometry'].update(column=column, node=index,
        observed_platform_box=node['platform_box'])
    if node['block_type'] not in (None, 1):
        raise PartialMapFailure('start_platform_type_conflicting')
    # Original glyph/gradient evidence remains intact; START is separate proof.
    node['glyph_block_type'] = node['block_type']
    node['block_type'] = 1
    node['type_source'] = 'same-frame independently paired START pillar artwork'


def normal_platform_label_pipeline():
    return {'LabyPlatformNormalLabel':dict(recognition='OCR',expected=r'^普通$',
        roi=list(_ROI),threshold=.85,action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)}


def _normal_label_kind(raw,box,*,hit):
    # A same-image ROI OCR word is independent of protocol/route/type hints.
    import math
    if type(hit) is not bool or type(raw) is not dict or set(raw)!={'all','filtered','best'}:
        raise PartialMapFailure('normal_label_readback_unknown')
    words=raw['filtered']
    if type(raw['all']) is not list or type(words) is not list:
        raise PartialMapFailure('normal_label_readback_unknown')
    if not hit:
        if words or raw['best'] is not None:raise PartialMapFailure('normal_label_readback_unknown')
        return None
    if len(words)!=1 or type(words[0]) is not dict or raw['best']!=words[0]:
        raise PartialMapFailure('normal_label_readback_unknown')
    word=words[0];score=word.get('score');actual=word.get('box')
    if (word.get('text')!='普通' or type(score) not in (int,float) or not math.isfinite(score)
            or not .85<=score<=1 or type(actual) is not list or len(actual)!=4
            or any(type(v) is not int for v in actual) or actual[2]<=0 or actual[3]<=0
            or not _inside(actual,box)):
        raise PartialMapFailure('normal_label_readback_unknown')
    return 2


def _apply_normal_labels(context,image,report):
    original=deepcopy(context.get_node_data('LabyPlatformNormalLabel'))
    if type(original) is not dict:raise PartialMapFailure('normal_label_scope_unknown')
    try:
        report['normal_label_evidence']=[]
        for i,column in enumerate(report['columns']):
            for j,node in enumerate(column['nodes']):
                box=node['platform_box']
                detail=context.run_recognition('LabyPlatformNormalLabel',image,
                    {'LabyPlatformNormalLabel':{'roi':box}})
                if detail is None or detail.algorithm!=AlgorithmEnum.OCR or type(detail.reco_id) is not int or detail.reco_id<=0:
                    raise PartialMapFailure('normal_label_readback_unknown')
                kind=_normal_label_kind(detail.raw_detail,box,hit=detail.hit)
                report['normal_label_evidence'].append(dict(column=i,node=j,roi=box,
                    reco_id=detail.reco_id,hit=detail.hit,raw=detail.raw_detail))
                if kind is not None:
                    if node['block_type'] not in (None,2):raise PartialMapFailure('normal_label_type_conflicting')
                    node['glyph_block_type']=node['block_type'];node['block_type']=2
                    node['type_source']='same argv.image Maa OCR exact normal label inside actual platform ROI'
    finally:
        if not context.override_pipeline({'LabyPlatformNormalLabel':original}):
            raise PartialMapFailure('normal_label_scope_restore_failed')



def partial_map_pipeline(roi=_ROI):
    roi = _roi(roi)
    return dict(area_pipeline(), LabyPartialMapObserved=dict(
        recognition='Custom', custom_recognition='LabyPartialMapObserved',
        roi=list(roi), action='DoNothing', next=[], timeout=1000, pre_delay=0, post_delay=0))


def _area(detail):
    # Reuse area.py's original same-frame And/OR parser, including preceding
    # failed Or candidates with box=None. No completed-task receipt is invented.
    if detail is None or detail.algorithm != AlgorithmEnum.And or detail.hit is not True:
        raise PartialMapFailure('current_area_unobserved')
    seen = {'LabyAreaObserved'}
    rows = _area_rows(detail.raw_detail, area_pipeline()['LabyAreaObserved']['all_of'], seen)
    _stable_map(rows['LabyMapStableObserved'], seen)
    if _word(rows['LabyAreaLabel'], LABEL_ROI, strict_all=True) != '区域':
        raise PartialMapFailure('current_area_unobserved')
    current = _word(rows['LabyAreaCurrent'], CURRENT_ROI, strict_all=True)
    if current not in ('1','2','3','4','5'):
        raise PartialMapFailure('current_area_unobserved')
    total, total_source = _total(rows['LabyAreaTotalOptional'], seen)
    if total is not None and int(current) > total:
        raise PartialMapFailure('current_area_conflicting')
    return int(current), total, total_source


class MaaPartialMapRecognition(CustomRecognition):
    """Compose fixed pixel inventory and existing native marker on argv.image.

    Geometry requires the entire annotated foot box in one actual platform_box,
    and the independent front-subregion center in that same platform_box. Match
    rectangles differ by a few pixels; neither is called a true footprint and
    neither is expanded. The full native front box is retained for diagnosis.
    """
    def __init__(self, templates, roi=_ROI, *, start_gate_template=None, normal_label_ocr=False):
        super().__init__()
        if type(templates) is not TemplateMapping:
            raise PartialMapFailure('partial_templates_required')
        if start_gate_template is not None and type(start_gate_template) is not StartGateTemplate:
            raise PartialMapFailure('partial_start_template_required')
        if type(normal_label_ocr) is not bool:raise PartialMapFailure('normal_label_option_invalid')
        self.normal_label_ocr=normal_label_ocr
        self.templates, self.roi = templates, _roi(roi)
        self.start_gate_template = start_gate_template
        self.start_gate_observer = None

    def install(self, resource):
        if self.start_gate_template is not None:
            self.start_gate_observer = MaaStartGateRecognition(self.start_gate_template, self.roi).install(resource)
        if self.normal_label_ocr and not resource.override_pipeline(normal_platform_label_pipeline()):
            raise PartialMapFailure('normal_label_install_failed')
        if (not resource.override_pipeline(partial_map_pipeline(self.roi))
                or not resource.register_custom_recognition('LabyPartialMapObserved', self)):
            raise PartialMapFailure('partial_install_failed')
        return self

    def analyze(self, context, argv):
        report = dict(status='unqualified', reason='partial_collection_unknown',
                      complete=False, safe_to_navigate=False, handoff_qualified=False,
                      qualification_pending=True, observation_without_frame=None)
        try:
            if tuple(argv.roi) != self.roi:
                raise PartialMapFailure('partial_roi_mismatch')
            guard = context.run_recognition('LabyMapStableObserved', argv.image)
            if guard is None or guard.algorithm != AlgorithmEnum.And or guard.hit is not True:
                return self.AnalyzeResult(None, dict(report, reason='map_page_unknown'))
            report = collect_partial_inventory(argv.image, self.roi, self.templates)
            report['observation_without_frame'] = None
            report['page_guard_reco_id'] = guard.reco_id
            if report['status'] != 'inventory_observed':
                return self.AnalyzeResult(None, report)
            report['normal_label_ocr_configured']=self.normal_label_ocr
            if self.normal_label_ocr:_apply_normal_labels(context,argv.image,report)
            report['start_gate_configured'] = self.start_gate_template is not None
            if self.start_gate_template is not None:
                start_detail = context.run_recognition('LabyStartGateObserved', argv.image)
                if start_detail is None:
                    raise PartialMapFailure('start_readback_unknown')
                start = start_gate_readback(start_detail.raw_detail, hit=start_detail.hit)
                report['start_gate_raw'] = start
                if start.get('frame_pixel_sha256') != report['frame_pixel_sha256']:
                    raise PartialMapFailure('start_frame_mismatch')
                if start_detail.hit is True:
                    _apply_start_gate_type(report, start)
                elif start.get('reason') != 'start_gate_missing':
                    raise PartialMapFailure('start_observation_unqualified')
            area_detail = context.run_recognition('LabyAreaObserved', argv.image)
            report['area'], report['area_total'], report['area_total_source'] = _area(area_detail)
            report['area_raw'] = area_detail.raw_detail
            marker_detail = context.run_recognition('LabyWorldMarkerObserved', argv.image)
            if marker_detail is None:
                raise PartialMapFailure('marker_readback_unknown')
            marker = world_marker_readback(marker_detail.raw_detail, hit=marker_detail.hit)
            report['marker_raw'] = marker
            if marker_detail.hit is not True:
                return self.AnalyzeResult(None, dict(report, status='unqualified', reason=marker.get('reason', 'marker_unqualified')))
            if marker.get('frame_pixel_sha256') != report['frame_pixel_sha256']:
                raise PartialMapFailure('marker_frame_mismatch')
            foot = marker['pixel_anchor']['box']
            front = marker['pixel_anchor']['platform_subregion_box']
            front_center = _center(front)
            possible = [(i,j,node) for i,column in enumerate(report['columns'])
                        for j,node in enumerate(column['nodes']) if _inside(foot,node['platform_box'])
                        and node['platform_box'][0] <= front_center[0] < node['platform_box'][0]+node['platform_box'][2]
                        and node['platform_box'][1] <= front_center[1] < node['platform_box'][1]+node['platform_box'][3]]
            if len(possible) != 1:
                return self.AnalyzeResult(None, dict(report, status='unqualified', reason='anchor_platform_geometry_unqualified'))
            column, node, platform = possible[0]
            report['anchor_geometry'] = dict(foot_box=foot, native_front_subregion_box=front,
                observed_platform_box=platform['platform_box'],
                entire_front_inside=_inside(front,platform['platform_box']),
                association='whole annotated foot and independent front center in unique actual platform box')
            report['observation_without_frame'] = dict(source='maa', scope='observed_subgraph', area=report['area'],
                columns=[dict(nodes=[dict(box=n['platform_box'], block_type=n['block_type'])
                                    for n in c['nodes']]) for c in report['columns']],
                positive_edges=[], current_marker=dict(column=column,node=node,box=foot))
            report.update(status='observed_subgraph', current_anchor_observed=True,
                          provenance=dict(platform_boxes='fixed-template raw pixel inventory',
                            node_types='independent supported glyphs and optional same-frame exact normal-label OCR; unknowns remain None',
                            current_marker='same-frame native actor + caller annotated offset + independent platform front',
                            area='same-frame Maa OCR',positive_edges='not collected; omission is not absence',
                            start_gate='same-frame paired artwork with unique observed platform geometry' if 'start_geometry' in report else 'not observed; unknown type preserved'))
            return self.AnalyzeResult(foot, report)
        except PartialMapFailure as failure:
            report.update(status='unqualified', reason=str(failure))
        except Exception:
            report.update(status='unqualified', reason='partial_collection_unknown')
        report['observation_without_frame'] = None
        return self.AnalyzeResult(None, report)


def partial_map_readback(raw, *, hit=None):
    # Direct Tasker receipts and borrowed Context raw_detail share one parser.
    if hit is None:
        if (type(raw) is not dict or type(raw.get('native_ok')) is not bool
                or raw.get('tasker_inited') is not True or type(raw.get('nodes')) is not list
                or len(raw['nodes']) != 1 or type(raw['nodes'][0]) is not dict
                or raw['nodes'][0].get('name') != 'LabyPartialMapObserved'):
            raise PartialMapFailure('partial_readback_unknown')
        receipt_ok = raw['native_ok']
        node = raw['nodes'][0]
        raw = node.get('recognition')
        if type(raw) is not dict or type(raw.get('filtered')) is not list:
            raise PartialMapFailure('partial_readback_unknown')
        hit = bool(raw['filtered'])
        if hit and (receipt_ok is not True or node.get('completed') is not True):
            raise PartialMapFailure('partial_readback_unknown')
    if (type(hit) is not bool or type(raw) is not dict or set(raw) != {'all','filtered','best'}
            or type(raw['all']) is not list or len(raw['all']) != 1):
        raise PartialMapFailure('partial_readback_unknown')
    row = raw['all'][0]
    if (type(row) is not dict or set(row) != {'box','detail'} or type(row['detail']) is not dict
            or raw['filtered'] != ([row] if hit else []) or raw['best'] != (row if hit else None)):
        raise PartialMapFailure('partial_readback_unknown')
    report = row['detail']
    if (report.get('complete') is not False or report.get('safe_to_navigate') is not False
            or report.get('handoff_qualified') is not False):
        raise PartialMapFailure('partial_readback_unknown')
    if hit:
        observation = report.get('observation_without_frame')
        if (report.get('status') != 'observed_subgraph' or type(observation) is not dict
                or row['box'] != observation.get('current_marker',{}).get('box') or 'frame' in observation):
            raise PartialMapFailure('partial_readback_unknown')
    elif row['box'] != [0,0,0,0] or report.get('observation_without_frame') is not None:
        raise PartialMapFailure('partial_readback_unknown')
    return report


def stamp_partial_observation(report, frame):
    """Caller stamps capture identity outside the callback; no freshness claim."""
    if type(frame) is not int or not 1 <= frame <= 2**63-1:
        raise PartialMapFailure('partial_frame_stamp_invalid')
    if type(report) is not dict or report.get('status') != 'observed_subgraph':
        raise PartialMapFailure('partial_observation_unqualified')
    observation = report.get('observation_without_frame')
    if type(observation) is not dict or set(observation) != {'source','scope','area','columns','positive_edges','current_marker'}:
        raise PartialMapFailure('partial_observation_unqualified')
    return dict(deepcopy(observation), frame=frame)
