import json
from pathlib import Path
import cv2
import numpy as np
import pytest
from pcr_maa import guide_identity as identity
from pcr_maa import guide_trial as trial
from pcr_maa.preset_execution import Journal


def test_source_is_independent_of_planned_team(monkeypatch, tmp_path):
    evidence = identity.read_source()
    assert [m['UnitId'] for m in evidence['Members']] == [129701,116701,134701,135501,104701]
    assert all(m['Attribute']=='FIRE' and m['Margin'] >= .08 for m in evidence['Members'])
    shipped = json.loads((identity.SOURCE_PATH.parent/'fire73-source-identity.json').read_text(encoding='utf-8'))
    assert shipped == evidence
    wrong = (129701,116701,122601,135501,104701)
    monkeypatch.setattr(trial, 'TEAM', wrong)
    plan = dict(version=2,server='cn-bilibili',stage=trial.STAGE,account_id='123456789',unit_ids=list(wrong),
        stars=list(trial.STARS),all_set=True,auto=True,max_attempts=3,source_hash='a'*64,
        source_image_sha256=identity.SOURCE_SHA256)
    class NoInput:
        def prepare(self, _): pytest.fail('source mismatch reached input port')
    result=trial.execute_trial(NoInput(),Journal(tmp_path),plan)
    assert result['error']=='guide_source_plan_mismatch' and not result['consumption_sent']
    assert not Journal(tmp_path).events()


def test_changed_source_stops_before_matching(monkeypatch,tmp_path):
    path=tmp_path/'row.png';path.write_bytes(identity.SOURCE_PATH.read_bytes()+b'changed')
    monkeypatch.setattr(identity,'SOURCE_PATH',path)
    with pytest.raises(ValueError,match='image_changed'): identity.read_source()


def test_ambiguous_or_poor_portrait_is_unknown():
    rng=np.random.default_rng(23)
    icon=rng.integers(0,255,(128,128,3),dtype=np.uint8)
    query=cv2.resize(icon[:,:,::-1],(50,50))[6:35,7:44]
    entries=[dict(character_id=str(i),name=str(i),attribute='FIRE',unit_icon_id=i*100+31) for i in (1001,1002)]
    with pytest.raises(ValueError,match='unknown_or_ambiguous'):
        identity.resolve_portrait(query,entries,[icon,icon])
    with pytest.raises(ValueError,match='unknown_or_ambiguous'):
        identity.resolve_portrait(rng.integers(0,255,query.shape,dtype=np.uint8),entries,[icon,icon])
