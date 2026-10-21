"""Battle polling short-circuits recognition while retaining result evidence."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa.labyrinth import autoplay, scene
from test_labyrinth_recognition_perf_r50 import SceneContext
from test_labyrinth_upstream_scene_reuse import Batch, Runtime, actions, owner, receipt


def recognize(keys, *, scoped):
    context = SceneContext(keys)
    context.observer.specs = scene._anchors()
    result = context.observer.analyze(context, NS(image=np.zeros((720, 1280, 3), np.uint8),
        custom_recognition_param=dict(probe_kinds=list(scene._RULES)) if scoped else {}))
    return result.detail, context.calls


def test_battle_poll_uses_all_page_rules(monkeypatch):
    class Poll(Runtime):
        def observe(self, timeout=scene.OBSERVATION_TIMEOUT_SECONDS, *, probe_kinds=None):
            assert set(probe_kinds or ()) == set(scene._RULES)
            return super().observe(timeout)

    runtime = Poll([scene.Scene('battle', receipt(), {}), scene.Scene('battle_victory', receipt(), {})])
    monkeypatch.setattr(autoplay.native, 'run', lambda *a, **kw: receipt())
    result = actions(runtime, Batch(), owner()).start_battle(NS(enter_id=7), NS(block_id=101, block_type=2))
    assert result['outcome'] == 'won' and runtime.reads == 2


@pytest.mark.parametrize('key', ['BATTLE_RESULT_BOSS_SUMMARY_NEXT_BUTTON', 'BATTLE_RESULT_NEXT_BUTTON'])
@pytest.mark.parametrize('ocr_next', [False, True])
def test_unknown_boss_result_keeps_original_control(key, ocr_next):
    keys = {key} | ({'NEXT'} if ocr_next else set())
    full, _ = recognize(keys, scoped=False)
    actual, _ = recognize(keys, scoped=True)
    assert full['kind'] == actual['kind'] == 'unknown'
    assert actual['payload']['controls']['next'] == full['payload']['controls']['next']
    node = NS(block_type=8)
    previous = autoplay._owned_boss_summary_scene(scene.Scene('unknown', receipt(), full['payload']), node)
    following = autoplay._owned_boss_summary_scene(scene.Scene('unknown', receipt(), actual['payload']), node)
    assert previous is not None and following is not None
    assert actual['payload']['controls']['next']['anchor'] == ('NEXT' if ocr_next else key)
    assert autoplay._owned_boss_summary_scene(scene.Scene('unknown', receipt(), actual['payload']), NS(block_type=2)) is None


@pytest.mark.parametrize('name', list(scene._RULES) + ['date_changed', 'disconnected'])
@pytest.mark.parametrize('battle_background', [False, True])
def test_short_circuit_retains_all_classification_conflicts(name, battle_background):
    keys = set(scene._RULES[name][0]) if name in scene._RULES else {dict(date_changed='DATE', disconnected='DISCONNECT')[name]}
    if battle_background:
        keys.update(scene._RULES['battle'][0])
    full, full_calls = recognize(keys, scoped=False)
    actual, calls = recognize(keys, scoped=True)
    assert actual['kind'] == full['kind']
    assert actual['payload']['global_scene_complete']
    assert {'LabySceneWord_DATE', 'LabySceneWord_DISCONNECT'} <= set(calls)
    assert len(calls) < len(full_calls)
    if full['kind'] in ('battle_victory', 'boss_summary'):
        assert actual['payload']['controls']['next'] == full['payload']['controls']['next']


def test_battle_has_bounded_recognition_work_without_result_claim():
    full, full_calls = recognize(scene._RULES['battle'][0], scoped=False)
    actual, calls = recognize(scene._RULES['battle'][0], scoped=True)
    assert actual['kind'] == full['kind'] == 'battle'
    assert len(calls) < len(full_calls) // 2
    assert actual['payload']['clear_verified'] is False
