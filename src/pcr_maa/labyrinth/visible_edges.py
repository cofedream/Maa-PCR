"""Experimental independent grey-rail candidates in a visible pixel inventory.

Only two private region-2 frames calibrate this geometry/color corridor. Endpoint
occlusion, column exhaustiveness, short residuals and highlighted rails remain
unqualified. This stage never claims all edges or produces a handoff observation.
No protocol map, route, selected target or expected-edge set is accepted.
"""
from hashlib import sha256
import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition
from .map_collection import inventory_readback

class VisibleEdgeFailure(ValueError):pass

CALIBRATION='two region-2 saved frames; grey inner-band candidate signals only'


def _box(value):
    if type(value) is not list or len(value)!=4 or any(type(v) is not int for v in value):
        raise VisibleEdgeFailure('edge_pixel_inventory_invalid')
    x,y,w,h=value
    if x<0 or y<80 or w<=0 or h<=0 or x+w>1280 or y+h>650:
        raise VisibleEdgeFailure('edge_pixel_inventory_invalid')
    return value


def _inventory(image,inventory):
    if type(image) is not np.ndarray or image.shape!=(720,1280,3) or image.dtype!=np.uint8:
        raise VisibleEdgeFailure('edge_frame_invalid')
    if (type(inventory) is not dict or inventory.get('source')!='maa_pixels'
            or inventory.get('stage')!='node_inventory' or inventory.get('status')!='inventory_observed'
            or inventory.get('complete') is not False or inventory.get('safe_to_navigate') is not False
            or inventory.get('handoff_qualified') is not False):
        raise VisibleEdgeFailure('edge_pixel_inventory_unavailable')
    if inventory.get('frame_sha256')!=sha256(image.tobytes()).hexdigest():
        raise VisibleEdgeFailure('edge_frame_inventory_mismatch')
    columns=inventory.get('columns')
    # A different count/layout needs independent pixel calibration, not a
    # protocol-conditioned repair or opportunistic geometry fallback.
    if type(columns) is not list or len(columns)!=2 or any(type(c) is not dict
            or c.get('complete') is not False or type(c.get('nodes')) is not list
            or len(c['nodes'])!=3 for c in columns):
        raise VisibleEdgeFailure('edge_layout_unqualified')
    for column in columns:
        previous=-1
        for node in column['nodes']:
            if type(node) is not dict or 'block_id' in node or 'enter_id' in node:
                raise VisibleEdgeFailure('edge_pixel_inventory_invalid')
            box=_box(node.get('platform_box'));_box(node.get('box'))
            if box[1]<=previous:raise VisibleEdgeFailure('edge_pixel_inventory_invalid')
            previous=box[1]
    return columns


def _masks(image):
    b,g,r=cv2.split(image);values=image.astype(np.int16)
    neutral=(r>=70)&(g>=55)&(b>=80)&(b<=185)&(values.max(axis=2)-values.min(axis=2)<=55)
    # No positive highlighted-edge calibration is available. Long white rails
    # are reported as unqualified rather than silently treated as grey/absent.
    highlighted=(b>=220)&(g>=190)&(r>=170)&(values.max(axis=2)-values.min(axis=2)<=70)
    return neutral,highlighted


def _geometry(a,b):
    ax,ay,aw,ah=a['platform_box'];bx,by,bw,bh=b['platform_box']
    cx=ax+aw/2;dx=bx+bw/2
    if dx-cx<200:raise VisibleEdgeFailure('edge_band_invalid')
    sy=ay+ah*.15;ty=by+bh*.30
    return dict(source_center_x=cx,target_center_x=dx,source_y=sy,target_y=ty,slope=(ty-sy)/(dx-cx))


def _y(geometry,x):return geometry['source_y']+(x-geometry['source_center_x'])*geometry['slope']


def _profile(mask,geometry,xs):
    ys=_y(geometry,xs);choices=[]
    for offset in range(-8,9):
        points=np.rint(ys+offset).astype(int)[None,:]+np.arange(-6,7)[:,None]
        if points.min()<80 or points.max()>=610:raise VisibleEdgeFailure('edge_band_invalid')
        counts=mask[points,xs[None,:]].sum(axis=0);valid=counts>=3
        gap=longest=0
        for value in valid:
            gap=0 if value else gap+1;longest=max(longest,gap)
        choices.append(dict(offset=offset,pixel_coverage=float(np.mean(valid)),
            mean_neutral_pixels=float(np.mean(counts)),longest_gap_pixels=longest))
    return max(choices,key=lambda v:(v['pixel_coverage'],v['mean_neutral_pixels']))


def _long_segments(mask,x0,x1):
    lines=cv2.HoughLinesP(mask[80:610,x0:x1+1].astype(np.uint8)*255,
        1,np.pi/180,threshold=70,minLineLength=100,maxLineGap=8)
    return [] if lines is None else [[int(xa)+x0,int(ya)+80,int(xb)+x0,int(yb)+80]
        for xa,ya,xb,yb in lines.reshape(-1,4)]


def _crosses(a,b):
    # Shared endpoints are forks/merges, not interior crossings.
    return (a['from'][1]-b['from'][1])*(a['to'][1]-b['to'][1])<0


def collect_visible_edge_candidates(image,inventory):
    """Analyze all nine pairs, preserving raw signals and explicit limitations."""
    columns=_inventory(image,inventory);left,right=columns
    x0=max(n['platform_box'][0]+n['platform_box'][2] for n in left['nodes'])+24
    x1=min(n['platform_box'][0] for n in right['nodes'])-24
    if x1-x0<100:raise VisibleEdgeFailure('edge_band_invalid')
    xs=np.arange(x0,x1+1);neutral,highlighted=_masks(image)
    result=dict(source='maa_pixels',stage='visible_edge_candidates',frame_sha256=inventory['frame_sha256'],
        roi=inventory['roi'],band=[x0,80,x1-x0+1,530],calibration=CALIBRATION,
        complete=False,edges_complete=False,all_visible_edges_verified=False,
        safe_to_navigate=False,handoff_qualified=False,qualification_pending=True,
        endpoints_occluded=True,highlighted_edges_qualified=False,
        pending=['full_column_exhaustiveness','platform_occluded_endpoints','short_or_obscured_residuals',
                 'highlighted_rail_calibration','all_visible_edges','current_marker','native_capture_freshness'])
    signals=[];observed=[];uncertain=[]
    for i,a in enumerate(left['nodes']):
        for j,b in enumerate(right['nodes']):
            geometry=_geometry(a,b);profile=_profile(neutral,geometry,xs)
            signal=dict(from_=[0,i],to=[1,j],geometry=geometry,**profile)
            signal['from']=signal.pop('from_')
            if profile['pixel_coverage']>=.90 and profile['longest_gap_pixels']<=6:
                signal['classification']='grey_candidate';observed.append(signal)
            elif profile['pixel_coverage']<=.30:
                signal['classification']='not_observed'
            else:
                signal['classification']='ambiguous';uncertain.append(signal)
            signals.append(signal)
    result.update(pair_signals=signals,observed_edges=observed)
    segments=_long_segments(neutral,x0,x1)
    residual=[line for line in segments if not any(all(abs(y-(_y(edge['geometry'],x)+edge['offset']))<=22
        for x,y in ((line[0],line[1]),(line[2],line[3]))) for edge in observed)]
    result.update(long_grey_segments=segments,residual_segments=residual,
        highlighted_segments=_long_segments(highlighted,x0,x1))
    if any(_crosses(a,b) for i,a in enumerate(observed) for b in observed[i+1:]):
        return dict(result,status='stopped',reason='edge_crossing_ambiguous')
    if uncertain:return dict(result,status='stopped',reason='edge_signal_ambiguous')
    if residual:return dict(result,status='stopped',reason='edge_residual_segment')
    if result['highlighted_segments']:
        return dict(result,status='stopped',reason='highlighted_edge_unqualified')
    if not observed:return dict(result,status='stopped',reason='edge_candidates_missing')
    return dict(result,status='edge_candidates_observed',reason='endpoints_columns_and_all_edges_unverified')


def visible_edges_pipeline(roi):
    return {'LabyVisibleEdges':dict(recognition='Custom',custom_recognition='LabyVisibleEdges',
        roi=list(_box(list(roi))),action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)}


class MaaVisibleEdgesRecognition(CustomRecognition):
    """Run registered pixel inventory on the same Maa image, then rail analysis."""
    def analyze(self,context,argv):
        try:
            native=context.run_recognition('LabyVisibleNodeInventory',argv.image)
            if native is None:
                return self.AnalyzeResult(None,dict(status='stopped',reason='pixel_inventory_unavailable',
                    complete=False,edges_complete=False,safe_to_navigate=False,handoff_qualified=False))
            inventory=inventory_readback(native.raw_detail,hit=native.hit)
            if native.hit is not True:
                return self.AnalyzeResult(None,dict(status='stopped',reason='pixel_inventory_unavailable',
                    inventory_reason=inventory.get('reason'),complete=False,edges_complete=False,
                    safe_to_navigate=False,handoff_qualified=False))
            if inventory['roi']!=list(argv.roi):raise VisibleEdgeFailure('edge_inventory_roi_mismatch')
            result=collect_visible_edge_candidates(argv.image,inventory)
            result['inventory_reco_id']=native.reco_id
            return self.AnalyzeResult(list(argv.roi) if result['status']=='edge_candidates_observed' else None,result)
        except Exception:
            return self.AnalyzeResult(None,dict(status='stopped',reason='edge_collection_unknown',complete=False,
                edges_complete=False,safe_to_navigate=False,handoff_qualified=False))


def visible_edges_readback(raw,*,hit):
    """Strict official wrapper readback; a hit never grants all-edge evidence."""
    def unknown():raise VisibleEdgeFailure('edge_native_readback_unknown')
    if (type(hit) is not bool or type(raw) is not dict or set(raw)!={'all','filtered','best'}
            or type(raw['all']) is not list or len(raw['all'])!=1):unknown()
    row=raw['all'][0]
    if type(row) is not dict or set(row)!={'box','detail'} or type(row['detail']) is not dict:unknown()
    if raw['filtered']!=([row] if hit else []) or raw['best']!=(row if hit else None):unknown()
    report=row['detail']
    if (report.get('complete') is not False or report.get('edges_complete') is not False
            or report.get('safe_to_navigate') is not False or report.get('handoff_qualified') is not False):unknown()
    if hit:
        if (report.get('status')!='edge_candidates_observed' or report.get('source')!='maa_pixels'
                or report.get('stage')!='visible_edge_candidates' or row['box']!=report.get('roi')
                or report.get('all_visible_edges_verified') is not False):unknown()
    elif report.get('status')!='stopped' or row['box']!=[0,0,0,0]:unknown()
    return report
