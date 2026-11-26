"""Business decisions and persisted intents for the optional daily sweeps."""
from datetime import date, timedelta

import pytest

from pcr_maa.game_day import current_game_day
from pcr_maa.optional_sweep import execute_optional_sweep
from pcr_maa.preset_execution import Journal


class SweepScreen:
    """The counter changes on submit, even if the later receipt is lost."""

    def __init__(self, module='sanctum_survey', *, stage=2, remaining=4, limit=5):
        self.module = module
        self.stage = stage
        self.remaining = remaining
        self.limit = limit
        self.state = 'ready'
        self.read_fields = {}
        self.quote_fields = {}
        self.result_fields = {}
        self.submits = []
        self.queries = []

    def read(self):
        return dict(module=self.module, stage=self.stage, state=self.state,
                    remaining=self.remaining, limit=self.limit) | self.read_fields

    def quote(self, count):
        self.queries.append(count)
        return dict(module=self.module, stage=self.stage, count=count, remaining=self.remaining, limit=self.limit,
                    stamina_cost=10 * count, stamina_before=300, tickets_cost=count,
                    tickets_before=50) | self.quote_fields

    def submit(self, quote):
        self.submits.append(quote['count'])
        self.remaining -= quote['count']
        return dict(native_ok=True)

    def result(self, quote):
        return dict(module=self.module, stage=self.stage, count=quote['count'],
                    remaining=self.remaining, limit=self.limit, verified=True) | self.result_fields

    def home(self):
        return dict(native_ok=True)


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / 'ledger')


def run(port, journal, *, target=3, game_day=None):
    device = dict(source='gui_adb_session_v1', serial='127.0.0.1:16384',
                  app_package='com.bilibili.priconne')
    return execute_optional_sweep(port, journal, module=port.module, stage=port.stage,
                                  daily_target=target, device=device, execute=True,
                                  game_day=game_day or current_game_day(), context='gui-current-state')


@pytest.mark.parametrize('module', ['sanctum_survey', 'temple_survey'])
def test_survey_fills_only_missing_attempts_and_reopens_without_resending(journal, module):
    screen = SweepScreen(module)
    report = run(screen, journal)
    assert report['outcome'] == 'executed' and report['settlement_verified'] and report['home_verified']
    assert screen.submits == [2]  # One of the five attempts was already used.
    assert [row['state'] for row in journal.events()] == ['in_flight', 'settled']
    saved = journal.path.read_bytes()

    screen.remaining -= 1  # The player completed another run outside Maa-PCR.
    again = run(screen, Journal(journal.directory))
    assert again['outcome'] == 'skipped_goal_met'
    assert screen.queries == [2] and screen.submits == [2]
    assert journal.path.read_bytes() == saved


@pytest.mark.parametrize('limit', [5, None])
def test_tower_sweeps_live_remaining_after_partial_completion(journal, limit):
    screen = SweepScreen('tower_cloister', stage=None, remaining=2, limit=limit)
    screen.quote_fields.update(stamina_applicable=False, stamina_before=None, stamina_cost=0)
    assert run(screen, journal, target=None)['settlement_verified']
    assert screen.submits == [2]
    assert run(screen, journal, target=None)['outcome'] == 'skipped_goal_met'
    assert screen.submits == [2]


@pytest.mark.parametrize('module', ['sanctum_survey', 'temple_survey'])
def test_investigations_require_stamina_quote(journal, module):
    screen = SweepScreen(module)
    screen.quote_fields.update(stamina_applicable=False, stamina_before=None, stamina_cost=0)
    report = run(screen, journal)
    assert report['outcome'] == 'stopped' and not report['consumption_sent']
    assert screen.submits == [] and journal.events() == []


@pytest.mark.parametrize(('module', 'stage', 'target', 'state', 'outcome'), [
    ('tower_cloister', None, None, 'closed', 'skipped_event_unavailable'),
    ('tower_cloister', None, None, 'locked', 'skipped_not_cleared'),
    ('temple_survey', 1, 3, 'locked', 'skipped_not_cleared'),
    ('sanctum_survey', 1, 0, 'ready', 'skipped_disabled'),
])
def test_unavailable_or_disabled_never_opens_a_quote(journal, module, stage, target, state, outcome):
    screen = SweepScreen(module, stage=stage)
    screen.state = state
    assert run(screen, journal, target=target)['outcome'] == outcome
    assert screen.queries == [] and screen.submits == [] and journal.events() == []


@pytest.mark.parametrize(('field', 'outcome'), [
    ('stamina_before', 'skipped_insufficient_stamina'),
    ('tickets_before', 'skipped_insufficient_tickets'),
])
def test_shortage_skips_without_input_or_intent(journal, field, outcome):
    screen = SweepScreen()
    screen.quote_fields[field] = 1
    report = run(screen, journal)
    assert report['outcome'] == outcome and report['home_verified']
    assert screen.submits == [] and journal.events() == []


@pytest.mark.parametrize(('notice', 'expected'), [
    ({'shortage': 'stamina', 'native_ok': True}, 'skipped_insufficient_stamina'),
    ({'shortage': 'tickets', 'native_ok': True}, 'skipped_insufficient_tickets'),
    ({'shortage': 'stamina'}, 'stopped'),
    ({'shortage': 'tickets', 'native_ok': False}, 'stopped'),
    ({'shortage': 'unknown', 'native_ok': True}, 'stopped'),
])
def test_native_shortage_dialog_without_numeric_quote(journal, monkeypatch, notice, expected):
    from pcr_maa.optional_sweep import step_status
    screen = SweepScreen()

    def dialog(count):
        screen.queries.append(count)
        return dict(module=screen.module, stage=screen.stage, count=count,
                    remaining=screen.remaining, limit=screen.limit) | notice

    monkeypatch.setattr(screen, 'quote', dialog)
    report = run(screen, journal)
    assert step_status(screen.module, report) == expected
    if expected != 'stopped':
        assert report['outcome'] == expected and report['home_verified']
    assert screen.queries == [2] and not report['consumption_sent']
    assert screen.submits == [] and journal.events() == []


@pytest.mark.parametrize(('evidence', 'change'), [
    ('read_fields', {'module': 'temple_survey'}),
    ('read_fields', {'stage': 3}),
    ('read_fields', {'remaining': 6}),
    ('quote_fields', {'module': 'tower_cloister'}),
    ('quote_fields', {'stage': 3}),
    ('quote_fields', {'remaining': 3}),
    ('quote_fields', {'limit': 6}),
    ('quote_fields', {'limit': None}),
    ('quote_fields', {'count': 3}),
    ('quote_fields', {'tickets_cost': -1}),
    ('quote_fields', {'stamina_cost': True}),
])
def test_changed_or_invalid_evidence_blocks_consumption(journal, evidence, change):
    screen = SweepScreen()
    getattr(screen, evidence).update(change)
    report = run(screen, journal)
    assert not report['settlement_verified']
    assert screen.submits == [] and journal.events() == []


@pytest.mark.parametrize('change', [
    {'verified': False}, {'remaining': 3}, {'count': 1}, {'module': 'temple_survey'}, {'limit': 6},
])
def test_unverified_result_is_durable_and_never_retried(journal, change):
    screen = SweepScreen()
    screen.result_fields.update(change)
    report = run(screen, journal)
    assert screen.submits == [2] and not report['settlement_verified']
    assert [row['state'] for row in journal.events()] == ['in_flight']
    saved = journal.path.read_bytes()
    screen.result_fields.clear()

    # An increased target could authorize a new click without pending-intent protection.
    again = run(screen, Journal(journal.directory), target=5)
    assert not again['settlement_verified']
    assert screen.queries == [2] and screen.submits == [2] and journal.path.read_bytes() == saved


def test_previous_game_day_never_authorizes_consumption(journal):
    screen = SweepScreen()
    yesterday = date.fromisoformat(current_game_day()) - timedelta(days=1)
    report = run(screen, journal, game_day=yesterday.isoformat())
    assert not report['settlement_verified']
    assert screen.submits == [] and journal.events() == []


def test_day_changes_while_preparing_quote_stops_before_intent(journal, monkeypatch):
    from pcr_maa import optional_sweep
    original_day = current_game_day()
    next_day = (date.fromisoformat(original_day) + timedelta(days=1)).isoformat()
    screen = SweepScreen()
    monkeypatch.setattr(optional_sweep, 'current_game_day',
                        lambda: next_day if screen.queries else original_day)
    report = run(screen, journal, game_day=original_day)
    assert not report['settlement_verified']
    assert screen.queries == [2] and screen.submits == [] and journal.events() == []


def test_counter_increases_after_settlement_stops_before_new_input(journal):
    screen = SweepScreen()
    assert run(screen, journal)['settlement_verified']
    saved = journal.path.read_bytes()
    screen.remaining += 1
    assert not run(screen, Journal(journal.directory), target=5)['settlement_verified']
    assert screen.queries == [2] and screen.submits == [2] and journal.path.read_bytes() == saved


def test_lost_home_readback_keeps_proven_settlement(journal, monkeypatch):
    from pcr_maa.optional_sweep import step_status
    screen = SweepScreen()
    monkeypatch.setattr(screen, 'home', lambda: {'native_ok': False})
    report = run(screen, journal)
    assert report['settlement_verified'] and not report['home_verified']
    assert step_status(screen.module, report) == 'stopped'
    assert [row['state'] for row in journal.events()] == ['in_flight', 'settled']
    assert screen.submits == [2]
