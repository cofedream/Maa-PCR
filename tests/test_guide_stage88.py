"""The preserved October 8-8 row follows the original single-battle execution path."""
import json
from types import SimpleNamespace as NS
import pytest
from pcr_maa.guide_identity import read_source, require_source_team, SOURCE_PATH, SOURCE_HASHES
from pcr_maa.guide_trial import execute_trial, validate_plan
from pcr_maa.guide_trial_characters import GuideTrialCharacters
from pcr_maa.guide_trial_battle import battle_pipeline
from pcr_maa.preset_execution import Journal
from test_guide_opening import candidate, SimulatedMaa
from test_guide_trial import plan as legacy_plan

STAGE = '红焰深域 8-8'


def test_preserved_real_row_and_source_binding():
    source = read_source(STAGE)
    assert source == json.loads((SOURCE_PATH.parent/'fire88-source-identity.json').read_text(encoding='utf-8'))
    assert [(m['UnitId'],m['IconUnitId']) for m in source['Members']] == [
        (129701,129731),(116701,116731),(134701,134731),(180901,180931),(107101,107161)]
    assert source['ImportedSource']['OriginalSha256'] == 'e0b31de83020c03ea36b310004b75ee99368849ed69cbb33c3b78dcc110f6119'
    plan = candidate(31,STAGE)
    assert len(require_source_team(plan)['ManualConfirmations']) == 5
    for changed in (dict(plan,source_confirmations=[]),dict(plan,source_material_sha256='c'*64),
                    dict(plan,unit_ids=[129701,116701,122601,180901,107101]),
                    dict(plan,source_image_sha256=SOURCE_HASHES['红焰深域 8-6'])):
        with pytest.raises(ValueError):require_source_team(changed)
    with pytest.raises(ValueError):
        validate_plan(dict(legacy_plan(),stage=STAGE,source_image_sha256=SOURCE_HASHES[STAGE]))


@pytest.mark.parametrize('mask',range(32))
@pytest.mark.parametrize('order',[(0,1,2,3,4),(4,1,3,0,2)])
def test_88_original_port_controls_and_journal(tmp_path,mask,order):
    plan=candidate(mask,STAGE); port=SimulatedMaa(tmp_path,order=order)
    journal=Journal(tmp_path/'ledger'); report=execute_trial(port,journal,plan)
    assert report['outcome']=='victory' and port.returned and report['attempts_total']==1
    assert report['battles'][0]['set_by_unit']==plan['set_by_unit']
    assert report['battles'][0]['all_set'] is (mask==31)
    assert port.state['auto'] is True
    assert journal.events()[1]['result_evidence']['set_by_unit']==plan['set_by_unit']
    assert port.tasker.resource.nodes['GTDetailTitle']['expected']==r'^红焰深域\s*8\s*[-－—]\s*8$'
    assert battle_pipeline(STAGE)['GTPausedStage']['expected']==r'^红焰深域\s*8\s*[-－—]\s*8$'


@pytest.mark.parametrize('label,next_hit,map_hit,x,y,expected',[
    (True,True,True,800,200,True),(False,True,True,800,200,False),
    (True,False,True,800,200,False),(True,True,False,800,200,False),
    (True,True,True,950,200,False),(True,True,True,800,100,False)])
def test_only_exact_uncleared_map_marker_opens(label,next_hit,map_hit,x,y,expected):
    reader=GuideTrialCharacters(STAGE,plan=candidate(31,STAGE))
    def recognize(name,image):
        if name=='GTFireMap':return NS(hit=map_hit)
        if name=='GTStage88Label':return NS(hit=label,box=[800,400,60,30])
        assert name=='GTStage88Next'
        return NS(hit=next_hit,box=[x,y,60,30])
    context=NS(tasker=NS(stopping=False),run_recognition=recognize)
    assert bool(reader.analyze(context,NS(image=None,custom_recognition_param='{"mode":"open_stage"}'))) is expected
    nodes=json.loads((SOURCE_PATH.parents[1]/'resource/pipeline/guide_trial.json').read_text(encoding='utf-8'))
    assert nodes['GTStage88Label']['expected']==r'^8\s*[-－—]\s*8$'
    assert nodes['GTStage88Next']==nodes['GTStage86Next']


@pytest.mark.parametrize('result,stop,count',[
    ('GTWinObserved','already_cleared',1),('GTDefeat','attempt_limit',3),
    ('unknown','guide_trial_previous_result_unknown',1)])
def test_player_restart_preserves_old_ledger_count_and_unknown(tmp_path,result,stop,count):
    journal=Journal(tmp_path/'ledger')
    execute_trial(SimulatedMaa(tmp_path),journal,candidate(31,'红焰深域 8-6'))
    old=journal.path.read_bytes()
    for attempt in range(1,count+1):
        port=SimulatedMaa(tmp_path,auto=True,initial=[True]*5,result=result)
        report=execute_trial(port,journal,candidate(31,STAGE))
        assert report['attempts_total']==attempt and sum(n=='GTBattleStart' for n,_ in port.calls)==1
        assert [n for n,c in port.calls if c]==['GTBattleStart']
        assert journal.path.read_bytes().startswith(old)
        if result=='GTDefeat':assert report['outcome']=='defeat' and port.returned
    before=journal.path.read_bytes(); changed=candidate(0,STAGE); changed['source_hash']='d'*64
    restarted=SimulatedMaa(tmp_path)
    report=execute_trial(restarted,Journal(tmp_path/'ledger'),changed)
    assert report.get('error',report['outcome'])==stop and not restarted.calls
    assert journal.path.read_bytes()==before


@pytest.mark.parametrize('fault',['no_effect','auto_lost','identity_changed'])
def test_88_failed_readback_stops_without_resend(tmp_path,fault):
    port=SimulatedMaa(tmp_path,auto=True,initial=[True]*5,fault=fault); journal=Journal(tmp_path/'ledger')
    report=execute_trial(port,journal,candidate(0,STAGE))
    assert report['error']=='guide_trial_settings_readback_unknown'
    assert sum(n=='GTBattleToggleSet' for n,_ in port.calls)==1 and len(journal.events())==1
    assert not port.returned
