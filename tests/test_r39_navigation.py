"""Synthetic routing and boundary evidence, never real device acceptance."""
from copy import deepcopy

import pytest

from pcr_maa import preset_execution, supported_daily
from pcr_maa.game_day import current_game_day
from pcr_maa.preset_scan import key


PRESET = dict(kind='very_hard', slot=2, label='test', target_per_stage=3)


def reading(remaining=3):
    return dict(native_ok=True, requested_slot=2, selected_slot=2, list_complete=True,
                view='checked', observation=dict(page='presets', selected_slot=2),
                ocr_receipt=dict(native_ok=True), rows=[dict(title='test-1', category='主线关卡',
                    difficulty='高难', checked=True, limit=3, remaining=remaining)])


class Port:
    def __init__(self, directory, *, remaining=3):
        self.journal = preset_execution.Journal(directory)
        self.before = reading(remaining)
        self.calls = []
        self.settled = True
        self.boundary = reading(0)
        self.count = 3

    def read(self, slot):
        self.calls.append('read')
        return reading(0) if 'submit' in self.calls else deepcopy(self.before)

    def quote(self, before):
        self.calls.append('quote')
        return dict(verified=True, slot=2, identities=[list(key(row)) for row in before['rows']],
                    counts=[self.count], stamina_before=200, stamina_after=140)

    def confirm(self, before, quote):
        self.calls.append('confirm')
        return dict(verified=True)

    def submit(self, quote):
        self.calls.append('submit')
        return dict(consumption_dispatched=True, result_verified=self.settled,
                    preset_return_verified=self.settled)

    def home(self):
        self.calls.append('home')
        return True

    def verify_preset(self, slot):
        self.calls.append('verify_preset')
        return deepcopy(self.boundary)

    def read_count(self, slot):
        self.calls.append('read_count')
        return self.count

    def fit_count(self, slot, count):
        self.calls.append('fit_count')
        self.count = count
        return dict(verified=True)


def execute(port, **options):
    return preset_execution.execute_one(port, PRESET, 'user-reported-unreset', port.journal,
        execute=True, game_day=current_game_day(), **options)


@pytest.mark.parametrize('exit_page', ['home', 'preset'])
def test_success_reports_actual_boundary(tmp_path, exit_page):
    port = Port(tmp_path)
    result = execute(port, exit_page=exit_page)
    assert result['outcome'] == 'executed' and result['settlement_verified']
    assert result['home_verified'] is (exit_page == 'home')
    assert result['boundary_verified'] is True
    assert result['boundary_page'] == ('home' if exit_page == 'home' else 'presets')
    assert port.calls[-1] == ('home' if exit_page == 'home' else 'verify_preset')
    assert supported_daily._status('very_hard', result, exit_page=exit_page) == 'executed'
    if exit_page == 'preset':
        assert supported_daily._status('very_hard', result) == 'stopped'
    perf = result['performance']
    assert sum(perf['port_calls'].values()) == len(port.calls)
    assert perf['elapsed_seconds'] >= sum(perf['phase_seconds'].values())
    assert perf['native_counters'] == dict(screenshots=None, ocr=None, inputs=None)


@pytest.mark.parametrize('fault', ['native', 'ocr', 'page', 'slot', 'selected', 'missing'])
def test_unverified_intermediate_boundary_stops(tmp_path, fault):
    port = Port(tmp_path)
    if fault == 'native':
        port.boundary['native_ok'] = False
    elif fault == 'ocr':
        port.boundary['ocr_receipt']['native_ok'] = False
    elif fault == 'page':
        port.boundary['observation']['page'] = 'home'
    elif fault == 'slot':
        port.boundary['observation']['selected_slot'] = 1
    elif fault == 'selected':
        port.boundary['selected_slot'] = 1
    else:
        port.boundary.pop('observation')
    result = execute(port, exit_page='preset')
    assert result['outcome'] == 'executed_boundary_failed'
    assert result['settlement_verified'] and result['home_verified'] is False
    assert result['boundary_verified'] is False and 'home' not in port.calls
    assert supported_daily._status('very_hard', result, exit_page='preset') == 'stopped'


def test_unknown_consumption_never_navigates_or_reads_boundary(tmp_path):
    port = Port(tmp_path)
    port.settled = False
    result = execute(port, exit_page='preset')
    assert result['outcome'] == 'unknown_settlement'
    assert port.calls == ['read', 'quote', 'confirm', 'submit']
    assert port.journal.events()[-1]['state'] == 'in_flight'


def test_stop_never_enters_intermediate_boundary(tmp_path):
    port = Port(tmp_path)
    def stopped(*args):
        port.calls.append('stop')
        raise KeyboardInterrupt('stop')
    port.submit = stopped
    result = execute(port, exit_page='preset')
    assert result['outcome'] == 'unknown_settlement'
    assert 'home' not in port.calls and 'verify_preset' not in port.calls
    assert port.journal.events()[-1]['state'] == 'in_flight'


def test_count_restore_precedes_boundary_read(tmp_path):
    port = Port(tmp_path, remaining=2)
    result = execute(port, exit_page='preset', fit_remaining=True)
    assert result['outcome'] == 'executed' and result['count_fit']['restored'] is True
    assert port.count == 3
    assert port.calls[-3:] == ['fit_count', 'read_count', 'verify_preset']


def test_goal_met_keeps_verified_preset_without_consumption(tmp_path):
    port = Port(tmp_path, remaining=0)
    result = execute(port, exit_page='preset')
    assert result['outcome'] == 'skipped_goal_met'
    assert port.calls == ['read', 'verify_preset'] and not result['consumption_sent']


def test_invalid_exit_page_has_no_input(tmp_path):
    port = Port(tmp_path)
    with pytest.raises(ValueError, match='invalid_preset_exit_page'):
        execute(port, exit_page='other')
    assert not port.calls and not tmp_path.joinpath('transactions.jsonl').exists()


def test_default_exit_remains_home(tmp_path):
    port = Port(tmp_path)
    result = execute(port)
    assert result['exit_page'] == 'home' and result['home_verified'] is True
    assert port.calls[-1] == 'home' and 'verify_preset' not in port.calls


def config():
    return dict(version=1, start='home', count_context=None,
                device=dict(input_display=2, screenshot_display='123'),
                presets=[dict(kind=kind, slot=slot, label=kind, target_per_stage=target)
                         for kind, slot, target in [('deep', 4, 10), ('very_hard', 2, 3),
                                                   ('hard', 6, 3), ('normal', 7, 1)]])


def result_for(name, cfg, exit_page):
    result = dict(outcome='executed', consumption_sent=True, settlement_verified=True,
                  home_verified=exit_page == 'home')
    if name in supported_daily.PRESET_STEPS:
        preset = next(p for p in cfg['presets'] if p['kind'] == name)
        slot = preset['slot']
        result.update(preset=preset, exit_page=exit_page, boundary_verified=True,
                      boundary_page='presets' if exit_page == 'preset' else 'home',
                      boundary_readback=dict(native_ok=True, requested_slot=slot, selected_slot=slot,
                          ocr_receipt=dict(native_ok=True), observation=dict(page='presets', selected_slot=slot)))
    if name in supported_daily.REWARD_STEPS:
        result.update(outcome='complete', items={kind: dict(status='no_claimable',
            home_verified=True, claim_attempted=False) for kind in ('missions', 'gifts')})
    return result


@pytest.mark.parametrize('selection,exits', [
    (['deep', 'very_hard', 'hard', 'normal'], ['preset', 'preset', 'preset', 'home']),
    (['normal', 'hard', 'deep'], ['preset', 'preset', 'home']),
    (['hard', 'rewards', 'deep', 'very_hard'], ['home', 'home', 'preset', 'home']),
    (['deep'], ['home']),
])
def test_adjacency_preserves_user_order_and_home_group_end(tmp_path, monkeypatch, selection, exits):
    cfg = config()
    before = deepcopy(cfg)
    calls = []
    def step(tasker, name, cfg, day, context, *, exit_page='home'):
        calls.append((name, exit_page))
        return result_for(name, cfg, exit_page)
    monkeypatch.setattr(supported_daily, '_run_step', step)
    report = supported_daily.execute_supported(None, cfg, execute=True,
        game_day=current_game_day(), context='user-reported-unreset', selected_modules=selection,
        rewards_final=False, directory=tmp_path)
    assert report['outcome'] == 'complete'
    assert calls == list(zip(selection, exits)) and cfg == before
    assert supported_daily._read_report(tmp_path) == report
    perf = report['performance']
    assert perf['module_elapsed_seconds'] == sum(perf['module_seconds'].values())
    assert perf['elapsed_seconds'] >= perf['module_elapsed_seconds']
    assert perf['orchestration_seconds'] >= 0


@pytest.mark.parametrize('disabled,next_active,expected', [('hard', False, 'home'), ('hard', True, 'preset')])
def test_disabled_tail_does_not_leave_group_on_preset(disabled, next_active, expected):
    cfg = config()
    next(p for p in cfg['presets'] if p['kind'] == disabled)['target_per_stage'] = 0
    plan = ('deep', disabled) + (('normal',) if next_active else ())
    assert supported_daily._preset_exit_page(plan, 0, cfg) == expected


@pytest.mark.parametrize('fault', ['unknown', 'wrong_slot', 'missing_boundary', 'stop'])
def test_failed_intermediate_step_truncates_tail(tmp_path, monkeypatch, fault):
    calls = []
    def step(tasker, name, cfg, day, context, *, exit_page='home'):
        calls.append(name)
        if fault == 'stop':
            raise KeyboardInterrupt('stop')
        result = result_for(name, cfg, exit_page)
        if fault == 'unknown':
            result.update(outcome='unknown_settlement', settlement_verified=False)
        elif fault == 'wrong_slot':
            result['boundary_readback']['observation']['selected_slot'] = 1
        else:
            result['boundary_verified'] = False
        return result
    monkeypatch.setattr(supported_daily, '_run_step', step)
    report = supported_daily.execute_supported(None, config(), execute=True,
        game_day=current_game_day(), context='user-reported-unreset', selected_modules=['deep', 'hard'],
        rewards_final=False, directory=tmp_path)
    assert calls == ['deep'] and report['steps'][1]['status'] == 'not_run'
    assert report['outcome'] in ('stopped', 'interrupted')
    assert supported_daily._read_report(tmp_path) == report


def test_boundary_only_is_not_accepted_for_rewards():
    cfg = config()
    result = result_for('hard', cfg, 'preset')
    assert supported_daily._status('rewards', result, exit_page='preset') == 'stopped'


def test_stop_request_at_verified_preset_boundary_sends_no_tail(tmp_path, monkeypatch):
    calls = []
    def step(tasker, name, cfg, day, context, *, exit_page='home'):
        calls.append(name)
        supported_daily._atomic_json(tmp_path / 'stop-request.json',
            dict(version=1, run_id=tmp_path.name, requested_at=1))
        return result_for(name, cfg, exit_page)
    monkeypatch.setattr(supported_daily, '_run_step', step)
    report = supported_daily.execute_supported(None, config(), execute=True,
        game_day=current_game_day(), context='user-reported-unreset', selected_modules=['deep', 'hard'],
        rewards_final=False, directory=tmp_path)
    assert report['outcome'] == 'stopped_by_request'
    assert calls == ['deep'] and report['steps'][1]['status'] == 'not_run'
    assert report['steps'][0]['result']['boundary_page'] == 'presets'
    assert report['steps'][0]['result']['home_verified'] is False
    assert supported_daily._read_report(tmp_path) == report
