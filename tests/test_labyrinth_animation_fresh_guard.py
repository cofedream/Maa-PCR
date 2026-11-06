"""Owned animations must recheck the current frame before their one-shot input."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.labyrinth import autoplay, scene, shop_execution as shops
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.timing import NativeTimings
from pcr_maa.replay import SavedImages
from test_labyrinth_autoplay import make, receipt, route_for
from test_labyrinth_recognition_perf_r50 import SceneContext
from test_labyrinth_reward_shop_upstream import harness


_RESULT_NEXT = ('BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON', 'BATTLE_RESULT_NEXT_BUTTON')


@pytest.mark.parametrize('action,initial,following,target', [
    (action, 'unknown', following, target)
    for action, target in [('advance_character_animation', None),
                           ('advance_final_unknown', [640, 520, 1, 1])]
    for following in ['map', 'move_confirmation', 'unknown']
] + [
    ('advance_character_animation', 'event_animation', following, None)
    for following in ['map', 'move_confirmation']
] + [
    ('advance_final_unknown', 'unknown', following, [640, 520, 1, 1])
    for following in _RESULT_NEXT
])
def test_animation_input_checks_the_fresh_scene(tmp_path, monkeypatch, action, initial, following, target):
    previous = SceneContext(scene._RULES[initial][0] if initial != 'unknown' else ())
    # A result's Next alone is still classified unknown; the final guard must reject it.
    current = SceneContext((following,) if following in _RESULT_NEXT else
        (*scene._RULES[following][0], 'AREA') if following != 'unknown' else ())
    original = scene.SceneRecognition.analyze

    def recognize(self, context, argv):
        # Only primitive recognitions are synthetic; keep the production scene guard.
        context = current if argv.image[0, 0, 0] else previous
        return original(context.observer, context, argv)

    monkeypatch.setattr(scene.SceneRecognition, 'analyze', recognize)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'native')
    resource = native.load_resource()
    controller = SavedImages([[np.zeros((720, 1280, 3), np.uint8)],
                              [np.full((720, 1280, 3), 37, np.uint8)]])
    owner = native.bind(resource, controller)
    runtime = scene.MaaSceneRuntime(owner)
    collector = NativeTimings(owner, controller, runtime.timings)
    try:
        stale = runtime.observe()
        assert stale.kind == initial
        controller.stage = 1
        observed = original(current.observer, current,
            NS(image=controller.stages[1][0], custom_recognition_param={}))
        assert observed.detail['kind'] == ('unknown' if following in _RESULT_NEXT else following)
        sent = True
        try:
            result = runtime.perform(stale, action, target=target)
            assert result['native_ok']
        except native.NativeInputNotSent as error:
            sent = False
            assert error.receipt['input_execution'] == 'not_sent'
            assert error.receipt['native_evidence']['recognition_failed'] is True
            assert error.receipt['native_evidence']['action_started'] is False
        assert controller.clicks == ([[640, 520]] if following == 'unknown' else [])
        assert sent is (following == 'unknown')
        assert not controller.swipes
    finally:
        assert native.wait_job(owner.post_stop(), seconds=10).succeeded
        collector.close()


def failed_animation(status):
    if status == 'uncertain':
        return RuntimeError('Native input outcome unknown; no retry')
    return native.NativeInputNotSent(dict(input_execution='not_sent',
        native_evidence=dict(entry='LabySceneActionOnce', finished=True,
            recognition_failed=status == 'proven_unsent', action_started=False)))


@pytest.mark.parametrize('status', ['proven_unsent', 'uncertain', 'unproven_unsent'])
def test_autoplay_animation_recovery_preserves_input_evidence(tmp_path, status):
    route = route_for([1, 5])
    runner, batch, runtime, _, _ = make(tmp_path, route, [('joined', {})])
    runner.route = route
    runner.cursor = autoplay.RunCursor(index=1)
    runner.actions.enter_id = route.enter_id
    attempts = []

    def perform(current, action):
        attempts.append(action)
        raise failed_animation(status)

    runtime.perform = perform
    source = scene.Scene('event_animation', receipt(), {})
    if status == 'proven_unsent':
        following = runner._do(batch, route, source, 'advance_character_animation', {'joined'})
        assert following.kind == 'joined' and runner.last_scene is following
        assert runtime.reads == 1
    else:
        expected = RuntimeError if status == 'uncertain' else BatchStopped
        with pytest.raises(expected) as caught:
            runner._do(batch, route, source, 'advance_character_animation', {'joined'})
        assert type(caught.value) is expected
        assert runtime.reads == 0
    assert attempts == ['advance_character_animation']
    event = batch.journal.events()[-1]
    assert event['state'] == ('in_flight' if status == 'uncertain' else 'settled')
    if status != 'uncertain':
        assert event['result']['input_execution'] == 'not_sent'
        assert event['result']['input_action_verified'] is False


@pytest.mark.parametrize('status', ['proven_unsent', 'uncertain', 'unproven_unsent'])
def test_shop_animation_recovery_keeps_joined_and_input_evidence(status):
    batch, stamp = harness()
    shop = shops.MaaShop.__new__(shops.MaaShop)
    shop.stamp, shop.tasker, shop.timings = stamp, object(), NS()
    pages = iter(['event_animation', 'joined', 'shop'])
    reads, attempts = [], []

    def wait_for(*args, **kwargs):
        kind = next(pages)
        reads.append(kind)
        return scene.Scene(kind, receipt(), {})

    def perform(current, action):
        attempts.append(action)
        if action == 'advance_character_animation':
            raise failed_animation(status)
        assert current.kind == 'joined' and action == 'close_reward'
        return receipt(action=True)

    shop.scene = NS(wait_for=wait_for, perform=perform)
    characters = NS(read=lambda *a, **kw: (dict(roles_joined=['confirmed']), receipt()))
    selected = dict(choice_imprint=False, role_class=None)
    if status == 'proven_unsent':
        assert shop._imprint_following(batch, 1, 'imprint', [], characters, selected) == ['confirmed']
        assert reads == ['event_animation', 'joined', 'shop']
        assert attempts == ['advance_character_animation', 'close_reward']
    else:
        expected = RuntimeError if status == 'uncertain' else BatchStopped
        with pytest.raises(expected) as caught:
            shop._imprint_following(batch, 1, 'imprint', [], characters, selected)
        assert type(caught.value) is expected
        assert reads == ['event_animation'] and attempts == ['advance_character_animation']
    events = [event for event in batch.journal.events()
              if event['operation'] == 'shop_imprint_advance_character_animation']
    assert len(events) == (1 if status == 'uncertain' else 2)
    assert events[-1]['state'] == ('in_flight' if status == 'uncertain' else 'settled')
    if status != 'uncertain':
        assert events[-1]['result']['input_execution'] == 'not_sent'
        assert events[-1]['result']['input_action_verified'] is False
