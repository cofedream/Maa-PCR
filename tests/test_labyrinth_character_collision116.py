"""Run116 saved-frame collision: uncertain offers do not erase trusted members."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import pytest
from pcr_maa import native
from pcr_maa.labyrinth import character_recognition as m
from pcr_maa.labyrinth.batch import Batch,Binding,BatchStopped
from pcr_maa.labyrinth.config import Settings,Goal
from pcr_maa.labyrinth.timing import NativeTimings,Timings
from pcr_maa.preset_execution import Journal
from pcr_maa.replay import SavedImages
from test_labyrinth_character_port import offline

ROOT=Path(__file__).resolve().parents[1]
Q=ROOT.parent/'evidence/resume-20261006/upstream-complete-20261007/isolation-after109'
SCREEN=ROOT/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/dbb8d295e57d46f3893fc7f68bb62586/acceptance-ending/screen.png'
PROOF=Q/'RUN116_ENDING_RECOGNITION_FAILURE.json'
SELECTED=['1075','1331','1351','1807']


def sample():
    if not SCREEN.is_file() or not PROOF.is_file():pytest.skip('private run116 evidence is excluded from public source')
    return native.read_image(SCREEN),json.loads(PROOF.read_text(encoding='utf-8'))


def assert_current_candidate_identity(observation,proof):
    assert observation['selected']==SELECTED
    assert len(observation['members'])==4 and all(c['trusted'] for c in observation['members'])
    previous={c['slot_id'] for c in proof['cards'] if c['character_id']=='1051'}
    assert len(previous)==2
    actual=[c for c in observation['cards'] if c['slot_id'] in previous]
    identified=[c for c in actual if c['trusted']];uncertain=[c for c in actual if not c['trusted']]
    assert len(identified)==len(uncertain)==1
    assert identified[0]['character_id']=='1051'
    assert identified[0]['confidence']>=m.CONFIDENCE and identified[0]['rival_margin']>=m.RIVAL_MARGIN
    unknown=uncertain[0]
    assert all(unknown[k] is None for k in ('character_id','name','variant','unit_icon_id'))
    assert unknown['candidates'][0]['character_id']=='1051'
    assert unknown['confidence']<m.CONFIDENCE or unknown['rival_margin']<m.RIVAL_MARGIN


def test_actual116_star_badge_masks_alias_while_preserving_four_real_members():
    image,proof=sample();recognizer=m.CharacterRecognizer()
    first=recognizer.recognize(image,'battle',roster=proof['roster'],current_filter='ALL')
    second=recognizer.recognize(image,'battle',roster=proof['roster'],current_filter='ALL')
    assert_current_candidate_identity(first,proof);assert_current_candidate_identity(second,proof)
    assert not first['stable'] and second['stable']

@pytest.mark.parametrize('role',['1807','1051','missing'])
def test_actual116_native_toggle_uses_current_identity_and_rejects_missing_target(role,tmp_path,monkeypatch):
    image,proof=sample();monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[image]]);owner=native.bind(native.load_resource(ROOT/'assets/resource'),controller)
    timings=Timings();evidence=NativeTimings(owner,controller,timings)
    chars=m.MaaCharacters(owner,timings=timings);journal=Journal(tmp_path/'ledger')
    batch=Batch(Settings((Goal(1,1),)),NS(),journal,Binding(123,'offline-collision116'))
    observations=[];receipt=None
    try:
        # Two independent native reads retain the original node/read budgets.
        for _ in range(2):
            seen,raw=chars.read(batch,'battle',roster=proof['roster'],stable=False)
            assert_current_candidate_identity(seen,proof);observations.append(dict(observation=seen,native=raw))
        assert seen['stable'] and not controller.clicks
        visible={c['character_id'] for c in seen['cards']+seen['members'] if c['trusted']}
        target=next(x for x in proof['roster'] if x not in visible) if role=='missing' else role
        params=dict(mode='toggle',page='battle',role_id=target,expected_selected=SELECTED,roster=proof['roster'])
        if role!='missing':
            business,receipt=chars.run('ToggleOnce',params,input=True)
            assert business is None and receipt['native_ok']
            if role=='1807':assert controller.clicks==[[274,606]]
            else:
                card=next(c for c in seen['cards'] if c['trusted'] and c['character_id']=='1051')
                x,y,w,h=card['box'];assert controller.clicks==[[x+w//2,y+h//2]]
            assert not receipt.get('business_result_verified')
        else:
            with pytest.raises(native.NativeInputNotSent) as caught:chars.run('ToggleOnce',params,input=True)
            receipt=caught.value.receipt
            assert receipt['input_execution']=='not_sent' and not controller.clicks
        assert not controller.swipes and not journal.events()
    finally:
        assert native.wait_job(owner.post_stop(),seconds=10).succeeded
        evidence.close()
        (tmp_path/'receipt.json').write_text(json.dumps(dict(observations=observations,receipt=receipt,
            clicks=controller.clicks,swipes=controller.swipes,actual_device_inputs=0,
            scope='SavedImages native fresh guard and ACK only; no business successor'),ensure_ascii=False,indent=2),encoding='utf-8')


def test_duplicate_actual_member_identity_still_stops(monkeypatch):
    image,proof=sample();box=tuple(proof['members'][0]['box'])
    # Isolate duplicated members from available-list identity evidence.
    monkeypatch.setattr(m,'detect_grid',lambda *a,**k:())
    monkeypatch.setattr(m,'member_slots',lambda *a,**k:(box,box))
    with pytest.raises(BatchStopped,match='^character_identity_ambiguous$'):
        m.CharacterRecognizer().recognize(image,'battle',roster=proof['roster'],current_filter='ALL')


@pytest.mark.parametrize('mode',['toggle','invite'])
def test_opening_collisions_never_authorize_selection_or_invitation(mode,monkeypatch):
    image,proof=sample();original=[c for c in proof['cards'] if c['character_id']=='1051']
    values={key:original[0][key] for key in ('character_id','name','variant','confidence','rival_confidence','rival_margin','trusted','unit_icon_id')}
    match=m.IconMatch(**values,candidates=tuple(m.IconCandidate(**c) for c in original[0]['candidates']))
    recognizer=m.CharacterRecognizer(matcher=NS(match=lambda *a,**k:match,names={'1051':None,'1075':None}))
    monkeypatch.setattr(m,'detect_grid',lambda *a,**k:tuple(tuple(c['box']) for c in original))
    monkeypatch.setattr(m,'opening_marker',lambda *a:1 if mode=='invite' else 0)
    monkeypatch.setattr(m,'_blue',lambda *a:1)
    count=2 if mode=='invite' else 0
    for _ in range(2):seen=recognizer.recognize(image,'opening',current_filter='ALL',observed_count=count)
    assert seen['stable'] and not seen['selected'] and all(not c['trusted'] for c in seen['cards'])
    def read(name,image):
        if name in ('LabyDateChangedObserved','LabyDisconnect6002Observed') or 'Filter' in name and not name.endswith('FilterALL'):return None
        word=dict(text=f'{count}/2',box=[50,130,90,30],score=.99)
        return NS(hit=True,raw_detail=dict(filtered=[word],best=word))
    params=dict(mode=mode,page='opening',role_id='1051',expected_selected=['1051','1075'] if mode=='invite' else [],required_count=2)
    result=m.CharacterRecognition('Collision',recognizer).analyze(NS(tasker=NS(stopping=False),run_recognition=read),
        NS(image=image,custom_recognition_param=json.dumps(params)))
    assert result is None


@pytest.mark.parametrize('page',['battle','boss'])
def test_candidate_duplicate_identities_still_remain_unknown(page,monkeypatch):
    import numpy as np
    image=np.full((720,1280,3),240,np.uint8)
    candidate=m.IconCandidate('1051','宫子','31',.9,'1051','105131')
    match=m.IconMatch('1051','宫子','31',.9,.1,.8,True,(candidate,),'105131')
    recognizer=m.CharacterRecognizer(matcher=NS(match=lambda *a,**k:match,names={'1051':None}))
    monkeypatch.setattr(m,'detect_grid',lambda *a,**k:((82,249,130,130),(224,249,130,130)))
    monkeypatch.setattr(m,'card_presence',lambda *a:1)
    monkeypatch.setattr(m,'member_slots',lambda *a,**k:())
    seen=recognizer.recognize(image,page,current_filter='ALL')
    assert len(seen['cards'])==2 and not seen['selected']
    assert all(not c['trusted'] and c['character_id'] is None for c in seen['cards'])
    assert all(c['candidates'][0]['character_id']=='1051' for c in seen['cards'])
