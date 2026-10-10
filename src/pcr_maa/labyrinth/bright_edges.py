"""Bounded same-frame bright-bridge observation, no logical adjacency inference.

Only one left platform and two right platforms are examined. Fixed mask and
Hough/corridor constants are reused from the earlier grey-edge diagnostic;
no protocol, START type, route, target or expected edges are inputs. A line
extrapolated into a platform is insufficient: fresh bright pixels must reach
inside both unexpanded fixed-template rectangles. Those rectangles are not
complete platform outlines; absent support therefore remains unknown, never
proof that a bridge does not exist. The original sampled frame's
source endpoints are occluded, so it currently yields candidates, not edges.
"""
from hashlib import sha256
import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition
from .visible_edges import _masks
from .partial_map import partial_map_readback

class BrightEdgeFailure(ValueError):pass


def _box(value):
    if (type(value) is not list or len(value)!=4 or any(type(n) is not int for n in value)
            or value[0]<0 or value[1]<80 or value[2]<=0 or value[3]<=0
            or value[0]+value[2]>1280 or value[1]+value[3]>610):
        raise BrightEdgeFailure('bright_platform_invalid')
    return list(value)


def _overlap(a,b):
    return max(a[0],b[0])<min(a[0]+a[2],b[0]+b[2]) and max(a[1],b[1])<min(a[1]+a[3],b[1]+b[3])


def _inventory(image,inventory):
    if type(image) is not np.ndarray or image.shape!=(720,1280,3) or image.dtype!=np.uint8:
        raise BrightEdgeFailure('bright_frame_invalid')
    if (type(inventory) is not dict or inventory.get('source')!='maa_pixels'
            or inventory.get('scope')!='observed_subgraph'
            or inventory.get('status') not in ('inventory_observed','observed_subgraph')
            or inventory.get('complete') is not False or inventory.get('safe_to_navigate') is not False
            or inventory.get('handoff_qualified') is not False):
        raise BrightEdgeFailure('bright_inventory_unqualified')
    if inventory.get('frame_pixel_sha256')!=sha256(image.tobytes()).hexdigest():
        raise BrightEdgeFailure('bright_frame_mismatch')
    columns=inventory.get('columns')
    if (type(columns) is not list or len(columns)!=2 or any(type(c) is not dict for c in columns)
            or any(type(c.get('nodes')) is not list for c in columns)
            or [len(c['nodes']) for c in columns]!=[1,2]):
        raise BrightEdgeFailure('bright_layout_unqualified')
    boxes=[]
    for column in columns:
        group=[]
        for node in column['nodes']:
            if type(node) is not dict or any(k in node for k in ('block_id','protocol_block_id','enter_id')):
                raise BrightEdgeFailure('bright_platform_invalid')
            group.append(_box(node.get('platform_box')))
        boxes.append(group)
    if any(_overlap(a,b) for i,a in enumerate(boxes[1]) for b in boxes[1][i+1:]):
        raise BrightEdgeFailure('bright_platform_multiple')
    if (boxes[1][0][1]>=boxes[1][1][1]
            or abs((boxes[1][0][0]+boxes[1][0][2]/2)-(boxes[1][1][0]+boxes[1][1][2]/2))>90
            or any(_overlap(boxes[0][0],b) for b in boxes[1])):
        raise BrightEdgeFailure('bright_platform_order_invalid')
    return boxes


def _y(line,x):
    a,c,d,f=line
    return c+(x-a)*(f-c)/(d-a)


def _profile(mask,line,xs):
    ys=np.rint([_y(line,int(x)) for x in xs]).astype(int)
    if ys.min()-6<80 or ys.max()+6>=610:
        raise BrightEdgeFailure('bright_line_outside')
    counts=mask[ys[None,:]+np.arange(-6,7)[:,None],xs[None,:]].sum(axis=0)
    good=counts>=3;gap=longest=0;gaps=[];start=None
    for x,value in zip(xs,good):
        if not value:
            if start is None:start=int(x)
            gap+=1;longest=max(longest,gap)
        else:
            if start is not None:gaps.append([start,int(x)-1]);start=None
            gap=0
    if start is not None:gaps.append([start,int(xs[-1])])
    return dict(pixel_coverage=float(good.mean()),longest_gap_pixels=longest,
                gap_x_ranges=gaps,bright_pixels_per_x=[int(c) for c in counts])


def _endpoint(mask,line,boxes,side):
    facts=[]
    for row,box in enumerate(boxes):
        x,y,w,h=box;x=x+w-1 if side=='source' else x+min(1,w-1)
        py=_y(line,x);center=int(round(py))
        # Entire evidence strip stays inside the original box. No expansion,
        # projected body point or inferred endpoint substitutes for pixels.
        low=max(y,center-6);high=min(y+h,center+7)
        count=int(mask[low:high,x].sum()) if y<=py<y+h and low<high else 0
        facts.append(dict(row=row,box=box,point=[x,py],bright_pixels=count,
                          inside=y<=py<y+h,supported=count>=3))
    return facts


def collect_bright_edges(image,inventory):
    boxes=_inventory(image,inventory);left,right=boxes
    x0=left[0][0]+left[0][2];x1=min(b[0] for b in right)
    if not 100<=x1-x0<=512:raise BrightEdgeFailure('bright_band_unqualified')
    y0=min(b[1] for group in boxes for b in group);y1=max(b[1]+b[3] for group in boxes for b in group)
    _,mask=_masks(image)
    found=cv2.HoughLinesP(mask[y0:y1,x0:x1].astype(np.uint8)*255,1,np.pi/180,
        threshold=70,minLineLength=100,maxLineGap=8)
    lines=[] if found is None else [[int(a)+x0,int(c)+y0,int(d)+x0,int(f)+y0]
        for a,c,d,f in found.reshape(-1,4)]
    lines=sorted(line if line[0]<line[2] else [line[2],line[3],line[0],line[1]]
                 for line in lines if line[0]!=line[2])
    result=dict(source='maa_pixels',stage='bright_edge_observation',scope='one_to_two_platforms',
        frame_pixel_sha256=inventory['frame_pixel_sha256'],platform_boxes=boxes,roi=[x0,y0,x1-x0,y1-y0],
        mask='existing highlighted B>=220 G>=190 R>=170 max-min<=70',
        parameters=dict(hough_threshold=70,min_line_length=100,max_line_gap=8,profile_half_width=6,
                        min_bright_pixels=3,min_coverage=.90,max_profile_gap=6),
        raw_lines=lines,candidates=[],positive_edges=[],complete=False,edges_complete=False,
        safe_to_navigate=False,handoff_qualified=False,qualification_pending=True)
    xs=np.arange(left[0][0]+left[0][2]-1,max(b[0]+min(1,b[2]-1) for b in right)+1)
    accepted=[]
    for line in lines:
        profile=_profile(mask,line,xs);source=_endpoint(mask,line,left,'source');target=_endpoint(mask,line,right,'target')
        supported_source=[i for i,f in enumerate(source) if f['supported']]
        supported_target=[i for i,f in enumerate(target) if f['supported']]
        if len(supported_source)!=1 or len(supported_target)!=1:
            reason='bright_endpoint_multiple' if len(supported_source)>1 or len(supported_target)>1 else 'bright_endpoint_occluded'
        elif profile['pixel_coverage']<.90 or profile['longest_gap_pixels']>6:reason='bright_bridge_interrupted'
        else:reason='pixel_bridge_supported'
        facts=dict(line=line,**profile,source_endpoints=source,target_endpoints=target,reason=reason)
        result['candidates'].append(facts)
        if reason=='pixel_bridge_supported':accepted.append((supported_target[0],line))
    # Distinct line traces may be the two stripes of one bridge. Reject any
    # crossing, and do not silently select one of inconsistent endpoint traces.
    crossing=any((_y(a,x0)-_y(b,x0))*(_y(a,x1)-_y(b,x1))<0
        for i,a in enumerate(lines) for b in lines[i+1:]
        if abs(_y(a,x0)-_y(b,x0))>13 and abs(_y(a,x1)-_y(b,x1))>13)
    result['crossing_observed']=crossing
    if crossing:return dict(result,status='unqualified',reason='bright_crossing_ambiguous')
    for target in sorted({row for row,_ in accepted}):
        same=[line for row,line in accepted if row==target]
        if any(abs(_y(a,x0)-_y(b,x0))>18 or abs(_y(a,x1)-_y(b,x1))>18
               for i,a in enumerate(same) for b in same[i+1:]):
            return dict(result,status='unqualified',reason='bright_multiple_bridges')
    # Every detected long trace must independently meet the endpoint and
    # continuity contract; unknown traces cannot be declared absent or ignored.
    if result['candidates'] and all(c['reason']=='pixel_bridge_supported' for c in result['candidates']):
        result['positive_edges']=[dict(**{'from':[0,0],'to':[1,row]}) for row in sorted({row for row,_ in accepted})]
        return dict(result,status='positive_edges_observed',reason='independent_pixel_endpoints_and_continuity')
    return dict(result,status='unqualified',reason='bright_candidates_missing' if not lines else 'bright_endpoint_or_continuity_unqualified')


def bright_edges_pipeline(roi=(0,80,1280,530)):
    roi=_box(list(roi))
    return {'LabyBrightEdgesObserved':dict(recognition='Custom',custom_recognition='LabyBrightEdgesObserved',
        roi=roi,action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)}


class MaaBrightEdgesRecognition(CustomRecognition):
    """Compose the installed partial collector on exactly the supplied Maa frame."""
    def install(self,resource):
        if (not resource.override_pipeline(bright_edges_pipeline())
                or not resource.register_custom_recognition('LabyBrightEdgesObserved',self)):
            raise BrightEdgeFailure('bright_observer_install_failed')
        return self

    def analyze(self,context,argv):
        try:
            detail=context.run_recognition('LabyPartialMapObserved',argv.image)
            if detail is None:raise BrightEdgeFailure('bright_inventory_unavailable')
            report=partial_map_readback(detail.raw_detail,hit=detail.hit)
            if detail.hit is not True:raise BrightEdgeFailure('bright_page_or_inventory_unknown')
            result=collect_bright_edges(argv.image,report)
            result['partial_reco_id']=detail.reco_id
            return self.AnalyzeResult(list(argv.roi) if result['positive_edges'] else None,result)
        except Exception as error:
            return self.AnalyzeResult(None,dict(status='unqualified',reason=str(error) if isinstance(error,BrightEdgeFailure) else 'bright_collection_unknown',
                positive_edges=[],complete=False,edges_complete=False,safe_to_navigate=False,handoff_qualified=False))
