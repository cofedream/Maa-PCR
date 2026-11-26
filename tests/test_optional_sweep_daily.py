"""Exercise a GUI-shaped config through the daily executor and its result summary."""
import pytest

from pcr_maa import optional_sweep_native, supported_daily
from pcr_maa.daily import validate_config
from test_gui_adb_schedule import config as gui_config


def test_zero_surveys_run_without_count_context_or_a_device_action(tmp_path):
    surveys = ['temple_survey', 'sanctum_survey']
    config = gui_config(tmp_path)
    config['investigations'] = {name: dict(stage=2, daily_target=0) for name in surveys}
    report = supported_daily.execute_supported(None, config, game_day='', context='',
        selected_modules=surveys, rewards_final=False, execute=True, directory=tmp_path / 'report')
    assert report['outcome'] == 'complete'
    assert [step['name'] for step in report['steps']] == surveys
    assert all(step['status'] == 'skipped_disabled' for step in report['steps'])
    assert all(not step['result']['consumption_sent'] for step in report['steps'])


@pytest.mark.parametrize('module,stage,state,expected', [
    ('sanctum_survey', 2, 'locked', 'skipped_not_cleared'),
    ('temple_survey', 4, 'locked', 'skipped_not_cleared'),
    ('tower_cloister', None, 'closed', 'skipped_event_unavailable'),
])
def test_selection_routes_to_named_port_and_run_summary(tmp_path, monkeypatch, module, stage, state, expected):
    from pcr_maa import native
    opened = []
    class Port:
        def __init__(self, tasker, name, chosen_stage, *, game_day):
            assert game_day == supported_daily.current_game_day()
            opened.append((name, chosen_stage))

        def read(self):
            return dict(module=module, stage=stage, state=state)

        def home(self):
            return dict(native_ok=True)

    monkeypatch.setattr(native, 'ROOT', tmp_path)
    monkeypatch.setattr(optional_sweep_native, 'NativeOptionalSweep', Port)
    config = gui_config(tmp_path)
    if stage:
        config['investigations'] = {module: dict(stage=stage, daily_target=3)}
    folder = tmp_path / 'report'
    report = supported_daily.execute_supported(None, config, game_day='', context='gui-current-state',
        selected_modules=[module], rewards_final=False, execute=True, directory=folder)
    assert opened == [(module, stage)]
    assert report['outcome'] == 'complete'
    assert supported_daily._read_report(folder)['steps'][0]['status'] == expected
    assert supported_daily.OPTIONAL_SWEEPS[module] in supported_daily.summary(report, player=True)


def test_selected_survey_requires_explicit_settings_and_valid_types(tmp_path):
    config = gui_config(tmp_path)
    with pytest.raises(ValueError, match='调查'):
        supported_daily.validate(config, execute=True, game_day='', context='gui-current-state',
                                 selected_modules=['sanctum_survey'])
    for key, value in [('stage', True), ('stage', 0), ('daily_target', '3'), ('daily_target', -1)]:
        with pytest.raises(ValueError, match='调查'):
            validate_config(config | {'investigations': {'sanctum_survey': {'stage': 1, 'daily_target': 3, key: value}}})


def test_goal_above_free_daily_limit_is_reported_as_partial(tmp_path):
    from pcr_maa.preset_execution import Journal
    from test_optional_sweeps import SweepScreen, run
    port = SweepScreen(remaining=3, limit=5)
    journal = Journal(tmp_path / 'ledger')
    report = run(port, journal, target=6)
    assert port.submits == [3] and supported_daily._status(port.module, report) == 'partial'
    again = run(port, journal, target=6)
    assert supported_daily._status(port.module, again) == 'skipped_no_attempts' and port.submits == [3]


@pytest.mark.parametrize(('module', 'change'), [
    ('sanctum_survey', {'tickets_cost': 1}),
    ('temple_survey', {'stamina_cost': 0}),
    ('tower_cloister', {'stamina_cost': 20}),
])
def test_receipt_and_execution_require_exact_cost_for_activity(tmp_path, module, change):
    from pcr_maa.preset_execution import Journal
    from test_optional_sweeps import SweepScreen, run
    tower = module == 'tower_cloister'
    target = None if tower else 4
    port = SweepScreen(module, stage=None if tower else 1, limit=5, remaining=3)
    if tower:
        port.quote_fields['stamina_cost'] = 0  # Visible zero and before-balance, or a ticket-only dialog.
    report = run(port, Journal(tmp_path / 'valid'), target=target)
    assert supported_daily._status(module, report) == 'executed'
    quote = report['quote']
    assert quote['count'] >= 2
    assert supported_daily._status(module, report | {'quote': quote | change}) == 'stopped'
    port.remaining = 3
    port.quote_fields.update(change)
    rejected = run(port, Journal(tmp_path / 'bad'), target=target)
    assert rejected['consumption_sent'] is False and len(port.submits) == 1
