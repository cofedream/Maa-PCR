"""Private original-frame replay through official Maa; never a live controller."""
import json
import os
import time
from pathlib import Path

import numpy as np
import pytest
from maa.custom_action import CustomAction

from pcr_maa import native, schedule_quick
from pcr_maa.context_backend import ContextTasker
from pcr_maa.preset_execution import Journal
from pcr_maa.replay import SavedImages
from pcr_maa.schedule_onekey import ITEMS

PROJECT = Path(os.environ.get('PCR_FEEDBACK_PROJECT', str(Path(__file__).resolve().parents[1])))
FEEDBACK = Path(os.environ.get('PCR_FEEDBACK_FRAME_ROOT', str(PROJECT / '.runtime/player-feedback-20261004-153502/logs/debug/on_error')))
EVENT = PROJECT / '.runtime/pilot-readiness-20261002/live-event-navigation-20261002'
EVENT_PREVIEW = PROJECT / '.runtime/pilot-readiness-20261002/live-event-preview-20261002'
SCHEDULE = PROJECT / 'tests/fixtures'


def frame(path):
    if not path.is_file():
        pytest.skip('Private original frame not available; no device fallback')
    return native.read_image(path)


def popup():
    return frame(FEEDBACK / '2026.10.04-15.24.27.222_EventPageGate.png')


def adventure():
    return frame(FEEDBACK / '2026.10.04-15.26.25.111_OneKeyQuickPrepare.png')


def event_stages():
    return [[popup()], [frame(EVENT / '03c-close-login-receipt/after.png')],
            [frame(EVENT / '04-event-map/after.png')],
            [frame(EVENT_PREVIEW / '04-selection-view/after.png')]]


def setup(stages, *, overrides=None, controller_type=SavedImages):
    resource = native.load_resource()
    if overrides:
        assert resource.override_pipeline(overrides)
    controller = controller_type(stages)
    tasker = native.bind(resource, controller)
    return tasker, controller


def names(receipt):
    return [node['name'] for node in receipt['nodes'] if node['completed']]


def save(label, receipt, controller):
    native.save_receipt(dict(receipt=receipt, clicks=controller.clicks,
                            swipes=controller.swipes, scope='offline original-frame replay'), label)


@pytest.mark.parametrize('entry', ['EventPageGate', 'EventFromEvent', 'EventFromHome'])
def test_original_event_login_popup_closes_once_then_full_entry_is_verified(entry):
    stages = event_stages()
    if entry == 'EventFromHome':
        stages = [[frame(SCHEDULE / 'schedule-onekey-home-before.png')], [adventure()]] + stages
    tasker, controller = setup(stages, overrides={entry: {'timeout': 800}})
    receipt = native.run(tasker, entry)
    save('feedback-event-login', receipt, controller)
    assert receipt['native_ok'] and 'EventListReady' in names(receipt), receipt
    assert names(receipt).count('EventLoginRewardClose') == 1
    assert 'StoryEventOpenQuests' in names(receipt)
    assert controller.clicks[2 if entry == 'EventFromHome' else 0] == [640, 495]
    assert len(controller.clicks) == (5 if entry == 'EventFromHome' else 3) and not controller.swipes
    assert not set(names(receipt)) & {'ExecConfirm', 'EventToggle', 'ExecOpenSweep'}


@pytest.mark.parametrize('masked', ['title', 'notice', 'close', 'unknown'])
def test_login_popup_requires_all_dedicated_anchors_zero_clicks(masked):
    image = popup()
    areas = dict(title=(520, 179, 246, 34), notice=(450, 237, 385, 30),
                 close=(601, 477, 78, 37))
    if masked == 'unknown':
        image[:] = 0
    else:
        x, y, w, h = areas[masked]
        image[y:y+h, x:x+w] = 0
    tasker, controller = setup([[image]], overrides={'EventPageGate': {'timeout': 500}})
    receipt = native.run(tasker, 'EventPageGate')
    save('feedback-event-negative-' + masked, receipt, controller)
    assert 'EventListReady' not in names(receipt)
    assert not controller.clicks and not controller.swipes


def test_close_only_same_named_other_result_cannot_be_clicked():
    image = frame(SCHEDULE / 'schedule-onekey-item-result.png')
    tasker, controller = setup([[image]], overrides={'EventPageGate': {'timeout': 500}})
    receipt = native.run(tasker, 'EventPageGate')
    assert 'EventLoginRewardClose' not in names(receipt)
    assert not controller.clicks and not controller.swipes


def test_event_popup_that_remains_after_close_stops_without_second_click():
    tasker, controller = setup([[popup()]], overrides={
        'EventAfterLoginRewardGate': {'timeout': 500}})
    receipt = native.run(tasker, 'EventPageGate')
    save('feedback-event-stuck-popup', receipt, controller)
    assert 'EventListReady' not in names(receipt)
    assert names(receipt).count('EventLoginRewardClose') == 1
    assert controller.clicks == [[640, 495]] and not controller.swipes


class RejectClick(SavedImages):
    def click(self, x, y):
        self.clicks.append([x, y])
        return False


def test_event_rejected_close_stops_without_retry():
    tasker, controller = setup([[popup()]], controller_type=RejectClick)
    receipt = native.run(tasker, 'EventPageGate')
    assert 'EventListReady' not in names(receipt)
    assert controller.clicks == [[640, 495]] and not controller.swipes


def schedule_stages(available=True):
    return [[adventure()], [frame(SCHEDULE / 'schedule-onekey-home-before.png')],
            [frame(SCHEDULE / ('schedule-onekey-pre-top.png' if available else 'schedule-onekey-post-bottom.png'))]]


def test_original_adventure_schedule_safely_returns_home_then_reads_button():
    tasker, controller = setup(schedule_stages(), overrides={'OneKeyQuickPrepare': {'timeout': 800}})
    receipt = native.run(tasker, 'OneKeyQuickPrepare')
    save('feedback-schedule-adventure', receipt, controller)
    assert receipt['native_ok'] and 'OneKeyQuickReady' in names(receipt), receipt
    assert names(receipt).count('OneKeyQuickAdventureHome') == 1
    assert 'OneKeyQuickHomeReady' in names(receipt)
    assert controller.clicks == [[105, 700], [1017, 575]] and not controller.swipes
    assert 'OneKeyQuickStart' not in names(receipt)


@pytest.mark.parametrize('state', ['unknown', 'event_popup', 'adventure_title_missing', 'home_control_missing', 'dim_modal_background'])
def test_schedule_unknown_or_unanchored_page_has_zero_input(state):
    image = adventure()
    if state == 'unknown':
        image[:] = 0
    elif state == 'event_popup':
        image = popup()
    elif state == 'dim_modal_background':
        image = (image.astype(np.float32) * .65).astype(np.uint8)
    elif state == 'adventure_title_missing':
        image[0:80, 60:200] = 0
    else:
        image[675:720, 35:175] = 0
    tasker, controller = setup([[image]], overrides={'OneKeyQuickPrepare': {'timeout': 500}})
    receipt = native.run(tasker, 'OneKeyQuickPrepare')
    assert not {'OneKeyQuickReady', 'OneKeyQuickUnavailable'} & set(names(receipt))
    assert not controller.clicks and not controller.swipes


def test_schedule_home_not_reached_sends_only_one_guarded_return_click():
    tasker, controller = setup([[adventure()]], overrides={'OneKeyQuickAdventureHome': {'timeout': 500}})
    receipt = native.run(tasker, 'OneKeyQuickPrepare')
    save('feedback-schedule-stuck-adventure', receipt, controller)
    assert 'OneKeyQuickReady' not in names(receipt)
    assert controller.clicks == [[105, 700]] and not controller.swipes


def test_adventure_unavailable_full_schedule_receipt_keeps_no_consumer(tmp_path):
    stages = schedule_stages(available=False)
    stages.append([frame(SCHEDULE / 'schedule-onekey-home.png')])
    tasker, controller = setup(stages)
    args = dict(game_day='2026-10-04', device=dict(serial='emulator-5556',
        source='gui_adb_session_v1', app_package='com.bilibili.priconne'),
        authorizations=ITEMS, invocation_id='feedback-unavailable', journal=Journal(tmp_path/'ledger'))
    report = schedule_quick.schedule_quick(tasker, **args)
    save('feedback-schedule-unavailable', report, controller)
    assert report['outcome'] == 'unavailable' and report['home_verified'], report
    assert not report['one_key_attempted'] and not report['schedule_executed']
    assert controller.clicks == [[105, 700], [1017, 575], [370, 640]] and not controller.swipes
    assert args['journal'].events()[-1]['state'] == 'settled'


def test_two_new_schedule_preparations_in_one_shared_context_rearm_only_navigation():
    stages = schedule_stages() + schedule_stages()
    tasker, controller = setup(stages)
    reports, errors = [], []

    class Borrow(CustomAction):
        def run(self, context, argv):
            try:
                with ContextTasker(context) as facade:
                    pipeline = json.loads((native.RESOURCE / 'pipeline/schedule_quick.json').read_text(encoding='utf-8'))
                    for index in range(2):
                        if index:
                            # Explicit new invocation starts on the next saved adventure;
                            # this is fixture state change, not a game input.
                            controller.stage += 1
                            controller.frame = 0
                        receipt = native.run(facade, 'OneKeyQuickPrepare', reset_hits=tuple(pipeline))
                        reports.append(receipt)
                        assert receipt['native_ok'] and 'OneKeyQuickReady' in names(receipt), receipt
                return True
            except BaseException as exc:
                errors.append(exc)
                return False

    resource = tasker.resource
    callback = Borrow()
    assert resource.register_custom_action('FeedbackBorrow', callback)
    assert resource.override_pipeline({'FeedbackOuter': dict(action='Custom',
        custom_action='FeedbackBorrow', next=[], post_delay=0)})
    outer = native.run(tasker, 'FeedbackOuter')
    if errors:
        raise errors[0]
    assert outer['native_ok'] and len(reports) == 2
    assert controller.clicks == [[105, 700], [1017, 575]] * 2 and not controller.swipes


def test_two_new_event_ports_rearm_navigation_and_preserve_real_story_identity():
    from pcr_maa.agent_callbacks import AgentCallbacks
    from pcr_maa.event_native import NativeEvent
    one = event_stages() + [[frame(EVENT_PREVIEW / '05-checked-list/after.png')]]
    tasker, controller = setup(one + one)
    resource = tasker.resource
    registry = AgentCallbacks()
    registry._ready = True
    for name, route in registry.recognitions.items():
        if name == 'HomeNoKnownLargePanel':
            assert resource.unregister_custom_recognition(name)
        assert resource.register_custom_recognition(name, route)
    for name, route in registry.actions.items():
        assert resource.register_custom_action(name, route)
    reports, errors = [], []

    class Borrow(CustomAction):
        def run(self, context, argv):
            try:
                with registry.scope(context, deadline=time.monotonic()+90,
                        owner_source=lambda original: original is context) as facade:
                    for index in range(2):
                        if index:
                            controller.stage += 1
                            controller.frame = 0
                        port = NativeEvent(facade, start='event')
                        reading = port.read()
                        reports.append(reading)
                        assert reading['event_verified'] and reading['list_complete'], reading
                        assert len(reading['rows']) == 3
                        assert reading['event_identity']['layout'] == 'story_event_h'
                        assert names(reading['navigation']).count('EventLoginRewardClose') == 1
                return True
            except BaseException as exc:
                errors.append(exc)
                return False

    callback = Borrow()
    assert resource.register_custom_action('FeedbackEventBorrow', callback)
    assert resource.override_pipeline({'FeedbackEventOuter': dict(action='Custom',
        custom_action='FeedbackEventBorrow', next=[], post_delay=0)})
    outer = native.run(tasker, 'FeedbackEventOuter')
    save('feedback-event-two-context-ports', dict(outer=outer, reports=reports), controller)
    if errors:
        raise errors[0]
    assert outer['native_ok'] and len(reports) == 2
    assert controller.clicks.count([640, 495]) == 2
    assert len(controller.clicks) == 8 and not controller.swipes


def test_failed_event_port_cannot_rearm_or_redispatch_close_on_second_read():
    from pcr_maa.event_native import NativeEvent
    tasker, controller = setup([[popup()]], overrides={
        'EventAfterLoginRewardGate': {'timeout': 500}})
    port = NativeEvent(tasker, start='event')
    first = port.read()
    before = list(controller.clicks)
    second = port.read()
    assert not first['event_verified'] and not second['event_verified']
    assert second['incomplete_reason'] == 'event_entry_already_attempted_no_retry'
    assert before == controller.clicks == [[640, 495]] and not controller.swipes
