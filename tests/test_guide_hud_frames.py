"""Real saved HUD calibration; derived switch states are explicitly synthetic."""
from pathlib import Path
import cv2
import numpy as np
import pytest
from pcr_maa.guide_trial_characters import GuideTrialCharacters
from test_guide_opening import candidate

RUNS=Path(__file__).resolve().parents[1]/'.runtime/guide-acquisition-20261018/candidate/player/.runtime/supported-daily-work'
CURRENT='run-306f3438518b480796e44244d5ff82d4/083-GTBattleStart.png'
OFF='run-b8ff26449fd44c6c9468bf8c08cd6a5f/080-GTPauseBattle.png'


def saved(name):
    path=RUNS/name
    if not path.exists():pytest.skip('private saved HUD is not distributed: '+name)
    return cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_COLOR)


@pytest.fixture(scope='module')
def reader():return GuideTrialCharacters('红焰深域 8-3',plan=candidate())


@pytest.mark.parametrize('name,auto,sets',[
    (CURRENT,True,[True]*5),
    ('run-d880a0aedfd94542b7c5d670cdd03f54/032-GTChoose.png',True,[True]*5),
    ('run-d880a0aedfd94542b7c5d670cdd03f54/036-GTChoose.png',True,[True]*5),
    (OFF,True,[False]*5),
    ('run-3463436807a244e39dc1e61676e4449a/090-GTCalibrationPause.png',False,[True]*5),
    ('run-808860dcc361470bbf66deff4c3d4b17/031-GTBattleStart.png',True,[True]*5),
    ('run-808860dcc361470bbf66deff4c3d4b17/115-GTBattleStart.png',True,[True]*5),
])
def test_saved_control_states(reader,name,auto,sets):
    state=reader.read_controls(saved(name))
    assert state['auto'] is auto and state['sets']==sets
    if name==CURRENT:assert state['unit_ids']==candidate()['unit_ids']


def synthetic_sets(mask):
    frame=saved(CURRENT);off=saved(OFF)
    for i in range(5):
        if not mask&(1<<i):
            frame[500:565,350+160*i:415+160*i]=off[500:565,350:415]
            frame[545:620,255+160*i:265+160*i]=off[545:620,255:265]
    return frame


@pytest.mark.parametrize('mask',range(32))
def test_derived_controls_keep_portraits_and_auto(reader,mask):
    state=reader.read_controls(synthetic_sets(mask))
    assert state['auto'] is True and state['sets']==[bool(mask&(1<<i)) for i in range(5)]
    assert state['unit_ids']==candidate()['unit_ids']


@pytest.mark.parametrize('missing',['badge','ring','portrait'])
def test_incomplete_visual_evidence_stays_unknown(reader,missing):
    frame=saved(CURRENT)
    if missing=='badge':frame[500:565,510:575]=0
    elif missing=='ring':frame[545:620,415:425]=0
    else:frame[550:620,435:510]=0
    state=reader.read_controls(frame)
    if missing=='portrait':assert state['unit_ids'][1] is None
    else:assert state['sets'][1] is None


def test_real_control_check_breathing_badges_keep_portraits_and_auto():
    plan=candidate(31,'红焰深域 8-5')
    reader=GuideTrialCharacters(plan['stage'],plan=plan)
    frame=saved('run-70afa389e71345d598fa709cf0b1a74f/032-GTBattleReadSettings.png')
    state=reader.read_controls(frame)
    assert state['auto'] is True and state['unit_ids']==plan['unit_ids']
    assert state['sets']==[True]*5


@pytest.mark.parametrize('frame_name',['032-GTBattleToggleSet.png','033-GTBattleReadSettings.png'])
def test_real_control_check_one_off_and_expanded_badges(frame_name):
    plan=candidate(31,'红焰深域 8-5')
    reader=GuideTrialCharacters(plan['stage'],plan=plan)
    state=reader.read_controls(saved('run-60a9d41a12b34f69b1bdbaec75b66440/'+frame_name))
    assert state['auto'] is True and state['unit_ids']==plan['unit_ids']
    assert state['sets']==[False,True,True,True,True]


@pytest.mark.parametrize('width',range(34,49))
def test_synthetic_badge_pulse_range_preserves_ring_requirement(reader,width):
    frame=saved(CURRENT)
    badge=cv2.resize(reader.controls['set-badge'],(width,width+1))
    frame[500:565,510:575]=0
    frame[504:505+width,515:515+width]=badge
    state=reader.read_controls(frame)
    assert state['auto'] is True and state['sets']==[True]*5
    frame[545:620,415:425]=0
    assert reader.read_controls(frame)['sets'][1] is None
