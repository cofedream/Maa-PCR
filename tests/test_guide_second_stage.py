import json
from pathlib import Path
import pytest
from pcr_maa.guide_identity import read_source, SOURCE_74_SHA256, require_source_team
from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_battle import battle_pipeline
from pcr_maa.preset_execution import Journal
from test_guide_trial import Port, plan


def second_plan():
    return dict(plan(),stage='红焰深域 7-4',source_image_sha256=SOURCE_74_SHA256)


def test_second_source_independently_matches_and_cannot_use_old_row():
    p=second_plan(); identity=read_source(p['stage'])
    assert [m['UnitId'] for m in identity['Members']]==p['unit_ids']
    assert identity['Stage']==p['stage'] and require_source_team(p)==identity
    p['source_image_sha256']=plan()['source_image_sha256']
    with pytest.raises(ValueError,match='source_plan_mismatch'):require_source_team(p)


def test_shared_ledger_keeps_win_and_limits_each_stage_across_plan_changes(tmp_path):
    journal=Journal(tmp_path); execute_trial(Port(),journal,plan()); original=journal.path.read_bytes()
    second=second_plan()
    for attempt in range(1,4):
        port=Port(('defeat',)*3);report=execute_trial(port,journal,second)
        assert report['attempts_used']==attempt and report['outcome']=='defeat' and port.starts==1
    assert journal.path.read_bytes().startswith(original)
    second['source_hash']='b'*64
    for p,expected in ((second,'attempt_limit'),(plan(),'already_cleared')):
        port=Port();assert execute_trial(port,Journal(tmp_path),p)['outcome']==expected
        assert port.starts==port.prepared==0


@pytest.mark.parametrize('first,second',[(plan,second_plan),(second_plan,plan)])
def test_unknown_cannot_be_bypassed_by_switching_stage(tmp_path,first,second):
    journal=Journal(tmp_path);execute_trial(Port(('unknown',)),journal,first())
    port=Port();report=execute_trial(port,journal,second())
    assert report['error']=='guide_trial_previous_result_unknown'
    assert port.starts==port.prepared==0 and len(journal.events())==1


def test_other_stage_unknown_blocks_even_a_previously_cleared_selection(tmp_path):
    journal=Journal(tmp_path);execute_trial(Port(),journal,plan())
    execute_trial(Port(('unknown',)),journal,second_plan())
    port=Port();assert execute_trial(port,journal,plan())['error']=='guide_trial_previous_result_unknown'
    assert not port.prepared


def test_static_start_has_no_pause_link_and_explicit_resume_is_stage_bound():
    root=Path(__file__).resolve().parents[1]
    nodes=json.loads((root/'assets/resource/pipeline/guide_trial.json').read_text(encoding='utf-8'))
    assert nodes['GTBattleStart']['next']==[]  # Runtime adds the read-only battle HUD check.
    assert battle_pipeline('红焰深域 7-4')['GTPausedStage']['expected'].endswith('4$')
    assert 'GTStage73Label' not in nodes['GTFireMap']['all_of']


@pytest.mark.parametrize('marker_x,expected',[(600,True),(750,False)])
def test_next_marker_must_belong_to_target_label(marker_x,expected):
    from types import SimpleNamespace as NS
    from pcr_maa.guide_trial_characters import GuideTrialCharacters
    recognizer=GuideTrialCharacters('红焰深域 7-4')
    def recognize(name,image):
        return NS(hit=True,box=[600,480,60,30] if name=='GTStage74Label' else [marker_x,280,60,30])
    ctx=NS(tasker=NS(stopping=False),run_recognition=recognize)
    args=NS(custom_recognition_param='{"mode":"open_stage"}',image=None)
    assert bool(recognizer.analyze(ctx,args)) is expected
