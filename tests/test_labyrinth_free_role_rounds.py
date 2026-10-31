"""Sequential choice imprints are pending until the independent JOINED readback."""
from types import SimpleNamespace as NS

import pytest

from pcr_maa.labyrinth import autoplay, rewards, scene, shop_execution
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.scene import Scene
from test_labyrinth_autoplay import receipt
from test_labyrinth_reward_shop_upstream import harness


class ChoiceImprints:
    def __init__(self, following='next', joined=('1071', '1088')):
        self.following = following
        self.joined = list(joined)
        self.selected = []
        self.invited = []
        self.calls = []

    def read(self, batch, page, **options):
        batch.checkpoint()
        if page == 'joined':
            return dict(roles_joined=self.joined), receipt()
        offered = ['1088', '1089', '1071'] if self.following == 'tail' else ['1071', '1088', '1089']
        if self.invited and self.following not in ('same_cards', 'untrusted_change', 'reordered'):
            offered = [role for role in offered if role not in self.invited]
        if self.invited and self.following == 'reordered':
            offered = list(reversed(offered))
        result = dict(page='opening', stable=self.following != 'unstable', count=len(self.selected),
                    required_count=4 if self.following == 'invalid_count' and self.invited else 1,
                    filter='FIRE' if self.invited and self.following == 'changed_filter' else 'ALL',
                    scrollbar=dict(visible=True, can_scroll=True,
                        position=(.5 if self.following == 'scrolled' else .015 if self.following == 'rebounded' else 0.0)
                            if self.invited else 0.0),
                    selected=self.selected[:], cards=[dict(character_id=role, trusted=True,
                    selected=role in self.selected, box=[80 + 140 * i, 180, 130, 130])
                    for i, role in enumerate(offered)])
        if self.invited and self.following == 'untrusted_change':
            result['cards'][0].update(character_id=None, trusted=False)
        return result, receipt()

    def probe(self, batch, page):
        return None if self.following == 'unreadable' else self.read(batch, page)[0]

    def wait_for(self, batch, page, predicate, **options):
        value, raw = self.read(batch, page)
        assert predicate(value)
        return value, raw

    def run(self, suffix, params, **options):
        self.calls.append((suffix, params))
        if suffix == 'ToggleOnce':
            assert not self.selected and params['role_id'] not in self.invited
            self.selected = [params['role_id']]
        else:
            assert suffix == 'InviteOnce' and params['expected_selected'] == self.selected
            self.invited.extend(self.selected)
            if self.following != 'still_selected':
                self.selected = []
        return None, receipt(action=True)


def setup_rounds(monkeypatch, following='next', joined=('1071', '1088')):
    batch, stamp = harness()
    characters = ChoiceImprints(following, joined)
    optimizer = NS(profiles={role: NS(rating=3-i) for i, role in enumerate(('1071', '1088', '1089'))})
    monkeypatch.setattr(rewards, 'load_optimizer', lambda: optimizer)
    monkeypatch.setattr(rewards, 'configured_optimizer', lambda strategy: optimizer)
    clock = [0.0]
    monkeypatch.setattr(rewards.native, 'active_now', lambda: clock[0])
    monkeypatch.setattr(autoplay, '_poll_pause', lambda *args: clock.__setitem__(0, clock[0]+1))
    real_runtime = scene.MaaSceneRuntime

    class Runtime(real_runtime):
        def __init__(self, tasker, stamp, **options):
            self.tasker = tasker
            self.stamp = stamp

        def observe(self, **options):
            kind = 'joined' if len(characters.invited) == 2 else 'free_role_reward'
            return Scene(kind, receipt(), {})

        def perform(self, *args, **options):
            pytest.fail('a recognized picker must never receive an animation click')

    monkeypatch.setattr(scene, 'MaaSceneRuntime', Runtime)
    actions = autoplay.MaaRunActions.__new__(autoplay.MaaRunActions)
    actions.tasker = NS()
    actions.batch, actions.stamp, actions.characters = batch, stamp, characters
    actions.owned, actions.role_pending, actions.acquisition_active = [], [], False
    return actions, characters


@pytest.mark.parametrize('following', ['next', 'rebounded', 'tail'])
@pytest.mark.parametrize('joined', [('1071', '1088'), ('1088',), ('wrong',)])
def test_two_one_person_imprints_do_not_finish_at_the_first_invitation(monkeypatch, joined, following):
    actions, characters = setup_rounds(monkeypatch, following, joined)
    route, node = NS(enter_id=2765), NS(block_id=30303)
    first = actions.choose_reward('free_role_reward', route, node, '30303:free:1')
    assert first['following_scene'].kind == 'free_role_reward'
    assert actions.role_pending == ['1071'] and not actions.owned
    second = actions.choose_reward('free_role_reward', route, node, '30303:free:2')
    assert second['following_scene'].kind == 'joined'
    assert actions.role_pending == ['1071', '1088'] and not actions.owned
    assert [suffix for suffix, _ in characters.calls] == ['ToggleOnce', 'InviteOnce'] * 2
    assert not any(event['result'].get('inventory_added') for event in actions.batch.journal.events()
                   if event['state'] == 'settled')
    if joined == ('wrong',):
        with pytest.raises(BatchStopped, match='^role_reward_readback_unknown$'):
            actions.accept_joined(route, node)
        assert not actions.owned
    else:
        actions.accept_joined(route, node)
        assert actions.owned == list(joined)
        assert actions.role_pending == ([] if len(joined) == 2 else ['1071'])
        if actions.role_pending:
            with pytest.raises(BatchStopped, match='^role_reward_readback_unknown$'):
                actions.prepare_team(route, node)


@pytest.mark.parametrize('following', ['same_cards', 'still_selected', 'unreadable',
                                      'untrusted_change', 'unstable', 'invalid_count',
                                      'reordered', 'scrolled', 'changed_filter'])
def test_unadvanced_or_unreadable_invitation_never_selects_again(monkeypatch, following):
    actions, characters = setup_rounds(monkeypatch, following)
    with pytest.raises(BatchStopped, match='^role_reward_readback_unknown$'):
        actions.choose_reward('free_role_reward', NS(enter_id=2765), NS(), 'free:1')
    assert [suffix for suffix, _ in characters.calls] == ['ToggleOnce', 'InviteOnce']
    assert not actions.owned and not actions.role_pending


def test_stop_after_invitation_does_not_dispatch_the_second_choice(monkeypatch):
    actions, characters = setup_rounds(monkeypatch)
    actions.batch.stopped = lambda: bool(characters.invited)
    with pytest.raises(BatchStopped, match='^stopped$'):
        actions.choose_reward('free_role_reward', NS(enter_id=2765), NS(), 'free:1')
    assert [suffix for suffix, _ in characters.calls] == ['ToggleOnce', 'InviteOnce']
    assert not actions.owned


def test_same_reward_identity_is_not_invited_again_after_a_fresh_picker(monkeypatch):
    actions, characters = setup_rounds(monkeypatch)
    route = NS(enter_id=2765)
    first = actions.choose_reward('free_role_reward', route, NS(), 'free:1')
    assert first['following_scene'].kind == 'free_role_reward'
    with pytest.raises(BatchStopped, match='^role_reward_already_attempted$'):
        actions.choose_reward('free_role_reward', route, NS(), 'free:1')
    assert [suffix for suffix, _ in characters.calls] == ['ToggleOnce', 'InviteOnce']
    assert actions.role_pending == ['1071'] and not actions.owned


def test_unknown_invitation_is_not_retried_under_another_reward_identity(monkeypatch):
    actions, characters = setup_rounds(monkeypatch)
    send = characters.run
    def uncertain(suffix, params, **options):
        result = send(suffix, params, **options)
        if suffix == 'InviteOnce':
            raise RuntimeError('native input outcome unknown')
        return result
    characters.run = uncertain
    route = NS(enter_id=2765)
    with pytest.raises(RuntimeError, match='native input outcome unknown'):
        actions.choose_reward('free_role_reward', route, NS(), 'free:1')
    with pytest.raises(BatchStopped, match='^blocked_in_flight$'):
        actions.choose_reward('free_role_reward', route, NS(), 'free:2')
    assert [suffix for suffix, _ in characters.calls] == ['ToggleOnce', 'InviteOnce']
    assert actions.batch.journal.events()[-1]['state'] == 'in_flight'


def test_stop_during_the_successor_probe_does_not_start_another_invitation(monkeypatch):
    actions, characters = setup_rounds(monkeypatch)
    probe = characters.probe
    def interrupted(batch, page):
        actual = probe(batch, page)
        batch.stopped = lambda: True
        return actual
    characters.probe = interrupted
    with pytest.raises(BatchStopped, match='^stopped$'):
        actions.choose_reward('free_role_reward', NS(enter_id=2765), NS(), 'free:1')
    assert [suffix for suffix, _ in characters.calls] == ['ToggleOnce', 'InviteOnce']
    assert not actions.owned and not actions.role_pending


def test_shop_choice_imprints_accumulate_pending_roles_until_actual_joined(monkeypatch):
    batch, stamp = harness()
    shop = shop_execution.MaaShop.__new__(shop_execution.MaaShop)
    shop.tasker, shop.stamp, shop.timings = NS(), stamp, batch.timings
    picker, joined, returned = (Scene(kind, receipt(), {}) for kind in ('free_role_reward', 'joined', 'shop'))
    pages, inputs, choices = iter([picker, returned]), [], []
    shop.scene = NS(wait_for=lambda *args, **options: next(pages),
                    perform=lambda value, action: inputs.append(action) or receipt(action=True))

    class Reward:
        def __init__(self, *args, **options):
            pass

        def choose_free(self, batch, enter_id, reward_id, acquired, **options):
            choices.append((reward_id, acquired))
            assert acquired == (('owned',) if len(choices) == 1 else ('owned', '1071'))
            return dict(roles_pending=['1071' if len(choices) == 1 else '1088'],
                        following_scene=picker if len(choices) == 1 else joined)

    monkeypatch.setattr(rewards, 'MaaRoleReward', Reward)
    characters = NS(read=lambda *args, **options: (dict(roles_joined=['1071', '1088']), receipt()))
    result = shop._imprint_following(batch, 2765, 'imprint', ['owned'], characters, dict(choice_imprint=True))
    assert result == ['1071', '1088'] and inputs == ['close_reward']
    assert choices[0][0] != choices[1][0]
