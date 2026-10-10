"""Run117: post-ACK member reads keep real identities within the original budget."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import time
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
SCREEN=ROOT/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/fae5ea0af024459ab94990d88a510fa5/acceptance-ending/screen.png'
ROSTER=['1059','1075','1351','1051','1297','1112','1001','1105','1145','1143','1264','1027','1260','1082','1355']
SELECTED=['1082','1112','1297','1351']


@pytest.fixture(scope='module')
def saved117():
    if not SCREEN.is_file():pytest.skip('private run117 evidence is excluded from public source')
    image=native.read_image(SCREEN)
    baseline=m.CharacterRecognizer().recognize(image,'battle',roster=ROSTER,current_filter='ALL')
    assert baseline['selected']==SELECTED and len(baseline['members'])==4
    assert all(c['trusted'] for c in baseline['members'])
    return image,baseline


def test_actual117_member_scope_keeps_original_identity_scores_without_candidate_scan(saved117,monkeypatch):
    image,baseline=saved117
    def no_candidates(*a,**k):raise AssertionError('member read scanned the available-card grid')
    monkeypatch.setattr(m,'detect_grid',no_candidates)
    recognizer=m.CharacterRecognizer();calls=[];match=recognizer.matcher.match
    def member_match(frame,box,**kw):
        assert box[1]>=500
        calls.append(box)
        return match(frame,box,**kw)
    monkeypatch.setattr(recognizer.matcher,'match',member_match)
    first=recognizer.recognize(image,'battle',roster=ROSTER,current_filter='ALL',members_only=True)
    second=recognizer.recognize(image,'battle',roster=ROSTER,current_filter='ALL',members_only=True)
    assert len(calls)>=8 and not first['stable'] and second['stable']  # Original member refine may try more than one crop.
    for seen in (first,second):
        assert seen['members']==baseline['members'] and seen['selected']==SELECTED
        assert seen['members_only'] is True and seen['cards']==[] and seen['boxes']==[]
        assert not seen.get('empty_effective_verified')


def test_actual117_native_member_feedback_finishes_within_unchanged_budget(saved117,tmp_path,monkeypatch):
    image,baseline=saved117
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[image]]);owner=native.bind(native.load_resource(ROOT/'assets/resource'),controller)
    timings=Timings();evidence=NativeTimings(owner,controller,timings)
    chars=m.MaaCharacters(owner,timings=timings)
    journal=Journal(tmp_path/'ledger');batch=Batch(Settings((Goal(1,1),)),NS(),journal,Binding(123,'offline-member117'))
    started=time.monotonic();receipt=None
    try:
        actual,receipt=chars.wait_for(batch,'battle',
            lambda v:v['selected']==SELECTED and all(c['trusted'] for c in v['members']),
            roster=ROSTER,members_only=True)
        elapsed=time.monotonic()-started
        assert elapsed<12 and actual['stable'] and actual['members']==baseline['members']
        assert actual['members_only'] and not actual['cards']
        assert not controller.clicks and not controller.swipes and not journal.events()
        (tmp_path/'member-feedback.json').write_text(json.dumps(dict(elapsed_seconds=elapsed,
            observation=actual,native=receipt,actual_device_inputs=0),ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        assert native.wait_job(owner.post_stop(),seconds=10).succeeded
        evidence.close()


@pytest.mark.parametrize('page,mode',[('opening','read'),('roster','read'),('battle','toggle'),
    ('battle','filter'),('battle','scroll'),('battle','start'),('boss','tab'),('boss','invite')])
def test_member_scope_cannot_authorize_other_pages_or_inputs(saved117,page,mode):
    image,_=saved117
    def forbidden(*a,**k):raise AssertionError('unsupported member scope reached recognition')
    result=m.CharacterRecognition('Scope',m.CharacterRecognizer()).analyze(
        NS(tasker=NS(stopping=False),run_recognition=forbidden),
        NS(image=image,custom_recognition_param=json.dumps(dict(page=page,mode=mode,members_only=True))))
    assert result is None


def test_member_scope_is_not_an_empty_effective_list(saved117,monkeypatch):
    image,_=saved117;names=[]
    monkeypatch.setattr(m,'_blue',lambda *a:1)
    def read(name,image):
        names.append(name)
        if name in ('LabyDateChangedObserved','LabyDisconnect6002Observed'):return None
        if 'Filter' in name and not name.endswith('FilterEFFECTIVE_EFFECT'):return None
        assert name!='ScopeEffectiveEmptyNotice'
        word=dict(text='有效效果',box=[50,130,90,30],score=.99)
        return NS(hit=True,raw_detail=dict(filtered=[word],best=word))
    result=m.CharacterRecognition('Scope',m.CharacterRecognizer()).analyze(
        NS(tasker=NS(stopping=False),run_recognition=read),
        NS(image=image,custom_recognition_param=json.dumps(dict(mode='read',page='battle',
            members_only=True,roster=ROSTER))))
    assert result is not None
    detail=json.loads(result.detail) if isinstance(result.detail,str) else result.detail
    assert detail['filter']=='EFFECTIVE_EFFECT' and detail['selected']==SELECTED
    assert detail['members_only'] and not detail['cards'] and not detail.get('empty_effective_verified')
    assert 'ScopeEffectiveEmptyNotice' not in names


def test_untrusted_actual_member_is_not_filled_from_expected_roster(saved117,monkeypatch):
    from dataclasses import replace
    image,_=saved117;recognizer=m.CharacterRecognizer();match=recognizer.matcher.match
    def unknown_first(frame,box,**kw):
        actual=match(frame,box,**kw)
        return replace(actual,character_id=None,trusted=False) if box[0]<300 else actual
    monkeypatch.setattr(recognizer.matcher,'match',unknown_first)
    monkeypatch.setattr(recognizer.matcher,'refine',lambda frame,box,match,**kw:match)
    actual=recognizer.recognize(image,'battle',roster=ROSTER,current_filter='ALL',members_only=True)
    assert len(actual['members'])==4 and actual['members'][0]['character_id'] is None
    assert not actual['members'][0]['trusted'] and actual['selected']==['1112','1297','1351']


def test_add_after_member_feedback_acquires_full_list_before_each_seek(monkeypatch):
    from pcr_maa.labyrinth.team_execution import MaaTeam
    selected=['1059'];calls=[]
    def view(partial=False):
        return dict(selected=sorted(selected),members=[dict(character_id=r,trusted=True) for r in selected],
            cards=[] if partial else [dict(character_id=r,trusted=True) for r in SELECTED],
            **({'members_only':True} if partial else {}))
    def read(*a,**kw):calls.append('full');return view(),{}
    def wait(batch,page,predicate,**kw):
        assert kw['members_only'] is True;calls.append('members')
        value=view(True);assert predicate(value);return value,{}
    def run(suffix,params,**kw):
        assert suffix=='ToggleOnce' and kw['input'] is True and 'members_only' not in params
        role=params['role_id'];calls.append('toggle:'+role)
        selected.remove(role) if role in selected else selected.append(role)
        return None,dict(native_ok=True)
    chars=NS(read=read,wait_for=wait,run=run)
    class Navigation:
        def __init__(self,*a,roster):assert roster==ROSTER
        def seek(self,batch,enter,page,observation,targets,**kw):
            assert not observation.get('members_only') and observation['cards']
            calls.append('seek');return observation,[dict(character_id=targets[0],trusted=True)]
    monkeypatch.setattr(m,'CharacterNavigation',Navigation)
    batch=NS(checkpoint=lambda:None,transaction=lambda kind,fields,send:send('offline-input'),
        observation=lambda value,**kw:value)
    team=object.__new__(MaaTeam);team._characters=chars;team.stamp=lambda **v:v
    team.roster=ROSTER;team.optimizer=NS(profiles={r:NS(data={'attribute':'DARK'}) for r in ROSTER})
    actual,_=team._edit(batch,117,20502,'battle',['1075','1351'],view())
    assert actual['selected']==['1075','1351'] and not actual.get('members_only')
    assert calls==['toggle:1059','members','full','seek','toggle:1075','members',
        'full','seek','toggle:1351','members','full']
