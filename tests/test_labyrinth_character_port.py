"""Full upstream character resources and official offline Maa transport.

Private saved frames are optional local fixtures; they are never Source assets.
Native tests assert actual CustomController inputs and native feedback in one
borrowed Context. They do not certify live account/run or hardware behavior.
"""
import copy,json,socket
from pathlib import Path
from types import SimpleNamespace
import cv2,numpy as np,pytest
from maa.custom_action import CustomAction
from pcr_maa import native
from pcr_maa.context_backend import ContextTasker
from pcr_maa.replay import SavedImages
from pcr_maa.preset_execution import Journal
from pcr_maa.labyrinth.batch import Batch,Binding,BatchStopped
from pcr_maa.labyrinth.config import Settings,Goal
from pcr_maa.labyrinth.character_recognition import (PACK,IconMatcher,IconMask,load_character_pack,
    canonical_id,resolve_character_name,CharacterRecognizer,MaaCharacters,CharacterNavigation)
from pcr_maa.labyrinth.opening import MaaOpening
from pcr_maa.labyrinth.roles import Profile,Context,Optimizer,load_optimizer,configured_optimizer
from pcr_maa.labyrinth.teams import boss_plan

BASE=Path(__file__).resolve().parents[2]
PRIVATE=BASE/'evidence/opening-sample'

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):raise AssertionError('network/device prohibited in offline character tests')
    monkeypatch.setattr(socket.socket,'connect',deny);monkeypatch.setattr(socket,'create_connection',deny)
    import maa.controller
    from maa.toolkit import Toolkit
    monkeypatch.setattr(maa.controller,'AdbController',deny);monkeypatch.setattr(Toolkit,'find_adb_devices',deny)

def test_all_original_icons_hashed_and_variants_retained():
    entries,images,names=load_character_pack()
    assert len(entries)==801 and images.shape==(801,128,128,3)
    assert len({e['unit_icon_id'] for e in entries})==801
    assert all(len(e['unit_icon_id'])==6 for e in entries)
    assert {e['variant'] for e in entries if e['character_id']=='1001'}>={'11','31','61'}
    assert '1807' in names and canonical_id('100101')=='100101'
    assert canonical_id('1183')==canonical_id('1184')=='1807'
    assert canonical_id('1075')!='1001'
    with pytest.raises(BatchStopped):canonical_id('1075x')

def test_matcher_star_variants_not_cross_character_rivals():
    matcher=IconMatcher();entries,images,_=load_character_pack()
    for variant in ('11','31','61'):
        i=next(i for i,e in enumerate(entries) if e['character_id']=='1001' and e['variant']==variant)
        # Source BGR frame, original upstream artwork at 720p card inner size.
        frame=np.full((720,1280,3),240,np.uint8);frame[206:322,206:322]=cv2.resize(images[i][...,::-1],(116,116))
        match=matcher.match(frame,(206,206,116,116))
        assert match.trusted and match.character_id=='1001' and match.variant==variant
        assert all(c.character_id!='1001' for c in match.candidates[1:])

def test_full_names_do_not_collapse_seasonal_variants_or_unknown():
    assert resolve_character_name('可可萝')=='1059'
    assert resolve_character_name('ペコリーヌ（サマー）')=='1075'
    with pytest.raises(BatchStopped):resolve_character_name('未识别名称')
    with pytest.raises(BatchStopped):resolve_character_name('雪菲(夏日)额外文字')

def test_incomplete_facts_are_zero_none_not_whole_pool_veto():
    base=load_optimizer();tank=copy.deepcopy(base.profiles['1075'].data)
    tank.update(position=100,roleClass='掩护者',functions=dict(reliableVanguard=100,selfSustain=100))
    incomplete=Profile(dict(characterId='partial',displayName='partial',position=300))
    assert not incomplete.ready and incomplete.rating is None
    optimizer=Optimizer({'1075':Profile(tank),'partial':incomplete},dict(base.config))
    assert optimizer.best(('1075','partial'),Context(),battle=True) is not None
    incomplete.position=None
    assert optimizer.best(('1075','partial'),Context(),battle=True).members==('1075',)
    assert optimizer.best(('partial',),Context(),battle=True) is None
    assert optimizer.choose_role(('partial',),('1075',),Context()) is not None
    assert optimizer.best(('1075','unknown'),Context()) is None

def test_strategy_does_not_mutate_cached_facts_and_partial_boss_is_safe():
    base=load_optimizer();rating=base.profiles['1075'].rating
    settings=SimpleNamespace(scoring_overrides={},personal_scores={'1075':99})
    updated=configured_optimizer(settings)
    assert updated.profiles['1075'].rating==99 and base.profiles['1075'].rating==rating
    assert updated is not base and updated.config==base.config
    assert updated.best(('1075','1351','1059'),Context(),battle=True) is not None
    plan=boss_plan(updated,('1075','1351','1059'),Context(boss=True),supplements=False)
    assert len(plan.teams)==1 and len(plan.teams[0].members)==3 and plan.safe_count==1
    with pytest.raises(ValueError):configured_optimizer(dict(scoring_overrides={'minimumReliableVanguard':0}))
    with pytest.raises(ValueError):configured_optimizer(dict(personal_scores={'missing':99}))

def test_unknown_frames_cannot_prove_scroll_edge():
    unknown=dict(stable=True,cards=[dict(trusted=False)],signature='same')
    assert not CharacterNavigation.trusted_content(unknown)
    assert not CharacterNavigation.trusted_content(dict(stable=True,cards=[],signature='same'))

class IntentImages(SavedImages):
    def __init__(self,stages,journal):super().__init__(stages);self.journal=journal;self.intents=[]
    def _intent(self):
        event=self.journal.events()[-1];assert event['state']=='in_flight'
        self.intents.append(dict(operation=event['operation'],stage=self.stage,operation_id=event['operation_id']))
    def click(self,x,y):self._intent();return super().click(x,y)
    def swipe(self,*args):self._intent();return super().swipe(*args)

class OpeningProbe(CustomAction):
    def __init__(self,characters,journal,controller,*,stop=False):
        super().__init__();self.characters,self.journal,self.controller=characters,journal,controller
        self.result={};self.stop=stop
    def run(self,context,argv):
        binding=Binding(123,'offline-characters');seq=[0]
        def stamp(**fields):seq[0]+=1;return dict(verified=True,binding=binding.payload(),frame=seq[0],**fields)
        try:
            with ContextTasker(context) as tasker,self.journal.locked():
                self.characters.tasker=tasker
                tasker._labyrinth_scene_resource=self.scene_resource
                batch=Batch(Settings((Goal(1,1),)),None,self.journal,binding,stopped=lambda:self.stop)
                opening=MaaOpening(tasker,stamp,characters=self.characters)
                result=opening.invite(batch,901,guild_id=1)
                count=len(self.controller.clicks)
                with pytest.raises(BatchStopped,match='opening_already_attempted'):opening.invite(batch,901,guild_id=1)
                assert len(self.controller.clicks)==count
                self.result.update(result=result)
            return True
        except BaseException as error:
            self.result.update(error=type(error).__name__+': '+str(error));return False

@pytest.mark.parametrize('stop',[False,True])
def test_official_fullpack_invitation_feedback_in_one_context(tmp_path,monkeypatch,stop):
    files=['close-departure-reward-after.png','select-1075-after.png','select-1351-after.png',
        'select-1059-after.png','invite-opening-after.png','close-joined-after.png']
    if not all((PRIVATE/f).is_file() for f in files):pytest.skip('private local opening frames absent')
    monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    resource=native.load_resource();journal=Journal(tmp_path/'ledger')
    controller=IntentImages([[native.read_image(PRIVATE/f)] for f in files],journal)
    owner=native.bind(resource,controller);characters=MaaCharacters(owner);trace=[];native_trace=[]
    original_native=native.run
    def traced_native(tasker,entry,**kw):
        value=original_native(tasker,entry,**kw);native_trace.append(dict(entry=entry,receipt=value));return value
    monkeypatch.setattr(native,'run',traced_native)
    original=characters.run
    def traced(*args,**kw):
        value=original(*args,**kw);trace.append(dict(suffix=args[0],observation=value[0],native=value[1]));return value
    characters.run=traced
    from pcr_maa.labyrinth.scene import install_scene
    install_scene(resource)
    probe=OpeningProbe(characters,journal,controller,stop=stop);probe.scene_resource=resource
    assert resource.register_custom_action('CharacterOpeningProbe',probe)
    assert resource.override_pipeline({'CharacterOpeningProbeRoot':dict(recognition='DirectHit',action='Custom',
        custom_action='CharacterOpeningProbe',next=[],pre_delay=0,post_delay=0)})
    try:
        receipt=native.run(owner,'CharacterOpeningProbeRoot')
        (tmp_path/'continuous.json').write_text(json.dumps(dict(root=receipt,probe=probe.result,clicks=controller.clicks,
            swipes=controller.swipes,intents=controller.intents,trace=trace,native_trace=native_trace),ensure_ascii=False,indent=2),encoding='utf-8')
        if stop:
            assert not receipt['native_ok'] and probe.result['error']=='BatchStopped: stopped'
            assert not controller.clicks and not journal.events()
        else:
            assert receipt['native_ok'] and 'error' not in probe.result,probe.result.get('error')
            assert probe.result['result']['roles_joined']==['1059','1075','1351']
            assert len(controller.clicks)==5 and not controller.swipes
            assert [e['operation'] for e in controller.intents]==['opening_select']*3+['opening_invite','opening_close']
            assert all(e['state']=='settled' for e in journal.events()[1::2])
    finally:assert owner.post_stop().wait().succeeded


def _run85_member_frame():
    frame=BASE/'candidate-r28-source/.runtime/labyrinth-work/runs/4aa8a3a109e34e629455b9e906615322/acceptance-ending/screen.png'
    roster=BASE/'evidence/resume-20261006/RUN85_CHARACTER_ENDING_PROBE.json'
    if not frame.is_file() or not roster.is_file():pytest.skip('private run85 saved frame excluded from source')
    return native.read_image(frame),json.loads(roster.read_text(encoding='utf-8'))['roster']


def test_run85_current_member_strip_preserves_all_five_real_identities():
    frame,roster=_run85_member_frame()
    observation=CharacterRecognizer().recognize(frame,'battle',roster=roster)
    assert observation['selected']==['1071','1075','1091','1293','1351']
    assert len(observation['members'])==5 and all(c['trusted'] for c in observation['members'])
    recovered=next(c for c in observation['members'] if c['character_id']=='1293')
    # A rival from the failed border crop must still compete with the fixed hint.
    assert recovered['rival_confidence']>=.5639


def test_run85_official_member_readback_uses_existing_budget_without_input(tmp_path,monkeypatch):
    frame,roster=_run85_member_frame();monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[frame]]);owner=native.bind(native.load_resource(),controller)
    characters=MaaCharacters(owner);batch=Batch(Settings((Goal(1,1),)),SimpleNamespace(),Journal(tmp_path/'ledger'),Binding(123,'offline-run85'))
    try:
        observation,receipt=characters.read(batch,'battle',roster=roster)
        assert observation['selected']==['1071','1075','1091','1293','1351'] and observation['stable'] and receipt['native_ok']
        assert not controller.clicks and not controller.swipes and not batch.journal.events()
    finally:assert native.wait_job(owner.post_stop(),seconds=10).succeeded


def _run147_boss_candidate_frame():
    root=BASE/'evidence/resume-20261009'
    frame=root/'run147-ending.png';params=root/'RUN147_LAST_CHARACTER_PARAMETERS.json'
    if not frame.is_file() or not params.is_file():pytest.skip('private run147 saved frame excluded from source')
    actual=json.loads(params.read_text(encoding='utf-8'))[-1]['params']
    assert actual['role_id']=='1088' and actual['expected_selected']==['1051','1059','1076']
    return native.read_image(frame),actual


def test_run147_boss_star_badge_does_not_alias_another_candidate_as1088():
    frame,params=_run147_boss_candidate_frame()
    actual=CharacterRecognizer().recognize(frame,'boss',roster=params['roster'],current_filter='LIGHT')
    assert actual['selected']==params['expected_selected']
    assert actual['cards'][0]['trusted'] and actual['cards'][0]['character_id']=='1088'
    assert not actual['cards'][2]['trusted'] and actual['cards'][2]['character_id'] is None
    assert all(c['trusted'] for c in actual['members'])


def test_run147_original_toggle_identity_guard_on_saved_frame_sends_no_input(tmp_path,monkeypatch):
    frame,params=_run147_boss_candidate_frame();monkeypatch.setattr(native,'RUNTIME',tmp_path/'native')
    controller=SavedImages([[frame]]);owner=native.bind(native.load_resource(),controller)
    try:
        characters=MaaCharacters(owner)
        for _ in range(2):characters.run('Read',dict(mode='read',page='boss',roster=params['roster']))
        actual,receipt=characters.run('Read',params)
        assert receipt['native_ok'] and actual['stable'] and actual['selected']==params['expected_selected']
        assert actual['cards'][0]['character_id']=='1088' and actual['cards'][0]['trusted']
        assert not controller.clicks and not controller.swipes
    finally:assert native.wait_job(owner.post_stop(),seconds=10).succeeded
