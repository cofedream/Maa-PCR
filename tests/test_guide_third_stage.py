import pytest
from pcr_maa.guide_identity import read_source, require_source_team, SOURCE_75_SHA256
from pcr_maa.guide_trial import execute_trial
from pcr_maa.guide_trial_battle import battle_pipeline
from pcr_maa.preset_execution import Journal
from test_guide_trial import Port, plan
from test_guide_second_stage import second_plan


def third_plan():
    return dict(plan(),stage='红焰深域 7-5',source_image_sha256=SOURCE_75_SHA256)


def test_source_is_independent_and_stage_bound():
    p=third_plan(); source=read_source(p['stage'])
    assert [m['UnitId'] for m in source['Members']]==p['unit_ids']
    assert require_source_team(p)==source
    for previous in (plan(),second_plan()):
        with pytest.raises(ValueError,match='source_plan_mismatch'):
            require_source_team(dict(p,source_image_sha256=previous['source_image_sha256']))
    assert battle_pipeline(p['stage'])['GTPausedStage']['expected'].endswith('5$')


@pytest.mark.parametrize('result,count',[('defeat',3),('unknown',1),('victory',1)])
def test_prior_wins_preserved_and_third_stage_never_resets(tmp_path,result,count):
    journal=Journal(tmp_path)
    for old in (plan(),second_plan()):execute_trial(Port(),journal,old)
    original=journal.path.read_bytes()
    p=third_plan()
    for attempt in range(1,count+1):
        port=Port((result,)*3);report=execute_trial(port,journal,p)
        assert report['attempts_used']==attempt and port.starts==1
    assert journal.path.read_bytes().startswith(original)
    for selected in (dict(p,source_hash='e'*64),plan(),second_plan()):
        port=Port();after=execute_trial(port,Journal(tmp_path),selected)
        assert port.starts==port.prepared==0
        if result=='unknown':assert after['error']=='guide_trial_previous_result_unknown'
        elif selected['stage']==p['stage']:assert after['outcome']==('already_cleared' if result=='victory' else 'attempt_limit')
        else:assert after['outcome']=='already_cleared'


@pytest.mark.parametrize('marker_x,expected',[(800,True),(950,False)])
def test_target_next_is_bound_to_75(marker_x,expected):
    from types import SimpleNamespace as NS
    from pcr_maa.guide_trial_characters import GuideTrialCharacters
    reader=GuideTrialCharacters('红焰深域 7-5')
    def recognize(name,image):
        assert name in ('GTFireMap','GTStage75Label','GTStage75Next')
        return NS(hit=True,box=[800,400,60,30] if name=='GTStage75Label' else [marker_x,200,60,30])
    ctx=NS(tasker=NS(stopping=False),run_recognition=recognize)
    assert bool(reader.analyze(ctx,NS(custom_recognition_param='{"mode":"open_stage"}',image=None))) is expected
