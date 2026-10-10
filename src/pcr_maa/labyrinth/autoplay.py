"""Single-run Maa state machine adapted from the pinned upstream NodeSession.

Protocol supplies the route. Same-frame pixels supply target/type/activation and
pages. A route-end or RESULT page never proves a clear. Inputs are journalled
once at native ACK; dedicated destination observations confirm page progress.
Source: wbero/limingjie-assistant (CC BY-NC-SA 4.0); see source manifest.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json
import math
import time
import cv2
import numpy as np
from maa.custom_recognition import CustomRecognition
from .. import native
from .batch import BatchStopped
from .routes import Route
from .roles import Context
from .scene import Scene

_TARGET = 'LabyAutoplayTarget'
_CLICK = 'LabyAutoplayPreviewOnce'
_NAME = 'LabyrinthAutoplayTarget'
_TYPES = {'normal_battle':2,'ex_battle':3,'link':4,'event':5,'relic':6,'shop':7,'boss':8}
_ARRIVALS = {2:{'normal_challenge','team'},3:{'ex_challenge','team'},
    4:{'link_reward'},5:{'event','event_animation','rest'},
    6:{'relic_reward'},7:{'shop'},8:{'boss_challenge','team'}}
_STOP = {'unknown':'scene_unknown','date_changed':'date_changed','disconnected':'disconnected'}


def _native_ok(receipt, *, action=False):
    if (type(receipt) is not dict or receipt.get('native_ok') is not True
            or receipt.get('tasker_inited') is not True or type(receipt.get('task_id')) is not int
            or receipt['task_id']<=0 or type(receipt.get('nodes')) is not list
            or not receipt['nodes'] or any(type(n) is not dict or n.get('completed') is not True for n in receipt['nodes'])):
        raise BatchStopped('autoplay_native_unknown')
    if action and not any(type(n.get('action')) is dict and n['action'].get('success') is True
                          and n['action'].get('name') not in ('DoNothing',None) for n in receipt['nodes']):
        raise BatchStopped('autoplay_input_unknown')
    return receipt


def _poll_pause(tasker,batch,node,reason):
    """One input-free Maa delay inside the caller's fixed inspection deadline."""
    batch.checkpoint()
    if not tasker.resource.override_pipeline({node:dict(recognition='DirectHit',action='DoNothing',
            next=[],pre_delay=0,post_delay=500,timeout=1000)}):
        raise BatchStopped(reason)
    with batch.timings.measure('page_wait'):_native_ok(native.run(tasker,node))
    batch.checkpoint()


def _owned_boss_summary_scene(scene,node):
    """Type one fresh UNKNOWN Boss result without broadening scene classification."""
    block_type=node.get('block_type') if type(node) is dict else getattr(node,'block_type',None)
    if type(scene) is not Scene or scene.kind!='unknown' or block_type!=8:return None
    payload=scene.payload
    if (type(payload) is not dict or payload.get('same_frame') is not True or payload.get('source')!='maa'
            or payload.get('clear_verified') is not False):return None
    anchors=payload.get('anchors');controls=payload.get('controls')
    if type(anchors) is not dict or type(controls) is not dict:return None
    control=controls.get('next')
    if type(control) is not dict:return None
    key=next((key for key in ('BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON','BATTLE_RESULT_NEXT_BUTTON') if key in anchors),None)
    if key is None:return None
    anchor=anchors[key]
    if type(anchor) is not dict:return None
    score=anchor.get('score');box=anchor.get('box')
    if (type(score) not in (int,float) or not math.isfinite(score) or score<.70
            or type(box) is not list or len(box)!=4 or any(type(v) is not int for v in box)
            or box[0]<0 or box[1]<0 or box[2]<=0 or box[3]<=0 or box[0]+box[2]>1280 or box[1]+box[3]>720
            or anchor.get('anchor')!=key or anchor.get('score')!=score or anchor.get('box')!=box):return None
    return Scene('boss_summary',scene.native,scene.payload)


def _detail(raw):
    if type(raw) is str:
        try: raw=json.loads(raw)
        except ValueError: return None
    if type(raw) is dict and type(raw.get('best')) is dict: raw=raw['best'].get('detail')
    if type(raw) is dict and 'detail' in raw: raw=raw['detail']
    if type(raw) is str:
        try: raw=json.loads(raw)
        except ValueError: return None
    return raw if type(raw) is dict else None


def route_viewport(route,current,columns):
    """Pinned upstream topology: geometry binds IDs; target semantics follow.

    Keep viewport evidence separate from a clickable target. A missing target
    inside a mapped column is a recognition gap, never a request to pan away.
    """
    if type(route) is not Route or current not in route.nodes:raise BatchStopped('route_cursor_unknown')
    index=route.nodes.index(current)
    if index+1>=len(route.nodes):return dict(target=None,columns={},direction=0)
    target=route.nodes[index+1]
    if target.area!=current.area or target.block_type==1:return dict(target=None,columns={},direction=0)
    if target.block_id not in current.next_ids:raise BatchStopped('route_edge_unknown')
    logical={}
    for node in route.full_map:
        if node.area==current.area and node.block_type!=1 and node.column>=current.column-1:
            logical.setdefault(node.column,[]).append(node)
    for rows in logical.values():rows.sort(key=lambda n:-n.row)
    visual=[]
    for column in columns or []:
        rows=[v for v in column if type(v) is dict and v.get('block_type') in range(2,9)
            and type(v.get('active')) is bool and type(v.get('box')) is list
            and len(v['box'])==4 and all(type(x) is int for x in v['box'])
            and v['box'][2]>0 and v['box'][3]>0]
        if rows:visual.append(sorted(rows,key=lambda v:v['box'][1]+v['box'][3]/2))
    if not visual or not logical:return dict(target=None,columns={},direction=0)
    center=lambda rows:sum(v['box'][0]+v['box'][2]/2 for v in rows)/len(rows)
    scores=[];keys=sorted(logical)
    def align(i,start,assigned,score):
        if i==len(visual):
            pairs=[(v,c) for v,c in enumerate(assigned) if c is not None]
            if not pairs:return
            for (v,c),(w,d) in zip(pairs,pairs[1:]):
                width=sum(n['box'][2] for n in visual[v]+visual[w])/(len(visual[v])+len(visual[w]))
                spacing=(center(visual[w])-center(visual[v]))/(d-c)/width
                score-=max(0,d-c-1)*1.5
                score+=-60 if spacing<1.05 else 4 if spacing<=2.40 else -min(20,(spacing-2.40)*24)
            score-=max(0,pairs[0][1]-current.column-1)*2
            scores.append((score,assigned));return
        align(i+1,start,assigned+[None],score-2.5)
        for k in range(start,len(keys)):
            col=keys[k];expected=logical[col];rows=visual[i];difference=abs(len(rows)-len(expected))
            value=8 if not difference else 5 if col==current.column and len(rows)<len(expected) else 2 if difference==1 else -4*difference
            active=sum(v['active'] for v in rows);reachable=sum(n.block_id in current.next_ids for n in expected)
            if reachable:value+=4 if active==reachable else -1.5*abs(active-reachable)
            value+=max(v.get('score',0) for v in rows)*2
            if col==target.column:value+=5+(2 if active else 0)
            if col==current.column:value+=2.5
            if col==current.column+1:value+=1+(3 if active else 0)
            align(i+1,k+1,assigned+[col],score+value)
    align(0,0,[],0)
    best=max(s for s,_ in scores);assignments=[a for s,a in scores if abs(s-best)<1e-9]
    second=max((score for score,assigned in scores if assigned!=assignments[0]),default=best-5)
    confidence=min(.92,max(.55,.68+(best-second)/20))
    mapped={};answers=[];conflict=False;topology=[]
    for visual_index,rows in enumerate(visual):
        assigned={a[visual_index] for a in assignments}
        if len(assigned)!=1 or None in assigned:continue
        col=assigned.pop();mapped[col]=center(rows)
        expected=logical[col];pairs=[];kind='full_column'
        heights=[v['box'][3] for v in rows];height=sum(heights)/len(heights)
        xs=[v['box'][0]+v['box'][2]/2 for v in rows];width=sum(v['box'][2] for v in rows)/len(rows)
        ys=[v['box'][1]+v['box'][3]/2 for v in rows];gaps=[y-x for x,y in zip(ys,ys[1:])]
        aligned=max(xs)-min(xs)<=width*.52
        if len(rows)==len(expected) and aligned and (len(rows)<3 or min(gaps)>=height*.62 and max(gaps)-min(gaps)<=height*.35):
            pairs=list(zip(expected,rows))
        elif len(expected)==3 and len(rows)==2 and aligned and ys[1]-ys[0]>=height*1.20:
            pairs=[(expected[0],rows[0]),(expected[-1],rows[-1])];kind='partial_column'
        else:
            active=[v for v in rows if v['active']];reachable=[n for n in expected if n.block_id in current.next_ids]
            if reachable and len(active)==len(reachable):pairs=list(zip(reachable,active));kind='reachable_subset'
            else:
                # Preserve the existing contiguous partial-row case without
                # forcing unrelated complete columns to match template types.
                candidates=[expected[i:i+len(rows)] for i in range(len(expected)-len(rows)+1)
                    if [n.block_type for n in expected[i:i+len(rows)]]==[v['block_type'] for v in rows]]
                if len(candidates)==1:pairs=list(zip(candidates[0],rows));kind='partial_column'
        binding_confidence=min(.99,confidence+(.15 if kind=='full_column' else .08 if kind=='partial_column' else .02))
        topology.extend((node,observed,kind,binding_confidence,visual_index) for node,observed in pairs
            if node.block_id in current.next_ids and node.column>current.column)
        for node,observed in pairs:
            if node.block_id!=target.block_id or not observed['active']:continue
            detected=observed['block_type'];box=observed['box']
            # Pinned action planner: LINK/RELIC is the known semantic swap,
            # resolved by ordered topology, not by the requested type alone.
            swap=({detected,target.block_type}=={4,6} and binding_confidence>=.75
                and (kind=='full_column' or any(n.column==target.column and n.block_id!=target.block_id
                    and v['active'] and conf>=.70 for n,v,_,conf,_ in topology)))
            compatible=detected==target.block_type or swap
            if not compatible:conflict=True;continue
            if box[0]<0 or box[1]<0 or box[0]+box[2]>1280 or box[1]+box[3]>720:continue
            answers.append(dict(target_block_id=target.block_id,current_block_id=current.block_id,
                block_type=target.block_type,detected_block_type=detected,box=list(box),
                binding_kind=kind,supporting_columns=len(mapped),visual_column=visual_index))
    bound=answers[0] if len({tuple(v['box']) for v in answers})==1 else None
    # Pinned NodePositionMapper.preferredTargetSemanticRepair: a unique active
    # reachable type repairs weak topology; strong ordered/owned crops keep it.
    if bound is None:
        reachable=[n for n in route.full_map if n.block_id in current.next_ids and n.block_type==target.block_type]
        matching=[(i,v) for i,rows in enumerate(visual) for v in rows
            if v['active'] and v['block_type']==target.block_type]
        if len(reachable)==1 and len(matching)==1:
            visual_index,observed=matching[0];box=observed['box']
            owned=any(node.block_id!=target.block_id and conf>=.70
                and abs((v['box'][0]+v['box'][2]/2)-(box[0]+box[2]/2))<=max(16,min(v['box'][2],box[2])*.30)
                and abs((v['box'][1]+v['box'][3]/2)-(box[1]+box[3]/2))<=max(16,min(v['box'][3],box[3])*.30)
                for node,v,_,conf,_ in topology)
            target_topology=next((t for t in topology if t[0].block_id==target.block_id),None)
            strong=target_topology is not None and target_topology[3]>=.90 and (
                target_topology[2]=='full_column' or any(n.column==target.column and n.block_id!=target.block_id
                    and v['active'] and v['block_type']==n.block_type and conf>=.90 for n,v,_,conf,_ in topology))
            single_override=(len(logical[target.column])==1 and target_topology is not None
                and not target_topology[1]['active'] and target_topology[1]['block_type']!=target.block_type
                and observed.get('score',0)>=.85 and observed.get('score',0)-target_topology[1].get('score',0)>=.12)
            if not owned and (not strong or single_override) and box[0]>=0 and box[1]>=0 and box[0]+box[2]<=1280 and box[1]+box[3]<=720:
                bound=dict(target_block_id=target.block_id,current_block_id=current.block_id,
                    block_type=target.block_type,detected_block_type=observed['block_type'],box=list(box),
                    binding_kind='route_semantic_repair',supporting_columns=len(mapped),visual_column=visual_index)
    # Pinned NodePositionMapper legacy fallback (355-621): unresolved blocks
    # use the ordered complete reachable subset, never compressed full-map rows.
    if bound is None and not any(n.block_id==target.block_id for n,_,_,_,_ in topology):
        candidates={col:[n for n in nodes if n.block_id in current.next_ids]
            for col,nodes in logical.items() if col>current.column}
        active={i+1:[v for v in rows if v['active']] for i,rows in enumerate(visual)}
        active={i:rows for i,rows in active.items() if rows}
        def evidence(offset):
            complete=preferred=matching=0;support=0.0
            for i,rows in active.items():
                nodes=candidates.get(i+offset,[])
                if not nodes:continue
                types={n.block_type for n in nodes}
                matched=[v for v in rows if v['block_type'] in types]
                complete+=len(rows)==len(nodes)
                preferred+=sum(v['block_type']==target.block_type for v in rows)
                matching+=len(matched);support+=sum(v.get('score',0) for v in matched)
            return complete,preferred,matching,support
        offsets=[current.column+1-i for i in active]
        best=max(map(evidence,offsets),default=None)
        offsets=[offset for offset in offsets if evidence(offset)==best]
        offset=offsets[0] if len(offsets)==1 else None
        for i,rows in active.items() if offset is not None else ():
            col=i+offset;nodes=candidates.get(col,[])
            complete=len(rows)==len(nodes) and bool(nodes)
            for index,observed in enumerate(rows):
                matches=[n for n in logical.get(col,[]) if n.block_type==observed['block_type']]
                node=nodes[index] if complete else matches[0] if len(matches)==1 and matches[0] in nodes else None
                if node is None or node.block_id!=target.block_id or node.block_type!=observed['block_type']:continue
                box=observed['box']
                claimed=any(v['active'] and
                    abs((v['box'][0]+v['box'][2]/2)-(box[0]+box[2]/2))<=max(16,min(v['box'][2],box[2])*.30) and
                    abs((v['box'][1]+v['box'][3]/2)-(box[1]+box[3]/2))<=max(16,min(v['box'][3],box[3])*.30)
                    for _,v,_,_,_ in topology)
                if claimed or box[0]<0 or box[1]<0 or box[0]+box[2]>1280 or box[1]+box[3]>720:continue
                bound=dict(target_block_id=target.block_id,current_block_id=current.block_id,
                    block_type=target.block_type,detected_block_type=observed['block_type'],box=list(box),
                    binding_kind='route_reachable_order' if complete else 'route_unique_type',
                    supporting_columns=len(mapped),visual_column=i-1)
    # Upstream viewport pitch is 540 at 1080p. Predict visibility only from
    # mapped geometry, never synthesize a click rectangle from this estimate.
    camera=sorted(col*360-x for col,x in mapped.items())
    left=camera[len(camera)//2] if camera else None
    likely=bound is not None or target.column in mapped or left is not None and 1280*.08<=target.column*360-left<=1280*.92
    direction=0 if likely or not mapped else 1 if target.column>max(mapped) else -1 if target.column<min(mapped) else 0
    return dict(target=bound,columns=mapped,direction=direction,
        reason='node_type_conflict' if conflict else 'target_detection_gap' if likely else 'target_offscreen' if direction else 'map_alignment_unknown')


def bind_route_target(route,current,columns):
    return route_viewport(route,current,columns)['target']


class NodePixels:
    """Cached upstream templates; independent frame classifier with local glow."""
    def __init__(self, directory=None):
        root=directory or Path(__file__).resolve().parents[3]/'assets/resource/image/labyrinth/node'
        self.templates=[]
        for path in sorted(Path(root).glob('node_*.png')):
            kind=next((v for k,v in _TYPES.items() if path.name.startswith('node_'+k)),None)
            if kind is None: continue
            image=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_UNCHANGED)
            if image is None: raise BatchStopped('node_resource_invalid')
            # Public pack has already been rescaled from 1080p to Maa 720p.
            body=image[:round(image.shape[0]*.78)] if '_bottom' in path.name else image
            rgb=body[:,:,:3]
            if path.name=='node_boss_platform.png':
                h,w=rgb.shape[:2]
                self.boss_label=cv2.resize(rgb[h*197//350:h*197//350+h*46//350,
                    w*225//500:w*225//500+w*122//500],(81,31),interpolation=cv2.INTER_AREA)
            mask=body[:,:,3]>=128 if body.shape[2]==4 else np.ones(rgb.shape[:2],bool)
            # Upstream ignores artificial alpha-boundary edges, and compares
            # both luminance and |central dx|+|central dy| with masked NCC.
            interior=cv2.erode(mask.astype(np.uint8),np.array([[0,1,0],[1,1,1],[0,1,0]],np.uint8))
            light,gradient=self._features(rgb)
            self.templates.append((path.name,kind,rgb,mask,interior,light,gradient))
        if {t[1] for t in self.templates}!={2,3,4,5,6,7,8}: raise BatchStopped('node_resources_incomplete')

    def final_boss(self,image):
        """Pinned FinalBossPlatformLocator: static nameplate, not moving Boss art."""
        h,w=self.boss_label.shape[:2]
        lights,gradients=self._features(image)
        refs=self._features(self.boss_label)
        # Upstream bounded platform band and independent minimum channel score.
        left,right=round(1280*.18),round(1280*.96)
        top,bottom=round(720*.46),round(720*.69)
        mask=np.zeros((h,w),np.uint8);mask[1:-1,1:-1]=1
        channels=[cv2.matchTemplate(frame[top:bottom,left:right],ref,
            cv2.TM_CCOEFF_NORMED,mask=mask) for frame,ref in zip((lights,gradients),refs)]
        scores=np.maximum(*channels)
        scores[(np.minimum(*channels)<.35)|~np.isfinite(scores)]=-1
        matches=[]
        for _ in range(8):
            _,score,_,(px,py)=cv2.minMaxLoc(scores)
            if score<.72:break
            x,y=px+left,py+top
            actual=image[y:y+h,x:x+w]
            color=1-float(np.mean(np.abs(actual[1:-1:2,1:-1:2].astype(np.float32)-
                self.boss_label[1:-1:2,1:-1:2].astype(np.float32))))/255
            cx=x+w/2
            def band(x1,x2,y1,y2):
                a,b=math.floor(cx+w*x1+.5),math.floor(cx+w*x2+.5)
                c,d=math.floor(y+h*y1+.5),math.floor(y+h*y2+.5)
                if a<0 or b>=1280 or c<0 or d>=720:return 0.0
                blue,green,red=cv2.split(image[c:d+1:3,a:b+1:3].astype(np.int16))
                return float(np.mean((red>=130)&(blue>=140)&(red-green>=30)&(blue-green>=35)))
            purple=min(band(-1.35,-.95,-.7,2.6),band(.95,1.35,-.7,2.6),band(-.8,.8,2,3))
            if color>=.80 or score>=.80 and purple>=.20:
                matches.append(dict(box=[x,y+math.floor(h*.18+.5),w,h],block_type=8,
                    active=True,score=float(score),color_score=color,purple_glow=purple,
                    template='node_boss_platform.png',activation_basis='upstream_boss_nameplate',
                    score_method='pinned FinalBossPlatformLocator masked gradient/luminance NCC'))
            dx,dy=math.ceil(w*.65),h
            scores[max(0,py-dy+1):py+dy,max(0,px-dx+1):px+dx]=-1
        # Upstream click rect needs one unambiguous fresh nameplate.
        return [matches] if len(matches)==1 else []

    @staticmethod
    def _features(image):
        b,g,r=cv2.split(image.astype(np.int32))
        light=((r*299+g*587+b*114+500)//1000).astype(np.float32)
        gradient=np.zeros(light.shape,np.float32)
        gradient[1:-1,1:-1]=np.abs(light[1:-1,2:]-light[1:-1,:-2])+np.abs(light[2:,1:-1]-light[:-2,1:-1])
        return light,gradient

    @staticmethod
    def _semantic(crop):
        # The upstream independent active-icon color decision tree. These
        # ratios describe actual pixels, never the expected route node type.
        h,w=crop.shape[:2]
        center=crop[round(h*.10):round(h*.52),round(w*.22):round(w*.78)]
        low=crop[round(h*.65):round(h*.94),round(w*.10):round(w*.90)]
        b,g,r=cv2.split(center.astype(np.int16))
        ratio=lambda value:float(np.mean(value))
        cyan=ratio((b>=150)&(g>=110)&(b-r>=35))
        dark=ratio(np.maximum(np.maximum(b,g),r)<95)
        red=ratio((r>=100)&(r-g>=30)&(r-b>=10))
        magenta=ratio((r>=140)&(b>=90)&(r-g>=45)&(b-g>=10))
        yellow=ratio((r>=140)&(g>=100)&(b<110)&(r-b>=60))
        green=ratio((g>=90)&(g-r>=20)&(g-b>=-30))
        lb,lg,lr=cv2.split(low.astype(np.int16))
        lower=ratio((lr>=140)&(lb>=90)&(lr-lg>=45)&(lb-lg>=10))
        if lower>=.20 and magenta>=.10 and red>=.08:return 3
        if magenta>=.30 and lower<.15:return 4
        if green>=.30 and green>magenta*2:return 7
        if lower>=.20:return 4
        if magenta>=.12 and cyan>=.10:return 4
        if yellow>=.10:return 5
        if cyan>=.30 and dark<.15:return 6
        if dark>=.15 and red>=.015:return 2
        return None

    def _type_support(self,image,lights,gradients,box,kind):
        # Pinned upstream classifyNode: independent active color may correct
        # geometry only with the inferred type's own structural/color support.
        x,y,width,height=box
        best=None
        for name,typ,template,mask,interior,light,gradient in self.templates:
            if typ!=kind or ('inactive' in name and typ not in (3,8)):continue
            h,w=light.shape
            xs=np.clip(np.arange(w)*(width-1)//(w-1),1,width-2)+x
            ys=np.clip(np.arange(h)*(height-1)//(h-1),1,height-2)+y
            score=max(float(cv2.matchTemplate(frame[ys[:,None],xs],ref,
                cv2.TM_CCOEFF_NORMED,mask=interior)[0,0])
                for frame,ref in ((lights,light),(gradients,gradient)))
            if not math.isfinite(score):continue
            crop=image[ys[:,None],xs];a,b=round(.30*h),round(.78*h)
            color=1-float(np.mean(np.abs(crop[a:b].astype(np.float32)-
                template[a:b].astype(np.float32))[mask[a:b]]))/255
            if best is None or score>best[1]:best=(name,score,color)
        return best if best is not None and best[1]>=.28 and best[2]>=.75 else None

    def collect(self,image):
        if type(image) is not np.ndarray or image.shape!=(720,1280,3): raise BatchStopped('map_frame_unknown')
        lights,gradients=self._features(image)
        proposals=[]
        for name,kind,template,mask,interior,light,gradient in self.templates:
            h,w=gradient.shape
            if h>550 or w>1100: continue
            # Pinned anchors permit full on-screen crops, including transparent
            # top margins, edge destination columns and lower EX platforms.
            grad_scores=cv2.matchTemplate(gradients[0:720,0:1280],gradient,
                cv2.TM_CCOEFF_NORMED,mask=interior)
            light_scores=cv2.matchTemplate(lights[0:720,0:1280],light,
                cv2.TM_CCOEFF_NORMED,mask=interior)
            scores=np.maximum(grad_scores,light_scores)
            scores[~np.isfinite(scores)]=-1
            for _ in range(24):
                _,score,_,location=cv2.minMaxLoc(scores)
                # Upstream EX has one shared template and its purple fallback.
                if score<(.20 if kind==3 else .60 if kind==8 or 'inactive' in name else .40): break
                x,y=location
                crop=image[y:y+h,x:x+w]
                a,b=round(.30*h),round(.78*h)
                opaque=mask[a:b]
                if np.count_nonzero(opaque)<40: break
                color=1-float(np.mean(np.abs(crop[a:b].astype(np.float32)-template[a:b].astype(np.float32))[opaque]))/255
                blue,green,red=cv2.split(crop.astype(np.int16))
                cyan=(blue>=180)&(green>=150)&(blue-red>=55)&(green-red>=25)
                purple=(red>=220)&(blue>=160)&(red-green>=70)&(blue-green>=30)
                cyan_ratio=float(np.mean(cyan));purple_ratio=float(np.mean(purple))
                coverage=float(np.mean(np.mean(cyan,axis=1)>=.08))
                cyan_ratio*=min(1,coverage/.30)
                platform=((blue>=190)&(green>=140)&(red<=170)&(blue-red>=45))[round(h*.65):round(h*.94)]
                platform_ratio=float(np.mean(platform))
                # Maa's existing B-server platform calibration is independent
                # of route/type and survives the pale initial NORMAL frame.
                # The gap .08..20 is unknown; only the measured positive band
                # may supplement upstream's whole-body cyan rails.
                platform_active=platform_ratio>=.20
                active=(kind==8 or ('inactive' not in name and
                        ((cyan_ratio>=.12 and coverage>=.30) or platform_active))
                        or kind==3 and purple_ratio>=.035)
                semantic=self._semantic(crop) if active and kind!=8 else None
                ex_fallback=False
                if kind==3 and semantic==3:
                    cb,cg,cr=(v[round(h*.10):round(h*.52),round(w*.22):round(w*.78)] for v in (blue,green,red))
                    lb,lg,lr=(v[round(h*.65):round(h*.94),round(w*.10):round(w*.90)] for v in (blue,green,red))
                    side=max(8,w*18//100);lower=h*68//100
                    rails=np.concatenate((purple[:lower,:side],purple[:lower,-side:]),axis=1)
                    activation=np.zeros_like(purple);activation[:,:side]=True;activation[:,-side:]=True;activation[lower:]=True
                    # Upstream purple score samples activation rails/platform,
                    # not the animated monster and background inside the body.
                    ex_fallback=(float(np.mean(purple[activation]))>=.08
                        and float(np.mean((cr>=140)&(cb>=90)&(cr-cg>=45)&(cb-cg>=10)))>=.15
                        and float(np.mean((lr>=140)&(lb>=90)&(lr-lg>=45)&(lb-lg>=10)))>=.25
                        and float(np.mean((cr>=100)&(cr-cg>=30)&(cr-cb>=10)))>=.10
                        and float(np.mean(rails))>=.035 and float(np.mean(purple[lower:]))>=.030
                        and float(np.mean(np.mean(rails,axis=1)>=.04))>=.22)
                threshold=.62 if ex_fallback else .75 if active else .84
                corrected=None
                if semantic is not None and semantic!=kind:
                    corrected=self._type_support(image,lights,gradients,[x,y,w,h],semantic)
                # Pinned classifyNode 528-530: an active LINK needs its own
                # independent LINK type; scenery with unknown type is not LINK.
                accepted=((score>=.40 or ex_fallback) and color>=threshold
                        and (semantic in (None,kind) or corrected is not None)
                        and (not active or kind!=4 or semantic is not None))
                if accepted:
                    detected_kind=semantic if corrected is not None else kind
                    detected_name,own_score,own_color=corrected or (name,float(score),color)
                    proposals.append(dict(box=[x,y,w,h],block_type=detected_kind,active=bool(active),
                        score=float(score),type_score=own_score,color_score=own_color,
                        cyan_glow=cyan_ratio,purple_glow=purple_ratio,
                        platform_cyan_ratio=platform_ratio,cyan_row_coverage=coverage,
                        activation_basis='lower_platform_cyan' if platform_active else 'upstream_rails',
                        template=detected_name,score_method='upstream opaque-interior gradient/luminance NCC'))
                px,py=location
                # Only an accepted crop owns its neighborhood. A rejected NCC
                # maximum must leave nearby independently valid crops searchable.
                if accepted:scores[max(0,py-70):py+71,max(0,px-70):px+71]=-1
                else:scores[py,px]=-1
        # Pinned classifier priority: a glowing active crop survives a higher
        # scoring inactive/shifted crop; distinct neighboring rows stay apart.
        nodes=[]
        for item in sorted(proposals,key=lambda n:(n['active'],'_active' in n['template'],
                math.floor(n['score']*10+.5),n['cyan_row_coverage'],n['score']),reverse=True):
            x,y,w,h=item['box'];cx,cy=x+w/2,y+h/2
            if any(abs(cx-(n['box'][0]+n['box'][2]/2))<w*1.10 and
                    abs(cy-(n['box'][1]+n['box'][3]/2))<max(h,n['box'][3])*.55 for n in nodes):continue
            nodes.append(item)
        columns=[]
        for n in sorted(nodes,key=lambda n:n['box'][0]+n['box'][2]/2):
            if not columns or abs(n['box'][0]+n['box'][2]/2-
                                  np.mean([v['box'][0]+v['box'][2]/2 for v in columns[-1]]))>90:
                columns.append([n])
            else: columns[-1].append(n)
        for column in columns: column.sort(key=lambda n:n['box'][1]+n['box'][3]/2)
        return columns


class RouteTargetRecognition(CustomRecognition):
    def __init__(self,pixels,stopped=lambda:False,timings=None):
        super().__init__();self.pixels=pixels;self.stopped=stopped;self.timings=timings
        self.route=self.current=None
    def analyze(self,context,argv):
        try:
            if self.stopped() or context.tasker.stopping or self.route is None: return None
            from .scene import NODE
            scene=context.run_recognition(NODE,argv.image)
            raw=_detail(scene.raw_detail) if scene is not None and scene.hit else None
            mode=json.loads(argv.custom_recognition_param or '{}').get('mode','read')
            if type(raw) is not dict:return None
            if raw.get('kind')!='map' or raw.get('payload',{}).get('area')!=self.current.area:
                return self.AnalyzeResult([0,0,1,1] if mode=='read' else None,dict(status='scene_changed',scene=raw,
                    frame_pixel_sha256=raw.get('payload',{}).get('frame_pixel_sha256')))
            index=self.route.nodes.index(self.current)
            target=self.route.nodes[index+1] if index+1<len(self.route.nodes) else None
            final_boss=getattr(self.pixels,'final_boss',None)
            columns=(final_boss(argv.image) if target is not None and target.block_type==8
                and callable(final_boss) else self.pixels.collect(argv.image))
            if self.timings is None:view=route_viewport(self.route,self.current,columns)
            else:
                with self.timings.measure('strategy'):
                    view=route_viewport(self.route,self.current,columns)
            bound=view['target']
            detail=dict(status='target_observed' if bound else 'target_not_visible',area=self.current.area,
                frame_pixel_sha256=sha256(argv.image.tobytes()).hexdigest(),columns=columns,target=bound,viewport=view)
            if self.stopped() or context.tasker.stopping: return None
            if mode=='preview' and bound is None: return self.AnalyzeResult(None,detail)
            box=bound['box'] if bound else [0,0,1,1]
            if mode=='preview':
                # Upstream NodeActionPlanner.getBaseClickPosition, first tap only.
                x,y,w,h=box
                box=[x+w//2,y+min(h-1,max(0,math.floor(h*.32+.5))),1,1]
            return self.AnalyzeResult(box,detail)
        except Exception as error:
            import logging
            logging.getLogger(__name__).error('Labyrinth node recognition failed: %s',type(error).__name__)
            return self.AnalyzeResult(None,dict(status='recognition_failed',error_type=type(error).__name__))


class MaaNodeNavigator:
    def __init__(self,tasker,*,stopped=lambda:False,pixels=None,timings=None):
        from .scene import runtime_resource
        self.tasker=tasker;resource=runtime_resource(tasker)
        previous=getattr(resource,'_labyrinth_autoplay_target',None)
        if isinstance(previous,RouteTargetRecognition):
            self.observer=previous;self.observer.stopped=stopped;self.observer.timings=timings
        else:
            self.observer=RouteTargetRecognition(pixels or NodePixels(),stopped,timings)
            if not resource.register_custom_recognition(_NAME,self.observer):
                raise BatchStopped('node_runtime_unavailable')
            resource._labyrinth_autoplay_target=self.observer
        nodes={_TARGET:dict(recognition='Custom',custom_recognition=_NAME,
                   custom_recognition_param=dict(mode='read'),action='DoNothing',next=[],timeout=3000),
               _CLICK:dict(recognition='Custom',custom_recognition=_NAME,
                   custom_recognition_param=dict(mode='preview'),action='Click',target=True,max_hit=1,
                   next=[],timeout=3000,pre_delay=0,post_delay=0)}
        if not resource.override_pipeline(nodes):raise BatchStopped('node_runtime_unavailable')

    def locate(self,route,current):
        self.observer.route,self.observer.current=route,current
        receipt=_native_ok(native.run(self.tasker,_TARGET))
        row=next((n for n in receipt['nodes'] if n['name']==_TARGET),None)
        detail=_detail(row.get('recognition')) if row else None
        if detail is None or detail.get('status') not in ('target_observed','target_not_visible','scene_changed'):
            raise BatchStopped('route_target_unknown')
        return detail,receipt

    def preview(self,route,current):
        self.observer.route,self.observer.current=route,current
        return _native_ok(native.run(self.tasker,_CLICK,reset_hits=(_CLICK,)),action=True)

    def scroll(self,scene,direction=1):
        # Fixed map-only Swipe, not a coordinate node click. Actual page is
        # re-recognized in this input frame; each viewport change is read back.
        from .scene import NAME
        nodes={'LabyAutoplayMapGuard':dict(recognition='Custom',custom_recognition=NAME,
                    custom_recognition_param=dict(kind='map'),action='DoNothing',next=[]),
            'LabyAutoplayMapScrollOnce':dict(recognition='And',all_of=['LabyAutoplayMapGuard'],
                # Pinned labyrinthMapSwipe: .8 of the 360px column pitch;
                # hold before release prevents inertia from skipping columns.
                action='Swipe',begin=[861 if direction==1 else 573,418,1,1],
                end=[573 if direction==1 else 861,418,1,1],duration=450,end_hold=220,
                max_hit=1,next=[],timeout=3000,pre_delay=0,post_delay=0)}
        if not self.tasker.resource.override_pipeline(nodes):raise native.NativeInputNotSent(dict(input_execution='not_sent',pre_input_validation_failed=True,reason='map_scroll_unavailable'))
        return _native_ok(native.run(self.tasker,'LabyAutoplayMapScrollOnce',
                          reset_hits=('LabyAutoplayMapScrollOnce',)),action=True)


@dataclass
class RunCursor:
    index:int=0
    pending:int|None=None
    final_boss_won:bool=False
    clear_observed:bool=False
    final_settlement_phase:str='none'
    node_finished:bool=False


class MaaRunActions:
    """Policy adapters. Their own transactions confirm recruitment/purchases."""
    def __init__(self,tasker,stamp,runtime,batch,opening):
        self.tasker,self.stamp,self.runtime,self.batch=tasker,stamp,runtime,batch
        self.opening=opening;self.characters=getattr(opening,'characters',None)
        from .strategies import MARKS
        self.owned=[];self.role_pending=[];self.relic_focus=None;self.acquisition_active=False
        self.encounter=None;self.effective_ids=();self.failed_teams={};self.last_failed_team={};self.force_multi_blocks=set();self.retry_numbers={}
        # Upstream startNew at the actual initial selector deletes old relic
        # history; markStacks begins these five ordinary marks at zero.
        # This adapter is allocated only after Batch's retained new-run handle.
        self.stacks={mark:0 for mark in MARKS}
        self.stack_sources={};self.stack_conflicts=set();self.map_read_nodes=set();self.item_results_seen=set()
        self.stack_basis='actual_opening_initial_selection_reset'
        self.team=None
        self.context_basis=dict(defense='actual_opening_confirmed_relic_ledger',
            targets='upstream_default_without_encounter_readback',enemy='unknown',
            encounter_strategy='not_observed',effective_ids='not_observed')
        from .links import MaaLinkReward,link_pipeline,result_pipeline as link_result_pipeline
        from .relics import MaaRelicReward,relic_pipeline
        if not tasker.resource.override_pipeline({**link_pipeline(),**link_result_pipeline(),**relic_pipeline()}):
            raise BatchStopped('reward_runtime_unavailable')
        self.link=MaaLinkReward(tasker,stamp,timings=batch.timings)
        self.relic=MaaRelicReward(tasker,stamp,timings=batch.timings)

    def calibrate_map(self,route,node):
        from .relics import read_map_relic_stacks,map_relic_consensus
        key=(route.enter_id,node.block_id)
        if key in self.map_read_nodes:return
        first=read_map_relic_stacks(self.tasker,self.batch,timings=self.batch.timings)
        second=read_map_relic_stacks(self.tasker,self.batch,timings=self.batch.timings)
        self.map_read_nodes.add(key)
        actual=map_relic_consensus(first,second)
        for mark,value in actual.items():
            # Pinned ledger: tiny MAP digits cannot roll back confirmed card,
            # purchase or acquisition facts. Conflict is retained, never a gate.
            if self.stack_sources.get(mark) not in (None,'map'):
                if self.stacks.get(mark)!=value:self.stack_conflicts.add(mark)
                continue
            self.stacks[mark]=value;self.stack_sources[mark]='map'
        if actual:
            self.batch.observation(self.stamp(source='maa',enter_id=route.enter_id,
                relic_map_stacks=actual,relic_stack_conflicts=sorted(self.stack_conflicts),
                native=dict(first=first['native'],second=second['native'])),enter_id=route.enter_id)

    def item_scene(self,scene,route,node,*,apply=False):
        from .relics import read_item_reward
        actual=read_item_reward(self.tasker,self.batch,timings=self.batch.timings)
        signature=actual.get('signature')
        if not signature:return scene
        signature=tuple(signature)
        self.batch.observation(self.stamp(source='maa',enter_id=route.enter_id,
            item_result_signature=signature,items=actual.get('items',()),native=actual['native']),enter_id=route.enter_id)
        key=(route.enter_id,node.block_id,signature)
        if apply and key not in self.item_results_seen:
            self.item_results_seen.add(key)
            for item in actual.get('relic_acquisitions',()):
                mark=item['mark'];self.stacks[mark]=min(30,self.stacks.get(mark,0)+item['bonus']*item['quantity'])
                self.stack_sources[mark]='acquisition';self.stack_conflicts.discard(mark)
        return Scene(scene.kind,scene.native,dict(scene.payload,item_result_signature=signature))

    def _context(self,node=None,*,combat=False):
        defense=self.stacks.get('守备')
        if type(defense) is not int or defense<0:raise BatchStopped('relic_stack_unknown')
        boss=bool(combat and node.block_type==8)
        return Context(defense=defense,boss=boss,
            prefer_single=bool(combat and (boss and self.batch.settings.strategy.prefer_pure_boss_damage_system
                or node.block_type==3 or getattr(self,'combat_kind',None)=='ex_challenge')),
            targets=(getattr(self,'encounter_targets',getattr(self.encounter,'targets',1))
                if combat or (node is not None and node.block_type==5) else 1),
            effective_ids=tuple(self.effective_ids) if combat else (),encounter=self.encounter if combat else None)

    def choose_reward(self,kind,route,node,reward_id):
        if kind=='link_reward':
            result=self.link.choose(self.batch,route.enter_id,reward_id)
            self.acquisition_active=True
            closed_signature=tuple(result['closed_reward_signature'])
            accepted_item=[None]
            def advanced(scene):
                if scene.kind!='item_reward':return True
                actual=self.item_scene(scene,route,node)
                signature=actual.payload.get('item_result_signature')
                if signature and tuple(signature)!=closed_signature:
                    accepted_item[0]=actual;return True
                return False
            scene=self.runtime.wait_for(self.batch,{'joined','item_reward','map','area_transition','link_reward'},
                source='item_reward',accept=advanced,timeout=20,reason='link_result_unknown',
                acquisition_fields=dict(source='maa',enter_id=route.enter_id,reward_id=reward_id))
            return dict(result,following_scene=accepted_item[0] if scene.kind=='item_reward' else scene)
        if kind=='relic_reward':
            from .relics import relic_offers
            # Actual printed currents take priority over history, as in the
            # upstream stack ledger. Selection uses its current native guard.
            offers=relic_offers(self.relic._run('LabyRelicObserved'))
            for offer in offers:
                self.stacks[offer.mark]=offer.current;self.stack_sources[offer.mark]='choice';self.stack_conflicts.discard(offer.mark)
            result=self.relic.choose(self.batch,route.enter_id,reward_id,node.area,self.stacks,offers=offers,locked_focus=self.relic_focus,priority=self.batch.settings.strategy.relic_mark_priority)
            self.relic_focus=result.get('focus',self.relic_focus)
            self.stacks[result['mark']]=min(30,self.stacks.get(result['mark'],0)+result['bonus'])
            self.stack_sources[result['mark']]='acquisition';self.stack_conflicts.discard(result['mark'])
            closed_signature=tuple(result['closed_reward_signature'])
            self.item_results_seen.add((route.enter_id,node.block_id,closed_signature))
            accepted_item=[None]
            def advanced(scene):
                if scene.kind!='item_reward':return True
                actual=self.item_scene(scene,route,node)
                signature=actual.payload.get('item_result_signature')
                if signature and tuple(signature)!=closed_signature:
                    accepted_item[0]=actual;return True
                return False
            following=self.runtime.wait_for(self.batch,{'item_reward','map','area_transition','relic_reward',
                'role_reward','free_role_reward','link_reward','joined','event','event_animation','chest',
                'score_result','final_clear','clear_congratulations','clear_character_summary',
                'clear_reward_animation','clear_chest_animation','home'},source='item_reward',accept=advanced,
                timeout=20,reason='relic_close_readback_unknown')
            return dict(result,following_scene=accepted_item[0] if following.kind=='item_reward' else following)
        from .rewards import MaaRoleReward
        reward=MaaRoleReward(self.tasker,self.stamp,timings=self.batch.timings,characters=self.characters)
        result=(reward.choose_free(self.batch,route.enter_id,reward_id,tuple(self.owned+self.role_pending),defer_joined=True)
            if kind=='free_role_reward' else reward.choose(
                self.batch,route.enter_id,reward_id,tuple(self.owned+self.role_pending),self._context(),defer_joined=True))
        self.role_pending.extend(result.get('roles_pending',()))
        if getattr(result.get('following_scene'),'kind',None) in ('joined','event_animation'):self.acquisition_active=True
        return result

    def accept_joined(self,route,node):
        characters=getattr(self,'characters',None)
        if characters is not None:
            actual,receipt=characters.read(self.batch,'joined',stable=False)
            observed=self.stamp(source='maa',enter_id=route.enter_id,page='joined',
                roles_joined=actual['roles_joined'],native=receipt)
            self.batch.observation(observed,enter_id=route.enter_id,page='joined')
        else:
            read=getattr(self.opening,'observe_joined',None)
            if not callable(read):raise BatchStopped('joined_adapter_unavailable')
            observed=read(self.batch,route.enter_id)
        _native_ok(observed['native'])
        actual=list(observed['roles_joined'])
        if self.role_pending:
            remaining=list(self.role_pending)
            for role in actual:
                if role not in remaining:raise BatchStopped('role_reward_readback_unknown')
                remaining.remove(role)
            self.role_pending=remaining
            if not remaining:self.acquisition_active=False
        self.last_joined_roles=tuple(actual)
        for role in actual:
            if role not in self.owned:self.owned.append(role)
        return observed

    def prepare_team(self,route,node,boss=False):
        from .team_execution import MaaTeam
        if self.role_pending:raise BatchStopped('role_reward_readback_unknown')
        from .encounters import scan_effective,boss_encounter
        if boss:
            self.encounter=boss_encounter(node);self.encounter_targets=getattr(self.encounter,'targets',1)
        if boss or getattr(self,'combat_kind',None)=='ex_challenge':
            self.effective_ids=scan_effective(self.characters,self.batch,self.stamp,route.enter_id,node.block_id,
                page='boss' if boss else 'battle',roster=tuple(self.owned))
            self.context_basis.update(effective_ids='maa_effective_filter_complete',encounter_strategy='pinned_encounter_readback')
        else:self.effective_ids=()
        self.team=MaaTeam(self.tasker,self.stamp,tuple(self.owned),timings=self.batch.timings,
            characters=self.characters,failed_teams=tuple(self.failed_teams.get(node.block_id,())),
            retry_number=self.retry_numbers.get(node.block_id,0),last_failed_team=self.last_failed_team.get(node.block_id))
        context=self._context(node,combat=True)
        if boss:return self.team.prepare_boss(self.batch,route.enter_id,node.block_id,context,requested=3,force_multi=node.block_id in self.force_multi_blocks)
        return self.team.prepare(self.batch,route.enter_id,node.block_id,context,
            hold_current=getattr(self,'combat_kind',None)=='normal_challenge')

    def observe_encounter(self,scene,node):
        if scene.kind=='ex_challenge':
            from .encounters import MaaEncounter
            observed=MaaEncounter(self.tasker,self.stamp,timings=self.batch.timings).observe_ex(
                self.batch,self.enter_id,node.block_id)
            self.encounter=observed['strategy'];self.encounter_targets=observed['target_count']
            self.context_basis.update(targets='maa_ex_details',encounter_strategy='pinned_ex_mechanism')
        elif scene.kind=='normal_challenge':self.encounter=None;self.encounter_targets=1

    def record_defeat(self,route,node):
        if self.team is None:raise BatchStopped('team_not_prepared')
        signature=self.team.failed_signature(route.enter_id,node.block_id)
        self.failed_teams.setdefault(node.block_id,set()).update(self.team.failed_signatures(route.enter_id,node.block_id))
        self.last_failed_team[node.block_id]=signature

    def start_battle(self,route,node):
        if self.team is None:raise BatchStopped('team_not_prepared')
        self.battle_result_scene=None
        def read(enter,block):
            started=time.monotonic();deadline=min(native.RUN_DEADLINE.get(),started+300)
            token=native.RUN_DEADLINE.set(deadline);team_grace=True
            try:
                while True:
                    self.batch.checkpoint();sampled_at=time.monotonic();remaining=deadline-sampled_at
                    if remaining<=0:raise BatchStopped('battle_timeout')
                    scene=self.runtime.observe(timeout=min(30,remaining));_native_ok(scene.native)
                    self.batch.checkpoint();now=time.monotonic()
                    if now>=deadline:raise BatchStopped('battle_timeout')
                    if scene.kind in ('battle_victory','battle_defeat','boss_summary'):
                        return dict(verified=True,enter_id=enter,block_id=block,
                            outcome='defeated' if scene.kind=='battle_defeat' else 'won',native=scene.native)
                    recovered=_owned_boss_summary_scene(scene,node)
                    if recovered is not None:
                        self.battle_result_scene=recovered
                        return dict(verified=True,enter_id=enter,block_id=block,outcome='won',native=scene.native)
                    if scene.kind in ('date_changed','disconnected'):raise BatchStopped(_STOP[scene.kind])
                    if scene.kind=='team':
                        if not team_grace or sampled_at-started>=3:raise BatchStopped('battle_result_unknown')
                    # Owned Start may briefly expose its challenge page while
                    # loading. Poll that existing source in the same 300s budget.
                    elif scene.kind in ('unknown','battle',getattr(self,'combat_kind',None)):
                        team_grace=False
                    else:raise BatchStopped('battle_result_unknown')
                    _poll_pause(self.tasker,self.batch,'LabyAutoplayBattlePollSpacing','battle_result_unknown')
            except RuntimeError as error:
                if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),
                        ('Inspection deadline exceeded before task input',),
                    ('Context deadline exceeded after synchronous return; outcome unknown',)):raise
                self.batch.checkpoint()
                raise BatchStopped('battle_timeout') from error
            finally:native.RUN_DEADLINE.reset(token)
        return self.team.start_once(self.batch,route.enter_id,node.block_id,read)

    def shop(self,route,node):
        from .shop_execution import MaaShop
        self.shop_adapter=MaaShop(self.tasker,self.stamp,timings=self.batch.timings)
        result=self.shop_adapter.run(self.batch,
            route.enter_id,node.block_id,owned=tuple(self.owned),stacks=self.stacks,context=self._context(),
            buy_relics=self.batch.settings.buy_relics,refresh_shop=self.batch.settings.refresh_shop,area=node.area,
            locked_focus=self.relic_focus,priority=self.batch.settings.strategy.relic_mark_priority,characters=self.characters)
        self.stacks.update(result.get('stacks',{}));self.relic_focus=result.get('focus',self.relic_focus)
        for mark in result.get('calibrated_marks',()):
            self.stack_sources[mark]='choice';self.stack_conflicts.discard(mark)
        for purchase in result.get('purchases',()):
            mark=purchase.get('mark')
            if mark in self.stacks and purchase.get('inventory_added') is True:self.stack_sources[mark]='acquisition'
        for role in result.get('roles',()):
            if role not in self.owned:self.owned.append(role)
        return result

    def event(self,route,node,scene):
        from .shop_execution import MaaEvent
        result=MaaEvent(self.tasker,self.stamp,timings=self.batch.timings).run(self.batch,
            route.enter_id,node.block_id,scene=scene,owned=tuple(self.owned),stacks=self.stacks,context=self._context(node),area=node.area,locked_focus=self.relic_focus)
        for role in result.get('roles',()):
            if role not in self.owned:self.owned.append(role)
        self.stacks.update(result.get('stacks',{}))
        self.acquisition_active=(result.get('character_acquisition_active') is True or result.get('page') in ('joined','event_animation'))
        return result


class AutoplayRunner:
    """One complete run; route decisions and cursor are local to a retained run."""
    def __init__(self,tasker,stamp,*,runtime=None,navigator=None,actions=None,max_steps=500):
        if not callable(stamp):raise ValueError('owner_stamp_required')
        self.tasker,self.stamp=tasker,stamp
        self.runtime,self.navigator,self.actions=runtime,navigator,actions
        if runtime is not None:runtime.tasker=tasker
        self.max_steps=max_steps;self.started=False;self.last_scene=None

    def _final_fields(self):
        cursor=getattr(self,'cursor',None)
        phase=getattr(cursor,'final_settlement_phase','none')
        if not getattr(cursor,'final_boss_won',False) or phase not in ('before_score','chest_sequence'):return None
        node=self.route.nodes[cursor.index]
        return dict(source='maa',enter_id=self.route.enter_id,block_id=node.block_id,
            block_type=node.block_type,settlement_phase=phase)

    def _observe(self,batch,route,timeout=10,*,drive_final=True):
        batch.checkpoint()
        from .scene import _RULES
        scene=self.runtime.wait_for(batch,set(_RULES),source='unknown',
            timeout=timeout,reason='scene_unknown',final_fields=self._final_fields() if drive_final else None)
        receipt=self.stamp(source='maa',enter_id=route.enter_id,scene=scene.kind,native=scene.native)
        batch.observation(receipt,enter_id=route.enter_id,source='maa')
        self.last_scene=scene
        return scene

    def _do(self,batch,route,scene,action,allowed,*,operation=None,extra=None):
        # After CLEAR, the map is settlement background, not another route node.
        if getattr(getattr(self,'cursor',None),'clear_observed',False):
            allowed=(set(allowed)|{'home'})-{'map'}
        expected=dict(source='maa',enter_id=route.enter_id)
        expected.update(extra or {})
        def send(oid):
            batch.checkpoint()
            clicked=_native_ok(self.runtime.perform(scene,action),action=True)
            return self.stamp(operation_id=oid,**expected,native=clicked,input_native=clicked,
                input_action_verified=True,client_page_verified=False)
        following=None
        try:clicked=batch.transaction(operation or action,expected,send)
        except BatchStopped as error:
            cause=error.__cause__
            if (action not in ('close_reward','settlement_next','advance_final_animation')
                    or error.args!=('native_input_not_sent',)
                    or not isinstance(cause,native.NativeInputNotSent)
                    or cause.receipt.get('native_evidence',{}).get('entry')!='LabySceneActionOnce'):
                raise
            # The completed task sent nothing. Read once through the existing
            # router; a page already advanced needs no stale closing click.
            following=self._observe(batch,route,drive_final=False)
            from .scene import classify_scene
            # Compare the actual source page, not its owned Boss result alias.
            source_kind=('unknown' if scene.kind=='boss_summary'
                and scene.payload.get('same_frame') is True and scene.payload.get('source')=='maa'
                and classify_scene(scene.payload.get('anchors',{}))[0]=='unknown' else scene.kind)
            if following.kind not in allowed or following.kind==source_kind:raise
        cursor=getattr(self,'cursor',None)
        if getattr(cursor,'final_boss_won',False):
            if action=='settlement_next' and scene.kind in ('score_result','final_clear'):
                cursor.final_settlement_phase='chest_sequence'
            elif action=='close_reward' and scene.kind=='item_reward' and getattr(cursor,'final_settlement_phase','none')=='chest_sequence':
                cursor.final_settlement_phase='waiting_home';allowed={'home'}
        if following is not None:
            self.last_scene=following
            return following
        if action=='confirm_move':
            following=self._node_transition_readback(batch,route.nodes[self.cursor.index],
                route.nodes[self.cursor.index+1],confirmed=True)
        else:following=self._action_transition_readback(batch,scene,allowed)
        batch.observation(self.stamp(operation_id=clicked['operation_id'],**expected,
            page=following.kind,native=following.native,input_native=clicked['input_native'],
            input_action_verified=True,client_page_verified=True),**expected)
        self.last_scene=following
        return following

    def _action_transition_readback(self,batch,source,allowed):
        from .scene import MaaSceneRuntime
        source_items=source.kind=='battle_victory' and 'BATTLE_RESULT_REWARD_BANNER' in source.payload.get('anchors',{})
        def arrived(following):
            if source.kind=='item_reward' and following.kind=='item_reward':
                reader=getattr(self.actions,'item_scene',None)
                if not callable(reader):return False
                actual=reader(following,self.route,self.route.nodes[self.cursor.index])
                signature=actual.payload.get('item_result_signature')
                previous=source.payload.get('item_result_signature')
                return bool(previous) and bool(signature) and tuple(signature)!=tuple(previous)
            same_items='BATTLE_RESULT_REWARD_BANNER' in following.payload.get('anchors',{})
            if source.kind=='joined' and following.kind=='joined':
                characters=getattr(self.actions,'characters',None)
                if characters is None:return False
                actual=characters.probe(batch,'joined')
                if actual is None:return False
                return tuple(actual['roles_joined'])!=getattr(self.actions,'last_joined_roles',())
            return (following.kind!=source.kind or following.kind=='map'
                or following.kind=='battle_victory' and not source_items and same_items)
        # Retry returns through MAP before reopening the same battle challenge.
        sources={source.kind,'map'} if source.kind=='battle_defeat' or getattr(getattr(self,'cursor',None),'clear_observed',False) else source.kind
        # Pinned fallback owns only reward successors of this run, never map/general dialogs.
        node=self.route.nodes[self.cursor.index] if hasattr(self,'route') else None
        acquisition=(getattr(self.actions,'acquisition_active',False) or bool(getattr(self.actions,'role_pending',())) or source.kind in ('event','event_animation','link_reward')
            or source.kind in ('item_reward','battle_victory','boss_summary') and node is not None and node.block_type in (2,3,4,8))
        final=self._final_fields() if source.kind not in ('joined','event','event_animation','link_reward') and not getattr(self.actions,'role_pending',()) else None
        waiting=getattr(getattr(self,'cursor',None),'final_settlement_phase','none')=='waiting_home'
        if waiting:sources={source.kind,'map'}
        fields=dict(source='maa',enter_id=self.actions.enter_id) if acquisition and final is None and not waiting else None
        return MaaSceneRuntime.wait_for(self.runtime,batch,allowed,source=sources,
            accept=arrived,timeout=20 if waiting else 30,reason='autoplay_readback_unknown',acquisition_fields=fields,final_fields=final)

    def _node_transition_readback(self,batch,current,target,*,confirmed=False):
        from .scene import MaaSceneRuntime
        def arrived(following):
            if following.kind=='map':
                if following.payload.get('area')!=current.area:raise BatchStopped('autoplay_readback_unknown' if confirmed else 'node_preview_unknown')
                return False
            if following.kind=='move_confirmation':
                kind=following.payload.get('node_type')
                if kind is None:return False
                if kind!=target.block_type:raise BatchStopped('node_type_conflict')
                return not confirmed
            return following.kind in _ARRIVALS[target.block_type]
        # Reuse the single pure wait. Each ACK retains its original 15s budget;
        # MAP/UNKNOWN/partial confirmation never sends a second tap.
        return MaaSceneRuntime.wait_for(self.runtime,batch,
            _ARRIVALS[target.block_type]|{'map','move_confirmation'},
            source=('map','move_confirmation'),accept=arrived,timeout=15,
            reason='autoplay_readback_unknown' if confirmed else 'node_preview_unknown')

    def _move(self,batch,route,scene):
        current=route.nodes[self.cursor.index]
        if self.cursor.index+1>=len(route.nodes):raise BatchStopped('terminal_clear_not_observed')
        target=route.nodes[self.cursor.index+1]
        if target.area!=current.area or target.block_type==1:raise BatchStopped('area_transition_unknown')
        batch.checkpoint()
        detail,receipt=self.navigator.locate(route,current)
        _native_ok(receipt)
        located=self.stamp(source='maa',enter_id=route.enter_id,native=receipt)
        batch.observation(located,enter_id=route.enter_id)
        key=(route.enter_id,current.block_id,target.block_id)
        fields=dict(source='maa',enter_id=route.enter_id,from_block_id=current.block_id,
                    target_block_id=target.block_id,block_type=target.block_type)
        def preview(oid):
            clicked=_native_ok(self.navigator.preview(route,current),action=True)
            return self.stamp(operation_id=oid,**fields,native=clicked,input_native=clicked,
                input_action_verified=True,client_page_verified=False)
        # The existing 15s target-observation budget also covers a proven
        # pre-input recognition miss. It never repeats an executed/unknown tap.
        deadline=min(native.RUN_DEADLINE.get(),time.monotonic()+15)
        pending_error=None
        while True:
            batch.checkpoint()
            if pending_error is not None and time.monotonic()>=deadline:
                raise BatchStopped('route_target_not_visible') from pending_error
            if detail.get('status')=='scene_changed':
                fresh=detail['scene'];return Scene(fresh['kind'],receipt,fresh['payload'])
            if detail.get('status')=='target_observed':
                if key in self.moves:raise BatchStopped('node_move_already_attempted')
                try:clicked=batch.transaction('node_preview',fields,preview)
                except BatchStopped as error:
                    cause=error.__cause__
                    if (error.args!=('native_input_not_sent',) or not isinstance(cause,native.NativeInputNotSent)
                            or cause.receipt.get('native_evidence',{}).get('entry')!=_CLICK):raise
                    pending_error=error
                else:
                    self.moves.add(key);self.cursor.pending=target.block_id
                    break
            elif detail.get('viewport',{}).get('direction',0):break
            if time.monotonic()>=deadline:
                raise BatchStopped(detail.get('viewport',{}).get('reason','route_target_not_visible')) from pending_error
            token=native.RUN_DEADLINE.set(deadline)
            try:
                _poll_pause(self.tasker,batch,'LabyAutoplayMapPollSpacing','route_target_not_visible')
                detail,receipt=self.navigator.locate(route,current);_native_ok(receipt)
                batch.observation(self.stamp(source='maa',enter_id=route.enter_id,native=receipt),enter_id=route.enter_id)
            except RuntimeError as error:
                if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),
                        ('Inspection deadline exceeded before task input',),
                        ('Context deadline exceeded after synchronous return; outcome unknown',)):raise
                batch.checkpoint();raise BatchStopped('route_target_not_visible') from error
            finally:native.RUN_DEADLINE.reset(token)
        if detail.get('status')!='target_observed':
            view=detail.get('viewport',{});direction=view.get('direction',0)
            if direction not in (-1,1):raise BatchStopped(view.get('reason','route_target_not_visible'))
            before=self.scrolls.get(('viewport',current.block_id))
            if before is not None and before['direction']==direction:
                common=set(before['columns'])&set(view.get('columns',{}))
                deltas=[abs(before['columns'][k]-view['columns'][k]) for k in common]
                # Camera motion is measured in column pitch, not exact hashes
                # or a demand that a node move a few prescribed pixels.
                if deltas and sorted(deltas)[len(deltas)//2]<360*.16:
                    raise BatchStopped('map_scroll_boundary')
            self.scrolls['viewport',current.block_id]=dict(direction=direction,columns=view.get('columns',{}))
            count=self.scrolls.get(current.block_id,0)
            if count>=4:raise BatchStopped('route_target_not_visible')
            self.scrolls[current.block_id]=count+1
            fields=dict(source='maa',enter_id=route.enter_id,block_id=current.block_id,viewport=count+1)
            def scroll(oid):
                clicked=_native_ok(self.navigator.scroll(scene,direction),action=True)
                return self.stamp(operation_id=oid,**fields,native=clicked,input_native=clicked,
                    input_action_verified=True,client_page_verified=False)
            clicked=batch.transaction('map_scroll',fields,scroll)
            following=self._action_transition_readback(batch,scene,{'map'})
            if following.payload.get('area')!=current.area:raise BatchStopped('map_scroll_readback_unknown')
            batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,native=following.native,
                input_native=clicked['input_native'],input_action_verified=True,client_page_verified=True),**fields)
            self.last_scene=following
            return following
        following=self._node_transition_readback(batch,current,target)
        batch.observation(self.stamp(operation_id=clicked['operation_id'],**fields,page=following.kind,
            native=following.native,input_native=clicked['input_native'],
            input_action_verified=True,client_page_verified=True),**fields)
        self.last_scene=following
        if following.kind=='move_confirmation':
            following=self._do(batch,route,following,'confirm_move',_ARRIVALS[target.block_type],
                operation='node_move_confirm',extra=fields)
        self.cursor.index+=1;self.cursor.pending=None;self.cursor.node_finished=False
        return following

    def _result(self,handle,route,outcome,scene):
        return self.stamp(source='maa',enter_id=route.enter_id,guild_id=handle.guild_id,
            difficulty=handle.difficulty,outcome=outcome,clear_verified=outcome=='cleared',
            failure_verified=outcome=='defeated' and getattr(self,'owned_defeat',False),
            current_block_id=route.nodes[self.cursor.index].block_id,
            confirmed_nodes=list(self.confirmed_nodes),native=scene.native,
            final_boss_won=self.cursor.final_boss_won,terminal_clear_observed=self.cursor.clear_observed,
            confirmed_roles=list(self.actions.owned),
            strategy_context_basis=getattr(self.actions,'context_basis',None))

    def _battle_following_scene(self,batch,route,node):
        recovered=getattr(self.actions,'battle_result_scene',None)
        if recovered is None:return self._observe(batch,route,timeout=30)
        self.actions.battle_result_scene=None;batch.checkpoint()
        if type(recovered) is not Scene or recovered.kind!='boss_summary' or node.block_type!=8:
            raise BatchStopped('battle_result_unknown')
        self.last_scene=recovered
        return recovered

    def play(self,route,batch,*,opening=None,roles=()):
        if self.started:raise BatchStopped('autoplay_already_attempted')
        handle=batch.game_session(route)
        if (self.tasker is not getattr(getattr(batch.port,'game',None),'tasker',None)
                or self.stamp!=getattr(getattr(batch.port,'game',None),'stamp',None)
                or handle.current_block_id!=route.nodes[0].block_id or handle.area!=1):
            raise BatchStopped('game_session_unknown')
        self.started=True;self.route=route;self.cursor=RunCursor();self.moves=set()
        self.scrolls={};self.confirmed_nodes=[handle.current_block_id];self.rewards={};self.battle_retries={}
        if self.runtime is None:
            from .scene import MaaSceneRuntime
            self.runtime=MaaSceneRuntime(self.tasker,self.stamp,timings=batch.timings,stopped=batch.stopped)
        if self.navigator is None:self.navigator=MaaNodeNavigator(self.tasker,stopped=batch.stopped,timings=batch.timings)
        if self.actions is None:self.actions=MaaRunActions(self.tasker,self.stamp,self.runtime,batch,opening)
        self.actions.owned=list(dict.fromkeys(roles));self.actions.enter_id=route.enter_id
        try:
            scene=self._observe(batch,route)
            for _ in range(self.max_steps):
                batch.checkpoint()
                node=route.nodes[self.cursor.index]
                batch.progress(dict(stage=scene.kind,area=node.area,block_id=node.block_id,
                    route_index=self.cursor.index,route_length=len(route.nodes),completed=dict(batch.completed)))
                if scene.kind=='battle_defeat':
                    # Only the just-finished owned battle may start a distinct retry.
                    retries=self.battle_retries.get(node.block_id,0)
                    strategy=batch.settings.strategy
                    boss=node.block_type==8
                    switched=node.block_id in getattr(self.actions,'force_multi_blocks',set())
                    fallback=(boss and strategy.boss_mode=='single' and not switched
                        and strategy.single_boss_fallback_to_multi_after_three_failures)
                    switch=fallback and retries>=strategy.single_boss_retry_count_before_multi
                    limit=max(1 if boss else 2,2 if strategy.reroll_after_three_battle_failures else 0,
                        strategy.single_boss_retry_count_before_multi if fallback else 0)
                    if (not getattr(self,'owned_defeat',False) or retries>=limit and not switch
                            or 'RETRY' not in scene.payload.get('controls',{})):
                        return self._result(handle,route,'defeated',scene)
                    next_retry=0 if switch else retries+1
                    scene=self._do(batch,route,scene,'retry_battle',{'team','normal_challenge','ex_challenge','boss_challenge'},
                        extra=dict(block_id=node.block_id,retry=next_retry,boss_fallback_to_multi=switch))
                    if switch:
                        self.actions.force_multi_blocks.add(node.block_id)
                        self.actions.failed_teams.pop(node.block_id,None);self.actions.last_failed_team.pop(node.block_id,None)
                    if hasattr(self.actions,'retry_numbers'):self.actions.retry_numbers[node.block_id]=next_retry
                    self.battle_retries[node.block_id]=next_retry;self.owned_defeat=False
                    continue
                if scene.kind=='map':
                    calibrate=getattr(self.actions,'calibrate_map',None)
                    if callable(calibrate):calibrate(route,node)
                    self.actions.acquisition_active=False
                    if getattr(self.actions,'role_pending',()):raise BatchStopped('role_reward_readback_unknown')
                    area=scene.payload.get('area')
                    if type(area) is not int:
                        scene=self._observe(batch,route,timeout=15);continue
                    if area!=node.area:
                        nxt=route.nodes[self.cursor.index+1] if self.cursor.index+1<len(route.nodes) else None
                        if (nxt is None or nxt.block_type!=1 or nxt.area!=area
                                or area!=node.area+1 or not self.cursor.node_finished):
                            raise BatchStopped('area_transition_unknown')
                        self.cursor.index+=1;self.cursor.node_finished=False
                        self.confirmed_nodes.append(nxt.block_id);node=nxt
                    if node.block_type!=1 and not self.cursor.node_finished:
                        raise BatchStopped('node_completion_unknown')
                    nxt=route.nodes[self.cursor.index+1] if self.cursor.index+1<len(route.nodes) else None
                    if nxt is not None and nxt.area!=node.area:
                        if nxt.area!=node.area+1 or nxt.block_type!=1 or not self.cursor.node_finished:
                            raise BatchStopped('area_transition_unknown')
                        # Upstream advances at the last route node automatically.
                        # The old MAP/black transition may linger after reward close;
                        # wait for actual next-area evidence without tapping a node.
                        scene=self.runtime.wait_for(batch,{'map','area_transition'},source='map',
                            accept=lambda s:s.kind!='map' or s.payload.get('area')!=node.area,
                            timeout=30,reason='area_transition_unknown')
                        continue
                    previous=self.cursor.index
                    scene=self._move(batch,route,scene)
                    if self.cursor.index!=previous:
                        self.confirmed_nodes.append(route.nodes[self.cursor.index].block_id)
                    continue
                if scene.kind in ('normal_challenge','ex_challenge','boss_challenge'):
                    event_combat=node.block_type==5 and scene.kind in ('normal_challenge','ex_challenge')
                    if not event_combat and scene.kind not in _ARRIVALS.get(node.block_type,set()):
                        raise BatchStopped('node_type_conflict')
                    # Arena events have an independently typed challenge page;
                    # upstream promotes its context from EVENT to NORMAL/EX.
                    self.actions.combat_kind=scene.kind;self.cursor.node_finished=False
                    observe=getattr(self.actions,'observe_encounter',None)
                    if callable(observe):observe(scene,node)
                    scene=self._do(batch,route,scene,'challenge',{'team'},extra=dict(block_id=node.block_id))
                    continue
                if scene.kind=='team':
                    try:self.actions.prepare_team(route,node,boss=node.block_type==8)
                    except BatchStopped as error:
                        last=getattr(self,'last_confirmed_defeat',None)
                        if error.args!=('battle_retry_team_exhausted',) or last is None or last[0]!=node.block_id:raise
                        self.owned_defeat=True
                        return self._result(handle,route,'defeated',last[1])
                    result=self.actions.start_battle(route,node)
                    if result.get('outcome')=='defeated':
                        # MaaTeam wraps its authenticated battle reader in its
                        # transaction receipt; preserve the actual native page.
                        receipt=result.get('native',{})
                        if type(receipt) is dict and receipt.get('native_ok') is not True:
                            receipt=receipt.get('native',{})
                        _native_ok(receipt)
                        self.owned_defeat=True
                        record=getattr(self.actions,'record_defeat',None)
                        if callable(record):record(route,node)
                        scene=self._observe(batch,route,timeout=30)
                        if scene.kind!='battle_defeat':raise BatchStopped('battle_result_unknown')
                        self.last_confirmed_defeat=(node.block_id,scene)
                        continue
                    self.owned_defeat=False
                    if result.get('outcome')!='won':raise BatchStopped('battle_result_unknown')
                    self.cursor.node_finished=True
                    if node.block_type==8 and self.cursor.index==len(route.nodes)-1:
                        self.cursor.final_boss_won=True;self.cursor.final_settlement_phase='before_score'
                    scene=self._battle_following_scene(batch,route,node);continue
                if scene.kind=='battle':raise BatchStopped('battle_without_owned_start')
                if scene.kind in ('battle_victory','boss_summary'):
                    if not self.cursor.node_finished:raise BatchStopped('battle_result_without_owned_start')
                    scene=self._do(batch,route,scene,'settlement_next',
                        {'battle_victory','boss_summary','free_role_reward','role_reward','relic_reward','link_reward','item_reward','chest',
                         'map','area_transition','score_result','final_clear','clear_congratulations','clear_character_summary','clear_reward_animation','clear_chest_animation'},extra=dict(block_id=node.block_id))
                    continue
                if scene.kind in ('free_role_reward','role_reward','relic_reward','link_reward'):
                    number=self.rewards.get((node.block_id,scene.kind),0)+1
                    self.rewards[node.block_id,scene.kind]=number
                    reward_id=f'{node.block_id}:{scene.kind}:{number}'
                    result=self.actions.choose_reward(scene.kind,route,node,reward_id)
                    self.cursor.node_finished=True
                    scene=result.get('following_scene') if isinstance(result,dict) else None
                    if scene is None:scene=self._observe(batch,route,timeout=20)
                    self.last_scene=scene;continue
                if scene.kind=='shop':
                    if node.block_type!=7:raise BatchStopped('node_type_conflict')
                    self.actions.shop(route,node);self.cursor.node_finished=True
                    scene=self._observe(batch,route,timeout=20);continue
                if scene.kind=='event':
                    if node.block_type!=5:raise BatchStopped('node_type_conflict')
                    self.actions.event(route,node,scene);self.cursor.node_finished=True
                    scene=self._observe(batch,route,timeout=20);continue
                if scene.kind=='rest':
                    if node.block_type!=5:raise BatchStopped('node_type_conflict')
                    scene=self._do(batch,route,scene,'rest',{'item_reward','map','area_transition'})
                    self.cursor.node_finished=True;continue
                if scene.kind=='event_animation':
                    if getattr(self.actions,'role_pending',()):
                        scene=self._do(batch,route,scene,'advance_character_animation',
                            {'joined','event_animation','item_reward'},extra=dict(block_id=node.block_id))
                        continue
                    if node.block_type!=5:raise BatchStopped('node_type_conflict')
                    scene=self._do(batch,route,scene,'skip_event_animation',
                        {'event','item_reward','free_role_reward','role_reward','relic_reward','link_reward','map',
                         'area_transition','normal_challenge','ex_challenge'})
                    if scene.kind not in ('event','normal_challenge','ex_challenge'):
                        self.cursor.node_finished=True
                    continue
                if scene.kind in ('item_reward','joined'):
                    if scene.kind=='joined':self.actions.accept_joined(route,node)
                    else:
                        reader=getattr(self.actions,'item_scene',None)
                        if callable(reader):scene=reader(scene,route,node,apply=True)
                    scene=self._do(batch,route,scene,'close_reward',
                        {'event','joined','event_animation','free_role_reward','role_reward','relic_reward','link_reward','item_reward','map','area_transition','chest','score_result','final_clear','clear_congratulations','clear_character_summary','clear_reward_animation','clear_chest_animation'})
                    continue
                if scene.kind=='area_transition':
                    if not self.cursor.node_finished:raise BatchStopped('area_transition_unknown')
                    scene=self._do(batch,route,scene,'next_area',{'map','relic_reward','role_reward','link_reward','item_reward'})
                    continue
                if scene.kind in ('clear_congratulations','clear_character_summary','clear_reward_animation','clear_chest_animation'):
                    if not self.cursor.final_boss_won:raise BatchStopped('unexpected_result')
                    action='settlement_next' if scene.kind=='clear_character_summary' else 'advance_final_animation'
                    scene=self._do(batch,route,scene,action,
                        {'final_clear','score_result','clear_congratulations','clear_character_summary',
                         'clear_reward_animation','clear_chest_animation','chest','item_reward','home'})
                    continue
                if scene.kind=='chest':
                    scene=self._do(batch,route,scene,'settlement_next',
                        {'chest','item_reward','score_result','final_clear','clear_congratulations','clear_character_summary','clear_reward_animation','clear_chest_animation','home'})
                    continue
                if scene.kind=='final_clear':
                    if not self.cursor.final_boss_won:raise BatchStopped('unexpected_clear')
                    self.cursor.clear_observed=True
                    scene=self._do(batch,route,scene,'settlement_next',{'chest','item_reward','score_result','home','clear_congratulations','clear_character_summary','clear_reward_animation','clear_chest_animation'})
                    continue
                if scene.kind=='score_result':
                    if not self.cursor.final_boss_won:raise BatchStopped('unexpected_result')
                    scene=self._do(batch,route,scene,'settlement_next',{'final_clear','chest','home','clear_congratulations','clear_character_summary','clear_reward_animation','clear_chest_animation'})
                    continue
                if scene.kind=='home':
                    if self.cursor.final_boss_won and self.cursor.clear_observed:
                        return self._result(handle,route,'cleared',scene)
                    raise BatchStopped('terminal_clear_not_observed')
                raise BatchStopped('scene_unhandled')
            raise BatchStopped('autoplay_step_limit')
        except BaseException:
            batch.invalidate_game_session()
            raise
