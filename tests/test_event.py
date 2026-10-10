"""Business counterexamples use explicitly synthetic ports, never device I/O."""
from copy import deepcopy
import pytest
from pcr_maa.preset_execution import Journal


@pytest.fixture(autouse=True)
def frozen_game_clock(monkeypatch):
    from pcr_maa import event, game_day
    from test_game_day_policy import freeze
    freeze(monkeypatch, event, '2026-09-23T14:00:00+08:00')
    freeze(monkeypatch, game_day, '2026-09-23T14:00:00+08:00')


def reading(remaining=3):
    return dict(native_ok=True, event_verified=True, list_complete=True, view='checked',
                incomplete_reason=None, rows=[dict(title=f'示例活动1-{i}', category='活动',
                difficulty='困难', limit=3, remaining=remaining, checked=True, title_score=.99)
                for i in range(1, 6)])


class Port:
    def __init__(self):
        self.calls = []
        self.before = reading()
        self.after = reading(0)
        self.sent = False
        self.result_ok = True
        self.counts = [3]*5

    def read(self):
        self.calls.append('read')
        return deepcopy(self.after if self.sent else self.before)

    def quote(self, r):
        from pcr_maa.preset_scan import key
        return dict(verified=True, identity_verified=True, scope='event-hard',
                    identities=[list(key(x)) for x in r['rows']], counts=self.counts,
                    stamina_before=200, stamina_after=68)

    def confirm(self, *args):
        self.calls.append('confirm')
        return dict(verified=True)

    def cancel_confirmation(self):
        self.calls.append('cancel')
        return True

    def submit(self, q):
        assert self.journal.events()[-1]['state'] == 'in_flight'
        self.calls.append('submit')
        self.sent = True
        return dict(consumption_dispatched=True, result_verified=self.result_ok,
                    preset_return_verified=True, result_images=[{'source': 'synthetic'}])

    def home(self):
        self.calls.append('home')
        return True


def invoke(tmp_path, port, **kwargs):
    from pcr_maa.event import execute_event
    port.journal = Journal(tmp_path)
    return execute_event(port, port.journal, context='user-reported-unreset',
                         game_day='2026-09-23', **kwargs)


def test_default_observe_and_explicit_preview(tmp_path):
    p = Port()
    assert invoke(tmp_path, p)['outcome'] == 'ready_not_executed'
    assert p.calls == ['read']
    assert invoke(tmp_path, p, preview=True)['outcome'] == 'preview_complete'
    assert p.calls[-3:] == ['confirm', 'cancel', 'home']
    assert not p.journal.events()


@pytest.mark.parametrize('fault', ['normal', 'boss', 'limit', 'over_target', 'missing_page', 'stamina'])
def test_synthetic_bad_scope_or_quote_never_submits(tmp_path, fault):
    p = Port()
    if fault == 'normal': p.before['rows'][0]['difficulty'] = '普通'
    if fault == 'boss': p.before['rows'][0]['title'] = '首领1-1'
    if fault == 'limit': p.before['rows'][0]['limit'] = 5
    if fault == 'over_target': p.counts = [4]*5
    if fault == 'missing_page': p.before['list_complete'] = False
    if fault == 'stamina':
        old = p.quote
        p.quote = lambda r: dict(old(r), stamina_after=201)
    r = invoke(tmp_path, p, execute=True)
    assert not r['consumption_sent'] and 'submit' not in p.calls
    assert not r['daily_complete'] and not p.journal.events()


def test_unknown_no_retry_and_zero_goal(tmp_path):
    p = Port()
    p.result_ok = False
    assert invoke(tmp_path, p, execute=True)['outcome'] == 'unknown_settlement'
    assert p.calls[-1] == 'submit'
    again = Port()
    assert invoke(tmp_path, again, execute=True)['outcome'] == 'blocked_in_flight'
    assert again.calls == []


def test_synthetic_settlement_and_goal_skip(tmp_path):
    p = Port()
    r = invoke(tmp_path, p, execute=True)
    assert r['settlement_verified'] and not r['daily_complete']
    assert r['outcome'] == 'executed'
    assert invoke(tmp_path, p, execute=True)['outcome'] == 'skipped_goal_met'
    assert p.calls.count('submit') == 1


@pytest.mark.parametrize('fault', ['post_missing', 'post_wrong', 'exception', 'mixed_gap',
                                   'unconfirmed', 'shortage', 'ticket_shortage'])
def test_synthetic_additional_fail_closed_cases(tmp_path, fault):
    p = Port()
    if fault == 'post_missing': p.after['list_complete'] = False
    if fault == 'post_wrong': p.after['rows'][0]['remaining'] = 1
    if fault == 'exception':
        def fail(q):
            raise RuntimeError('synthetic interrupted submit')
        p.submit = fail
    if fault == 'mixed_gap': p.before['rows'][0]['remaining'] = 2
    if fault in ('shortage', 'ticket_shortage'):
        old = p.quote
        p.quote = lambda r: dict(old(r), insufficient='tickets' if fault == 'ticket_shortage'
                                  else 'stamina', verified=False)
    if fault == 'unconfirmed':
        from pcr_maa.event import execute_event
        r = execute_event(p, Journal(tmp_path), game_day='2026-09-23', execute=True)
        assert r['outcome'] == 'reading_unknown' and p.calls == ['read']
        return
    r = invoke(tmp_path, p, execute=True)
    if fault in ('post_missing', 'post_wrong', 'exception'):
        assert not r['settlement_verified'] and p.journal.events()[-1]['state'] == 'in_flight'
        assert 'home' not in p.calls
    else:
        assert not r['consumption_sent'] and not p.journal.events()
    if fault == 'shortage': assert r['outcome'] == 'skipped_insufficient_stamina'
    if fault == 'ticket_shortage': assert r['outcome'] == 'skipped_insufficient_tickets'


def test_cli_modes_without_device(monkeypatch):
    from pcr_maa import cli, device
    seen = []
    def navigate(*args, **kwargs):
        seen.append(kwargs['event'])
        return dict(outcome='ready_not_executed', daily_complete=False)
    monkeypatch.setattr(device, 'navigate', navigate)
    args = ['event-hard', '--start', 'event', '--input-display', '2', '--screenshot-display', '123',
            '--game-day', '2026-09-23', '--count-context', 'user-reported-unreset']
    assert cli.main(args) == 0 and not seen[-1]['execute'] and not seen[-1]['preview']
    assert cli.main(args+['--preview-confirm']) == 0 and seen[-1]['preview']
    assert cli.main(args+['--execute']) == 0 and seen[-1]['execute']
    with pytest.raises(SystemExit): cli.main(args+['--execute','--preview-confirm'])
    assert cli.main([*args[:args.index('--game-day')], '--game-day', 'invalid']) != 0
    assert len(seen) == 3


def test_device_event_conflicts_rejected_before_controller():
    from pcr_maa.device import navigate
    valid = dict(start='event', game_day='2026-09-23', context='user-reported-unreset', execute=False, preview=False)
    with pytest.raises(ValueError): navigate(2, '123', event=valid, slot=1)
    with pytest.raises(ValueError): navigate(2, '123', event=dict(valid, execute=True, preview=True))
    with pytest.raises(ValueError): navigate(2, '123', event=dict(valid, reset=True))


def test_home_exception_preserves_verified_settlement(tmp_path):
    p = Port()
    def fail():
        raise RuntimeError('synthetic home failure')
    p.home = fail
    r = invoke(tmp_path, p, execute=True)
    assert r['outcome'] == 'executed_home_failed' and r['settlement_verified']
    assert p.journal.events()[-1]['state'] == 'settled'


def test_partial_quote_settles_exact_counts_and_can_resume(tmp_path):
    p = Port()
    p.counts = [1]*5  # Existing quoted count; no preset editing.
    p.after = reading(2)
    r = invoke(tmp_path, p, execute=True)
    assert r['outcome'] == 'executed' and r['settlement_verified']
    assert r['goal_complete'] is False and not r['daily_complete']
    assert p.journal.events()[-1]['remaining'] == [2]*5
    again = Port()
    again.before = reading(2)
    again.counts = [2]*5
    r = invoke(tmp_path, again, execute=True)
    assert r['outcome'] == 'executed' and r['goal_complete']


def test_completed_rows_are_excluded_without_changing_selection(tmp_path):
    p = Port()
    p.before['rows'][0]['remaining'] = 0
    p.counts = [3]*4
    original = deepcopy(p.before)
    r = invoke(tmp_path, p, execute=True)
    assert r['outcome'] == 'executed' and r['goal_complete']
    assert len(r['reading']['rows']) == 5 and len(r['quote']['identities']) == 4
    assert p.before == original


@pytest.mark.parametrize('counts', [[0]*5, [True]*5, [-1]*5, [4]*5, [1]*4])
def test_partial_invalid_counts_never_submit(tmp_path, counts):
    p = Port()
    p.counts = counts
    r = invoke(tmp_path, p, execute=True)
    assert not r['consumption_sent'] and not p.journal.events()


def test_partial_unknown_settlement_blocks_cross_day_retry(tmp_path):
    from pcr_maa.event import execute_event
    p = Port()
    p.counts = [1]*5
    p.result_ok = False
    assert invoke(tmp_path, p, execute=True)['outcome'] == 'unknown_settlement'
    again = Port()
    r = execute_event(again, Journal(tmp_path), game_day='2026-09-26',
                      context='user-reported-unreset', execute=True)
    assert r['outcome'] == 'blocked_in_flight' and again.calls == []


@pytest.mark.parametrize('fault', ['wrong_partial_delta', 'completed_row_changed', 'missing_after', 'crash'])
def test_partial_post_failure_keeps_intent_and_does_not_navigate(tmp_path, fault):
    p = Port()
    p.counts = [1]*5
    p.after = reading(2)
    if fault == 'wrong_partial_delta': p.after['rows'][0]['remaining'] = 1
    if fault == 'completed_row_changed':
        p.before['rows'][0]['remaining'] = 0
        p.counts = [1]*4
    if fault == 'missing_after': p.after['list_complete'] = False
    if fault == 'crash':
        def crash(q): raise RuntimeError('synthetic post-intent crash')
        p.submit = crash
    r = invoke(tmp_path, p, execute=True)
    assert r['outcome'] in ('unknown_settlement', 'unknown_post_counts')
    assert not r['settlement_verified'] and not r['goal_complete']
    assert p.journal.events()[-1]['state'] == 'in_flight' and 'home' not in p.calls
    again = Port()
    assert invoke(tmp_path, again, execute=True)['outcome'] == 'blocked_in_flight'
    assert not again.calls


@pytest.mark.parametrize('fault', ['tickets_missing', 'tickets_wrong', 'count_wrong', 'stamina_short'])
def test_partial_confirmation_requires_current_tickets_counts_and_stamina(fault):
    from pcr_maa.preset_confirmation import check_confirmation
    p = Port()
    p.counts = [1]*5
    q = p.quote(p.before)
    rows = [dict(r, count=1, fields_verified=True) for r in p.before['rows']]
    summary = dict(stages=5, times=5, tickets_cost=5, stamina_cost=132, stamina_owned=200)
    c = dict(native_ok=True, list_complete=True, rows=rows, summary=summary)
    assert check_confirmation(c, p.before, q)
    if fault == 'tickets_missing': summary['tickets_cost'] = None
    if fault == 'tickets_wrong': summary['tickets_cost'] = 4
    if fault == 'count_wrong': rows[0]['count'] = 3
    if fault == 'stamina_short': summary['stamina_owned'] = 100
    assert not check_confirmation(c, p.before, q)


@pytest.mark.parametrize('remaining,counts,expected', [(0,3,'skipped_goal_met'), (2,3,'count_does_not_match_full_goal'), (3,3,'executed')])
def test_gui_current_state_event_uses_observed_remaining(tmp_path, remaining, counts, expected):
    from pcr_maa.event import execute_event
    port = Port()
    port.before = reading(remaining)
    port.counts = [counts]*5
    port.journal = Journal(tmp_path)
    report = execute_event(port, port.journal, context='gui-current-state', game_day='2026-09-23', execute=True)
    assert report['outcome'] == expected
    assert report['count_context_source'] == 'current-game-observation'
    assert ('submit' in port.calls) == (expected == 'executed')
