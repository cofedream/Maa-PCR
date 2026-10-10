"""Synthetic business receipts; native replay tests are labelled separately."""
from copy import deepcopy
import json
import pytest

from pcr_maa.daily_goal import evaluate_daily_goal


@pytest.fixture(autouse=True)
def frozen_game_clock(monkeypatch):
    from pcr_maa import preset_execution, game_day
    from test_game_day_policy import freeze
    freeze(monkeypatch, preset_execution, '2026-09-23T14:00:00+08:00')
    freeze(monkeypatch, game_day, '2026-09-23T14:00:00+08:00')


def reading(remaining=3, limit=3):
    return dict(native_ok=True, list_complete=True, incomplete_reason=None,
                requested_slot=2, selected_slot=2, view='checked',
                observation=dict(page='presets', selected_slot=2),
                ocr_receipt=dict(native_ok=True),
                rows=[dict(title='测试1-1', category='主线关卡', difficulty='高难',
                           checked=True, limit=limit, remaining=remaining)])


def test_user_reported_context_is_usable_but_not_verified():
    goal = evaluate_daily_goal(reading(), 3, count_context='user-reported-unreset')
    assert goal['preset_goal_met'] is False
    assert goal['rows'][0]['needed'] == 3
    assert goal['count_context_source'] == 'user-reported'


PRESET = dict(slot=2, kind='very_hard', target_per_stage=3, label='仅展示')


def quote_for(r):
    from pcr_maa.preset_scan import key
    return dict(verified=True, slot=2, identities=[list(key(row)) for row in r['rows']],
                counts=[3]*len(r['rows']), stamina_before=200, stamina_after=140)


class Port:
    def __init__(self, r=None):
        self.before = r or reading()
        self.after = deepcopy(self.before)
        for row in self.after['rows']:
            row['remaining'] = max(0, row['remaining']-3)
        self.q = quote_for(self.before)
        self.calls = []
        self.settled = True
        self.journal = None

    def read(self, slot):
        self.calls.append('read')
        return deepcopy(self.after if 'submit' in self.calls else self.before)

    def quote(self, r):
        self.calls.append('quote')
        return self.q

    def submit(self, q):
        assert self.journal.events()[-1]['state'] == 'in_flight'
        self.calls.append('submit')
        return {'result_verified': self.settled, 'preset_return_verified': self.settled,
                'consumption_dispatched': True}

    def confirm(self, reading, quote):
        self.calls.append('confirm')
        return {'verified': True}

    def cancel_confirmation(self):
        self.calls.append('cancel')
        return True

    def home(self):
        self.calls.append('home')
        return True


def invoke(tmp_path, port, execute=True):
    from pcr_maa.preset_execution import Journal, execute_one
    port.journal = Journal(tmp_path)
    return execute_one(port, PRESET, 'user-reported-unreset', port.journal,
                       execute=execute, game_day='2026-09-23')


def test_normal_execution_and_no_daily_complete(tmp_path):
    port = Port()
    result = invoke(tmp_path, port)
    assert result['outcome'] == 'executed'
    assert result['settlement_verified'] and not result['daily_complete']
    assert port.calls == ['read', 'quote', 'confirm', 'submit', 'read', 'home']


@pytest.mark.parametrize('remaining,limit', [(0, 3), (2, 5)])
def test_goal_met_zero_submission_even_side_story(tmp_path, remaining, limit):
    r = reading(remaining, limit)
    if limit == 5:
        r['rows'][0]['category'] = '外传'
    port = Port(r)
    result = invoke(tmp_path, port)
    assert result['outcome'] == 'skipped_goal_met'
    assert port.calls == ['read', 'home'] and not result['consumption_sent']


def test_mixed_preset_cannot_replenish_met_rows(tmp_path):
    r = reading()
    r['rows'].append(dict(r['rows'][0], title='外传2-1', category='外传', limit=5, remaining=2))
    port = Port(r)
    assert invoke(tmp_path, port)['outcome'] == 'preset_would_exceed_goal'
    assert 'submit' not in port.calls


@pytest.mark.parametrize('patch,outcome', [
    ({'counts': [4]}, 'preset_would_exceed_goal'),
    ({'stamina_after': 210}, 'quote_contradiction'),
    ({'slot': 1}, 'quote_identity_mismatch'),
    ({'identities': [['other', '主线关卡', '高难']]}, 'quote_identity_mismatch'),
    ({'verified': False}, 'quote_unknown'),
    ({'insufficient': 'stamina'}, 'skipped_insufficient_stamina'),
    ({'insufficient': 'tickets'}, 'skipped_insufficient_tickets')])
def test_quote_refusals(tmp_path, patch, outcome):
    port = Port()
    port.q.update(patch)
    assert invoke(tmp_path, port)['outcome'] == outcome
    assert 'submit' not in port.calls


@pytest.mark.parametrize('patch', [{'selected_slot': 1}, {'list_complete': False}, {'view': 'unknown'}])
def test_unverified_reading_never_quotes(tmp_path, patch):
    port = Port()
    port.before.update(patch)
    assert invoke(tmp_path, port)['outcome'] == 'reading_unknown'
    assert port.calls == ['read']


@pytest.mark.parametrize('patch', [{'title_score': .3}, {'difficulty': '困难'}, {'limit': 5}, {'category': '未知'}])
def test_wrong_identity_or_noise_never_quotes(tmp_path, patch):
    port = Port()
    port.before['rows'][0].update(patch)
    assert invoke(tmp_path, port)['outcome'] == 'identity_mismatch'
    assert port.calls == ['read']


def test_without_execute_no_consumption(tmp_path):
    port = Port()
    assert invoke(tmp_path, port, False)['outcome'] == 'ready_not_executed'
    assert port.calls == ['read', 'quote']
    assert port.journal.events() == []


def test_partial_usage_does_not_repeat_saved_three(tmp_path):
    port = Port(reading(2))
    assert invoke(tmp_path, port)['outcome'] == 'preset_would_exceed_goal'
    assert 'submit' not in port.calls


def test_insufficient_with_unknown_after_balance_is_safe_skip(tmp_path):
    port = Port()
    port.q.update(verified=False, identity_verified=True, insufficient='stamina', stamina_after=None)
    assert invoke(tmp_path, port)['outcome'] == 'skipped_insufficient_stamina'
    assert port.calls == ['read', 'quote', 'home']


def test_old_null_stops_execution(tmp_path):
    from pcr_maa.preset_execution import execute_one, Journal
    port = Port()
    result = execute_one(port, PRESET, None, Journal(tmp_path), execute=True, game_day='2026-09-23')
    assert result['outcome'] == 'reading_unknown'
    assert result['count_context_source'] == 'unconfirmed'
    assert port.calls == ['read']


@pytest.mark.parametrize('failure', ['settlement', 'counts', 'exception'])
def test_unknown_keeps_inflight_and_restart_refuses(tmp_path, failure):
    port = Port()
    if failure == 'settlement':
        port.settled = False
    elif failure == 'counts':
        port.after['rows'][0]['remaining'] = 1
    else:
        def lost(_):
            raise OSError('response lost')
        port.submit = lost
    assert invoke(tmp_path, port)['outcome'].startswith('unknown_')
    assert port.journal.events()[-1]['state'] == 'in_flight'
    restarted = Port()
    assert invoke(tmp_path, restarted)['outcome'] == 'blocked_in_flight'
    assert restarted.calls == []


def test_reset_signs_stop_even_after_previous_success(tmp_path):
    assert invoke(tmp_path, Port())['outcome'] == 'executed'
    port = Port()
    assert invoke(tmp_path, port)['outcome'] == 'counter_reset_or_identity_change'
    assert port.calls == ['read']


def test_malformed_journal_and_write_failure_do_not_submit(tmp_path, monkeypatch):
    from pcr_maa.preset_execution import Journal
    def fail(self, event):
        raise OSError('disk full')
    monkeypatch.setattr(Journal, 'append', fail)
    port = Port()
    assert invoke(tmp_path, port)['outcome'] == 'blocked_or_failed'
    assert 'submit' not in port.calls
    (tmp_path/'transactions.jsonl').write_text('{', encoding='utf-8')
    port = Port()
    assert invoke(tmp_path, port)['outcome'] == 'blocked_or_failed'
    assert not port.calls


def test_concurrent_lock_is_zero_input(tmp_path):
    from pcr_maa.preset_execution import Journal
    with Journal(tmp_path).locked():
        port = Port()
        assert invoke(tmp_path, port)['outcome'] == 'blocked_or_failed'
        assert port.calls == []


def test_new_context_config_legacy_null_unchanged():
    from test_inspect_daily import config
    from pcr_maa.daily import validate_config, new_report
    c = config()
    assert validate_config(c)['count_context'] is None
    c['count_context'] = 'user-reported-unreset'
    assert new_report(validate_config(c), 'synthetic')['count_context_source'] == 'user-reported'


def test_cli_no_execute_remains_false(tmp_path, monkeypatch):
    from test_inspect_daily import config
    from pcr_maa import cli, device
    p = tmp_path/'config.json'
    p.write_text(json.dumps(config()), encoding='utf-8')
    def capture(**kwargs):
        assert kwargs['execution']['execute'] is False
        assert kwargs['execution']['preset']['slot'] == 2
        return {'outcome': 'ready_not_executed', 'daily_complete': False}
    monkeypatch.setattr(device, 'navigate', capture)
    assert cli.main(['execute-preset', '--config', str(p), '--kind', 'very_hard',
                     '--game-day', '2026-09-23', '--count-context', 'user-reported-unreset']) == 0
