"""Pinned upstream scene combinations reused without device or protocol access."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from pcr_maa.labyrinth import autoplay as autoplay_module
from pcr_maa.labyrinth import scene as scene_module
from pcr_maa.labyrinth.autoplay import AutoplayRunner,MaaRunActions
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.scene import Scene,classify_scene,perform_scene_action


def hit(key,score=.9,box=None):
    return dict(anchor=key,score=score,box=list(box or [941,644,276,61]),
                text=None,native={},reco_id=1)


def receipt(index=1,*,action=False):
    return dict(native_ok=True,tasker_inited=True,task_id=index,
                nodes=[dict(name='LabySceneActionOnce' if action else 'offline',completed=True,
                            action=dict(name='Click' if action else 'DoNothing',success=True))])


class Batch:
    def __init__(self):self.timings=SimpleNamespace(measure=lambda category:nullcontext())
    def checkpoint(self):pass


class Runtime:
    def __init__(self,pages):self.pages=list(pages);self.reads=0
    def observe(self,timeout=scene_module.OBSERVATION_TIMEOUT_SECONDS,*,probe_kinds=None):
        self.reads+=1
        if not self.pages:raise AssertionError('unexpected scene read')
        return self.pages.pop(0)


def owner():
    installed=[]
    def override(nodes):installed.append(nodes);return True
    return SimpleNamespace(resource=SimpleNamespace(override_pipeline=override),installed=installed,stopping=False)


def actions(runtime,batch,tasker):
    adapter=MaaRunActions.__new__(MaaRunActions)
    adapter.tasker=tasker;adapter.runtime=runtime;adapter.batch=batch
    adapter.team=SimpleNamespace(start_once=lambda batch,enter,block,read:read(enter,block))
    return adapter


def unknown_boss_scene(key='BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON',score=.9):
    anchor=hit(key,score)
    payload=dict(anchors={key:anchor},controls=dict(next=anchor),score=0,
                 frame_pixel_sha256='a'*64,same_frame=True,source='maa',
                 complete_map=False,clear_verified=False)
    return Scene('unknown',receipt(10),payload)


def test_battle_result_reuses_upstream_template_pair_without_win_ocr():
    anchors={key:hit(key) for key in ('BATTLE_RESULT_REWARD_BANNER','BATTLE_RESULT_NEXT_BUTTON')}
    assert classify_scene(anchors)[0]=='battle_victory'


def test_decisive_result_logo_is_score_only_and_preserves_explicit_clear():
    map_anchors={key:hit(key,.9) for key in
        ('NODE_HEADER_STANDARD','NODE_RELICS_STANDARD','NODE_RETREAT_STANDARD','NODE_RETURN_STANDARD')}
    assert classify_scene(dict(map_anchors,RUN_RESULT_LOGO=hit('RUN_RESULT_LOGO',.93)))[0]=='score_result'
    assert classify_scene(dict(map_anchors,RUN_RESULT_LOGO=hit('RUN_RESULT_LOGO',.91)))[0]=='map'
    clear=dict(map_anchors,RUN_RESULT_LOGO=hit('RUN_RESULT_LOGO',.99),
               RUN_CLEAR_BADGE=hit('RUN_CLEAR_BADGE'),SCORE_DETAIL=hit('SCORE_DETAIL'),
               RUN_RESULT_CLOSE_BUTTON=hit('RUN_RESULT_CLOSE_BUTTON'))
    assert classify_scene(clear)[0]=='final_clear'


@pytest.mark.parametrize('left,right',[
    ('NODE_CHARACTERS_STANDARD','NODE_RETREAT_STANDARD'),
    ('NODE_CHARACTERS_STANDARD','NODE_RETURN_STANDARD'),
    ('NODE_RELICS_STANDARD','NODE_RETREAT_STANDARD'),
    ('NODE_RELICS_STANDARD','NODE_RETURN_STANDARD')])
def test_map_accepts_one_upstream_left_and_right_control(left,right):
    anchors={key:hit(key) for key in ('NODE_HEADER_STANDARD',left,right)}
    assert classify_scene(anchors)[0]=='map'


def test_headerless_map_view_does_not_become_owned_map():
    anchors={key:hit(key) for key in ('NODE_CHARACTERS_STANDARD','NODE_RETURN_STANDARD')}
    assert classify_scene(anchors)[0]=='unknown'


@pytest.mark.parametrize('block_type,score',[ (2,.9),(8,.69) ])
def test_boss_unknown_recovery_requires_boss_context_and_current_anchor(monkeypatch,block_type,score):
    tasker=owner();batch=Batch();runtime=Runtime([
        unknown_boss_scene(score=score),Scene('battle_defeat',receipt(11),{})])
    pauses=[]
    monkeypatch.setattr(autoplay_module.native,'run',lambda *a,**k:(pauses.append(1) or receipt(12)))
    result=actions(runtime,batch,tasker).start_battle(
        SimpleNamespace(enter_id=7),SimpleNamespace(block_id=30301,block_type=block_type))
    assert result['outcome']=='defeated' and runtime.reads==2 and pauses==[1]


@pytest.mark.parametrize('key',[
    'BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON','BATTLE_RESULT_NEXT_BUTTON'])
def test_owned_boss_unknown_becomes_one_consumable_summary_scene(monkeypatch,key):
    tasker=owner();batch=Batch();actual=unknown_boss_scene(key);runtime=Runtime([actual])
    monkeypatch.setattr(autoplay_module.native,'run',lambda *a,**k:receipt(20,action=True))
    adapter=actions(runtime,batch,tasker)
    result=adapter.start_battle(SimpleNamespace(enter_id=7),
                                SimpleNamespace(block_id=30301,block_type=8))
    recovered=adapter.battle_result_scene
    assert result['outcome']=='won' and recovered.kind=='boss_summary'
    assert recovered.native is actual.native and recovered.payload is actual.payload
    perform_scene_action(tasker,recovered,'settlement_next')
    nodes=tasker.installed[-1]
    guard=nodes['LabySceneActionGuard']['custom_recognition_param']
    assert guard['kind']=='unknown' and guard['control_logical']=='next'
    assert nodes['LabySceneActionControl']['recognition']=='Or'


def test_runner_consumes_recovered_summary_once_without_an_unknown_reread():
    tasker=owner();batch=Batch();runtime=Runtime([]);recovered=Scene(
        'boss_summary',receipt(30),unknown_boss_scene().payload)
    owned=SimpleNamespace(battle_result_scene=recovered)
    runner=AutoplayRunner(tasker,lambda **fields:fields,runtime=runtime,actions=owned)
    route=SimpleNamespace(enter_id=7)
    assert runner._battle_following_scene(batch,route,SimpleNamespace(block_type=8)) is recovered
    assert owned.battle_result_scene is None and runtime.reads==0
    owned.battle_result_scene=recovered
    with pytest.raises(BatchStopped,match='^battle_result_unknown$'):
        runner._battle_following_scene(batch,route,SimpleNamespace(block_type=2))


@pytest.mark.parametrize('diagnostic_hash',[None,'a'*64])
def test_owned_boss_template_proof_keeps_ocr_next_control(monkeypatch,diagnostic_hash):
    actual=unknown_boss_scene();ocr=hit('NEXT',.95,box=[1070,638,80,33]);ocr['text']='下一步'
    actual.payload['anchors']['NEXT']=ocr;actual.payload['controls']['next']=ocr
    actual.payload['frame_pixel_sha256']=diagnostic_hash
    tasker=owner();batch=Batch();runtime=Runtime([actual])
    monkeypatch.setattr(autoplay_module.native,'run',lambda *a,**k:receipt(20,action=True))
    adapter=actions(runtime,batch,tasker)
    result=adapter.start_battle(SimpleNamespace(enter_id=7),SimpleNamespace(block_id=30301,block_type=8))
    assert result['outcome']=='won'
    perform_scene_action(tasker,adapter.battle_result_scene,'settlement_next')
    nodes=tasker.installed[-1]
    assert nodes['LabySceneActionGuard']['custom_recognition_param']['kind']=='unknown'
    assert nodes['LabySceneActionControl']['recognition']=='OCR'
    assert nodes['LabySceneActionControl']['threshold']==.8


def test_ocr_next_alone_never_proves_owned_boss_summary():
    assert autoplay_module._owned_boss_summary_scene(unknown_boss_scene('NEXT'),SimpleNamespace(block_type=8)) is None


def test_run82_clear_badge_survives_optional_score_detail_animation():
    import json
    from pathlib import Path
    o=Path(__file__).resolve().parents[1].parent/'evidence/resume-20261006'
    evidence=o/'RUN82_FINAL_NATIVE_READS.json'
    if not evidence.is_file():pytest.skip('private original run82 native reads excluded from release')
    reads=[r for entry in json.loads(evidence.read_text(encoding='utf-8')) for r in entry['reads']]
    originals=[r for r in reads if r.get('kind')=='score_result' and 'RUN_CLEAR_BADGE' in r['anchors'] and 'SCORE_DETAIL' not in r['anchors']]
    assert originals
    for original in originals:
        anchors=original['anchors']
        assert classify_scene(anchors)[0]=='final_clear'
        without_badge={k:v for k,v in anchors.items() if k!='RUN_CLEAR_BADGE'}
        assert classify_scene(without_badge)[0]=='score_result'
