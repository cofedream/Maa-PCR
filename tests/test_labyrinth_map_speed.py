"""Same-frame battle text and two-frame map calibration on the normal runner."""
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa.labyrinth import autoplay, relics, scene
from test_labyrinth_autoplay import make, receipt, route_for, sequence
from test_labyrinth_recognition_perf_r50 import SceneContext, target_callback


def classify(keys, probe, scope=None):
    context = SceneContext(keys)
    read = context.run_recognition
    def recognize(name, image, override=None):
        if name == 'LabySceneBattleWords':
            context.calls.append(name)
            return probe
        return read(name, image, override)
    context.run_recognition = recognize
    result = context.observer.analyze(context, NS(image=np.zeros((720, 1280, 3), np.uint8),
        custom_recognition_param={'probe_kinds': scope or sorted(scene._RULES)}))
    return result.detail, context.calls


def test_complete_battle_text_miss_skips_only_equivalent_native_words():
    nodes = scene.scene_pipeline()
    group = nodes['LabySceneBattleWords']
    singles = [nodes['LabySceneWord_'+key] for key in scene._BATTLE_WORDS]
    assert group['expected'] == [n['expected'] for n in singles]
    for leaf in singles:
        assert {**leaf, 'expected':group['expected']} == group
    miss = NS(hit=False, reco_id=4, raw_detail={'all':[], 'filtered':[], 'best':None})
    result, calls = classify(set(scene._RULES['map'][0]) | {'AREA'}, miss)
    assert result['kind'] == 'map' and result['payload']['area']==1
    assert calls.count('LabySceneBattleWords')==1
    assert all('LabySceneWord_'+key not in calls for key in scene._BATTLE_WORDS)
    assert {'LabySceneWord_DATE','LabySceneWord_DISCONNECT'} <= set(calls)
    _, narrow = classify({'LOSE'}, miss, ['battle_defeat'])
    assert 'LabySceneBattleWords' not in narrow and 'LabySceneWord_LOSE' in narrow


@pytest.mark.parametrize('probe', [None, NS(hit=True,reco_id=4,raw_detail={}),
    NS(hit=False,reco_id=0,raw_detail={'all':[], 'filtered':[], 'best':None}),
    NS(hit=False,reco_id=4,raw_detail={'filtered':[]}),
    NS(hit=False,reco_id=4,raw_detail={'all':[{}], 'filtered':[], 'best':None})])
@pytest.mark.parametrize('keys,kind', [({'WIN','NEXT'},'battle_victory'), ({'LOSE'},'battle_defeat'),
                                      ({'DEFEAT'},'battle_defeat'), ({'WIN','NEXT','DISCONNECT'},'disconnected')])
def test_positive_or_incomplete_combined_probe_uses_individual_receipts(probe,keys,kind):
    result,calls = classify(keys, probe)
    assert result['kind']==kind
    assert {'LabySceneWord_'+key for key in scene._BATTLE_WORDS} <= set(calls)
    if kind=='battle_victory':assert result['payload']['controls']['next']['anchor']=='NEXT'


def test_combined_read_keeps_target_and_counters_on_one_image_and_rechecks_preview(monkeypatch):
    image=np.zeros((720,1280,3),np.uint8)
    keys=set(scene._RULES['map'][0]) | {'AREA'}
    context=SceneContext(keys)
    callback=target_callback()
    frames=[]
    def marks(_, current):
        frames.append(current)
        return {'values':{'守备':2}}
    monkeypatch.setattr(relics,'read_map_marks_frame',marks)
    read=NS(image=image,custom_recognition_param=json.dumps({'mode':'read','read_marks':True}))
    result=callback.analyze(context,read)
    assert result.detail['status']=='target_observed' and result.detail['map_marks']=={'values':{'守备':2}}
    assert len(context.scopes)==1 and len(frames)==1 and frames[0] is image
    context.keys.update(scene._RULES['joined'][0])
    changed=callback.analyze(context,NS(image=image.copy(),custom_recognition_param=read.custom_recognition_param))
    assert changed.detail['status']=='scene_changed' and 'map_marks' not in changed.detail
    preview=callback.analyze(context,NS(image=image.copy(),custom_recognition_param='{"mode":"preview"}'))
    assert preview.box is None and preview.detail['scene']['kind']=='joined' and len(frames)==1


@pytest.mark.parametrize('second,expected', [(3,3), (4,None)])
def test_consensus_still_requires_two_reads_and_both_receipts(monkeypatch, second, expected):
    actor=autoplay.MaaRunActions.__new__(autoplay.MaaRunActions)
    observations=[]; reads=[]
    actor.map_read_nodes=set();actor.stacks={};actor.stack_sources={};actor.stack_conflicts=set()
    actor.tasker=object();actor.batch=NS(timings=None,checkpoint=lambda:None,
        observation=lambda value,**kw:observations.append(value))
    actor.stamp=lambda **fields:fields
    def first(*args,**kwargs):
        reads.append('first')
        return {'native':receipt(1), 'values':{'守备':3}}
    def locate(route,node,*,read_marks):
        assert read_marks
        reads.append('second')
        return {'status':'target_observed','map_marks':{'values':{'守备':second}}},receipt(2)
    monkeypatch.setattr(relics,'read_map_relic_stacks',first)
    route=route_for([1,2]);node=route.nodes[0]
    result=actor.calibrate_map(route,node,navigator=NS(locate=locate))
    assert result[1]['task_id']==2 and actor.stacks.get('守备')==expected
    assert actor.calibrate_map(route,node,navigator=NS(locate=locate)) is None
    assert reads==['first','second']
    assert [o['native'] for o in observations]==([{'first':receipt(1),'second':receipt(2)}] if expected else [])


def test_runner_reuses_only_the_scoped_read_and_preserves_every_input(tmp_path):
    types=[1,2,8]
    route=route_for(types)
    runner,batch,_,navigator,actions=make(tmp_path,route,sequence(types,confirm=True))
    reads=[]
    def combined(actual_route,node,*,navigator):
        assert actual_route is route
        reads.append(node.block_id)
        return {'status':'target_observed'},receipt(55)
    actions.calibrate_map=combined
    navigator.locate=lambda *args:pytest.fail('unused third read')
    result=runner.play(route,batch)
    assert result['clear_verified'] and reads==navigator.previews==[n.block_id for n in route.nodes[:-1]]
