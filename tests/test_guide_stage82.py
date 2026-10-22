import json
import re
from types import SimpleNamespace as NS
import pytest
from pcr_maa.guide_identity import read_source, require_source_team, SOURCE_82_SHA256, SOURCE_75_SHA256, SOURCE_PATH
from pcr_maa.guide_trial import validate_plan
from pcr_maa.guide_trial_battle import battle_pipeline
from test_guide_trial import plan


def plan82():
    return dict(plan(),stage='红焰深域 8-2',source_image_sha256=SOURCE_82_SHA256)


def test_82_independent_source_and_exact_form_match():
    p=plan82(); source=read_source(p['stage'])
    assert source==json.loads((SOURCE_PATH.parent/'fire82-source-identity.json').read_text(encoding='utf-8'))
    assert [m['UnitId'] for m in source['Members']]==p['unit_ids']
    assert source['Members'][2]['UnitId']==134701 and source['Members'][2]['Name']=='凤凰'
    assert all(m['Score']>=.82 and m['Margin']>=.08 for m in source['Members'])
    assert require_source_team(p)==source
    with pytest.raises(ValueError,match='source_plan_mismatch'):
        require_source_team(dict(p,source_image_sha256=SOURCE_75_SHA256))
    wrong=dict(p,unit_ids=[129701,116701,122601,135501,104701])
    with pytest.raises(ValueError,match='plan_invalid'):
        validate_plan(wrong)


def test_82_paused_identity_is_not_72_or_83():
    pattern=battle_pipeline('红焰深域 8-2')['GTPausedStage']['expected']
    assert re.fullmatch(pattern,'红焰深域 8-2')
    assert not re.fullmatch(pattern,'红焰深域 7-2')
    assert not re.fullmatch(pattern,'红焰深域 8-3')


@pytest.mark.parametrize('marker_x,marker_y,label_hit,next_hit,expected',[
    (600,230,True,True,True),(800,230,True,True,False),
    (600,100,True,True,False),(600,230,False,True,False),(600,230,True,False,False)])
def test_82_next_is_same_frame_and_corresponding_stage(marker_x,marker_y,label_hit,next_hit,expected):
    from pcr_maa.guide_trial_characters import GuideTrialCharacters
    reader=GuideTrialCharacters('红焰深域 8-2')
    def recognize(name,image):
        assert name in ('GTFireMap','GTStage82Label','GTStage82Next')
        if name=='GTStage82Label':return NS(hit=label_hit,box=[600,430,60,30])
        return NS(hit=next_hit,box=[marker_x,marker_y,60,30])
    ctx=NS(tasker=NS(stopping=False),run_recognition=recognize)
    assert bool(reader.analyze(ctx,NS(custom_recognition_param='{"mode":"open_stage"}',image=None))) is expected
