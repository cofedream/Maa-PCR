import json
from pathlib import Path
import cv2
import numpy as np
import pytest
from pcr_maa import guide_identity as identity
from pcr_maa import guide_trial as trial
from pcr_maa.preset_execution import Journal


def assert_same_source_identity(actual, expected):
    # OpenCV template scores differ slightly across CPU backends.
    members = [dict(member,
        Score=pytest.approx(member['Score'], rel=0, abs=1e-5),
        Margin=pytest.approx(member['Margin'], rel=0, abs=1e-5))
        for member in expected['Members']]
    assert actual == dict(expected, Members=members)


@pytest.fixture
def source_identity():
    return dict(SourceUrl='https://example.invalid/source', Server='fixture', Stage='fixture',
        ImageSha256='a'*64, ImageBase64='AA==', Members=[dict(UnitId=129701, IconUnitId=129731,
        Name='fixture', Attribute='FIRE', Score=.8337068557739258, Margin=.3984590470790863)])


def test_source_comparison_accepts_ci_score_drift_without_changing_evidence(source_identity):
    actual = dict(source_identity, Members=[dict(source_identity['Members'][0],
        Score=.83370441198349, Margin=.3984554708003998)])
    assert actual != source_identity
    assert_same_source_identity(actual, source_identity)
    assert source_identity['Members'][0]['Score'] == .8337068557739258
    assert actual['Members'][0]['Score'] == .83370441198349


@pytest.mark.parametrize('key,value', [('UnitId', 999901), ('IconUnitId', 999931),
    ('Name', 'different'), ('Attribute', 'WATER'), ('Score', .8), ('Margin', .1),
    ('Score', float('nan')), ('Margin', float('inf'))])
def test_source_comparison_rejects_changed_identity_or_scores(source_identity, key, value):
    actual = dict(source_identity, Members=[dict(source_identity['Members'][0], **{key: value})])
    with pytest.raises(AssertionError):
        assert_same_source_identity(actual, source_identity)


@pytest.mark.parametrize('key', ['SourceUrl', 'Server', 'Stage', 'ImageSha256', 'ImageBase64'])
def test_source_comparison_keeps_exact_source_binding(source_identity, key):
    with pytest.raises(AssertionError):
        assert_same_source_identity(dict(source_identity, **{key: 'changed'}), source_identity)


def test_source_is_independent_of_planned_team(monkeypatch, tmp_path):
    evidence = identity.read_source()
    assert [m['UnitId'] for m in evidence['Members']] == [129701,116701,134701,135501,104701]
    assert all(m['Attribute']=='FIRE' and m['Margin'] >= .08 for m in evidence['Members'])
    shipped = json.loads((identity.SOURCE_PATH.parent/'fire73-source-identity.json').read_text(encoding='utf-8'))
    assert_same_source_identity(evidence, shipped)
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
