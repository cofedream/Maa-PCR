import pytest
from pcr_maa.guide_trial_battle import battle_pipeline, finish_battle, return_to_stage, prepare_retry, ensure_running_settings


class Port:
    def __init__(self, choices=(), result='GTWinObserved', transitions=()):
        self.choices = iter(choices)
        self.transitions = iter(transitions)
        self.result = result
        self.calls = []
        self.tasker = self
        self.resource = self

    def override_pipeline(self, nodes):
        self.nodes = nodes

    def run(self, name, *, click=False):
        self.calls.append((name, click))
        return {'node': name}

    def choose(self, names):
        value = next(self.choices)
        assert value in names or value == 'GTUnknown'
        return value

    def wait(self, names, **kwargs):
        if 'GTAllSet' in names and len(names) > 1:
            value=next(self.transitions)
            assert value in names
            return value
        if self.result in names:
            return self.result
        return names[0]

    def form_team(self):
        self.calls.append(('form_team', False))


def test_readonly_settings_can_be_checked_repeatedly():
    for node in battle_pipeline().values():
        if node['action'] == 'DoNothing':
            assert 'max_hit' not in node
        else:
            assert node['max_hit'] == 1


def test_pause_requires_menu_and_battle_timer_in_same_frame():
    nodes=battle_pipeline()
    assert nodes['GTPauseBattle']['all_of']==['GTBattleMenu','GTBattleTimer']


@pytest.mark.parametrize('timer,ready',[('1:30',False),('1：30',False),('1:29',True),('1 : 27',True),('0:59',True),('2:30',False)])
def test_opening_animation_is_not_an_interactive_battle(timer,ready):
    import re
    assert bool(re.fullmatch(battle_pipeline()['GTBattleTimer']['expected'],timer)) is ready


@pytest.mark.parametrize('result,outcome', [('GTWinObserved','victory'),
                                         ('GTDefeat','defeat')])
def test_confirm_settings_before_resuming_and_read_actual_result(result, outcome):
    port = Port(['GTAllSet','GTAutoOff'], result)
    report = finish_battle(port,paused=True)
    assert report['outcome'] == outcome and report['result_verified'] and report['settings_verified']
    assert port.calls == [('GTEnableAuto',True),
                          ('GTFullSettings',False), ('GTResumeBattle',True), (result,False)]


def test_partial_set_is_enabled_and_checked_before_resume():
    port = Port(['GTSetOn','GTAutoOn'], transitions=['GTSetOff','GTSetDisabled','GTAllSet'])
    finish_battle(port, paused=True)
    assert port.calls[:4] == [('GTDisableSet',True), ('GTEnableSet',True),
                             ('GTEnableSetFromDisabled',True), ('GTFullSettings',False)]


def test_real_cn_off_disabled_on_sequence_requires_all_five_set_marks():
    port=Port(['GTSetOff','GTAutoOn'],transitions=['GTSetDisabled','GTAllSet'])
    finish_battle(port,paused=True)
    assert port.calls[:3]==[('GTEnableSet',True),('GTEnableSetFromDisabled',True),('GTFullSettings',False)]


def test_three_mode_cycle_without_all_set_stops_without_resume():
    port=Port(['GTSetOff'],transitions=['GTSetDisabled','GTSetOn','GTSetOff'])
    with pytest.raises(RuntimeError,match='all_set_not_verified'):finish_battle(port,paused=True)
    assert len(port.calls)==3 and not any(name=='GTResumeBattle' for name,_ in port.calls)


def test_unchanged_mode_after_click_cannot_resume():
    port=Port(['GTSetOff'])
    original=port.wait
    def wait(names,**kwargs):
        if 'GTAllSet' in names:
            assert 'GTSetOff' not in names
            raise RuntimeError('guide_trial_page_timeout')
        return original(names,**kwargs)
    port.wait=wait
    with pytest.raises(RuntimeError):finish_battle(port,paused=True)
    assert port.calls==[('GTEnableSet',True)]


def test_unknown_settings_cannot_resume():
    port = Port(['GTUnknown'])
    original = port.wait
    def wait(names, **kwargs):
        if names == ['GTAllSet']:
            raise RuntimeError('guide_trial_page_timeout')
        return original(names, **kwargs)
    port.wait = wait
    with pytest.raises(RuntimeError): finish_battle(port, paused=True)
    assert not any(click for _, click in port.calls)


def test_disconnect_does_not_create_known_result_or_retry():
    port = Port(['GTAllSet','GTAutoOn'], 'LabyDisconnect6002Observed')
    with pytest.raises(RuntimeError, match='result_unknown'):
        finish_battle(port,paused=True)
    assert not any('Retry' in name or 'End' in name for name,_ in port.calls)


def test_unknown_return_page_never_clicks():
    port = Port(['GTUnknown'])
    with pytest.raises(RuntimeError, match='return_page_unknown'): return_to_stage(port)
    assert port.calls == []


def test_known_defeat_returns_before_preparing_next_team():
    port = Port(['GTDefeat'])
    prepare_retry(port, {})
    assert port.calls == [('GTDefeatEnd',True), ('GTOpenStage',True), ('GTChallenge',True), ('form_team',False)]
    assert all(name != 'GTBattleStart' for name,_ in port.calls)


@pytest.mark.parametrize('initial', ['GTWinObserved','GTRewardsObserved'])
def test_win_rewards_page_is_confirmed_before_next(initial):
    port = Port([initial])
    def wait(names, **kwargs):
        return 'GTRewardsObserved' if 'GTRewardsObserved' in names else names[0]
    port.wait = wait
    return_to_stage(port)
    expected = [('GTWinNext',True)] if initial == 'GTWinObserved' else []
    assert port.calls == expected + [('GTRewardsNext',True)]


def test_unknown_after_win_does_not_send_another_next():
    port = Port(['GTWinObserved'])
    def wait(names, **kwargs): raise RuntimeError('guide_trial_page_timeout')
    port.wait = wait
    with pytest.raises(RuntimeError): return_to_stage(port)
    assert port.calls == [('GTWinNext',True)]


class RunningPort(Port):
    def __init__(self, states, result='GTWinObserved'):
        super().__init__(result=result)
        self.states=iter(states)
        self.overrides=[]

    def read_battle_settings(self, receipt=None):
        return next(self.states)

    def override_pipeline(self, nodes):
        self.overrides.append(nodes)


def running(auto=True,slots=(True,)*5,button=True):
    return dict(auto=auto,sets=list(slots),set_button=button)


def test_already_enabled_needs_no_pause_or_toggle():
    port=RunningPort([running()])
    result=finish_battle(port)
    assert result['settings_verified'] and result['settings_evidence']['source']=='battle_hud'
    assert port.calls==[('GTWinObserved',False)]


def test_auto_is_enabled_before_any_set_change():
    port=RunningPort([running(False,(False,)*5,False),running(True,(False,)*5,False),running()])
    assert ensure_running_settings(port)['set_slots']==[True]*5
    assert port.calls==[('GTBattleEnableAuto',True),('GTBattleEnableAllSet',True)]


@pytest.mark.parametrize('before,target',[(False,True),(True,False)])
def test_only_selected_portrait_changes_and_auto_stays_on(before,target):
    slots=[True]*5;slots[2]=before;desired=slots.copy();desired[2]=target
    port=RunningPort([running(slots=slots),running(slots=desired)])
    result=ensure_running_settings(port,desired)
    assert result['auto'] and result['set_slots']==desired
    assert port.calls==[('GTBattleToggleSet',True)]
    assert port.overrides[-1]['GTBattleToggleSet']['custom_recognition_param']==dict(mode='battle_set',slot=2,previous=before)


@pytest.mark.parametrize('state',[running(None),running(slots=(True,None,True,True,True)),running(slots=(True,)*4)])
def test_unknown_controls_never_click(state):
    port=RunningPort([state])
    with pytest.raises(RuntimeError,match='settings_unknown'):ensure_running_settings(port)
    assert not port.calls


@pytest.mark.parametrize('after',[running(False),running(slots=(True,False,True,True,True))])
def test_readback_must_preserve_auto_and_other_slots(after):
    port=RunningPort([running(False),after])
    with pytest.raises(RuntimeError,match='readback_unknown'):ensure_running_settings(port)
    assert port.calls==[('GTBattleEnableAuto',True)]


def test_unchanged_set_after_click_never_toggles_again():
    before=running(slots=(True,False,True,True,True))
    port=RunningPort([before,before])
    with pytest.raises(RuntimeError,match='readback_unknown'):ensure_running_settings(port)
    assert port.calls==[('GTBattleToggleSet',True)]


def test_invalid_requested_slots_do_not_read_or_click():
    for desired in ((True,)*4,(True,True,1,True,True)):
        port=RunningPort([])
        with pytest.raises(ValueError,match='selection_invalid'):ensure_running_settings(port,desired)
        assert not port.calls


def test_rejected_action_parameters_stop_before_input():
    port=RunningPort([running(False)])
    port.override_pipeline=lambda _:False
    with pytest.raises(RuntimeError,match='pipeline_rejected'):ensure_running_settings(port)
    assert not port.calls
