"""Independent saved-frame node inventory; never a complete-map/handoff proof.

Fixed template crops are calibrated only against two private region-2 frames.
Their gradient NCC scores stay raw scores, not probabilities or confidence .9.
Full-column exhaustiveness, visible edges, and current marker are unverified.
No protocol/route/current-node input or game input is accepted by this module.
Game pixel templates stay outside the candidate and are hash-checked on injection.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum

class MapCollectionFailure(ValueError):pass

# Source pixels: fixed upstream cn-bilibili resource pack. These hashes qualify
# only this narrow image transform/calibration, not broader game layouts.
_SPECS={
    'event_icon': ('node_event_active_bottom.png', '0824af12ce7ad1c2cba33948ebc0a37d089f2e2afe5adf3ee38e1c310acb8861', (45, 20, 195, 165), 0.3, 5),
    'link_icon': ('node_link_active.png', 'e7a26e10c8f7e377a9efd0b0159f009387341ae93cd48bff717bd2d3b11e5453', (70, 35, 165, 155), 0.3, 4),
    'normal_icon': ('node_normal_battle_inactive_bottom.png', 'f6323d08d6b8be597bbdf2b71132a11a977985e4a25bb2248012c5eaad6f4755', (40, 0, 180, 165), 0.35, 2),
    'active_platform': ('node_event_active_bottom.png', '0824af12ce7ad1c2cba33948ebc0a37d089f2e2afe5adf3ee38e1c310acb8861', (35, 175, 220, 150), 0.3, None),
    'inactive_platform': ('node_normal_battle_inactive_bottom.png', 'f6323d08d6b8be597bbdf2b71132a11a977985e4a25bb2248012c5eaad6f4755', (8, 165, 260, 118), 0.4, None),
}
_SCALE=2/3
_SEARCH=(0,80,1280,570)
CALIBRATION_SCOPE='two saved region-2 frames; candidate inventory only'

@dataclass(frozen=True)
class PixelTemplate:
    role:str
    pixels:np.ndarray
    minimum:float
    block_type:int|None
    filename:str
    source_sha256:str

class TemplateMapping:
    """Exact five fixed roles, no disk defaults or route-conditioned templates."""
    def __init__(self,paths):
        if type(paths) is not dict or set(paths)!=set(_SPECS):
            raise MapCollectionFailure('template_mapping_incomplete')
        values={}
        for role,(filename,digest,crop,minimum,kind) in _SPECS.items():
            try:content=Path(paths[role]).read_bytes()
            except (OSError,TypeError,ValueError):raise MapCollectionFailure('template_resource_missing') from None
            if sha256(content).hexdigest()!=digest:
                raise MapCollectionFailure('template_resource_hash_mismatch')
            image=cv2.imdecode(np.frombuffer(content,dtype=np.uint8),cv2.IMREAD_COLOR)
            if image is None or image.shape!=(350,280,3):
                raise MapCollectionFailure('template_resource_invalid')
            x,y,w,h=crop
            image=cv2.resize(image[y:y+h,x:x+w],(round(w*_SCALE),round(h*_SCALE)),interpolation=cv2.INTER_AREA)
            pixels=cv2.Canny(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),50,100)
            pixels.setflags(write=False)
            values[role]=PixelTemplate(role,pixels,minimum,kind,filename,digest)
        self.values=MappingProxyType(values)

    def provenance(self):
        return [dict(role=v.role,filename=v.filename,source_sha256=v.source_sha256,
            source='upstream cn-bilibili game pixel resource',scale=_SCALE,
            minimum_gradient_ncc=v.minimum,calibration=CALIBRATION_SCOPE)
            for v in self.values.values()]


def _roi(value):
    if type(value) not in (list,tuple) or len(value)!=4 or any(type(v) is not int for v in value):
        raise MapCollectionFailure('inventory_roi_invalid')
    x,y,w,h=value
    if x<0 or y<80 or w<=0 or h<=0 or x+w>1280 or y+h>650:
        raise MapCollectionFailure('inventory_roi_invalid')
    return tuple(value)


def _inside(box,roi):
    x,y,w,h=box;a,b,c,d=roi
    return x>=a and y>=b and x+w<=a+c and y+h<=b+d


def _overlap(a,b):
    return max(a[0],b[0])<min(a[0]+a[2],b[0]+b[2]) and max(a[1],b[1])<min(a[1]+a[3],b[1]+b[3])


def _center(box):return box[0]+box[2]/2,box[1]+box[3]/2


def _detect(edges,template):
    x,y,w,h=_SEARCH
    scores=cv2.matchTemplate(edges[y:y+h,x:x+w],template.pixels,cv2.TM_CCOEFF_NORMED)
    found=[]
    for _ in range(24):
        _,score,_,location=cv2.minMaxLoc(scores)
        if score<template.minimum:return found
        px,py=location;tw,th=template.pixels.shape[1],template.pixels.shape[0]
        found.append(dict(role=template.role,box=[px+x,py+y,tw,th],gradient_ncc=float(score),
            block_type=template.block_type))
        # Local maxima of one physical object are not different nodes. The
        # 80px radius is smaller than measured column/row gaps (350/175px).
        scores[max(0,py-80):py+81,max(0,px-80):px+81]=-1
    raise MapCollectionFailure('inventory_candidate_limit')


def _active(image,box):
    x,y,w,h=box;b,g,r=cv2.split(image[y:y+h,x:x+w])
    ratio=float(np.mean((b>=190)&(g>=140)&(r<=170)&(b.astype(np.int16)-r>=45)))
    # Measured region-2 positives >= .277 and negatives <= .060. A ratio in
    # the gap is unresolved, not a probability or a promoted type confidence.
    if .08<ratio<.20:return None,ratio
    return ratio>=.20,ratio


def _bind(platform,icons):
    px,py=_center(platform['box'])
    matches=[node for node in icons if abs(_center(node['box'])[0]-px)<=45
        and -45<=node['box'][1]+node['box'][3]-platform['box'][1]<=35]
    if len(matches)!=1:return None,'node_type_unknown' if not matches else 'node_type_ambiguous'
    return matches[0],None


def collect_node_inventory(image,roi,templates):
    """Return measured candidate columns or a fail-closed stopped diagnostic.

    Even a successful inventory has complete=False, safe_to_navigate=False.
    It is deliberately not the observation schema accepted by match_visible_map.
    A smaller ROI selects pixel candidates only; it cannot assert no unseen nodes.
    """
    if type(image) is not np.ndarray or image.shape!=(720,1280,3) or image.dtype!=np.uint8:
        raise MapCollectionFailure('inventory_frame_invalid')
    if type(templates) is not TemplateMapping:
        raise MapCollectionFailure('template_mapping_required')
    roi=_roi(roi)
    result=dict(source='maa_pixels',stage='node_inventory',complete=False,safe_to_navigate=False,
        handoff_qualified=False,edges_verified=False,marker_verified=False,
        qualification_pending=True,roi=list(roi),frame_sha256=sha256(image.tobytes()).hexdigest(),
        calibration=CALIBRATION_SCOPE,columns=[],platform_candidates=[],
        pending=['full_column_exhaustiveness','all_visible_edges','current_marker','native_capture_freshness'],
        template_provenance=templates.provenance())
    edges=cv2.Canny(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),50,100)
    detected={role:_detect(edges,template) for role,template in templates.values.items()}
    platforms=detected['active_platform']+detected['inactive_platform']
    icons=detected['event_icon']+detected['link_icon']+detected['normal_icon']
    selected=[p for p in platforms if _overlap(p['box'],roi)]
    result['platform_candidates']=selected
    if not selected:return dict(result,status='stopped',reason='node_candidates_missing')
    if any(not _inside(p['box'],roi) for p in selected):
        return dict(result,status='stopped',reason='roi_clips_platform')
    if any(_overlap(a['box'],b['box']) for i,a in enumerate(selected) for b in selected[i+1:]):
        return dict(result,status='stopped',reason='platform_candidates_ambiguous')
    nodes=[];unknown=[]
    for platform in selected:
        node,reason=_bind(platform,icons)
        active,ratio=_active(image,platform['box'])
        if reason or active is None:
            unknown.append(dict(platform=platform,reason=reason or 'node_activation_unknown',cyan_ratio=ratio))
            continue
        nodes.append(dict(box=node['box'],platform_box=platform['box'],block_type=node['block_type'],
            active=active,type_gradient_ncc=node['gradient_ncc'],platform_gradient_ncc=platform['gradient_ncc'],
            cyan_ratio=ratio,template_role=node['role']))
    groups=[]
    for node in sorted(nodes,key=lambda n:_center(n['box'])):
        if not groups or _center(node['box'])[0]-np.mean([_center(n['box'])[0] for n in groups[-1]])>90:
            groups.append([node])
        else:groups[-1].append(node)
    for group in groups:group.sort(key=lambda n:_center(n['box'])[1])
    result['columns']=[dict(complete=False,nodes=group) for group in groups]
    result['unclassified_platforms']=unknown
    if unknown:return dict(result,status='stopped',reason=unknown[0]['reason'])
    if len(groups)<2:return dict(result,status='stopped',reason='adjacent_columns_missing')
    if roi[0]==0 or roi[0]+roi[2]==1280:
        return dict(result,status='stopped',reason='viewport_edge_unverified')
    return dict(result,status='inventory_observed',reason='full_column_edges_marker_unverified')



def inventory_readback(raw,*,hit):
    """Consume Maa 5.13 CustomRecognition's all/filtered/best wrapper.

    A native hit still only yields an inventory; complete/navigation/handoff
    claims are rejected. No caller-provided protocol identity is accepted.
    """
    def unknown():raise MapCollectionFailure('inventory_native_readback_unknown')
    if (type(hit) is not bool or type(raw) is not dict or set(raw)!={'all','filtered','best'}
            or type(raw['all']) is not list or len(raw['all'])!=1):unknown()
    row=raw['all'][0]
    if type(row) is not dict or set(row)!={'box','detail'} or type(row['detail']) is not dict:unknown()
    if raw['filtered']!=([row] if hit else []) or raw['best']!=(row if hit else None):unknown()
    report=row['detail']
    if (report.get('complete') is not False or report.get('safe_to_navigate') is not False
            or report.get('handoff_qualified') is not False):unknown()
    if hit:
        if (report.get('status')!='inventory_observed' or report.get('source')!='maa_pixels'
                or report.get('stage')!='node_inventory' or row['box']!=report.get('roi')
                or report.get('edges_verified') is not False or report.get('marker_verified') is not False
                or report.get('qualification_pending') is not True or report.get('calibration')!=CALIBRATION_SCOPE):unknown()
        try:_roi(row['box'])
        except MapCollectionFailure:unknown()
        columns=report.get('columns')
        if type(columns) is not list or len(columns)<2 or any(type(c) is not dict or c.get('complete') is not False
                or type(c.get('nodes')) is not list or not c['nodes'] for c in columns):unknown()
    # Maa 5.13 exposes a Custom miss as a zero Rect, not Python None.
    elif row['box']!=[0,0,0,0] or report.get('status')!='stopped':unknown()
    return report


def inventory_pipeline(roi):
    """Input-free collection task, not a navigation or handoff gate."""
    roi=_roi(roi)
    return {'LabyVisibleNodeInventory':dict(recognition='Custom',custom_recognition='LabyVisibleNodeInventory',
        roi=list(roi),action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)}


class MaaNodeInventoryRecognition(CustomRecognition):
    """One same-frame stable-page guard and pixel inventory; no screenshot/input.

    A hit means only supported node inventory observed. Every detail explicitly
    denies completeness/navigation/handoff; stopped cases return a native miss.
    """
    def __init__(self,templates):
        super().__init__()
        if type(templates) is not TemplateMapping:raise MapCollectionFailure('template_mapping_required')
        self.templates=templates
    def analyze(self,context,argv):
        try:
            guard=context.run_recognition('LabyMapStableObserved',argv.image)
            if guard is None or guard.algorithm!=AlgorithmEnum.And or guard.hit is not True:
                return self.AnalyzeResult(None,dict(status='stopped',reason='map_page_unknown',complete=False,
                    safe_to_navigate=False,handoff_qualified=False))
            report=collect_node_inventory(argv.image,tuple(argv.roi),self.templates)
            report['page_guard_reco_id']=guard.reco_id
            return self.AnalyzeResult(list(argv.roi) if report['status']=='inventory_observed' else None,report)
        except Exception:
            # Static reasons only; never escape ctypes or serialize exception text.
            return self.AnalyzeResult(None,dict(status='stopped',reason='inventory_collection_unknown',complete=False,
                safe_to_navigate=False,handoff_qualified=False))
