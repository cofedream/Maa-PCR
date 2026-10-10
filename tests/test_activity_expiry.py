"""Activity lifecycle and leftover-page regression, offline Maa only."""
import os
import time
from pathlib import Path
import numpy as np
import pytest
from maa.custom_action import CustomAction
from pcr_maa import native
from pcr_maa.replay import SavedImages
from pcr_maa.context_backend import ContextTasker
from pcr_maa.agent_callbacks import AgentCallbacks
from pcr_maa.abyss import execute_abyss, assess_home
from pcr_maa.event import execute_event
from pcr_maa.abyss_native import NativeAbyss
from pcr_maa.event_native import NativeEvent
from pcr_maa.preset_execution import Journal
from pcr_maa.game_day import current_game_day
from pcr_maa.supported_daily import _status

PROJECT = Path(os.environ.get('PCR_ACTIVITY_PROJECT', str(Path(__file__).resolve().parents[1])))
PLAYER = PROJECT / '.runtime/codex-takeover-20260930/product-verification-20261001/player-candidate-checkbox-font/player'
DEVICE = dict(serial='emulator-5556', source='gui_adb_session_v1', app_package='com.bilibili.priconne')


def frame(path):
    if not path.is_file():
        pytest.skip('Private original frame missing; no live fallback')
    return native.read_image(path)


def adventure():
    return frame(PLAYER / 'debug/on_error/2026.10.07-13.40.12.78_AbyssFromHome.png')


def leftover():
    return frame(PLAYER / 'debug/on_error/2026.10.07-13.42.58.332_EventClosedHomeNoticeRead.png')


def home():
    return frame(PROJECT / 'tests/fixtures/schedule-onekey-home-before.png')


def setup(stages, overrides=None):
    resource = native.load_resource()
    if overrides:
        assert resource.override_pipeline(overrides)
    controller = SavedImages(stages)
    return native.bind(resource, controller), controller


def borrowed(tasker, action):
    resource = tasker.resource
    registry = AgentCallbacks()
    registry._ready = True
    for name, route in registry.recognitions.items():
        if name == 'HomeNoKnownLargePanel':
            assert resource.unregister_custom_recognition(name)
        assert resource.register_custom_recognition(name, route)
    for name, route in registry.actions.items():
        assert resource.register_custom_action(name, route)
    errors = []
    class Run(CustomAction):
        def run(self, context, argv):
            try:
                with registry.scope(context, deadline=time.monotonic()+90,
                        owner_source=lambda original: original is context) as facade:
                    action(facade)
                return True
            except BaseException as exc:
                errors.append(exc)
                return False
    assert resource.register_custom_action('ExpiryTest', Run())
    assert resource.override_pipeline({'ExpiryTest':dict(action='Custom',custom_action='ExpiryTest',next=[],post_delay=0),
                                      'AfterExpiry':dict(action='DoNothing',next=[],post_delay=0)})
    receipt = native.run(tasker, 'ExpiryTest')
    if errors:
        raise errors[0]
    assert receipt['native_ok'], receipt


@pytest.mark.parametrize('start', ['home', 'adventure'])
def test_original_missing_abyss_skips_returns_home_and_context_continues(tmp_path, start):
    stages = [[adventure()], [home()]]
    if start == 'home':
        stages.insert(0, [home()])
    tasker, controller = setup(stages)
    journal = Journal(tmp_path / 'ledger')
    def action(facade):
        result = execute_abyss(NativeAbyss(facade, start=start), journal,
            game_day=current_game_day(), context='gui-current-state', execute=True, device=DEVICE)
        assert result['outcome'] == 'skipped_event_unavailable', result
        assert _status('abyss', result) == 'skipped_event_unavailable'
        assert result['home_verified'] and not result['consumption_sent']
        assert journal.events() == []
        assert native.run(facade, 'AfterExpiry')['native_ok']
        native.save_receipt(result, 'expiry-original-abyss-'+start)
    borrowed(tasker, action)
    assert controller.clicks == ([[718,681]] if start=='home' else []) + [[105,700]]
    assert not controller.swipes


def test_original_leftover_adventure_event_enters_and_reads_existing_event():
    event = PROJECT / '.runtime/pilot-readiness-20261002/live-event-navigation-20261002'
    preview = PROJECT / '.runtime/pilot-readiness-20261002/live-event-preview-20261002'
    popup = PROJECT / '.runtime/player-feedback-20261004-153502/logs/debug/on_error/2026.10.04-15.24.27.222_EventPageGate.png'
    stages = [[leftover()], [home()], [adventure()], [frame(popup)],
              [frame(event/'03c-close-login-receipt/after.png')],
              [frame(event/'04-event-map/after.png')],
              [frame(preview/'04-selection-view/after.png')],
              [frame(preview/'05-checked-list/after.png')]]
    tasker, controller = setup(stages)
    def action(facade):
        result = NativeEvent(facade,start='home').read()
        assert result['event_verified'] and result['list_complete'],result
        assert len(result['rows']) == 3 and result['event_identity']['layout']=='story_event_h'
        assert native.run(facade, 'AfterExpiry')['native_ok']
        native.save_receipt(result, 'expiry-original-event-leftover')
    borrowed(tasker, action)
    assert controller.clicks[:2] == [[105,700],[718,681]]
    assert not controller.swipes


def test_synthetic_expired_event_skips_and_context_continues(tmp_path):
    image = adventure()
    # Explicit synthetic event-entry removal; original gameplay evidence stays unchanged.
    image[470:615,470:620] = 0
    tasker, controller = setup([[home()],[image],[home()]])
    journal=Journal(tmp_path/'ledger')
    def action(facade):
        result=execute_event(NativeEvent(facade,start='home'),journal,
            game_day=current_game_day(),context='gui-current-state',execute=True)
        assert _status('event',result)=='skipped_event_unavailable',result
        assert result['home_verified'] and not result['consumption_sent'] and journal.events()==[]
        assert native.run(facade,'AfterExpiry')['native_ok']
        native.save_receipt(result,'expiry-synthetic-event')
    borrowed(tasker,action)
    assert controller.clicks==[[718,681],[105,700]] and not controller.swipes


@pytest.mark.parametrize('fault',['blank','dim','title','home_control'])
def test_unknown_or_obscured_adventure_never_skips_or_clicks(fault):
    from pcr_maa.activity_navigation import absent_entry
    image=adventure()
    if fault=='blank': image[:]=0
    elif fault=='dim': image=(image.astype(np.float32)*.65).astype(np.uint8)
    elif fault=='title': image[0:80,60:210]=0
    else: image[675:720,40:175]=0
    tasker,controller=setup([[image]],{'AbyssAdventureGate':{'timeout':500}})
    with absent_entry(tasker,'abyss'):
        result=native.run(tasker,'AbyssAdventureGate')
    assert not any(n['name']=='AbyssUnavailable' and n['completed'] for n in result['nodes'])
    assert not controller.clicks and not controller.swipes


def test_present_event_is_not_reported_closed():
    from pcr_maa.activity_navigation import absent_entry
    tasker,controller=setup([[adventure()]],{'EventUnavailable':{'timeout':500}})
    with absent_entry(tasker,'event'):
        result=native.run(tasker,'EventUnavailable')
    assert not result['native_ok'] and not controller.clicks


def test_failed_return_home_does_not_report_safe_skip(tmp_path):
    tasker,controller=setup([[home()],[adventure()]],{'ReturnHomeGate':{'timeout':500}})
    result=execute_abyss(NativeAbyss(tasker),Journal(tmp_path/'ledger'),
        game_day=current_game_day(),context='gui-current-state',execute=True,device=DEVICE)
    assert result['outcome']=='closed_home_unverified' and _status('abyss',result)=='stopped'
    assert controller.clicks==[[718,681],[105,700]]


def test_old_unknown_blocks_before_availability_navigation(tmp_path):
    journal=Journal(tmp_path/'ledger')
    with journal.locked():
        journal.append(dict(state='in_flight',scope='abyss-outpost',intent_id='original-unknown',
                            operation='abyss_outpost',game_day=current_game_day(),device=DEVICE))
    original=journal.path.read_bytes()
    class NoNavigation:
        def read(self): raise AssertionError('An unresolved consumer must block before navigation')
    result=execute_abyss(NoNavigation(),journal,game_day=current_game_day(),
                         context='gui-current-state',execute=True,device=DEVICE)
    assert result['outcome']=='blocked_in_flight' and journal.path.read_bytes()==original


def test_absence_requires_positive_closed_page_and_no_rows():
    closed=dict(native_ok=True,closed_verified=True,availability='closed',rows=[])
    assert assess_home(closed)=='skipped_event_unavailable'
    for key,value in [('native_ok',False),('closed_verified',False),('availability','unknown'),('rows',[{}])]:
        assert assess_home(dict(closed,**{key:value}))=='reading_unknown'


@pytest.mark.parametrize('reader', ['entry', 'home'])
@pytest.mark.parametrize('failure', [RuntimeError, KeyError])
def test_native_callback_fault_is_a_defined_miss_with_traceback(reader, failure, capsys):
    from types import SimpleNamespace
    from pcr_maa.activity_navigation import ActivityEntryAbsent, ActivityHomePage
    class BrokenContext:
        def run_recognition(self, *args):
            raise failure('activity_native_fault')
    callback = ActivityEntryAbsent('abyss') if reader=='entry' else ActivityHomePage()
    assert callback.analyze(BrokenContext(),SimpleNamespace(image=np.zeros((720,1280,3),dtype=np.uint8))) is None
    assert 'activity_native_fault' in capsys.readouterr().err
