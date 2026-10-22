"""New source/stage uses the existing flexible plan, Maa port and Journal."""
import json
from types import SimpleNamespace as NS
import pytest
from pcr_maa.guide_identity import read_source, require_source_team, SOURCE_HASHES, SOURCE_PATH
from pcr_maa.guide_trial import execute_trial, validate_plan
from pcr_maa.guide_trial_characters import GuideTrialCharacters
from pcr_maa.preset_execution import Journal
from test_guide_opening import candidate, SimulatedMaa
from test_guide_trial import plan as legacy_plan
from test_guide_identity import assert_same_source_identity

STAGE = '红焰深域 8-5'


def test_independent_row_identity_and_legacy_plan_rejection():
    source = read_source(STAGE)
    assert_same_source_identity(source, json.loads((SOURCE_PATH.parent / 'fire85-source-identity.json').read_text(encoding='utf-8')))
    assert [(m['UnitId'], m['IconUnitId']) for m in source['Members']] == [
        (129701,129731),(116701,116731),(134701,134731),(135501,135531),(104701,104761)]
    p = candidate(31, STAGE)
    assert require_source_team(p) == source
    with pytest.raises(ValueError, match='source_plan_mismatch'):
        require_source_team(dict(p, source_image_sha256=SOURCE_HASHES['红焰深域 8-4']))
    with pytest.raises(ValueError, match='source_plan_mismatch'):
        require_source_team(dict(p, unit_ids=[129701,116701,122601,135501,104701]))
    with pytest.raises(ValueError, match='plan_invalid'):
        validate_plan(dict(legacy_plan(), stage=STAGE, source_image_sha256=SOURCE_HASHES[STAGE]))


@pytest.mark.parametrize('x,y,label,next_hit,expected',[
    (800,200,True,True,True),(950,200,True,True,False),(800,100,True,True,False),
    (800,200,False,True,False),(800,200,True,False,False)])
def test_next_marker_must_match_stage_in_same_frame(x,y,label,next_hit,expected):
    reader = GuideTrialCharacters(STAGE)
    def recognize(name,image):
        assert name in ('GTFireMap','GTStage85Label','GTStage85Next')
        if name == 'GTStage85Label': return NS(hit=label,box=[800,400,60,30])
        return NS(hit=next_hit,box=[x,y,60,30])
    context = NS(tasker=NS(stopping=False),run_recognition=recognize)
    assert bool(reader.analyze(context,NS(custom_recognition_param='{"mode":"open_stage"}',image=None))) is expected


def test_same_team_already_correct_sets_stay_untouched_and_old_win_preserved(tmp_path):
    journal = Journal(tmp_path / 'ledger')
    execute_trial(SimulatedMaa(tmp_path,auto=True,initial=[True]*5),journal,candidate(31,'红焰深域 8-4'))
    old = journal.path.read_bytes()
    port = SimulatedMaa(tmp_path,order=(4,1,3,0,2),auto=True,initial=[True]*5)
    report = execute_trial(port,journal,candidate(31,STAGE))
    assert report['outcome'] == 'victory' and report['attempts_used'] == 1 and port.returned
    assert [name for name,click in port.calls if click] == ['GTBattleStart']
    assert journal.path.read_bytes().startswith(old)
    assert report['battles'][0]['set_by_unit'] == candidate(31,STAGE)['set_by_unit']


@pytest.mark.parametrize('result,expected,count',[
    ('GTDefeat','attempt_limit',3),('GTWinObserved','already_cleared',1),('unknown','guide_trial_previous_result_unknown',1)])
def test_new_plan_and_restart_keep_original_count_and_unclear_block(tmp_path,result,expected,count):
    journal = Journal(tmp_path / 'ledger')
    port = SimulatedMaa(tmp_path,auto=True,initial=[True]*5,result=result)
    for attempt in range(1,count+1):
        port = SimulatedMaa(tmp_path,auto=True,initial=[True]*5,result=result)
        report = execute_trial(port,journal,candidate(31,STAGE))
        assert report['attempts_used'] == attempt and sum(n=='GTBattleStart' for n,_ in port.calls)==1
    old = journal.path.read_bytes()
    p = candidate(0,STAGE); p['source_hash'] = 'c'*64
    resumed = SimulatedMaa(tmp_path)
    again = execute_trial(resumed,Journal(tmp_path / 'ledger'),p)
    assert again.get('error',again['outcome']) == expected
    assert not resumed.calls and journal.path.read_bytes() == old
