"""Offline reward control checks; fake receipts do not prove live recognition."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pcr_maa import native, rewards


@pytest.fixture
def reward_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    calls, state = [], dict(fault=None, ready=False, close_state='Empty')
    image = np.zeros((9, 16, 3), dtype=np.uint8)
    tasker = SimpleNamespace(controller=SimpleNamespace(cached_image=image,
        post_screencap=lambda: SimpleNamespace(done=True, succeeded=True, get=lambda: image)))

    def run(tasker, entry, *, reset_hits=()):
        calls.append(entry)
        if state['fault'] == (entry, calls.count(entry)):
            return dict(native_ok=False, nodes=[])
        if entry in ('RewardsMissionsCloseStart', 'RewardsGiftsCloseStart'):
            prefix = entry.removesuffix('CloseStart')
            names = [prefix + 'Close']
            if state['close_state'] is not None:
                names.extend(prefix + name for name in state['close_state'].split(','))
            return dict(native_ok=True, nodes=[dict(name=name, completed=True) for name in names])
        node = {'RewardsHomeRead': 'ObserveHome',
                'RewardsGiftsAfterClaim': 'RewardsGiftsResult'}.get(entry)
        if entry in ('RewardsMissionsRead', 'RewardsGiftsRead'):
            prefix = entry.removesuffix('Read')
            node = prefix + ('Ready' if state['ready'] and prefix + 'ClaimStart' not in calls else 'Empty')
        node = node or entry.removesuffix('Start').removesuffix('Read')
        return dict(native_ok=True, nodes=[dict(name=node, completed=True)])

    monkeypatch.setattr(native, 'run', run)
    return tasker, calls, state


@pytest.mark.parametrize('execute', [False, True])
def test_complete_pass_reuses_only_adjacent_verified_home(reward_pass, execute):
    tasker, calls, _ = reward_pass
    report = rewards.claim_rewards(tasker, execute=execute)
    assert report['outcome'] == 'complete' and report['home_verified']
    assert calls.count('RewardsHomeRead') == 3
    gift = report['items']['gifts']
    assert gift['start_home_reused_from'] == 'missions'
    assert [r['entry'] for r in gift['native_receipts']].count('RewardsHomeRead') == 1
    mission_image = Path(report['items']['missions']['evidence']['missions-home'])
    gift_image = Path(gift['evidence']['gifts-start-home'])
    assert mission_image.read_bytes() == gift_image.read_bytes()
    index = calls.index('RewardsGiftsOpenStart')
    assert calls[index - 2:index] == ['RewardsMissionsHomeStart', 'RewardsHomeRead']


@pytest.mark.parametrize('only', ['missions', 'gifts'])
def test_single_module_still_reads_home_twice(reward_pass, only):
    tasker, calls, _ = reward_pass
    report = rewards.claim_rewards(tasker, execute=True, only=only)
    assert report['outcome'] == 'complete'
    assert calls.count('RewardsHomeRead') == 2
    assert 'start_home_reused_from' not in report['items'][only]


def test_new_call_does_not_reuse_previous_home(reward_pass):
    tasker, calls, _ = reward_pass
    for _ in range(2):
        start = len(calls)
        report = rewards.claim_rewards(tasker, execute=True)
        assert report['outcome'] == 'complete'
        assert calls[start] == 'RewardsHomeRead'
        assert calls[start:].count('RewardsHomeRead') == 3


@pytest.mark.parametrize('fault', [('RewardsMissionsHomeStart', 1), ('RewardsHomeRead', 2)])
def test_failed_mission_home_never_opens_gifts(reward_pass, fault):
    tasker, calls, state = reward_pass
    state['fault'] = fault
    report = rewards.claim_rewards(tasker, execute=True)
    assert report['outcome'] == 'unknown' and not report['home_verified']
    assert not report['items']['missions']['home_verified']
    assert 'gifts' not in report['items'] and 'RewardsGiftsOpenStart' not in calls


def test_failed_mission_home_evidence_never_opens_gifts(reward_pass, monkeypatch):
    tasker, calls, _ = reward_pass
    write_png = native.write_png
    monkeypatch.setattr(native, 'write_png',
        lambda path, image: False if path.name == 'missions-home.png' else write_png(path, image))
    report = rewards.claim_rewards(tasker, execute=True)
    assert report['outcome'] == 'unknown' and not report['items']['missions']['home_verified']
    assert 'RewardsGiftsOpenStart' not in calls


def test_changed_home_must_pass_gift_action_guard(reward_pass):
    tasker, calls, state = reward_pass
    state['fault'] = ('RewardsGiftsOpenStart', 1)
    report = rewards.claim_rewards(tasker, execute=True)
    assert report['items']['missions']['home_verified']
    gift = report['items']['gifts']
    assert report['outcome'] == 'unknown' and gift['start_home_reused_from'] == 'missions'
    assert not gift['claim_attempted'] and not gift['result_verified']
    assert calls[-1] == 'RewardsGiftsOpenStart'
    assert not any('Stamina' in entry or 'GiftsClaim' in entry for entry in calls)


@pytest.mark.parametrize('fault', [('RewardsGiftsAfterClaim', 1),
    ('RewardsGiftsResultRead', 1), ('RewardsGiftsCloseStart', 1)])
def test_unknown_gift_claim_never_resends(reward_pass, fault):
    tasker, calls, state = reward_pass
    state.update(ready=True, fault=fault)
    report = rewards.claim_rewards(tasker, execute=True)
    assert report['outcome'] == 'unknown'
    assert report['items']['gifts']['claim_attempted']
    assert calls.count('RewardsGiftsClaimStart') == 1
    assert 'RewardsGiftsHomeStart' not in calls


@pytest.mark.parametrize('close_state', [None, 'Ready,Empty'])
def test_closed_but_unknown_or_conflicting_state_never_returns_home_or_opens_next(reward_pass, close_state):
    tasker, calls, state = reward_pass
    state.update(ready=True, close_state=close_state)
    report = rewards.claim_rewards(tasker, execute=True)
    assert report['outcome'] == 'unknown'
    item = report['items']['missions']
    assert item['result_verified'] and item['claim_attempted']
    assert item['claimable_after'] is None and item['status'] == 'unknown'
    assert calls.count('RewardsMissionsRead') == calls.count('RewardsMissionsClaimStart') == 1
    assert calls[-1] == 'RewardsMissionsCloseStart'
    assert 'RewardsMissionsHomeStart' not in calls and 'gifts' not in report['items']


@pytest.mark.parametrize('only,close_state,status', [('missions', 'Empty', 'claimed'),
    ('missions', 'Ready', 'remaining'), ('gifts', 'StaminaDeferred', 'deferred_stamina')])
def test_post_claim_uses_actual_close_receipt_and_cached_frame(reward_pass, monkeypatch, only, close_state, status):
    tasker, calls, state = reward_pass
    state.update(ready=True, close_state=close_state)
    monkeypatch.setattr(tasker.controller, 'post_screencap', lambda: pytest.fail('extra screenshot'))
    original_run = native.run
    final_image = np.full((720, 1280, 3), 127, dtype=np.uint8)
    def close_frame(tasker, entry, **kwargs):
        receipt = original_run(tasker, entry, **kwargs)
        if entry.endswith('CloseStart'):
            tasker.controller.cached_image = final_image
        return receipt
    monkeypatch.setattr(native, 'run', close_frame)
    report = rewards.claim_rewards(tasker, execute=True, only=only)
    item = report['items'][only]
    assert report['outcome'] == 'complete' and item['status'] == status
    prefix = 'Rewards' + only.title()
    assert calls.count(prefix + 'Read') == 1
    after_image = native.read_image(item['evidence'][only + '-after'])
    assert np.array_equal(after_image, final_image)
    close_receipt = next(r['receipt'] for r in item['native_receipts'] if r['entry'] == prefix + 'CloseStart')
    assert rewards.completed(close_receipt, prefix + close_state)


def test_gift_open_retains_current_home_guard():
    pipeline = json.loads((native.RESOURCE / 'pipeline/rewards.json').read_text(encoding='utf-8'))
    assert 'RewardsGiftsOpen' in pipeline['RewardsGiftsOpenStart']['next']
    assert 'RewardsHomeGiftsGuard' in pipeline['RewardsGiftsOpen']['all_of']
    assert 'ObserveHome' in pipeline['RewardsHomeGiftsGuard']['all_of']


def test_native_gift_open_rejects_changed_frame(tmp_path, monkeypatch):
    from pcr_maa.replay import SavedImages

    monkeypatch.setattr(native, 'ROOT', tmp_path)
    resource = native.load_resource()
    # A short test-only failure budget; production guards and delays stay unchanged.
    assert resource.override_pipeline({'RewardsGiftsOpenStart': {'timeout': 200}})
    controller = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    receipt = native.run(native.bind(resource, controller), 'RewardsGiftsOpenStart')
    assert not rewards.completed(receipt, 'RewardsGiftsOpen')
    assert controller.clicks == []
