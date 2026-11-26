"""The user-confirmed 90-second rule is bound to one live first-pause readback."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace as NS

import numpy as np
import pytest

from pcr_maa import clan_battle as battle, clan_battle_native as native
from pcr_maa.clan_battle_characters import ClanBattleCharacters
from test_clan_battle import Controls, plan
from test_clan_battle_native import Registry


def make_reader(plan, *, start=90, kind='simulation', owner='live-owner', valid=True, policy=None):
    reader = object.__new__(ClanBattleCharacters)
    reader.team = list(plan['unit_ids'])
    lease = NS(valid=valid)
    reader.begin_opening('actual-owner-start', owner_source=owner, control_policy=policy,
        start_seconds=start, action_kind=kind, owner_valid=lambda: lease.valid)
    reader.opening_started = True
    return reader, lease


def pause(plan, *, remaining=85, auto=True):
    image = np.zeros((720, 1280, 3), np.uint8)
    state = dict(unit_ids=list(plan['unit_ids']), sets=[True]*5, auto=auto, battle_speed=2,
        paused=True, same_frame=True, source='maa', remaining_seconds=remaining,
        frame_pixel_sha256=hashlib.sha256(image.tobytes()).hexdigest())
    return state, image


@pytest.mark.parametrize('remaining', range(85, 91))
@pytest.mark.parametrize('kind', ['simulation', 'regular'])
def test_user_rule_covers_all_roles_before_124_even_when_auto_and_set_enabled(plan, remaining, kind):
    reader, _ = make_reader(plan, kind=kind)
    state, image = pause(plan, remaining=remaining)
    state.update(damage=123456, tp=[10, 20, 30, 40, 50])
    result = reader.bind_paused_opening(state, 'opening_read', image=image)
    proof = result['first_ub_evidence']
    assert proof['source'] == battle.USER_CONFIRMED_OPENING_RULE
    assert proof['basis'] == 'user_confirmed_game_rule'
    assert proof['action_kind'] == kind and proof['start_seconds'] == 90
    assert proof['owner_bound'] is proof['first_pause'] is True
    assert proof['not_fired_unit_ids'] == plan['unit_ids']
    assert result['source'] == 'maa'
    assert result['first_ub_observation']['coverage'] == 'unknown'
    battle.require_first_ub(result, plan['unit_ids'])


@pytest.mark.parametrize('remaining', [84, 83, 80, 1, 0, 91, True])
def test_at_or_after_124_or_invalid_clock_cannot_claim_the_rule(plan, remaining):
    reader, _ = make_reader(plan)
    state, image = pause(plan, remaining=remaining)
    result = reader.bind_paused_opening(state, 'opening_read', image=image)
    assert 'first_ub_evidence' not in result
    with pytest.raises(RuntimeError, match='first_ub_unverified'):
        battle.require_first_ub(result, plan['unit_ids'])


@pytest.mark.parametrize('start,kind', [(34, 'carryover'), (90, 'carryover'), (34, 'regular'),
    (None, 'simulation'), (True, 'simulation'), (90, None), (90, 'unknown')])
def test_short_or_carryover_and_unknown_start_kind_remain_excluded(plan, start, kind):
    reader, _ = make_reader(plan, start=start, kind=kind)
    state, image = pause(plan)
    if kind == 'carryover':
        state.update(formal=True, intent_id=reader.opening_token)
    result = reader.bind_paused_opening(state, 'opening_read', image=image)
    assert 'first_ub_evidence' not in result


@pytest.mark.parametrize('failure', ['owner_missing', 'owner_invalid', 'owner_changed', 'not_started',
    'not_paused', 'different_frame', 'not_same_frame', 'wrong_team', 'unknown_source', 'no_image'])
def test_unknown_owner_or_first_readback_never_grants_the_rule(plan, failure):
    reader, lease = make_reader(plan)
    state, image = pause(plan)
    if failure == 'owner_missing': reader.opening_owner_source = None
    elif failure == 'owner_invalid': lease.valid = False
    elif failure == 'owner_changed': reader.opening_owner_source = 'another-owner'
    elif failure == 'not_started': reader.opening_started = False
    elif failure == 'not_paused': state['paused'] = False
    elif failure == 'different_frame': state['frame_pixel_sha256'] = 'a'*64
    elif failure == 'not_same_frame': state['same_frame'] = False
    elif failure == 'wrong_team': state['unit_ids'][-1] = 999999
    elif failure == 'unknown_source': state['source'] = 'unknown'
    else: image = None
    result = reader.bind_paused_opening(state, 'opening_read', image=image)
    assert 'first_ub_evidence' not in result


@pytest.mark.parametrize('change', ['owner', 'token', 'kind', 'start'])
def test_first_pause_proof_cannot_survive_binding_change(plan, change):
    reader, lease = make_reader(plan)
    state, image = pause(plan)
    result = reader.bind_paused_opening(deepcopy(state), 'opening_read', image=image)
    battle.require_first_ub(result, plan['unit_ids'])
    if change == 'owner': lease.valid = False
    elif change == 'token': reader.opening_token = 'another-start'
    elif change == 'kind': reader.opening_action_kind = 'carryover'
    else: reader.opening_start_seconds = 34
    assert reader.bind_paused_opening(deepcopy(state), 'opening_read', image=image) is None


def test_later_earlier_looking_frame_does_not_replace_the_actual_first_pause(plan):
    reader, _ = make_reader(plan)
    state, image = pause(plan, remaining=84)
    result = reader.bind_paused_opening(state, 'opening_read', image=image)
    assert 'first_ub_evidence' not in result
    assert reader.bind_paused_opening(dict(state, remaining_seconds=85), 'opening_read', image=image) is None


@pytest.mark.parametrize('field,value', [('opening_start_token','other-start'), ('opening_owner_bound',False),
    ('opening_action_kind','carryover'), ('opening_start_seconds',34), ('same_frame',False), ('paused',False),
    ('opening_initial_frame_pixel_sha256','a'*64), ('frame_pixel_sha256','bad'), ('remaining_seconds',84)])
def test_consumer_rejects_changed_bound_rule_state(plan, field, value):
    reader, _ = make_reader(plan)
    state, image = pause(plan)
    result = reader.bind_paused_opening(state, 'opening_read', image=image)
    result[field] = value
    with pytest.raises(RuntimeError, match='first_ub_unverified'):
        battle.require_first_ub(result, plan['unit_ids'])


def test_actual_witnessed_ub_still_blocks_the_role(plan):
    reader, _ = make_reader(plan)
    state, image = pause(plan)
    result = reader.bind_paused_opening(state, 'opening_read', image=image)
    result['first_ub_observation']['fired_unit_ids'] = [plan['unit_ids'][0]]
    with pytest.raises(RuntimeError, match='first_ub_already_fired'):
        battle.require_first_ub(result, [plan['unit_ids'][0]])
    battle.require_first_ub(result, plan['unit_ids'][1:])


def test_older_independent_controls_proof_still_works_at_124(plan):
    policy = dict(source='owner_account_bound_settings_readback', same_frame=True,
        auto_release_on_ub=False, frame_pixel_sha256='b'*64)
    reader, _ = make_reader(plan, policy=policy)
    state, image = pause(plan, remaining=84, auto=False)
    state['sets'] = [False]*5
    result = reader.bind_paused_opening(state, 'opening_read', image=image)
    assert result['first_ub_evidence']['source'] == 'controls_disabled_since_start'
    battle.require_first_ub(result, plan['unit_ids'])


@pytest.mark.parametrize('initial_auto,desired_auto', [(True, True), (True, False), (False, True)])
def test_auto_and_set_enabled_differences_use_individual_readbacks(plan, initial_auto, desired_auto):
    plan['auto'] = desired_auto
    plan['set_by_unit'] = dict(zip(map(str, plan['unit_ids']), [False, False, True, True, False]))
    reader, _ = make_reader(plan)
    state, image = pause(plan, auto=initial_auto)
    state = reader.bind_paused_opening(state, 'opening_read', image=image)
    port = Controls(plan)
    port.calibration = dict(version=2, auto_battle=dict(speeds=[1, 2, 4]))
    port.opening_paused, port.battle_speed, port.state = True, 2, state
    evidence = battle.execute_switches(port, plan)
    assert [row for row in port.actions if row[0] == 'set'] == [
        ('set', plan['unit_ids'][i], True, False) for i in (0, 1, 4)]
    auto_actions = [row for row in port.actions if row[0] == 'auto']
    assert auto_actions == ([] if initial_auto == desired_auto else [('auto', initial_auto, desired_auto)])
    if initial_auto != desired_auto:
        assert port.actions[-1 if desired_auto else 0][0] == 'auto'
    assert evidence['settings_verified'] is True
    assert evidence['observation']['remaining_seconds'] == 85
    assert evidence['observation']['first_ub_evidence'] == state['first_ub_evidence']


def test_arm_passes_actual_start_kind_and_validates_the_same_owner(plan, tmp_path, monkeypatch):
    port = native.NativeClanBattle(NS(resource=Registry()), tmp_path)
    port.plan, port._account_binding = plan, ('account', 'connection')
    calls = []
    port.characters = NS(begin_opening=lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(port, 'freeze_opening_control_policy', lambda _: None)
    monkeypatch.setattr(port, 'account_binding_valid', lambda _: True)
    monkeypatch.setattr(port, '_start_opening_capture', lambda _: None)
    port.arm_opening_start('formal-intent', start_seconds=90, action_kind='carryover')
    args, kwargs = calls[0]
    assert args == ('formal-intent',)
    assert kwargs['start_seconds'] == 90 and kwargs['action_kind'] == 'carryover'
    assert kwargs['owner_source'] == ('account', 'connection') and kwargs['owner_valid']() is True
    port._account_binding = ('account', 'new-connection')
    assert kwargs['owner_valid']() is False


@pytest.mark.parametrize('kind,seconds', [('regular', 90), ('carryover', 34), ('carryover', 90)])
def test_formal_caller_passes_actual_action_kind_and_seconds(plan, tmp_path, monkeypatch, kind, seconds):
    from datetime import datetime, timezone
    from pcr_maa import clan_battle_daily_native as daily, clan_battle_calibration
    from pcr_maa.game_day import current_game_day
    from test_clan_formal_observed_scope import scoped_owner
    now = datetime.now(timezone.utc)
    port, target = scoped_owner()
    plan = dict(plan, **target)
    port.action = dict(kind=kind, start_seconds=seconds, boss=plan['boss'],
        phase='C' if kind == 'regular' else 'B')
    port.pending, port.plan, port.now = None, plan, lambda: now
    team = dict(unit_ids=plan['unit_ids'], stars=plan['stars'], support_unit_id=None, support_owner_viewer_id=None)
    port.read_team = lambda: deepcopy(team)
    port.page = NS(require_team=lambda _: None)
    monkeypatch.setattr(battle, 'require_timing', lambda *_: None)
    monkeypatch.setattr(clan_battle_calibration, 'load_calibration', lambda *_a, **_k: port.calibration)
    monkeypatch.setattr(daily, 'require_daily_execution', lambda *_, **__: None)
    class ArmReached(Exception): pass
    calls = []
    def arm(token, **kwargs):
        calls.append((token, kwargs))
        raise ArmReached()
    port.arm_opening_start = arm
    intent = dict(intent_id='same-daily-intent', action=deepcopy(port.action), game_day=current_game_day(now),
        ready=dict(unit_ids=plan['unit_ids'], stars=plan['stars'], support_unit=None, support_owner_viewer_id=None))
    with pytest.raises(ArmReached): port._execute_once(intent, plan)
    assert calls == [('same-daily-intent', dict(start_seconds=seconds, action_kind=kind))]
