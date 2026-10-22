"""A newly acquired source row uses the same plan, callbacks and Journal."""
import json
from pathlib import Path
import pytest
from test_guide_opening import candidate, SimulatedMaa
from pcr_maa.guide_identity import read_source
from pcr_maa.guide_trial import execute_trial, validate_plan
from pcr_maa.preset_execution import Journal


def test_source86_keeps_independent_identities_and_form():
    source=read_source('红焰深域 8-6')
    assert [m['UnitId'] for m in source['Members']]==[132301,129701,134701,130201,104701]
    assert source['Members'][2]['IconUnitId']==134731
    nodes=json.loads((Path(__file__).resolve().parents[1]/'assets/resource/pipeline/guide_trial.json').read_text(encoding='utf-8'))
    assert nodes['GTStage86Label']['expected']==r'^8\s*[-－—]\s*6$'
    assert nodes['GTStage86Next']['template']==['guide-trial/next-visible-tail.png',
        'guide-trial/next-arrow-recenter-a.png','guide-trial/next-arrow-recenter-b.png']
    assert nodes['GTStage86Next']['threshold']==.95


@pytest.mark.parametrize('mask',range(32))
def test_source86_settings_use_original_execution_chain(tmp_path,mask):
    plan=candidate(mask,'红焰深域 8-6'); port=SimulatedMaa(tmp_path)
    report=execute_trial(port,Journal(tmp_path/'ledger'),plan)
    assert report['outcome']=='victory' and report['battles'][0]['set_by_unit']==plan['set_by_unit']
    assert report['attempts_total']==1 and port.returned


def test_source86_never_uses_legacy_fixed_team():
    plan=candidate(31,'红焰深域 8-6');plan.update(version=2,all_set=True)
    for key in ('set_by_unit','source_confirmations','source_material_sha256'):plan.pop(key)
    with pytest.raises(ValueError):validate_plan(plan)
