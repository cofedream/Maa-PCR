"""Confirmed-off join animation must not spend blind taps on transition frames.

Once the client is known to have 角色加入动画 = 不播放, `unknown` frames in an
acquisition wait are page transitions, not skippable animation.  An explicit
`character_animation` classification still re-enables the skip driver so a
mid-run change of the setting cannot stall the wait.
"""
from types import SimpleNamespace as NS

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import autoplay, batch as batch_module, owned_runtime, scene
from pcr_maa.labyrinth.batch import Batch, Binding
from test_labyrinth_autoplay import receipt
from test_labyrinth_reward_shop_upstream import Journal, harness


def observe_wait(monkeypatch, flag, pages, *, source='role_reward'):
    batch, stamp = harness()
    batch.animation_setting_off = flag
    clock = [0.0]
    performs = []

    def observe(**kwargs):
        kind, cost = pages.pop(0)
        clock[0] += cost
        return scene.Scene(kind, receipt(), {})

    def perform(current, action):
        performs.append(action)
        clock[0] += 2.0
        return receipt(action=True)

    runtime = NS(tasker=NS(), stamp=stamp, observe=observe, perform=perform)
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *a: clock.__setitem__(0, clock[0] + .5))
    monkeypatch.setattr('pcr_maa.native.active_now', lambda: clock[0])
    token = native.RUN_DEADLINE.set(float('inf'))
    try:
        result = scene.MaaSceneRuntime.wait_for(runtime, batch, {'joined'}, source=source,
            timeout=20, reason='role_reward_readback_unknown',
            acquisition_fields=dict(source='maa', enter_id=1))
    finally:
        native.RUN_DEADLINE.reset(token)
    return result, performs


def test_confirmed_off_unknown_transition_is_not_clicked(monkeypatch):
    result, performs = observe_wait(monkeypatch, True,
        [('unknown', 2.0), ('joined', 2.0)])
    assert result.kind == 'joined'
    assert performs == []


def test_unconfirmed_unknown_transition_keeps_the_old_driver(monkeypatch):
    result, performs = observe_wait(monkeypatch, False,
        [('unknown', 2.0), ('joined', 2.0)])
    assert result.kind == 'joined'
    assert performs == ['advance_character_animation']


def test_confirmed_off_still_skips_a_real_animation_frame(monkeypatch):
    # The setting was changed after the confirmation: explicit animation frames
    # must still be skipped instead of timing out.
    result, performs = observe_wait(monkeypatch, True,
        [('character_animation', 2.0), ('joined', 2.0)])
    assert result.kind == 'joined'
    assert performs == ['advance_character_animation']


def test_confirmed_off_still_advances_a_late_link_entrance(monkeypatch):
    result, performs = observe_wait(monkeypatch, True,
        [('character_animation', 2.0), ('joined', 2.0)], source='character_animation')
    assert result.kind == 'joined'
    assert performs == ['advance_character_animation']


def test_batch_carries_the_confirmed_off_fact():
    binding = Binding(1, 'animation-gate')
    default = Batch(NS(goals=()), NS(), Journal(), binding)
    assert default.animation_setting_off is False
    explicit = Batch(NS(goals=()), NS(), Journal(), binding, animation_setting_off=True)
    assert explicit.animation_setting_off is True


@pytest.mark.parametrize('status,expected', [
    ('fixed', True), ('already_off', True),
    ('no_root', False), ('unknown', False), ('write_failed', False), (None, False),
])
def test_owned_runtime_seeds_the_flag_from_the_pref_result(monkeypatch, status, expected):
    captured = {}

    class FakeBatch:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
        def run(self):
            return dict(outcome='complete')

    monkeypatch.setattr(batch_module, 'Batch', FakeBatch)
    runtime = owned_runtime.OwnedRuntime.__new__(owned_runtime.OwnedRuntime)
    runtime.request = NS(settings=NS(goals=()), keep_old_run=False)
    runtime.port = NS()
    runtime.stop = NS()
    runtime._progress = lambda value: None
    runtime._log = lambda text: None
    runtime.animation_setting_status = status
    runtime._run_batch(None, None)
    assert captured['animation_setting_off'] is expected
