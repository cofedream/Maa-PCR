import cv2
import numpy as np
import pytest
from pcr_maa.guide_trial import TEAM
from pcr_maa.guide_trial_characters import GuideTrialCharacters
from pcr_maa.labyrinth.character_recognition import PACK


@pytest.fixture(scope='module')
def recognizer():
    return GuideTrialCharacters()


def frame(codes=('129731','116731','134731','135531','104761')):
    value=np.full((720,1280,3),255,np.uint8)
    for i,code in enumerate(codes):
        icon=cv2.imdecode(np.fromfile(PACK/f'icons/characters/icon_unit_{code}.png',np.uint8),cv2.IMREAD_COLOR)
        value[542:668,65+146*i:191+146*i]=cv2.resize(icon,(126,126))
    return value


def test_exact_five_forms(recognizer):
    rows=recognizer.read(frame())
    assert [r['unit_id'] for r in rows]==list(TEAM)


@pytest.mark.parametrize('wrong',['100261','122631','116631'])
def test_other_character_or_form_is_not_phoenix(recognizer,wrong):
    rows=recognizer.read(frame(('129731','116731',wrong,'135531','104761')))
    assert rows[2] is None
    assert rows[0]['unit_id']==129701 and rows[4]['unit_id']==104701


def test_blank_and_wrong_layout_never_pass(recognizer):
    assert recognizer.read(np.full((720,1280,3),255,np.uint8))==[None]*5
    assert [r['unit_id'] if r else None for r in recognizer.read(np.roll(frame(),80,axis=1))]!=list(TEAM)


@pytest.mark.parametrize('auto,slot,previous,allowed',[(True,2,False,True),(False,2,False,False),(True,2,True,False),(True,-1,False,False),(True,5,False,False)])
def test_portrait_click_rechecks_auto_and_selected_state(recognizer,monkeypatch,auto,slot,previous,allowed):
    from types import SimpleNamespace as NS
    import json
    monkeypatch.setattr(recognizer,'read_controls',lambda _:dict(auto=auto,sets=[True,True,False,True,True],set_button=True,unit_ids=list(TEAM)))
    context=NS(tasker=NS(stopping=False),run_recognition=lambda name,image:NS(hit=name!='GTPauseTitle'))
    args=NS(custom_recognition_param=json.dumps(dict(mode='battle_set',slot=slot,previous=previous)),image=None)
    result=recognizer.analyze(context,args)
    assert bool(result) is allowed
    if result:assert result.box==[600,560,75,65]
