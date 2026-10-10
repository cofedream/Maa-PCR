"""Synthetic business receipts; these tests do not prove live count changes."""
from copy import deepcopy
import pytest
from test_preset_execution import Port, reading, frozen_game_clock
from pcr_maa.preset_execution import Journal, execute_one


class FitPort(Port):
    def __init__(self, remaining=4):
        r = reading(remaining, 10)
        r['rows'][0].update(category='深域关卡', difficulty=None)
        r['rows'] = [dict(r['rows'][0], title=f'测试{i}') for i in range(5)]
        super().__init__(r)
        for row in self.after['rows']:
            row['remaining'] = 0
        self.count = 3
        self.fail_restore = False

    def read_count(self, slot):
        self.calls.append('count')
        return self.count

    def fit_count(self, slot, target):
        self.calls.append(('fit', target))
        if self.fail_restore and target == 3:
            return dict(verified=False, observed=4)
        self.count = target
        return dict(verified=True, observed=target)

    def quote(self, r):
        q = deepcopy(super().quote(r))
        q.update(counts=[self.count]*len(r['rows']))
        return q


def invoke_fit(tmp_path, port, **kwargs):
    port.journal = Journal(tmp_path)
    return execute_one(port, dict(slot=2, kind='deep', target_per_stage=10),
                       'user-reported-unreset', port.journal, game_day='2026-09-23',
                       fit_remaining=True, **kwargs)


def test_fit_four_execute_restore_three(tmp_path):
    port = FitPort()
    result = invoke_fit(tmp_path, port, execute=True)
    assert result['outcome'] == 'executed', result
    assert result['count_fit']['original'] == 3
    assert result['count_fit']['restored'] is True
    assert port.count == 3 and result['quote']['counts'] == [4]*5
    assert result['after_goal']['preset_goal_met'] is True
    assert port.calls.index(('fit', 4)) < port.calls.index('quote')
    assert port.calls.index(('fit', 3)) < port.calls.index('home')


def test_preview_restores_without_challenge_or_journal(tmp_path):
    port = FitPort()
    result = invoke_fit(tmp_path, port, preview=True)
    assert result['outcome'] == 'preview_complete'
    assert result['cancel_verified'] and result['home_verified']
    assert not result['consumption_sent'] and port.journal.events() == []
    assert 'submit' not in port.calls and port.count == 3
    assert port.calls.index('cancel') < port.calls.index(('fit',3)) < port.calls.index('home')


def test_all_met_zero_adjustment(tmp_path):
    port = FitPort(0)
    result = invoke_fit(tmp_path,port,execute=True)
    assert result['outcome'] == 'skipped_goal_met'
    assert port.calls == ['read','home']


@pytest.mark.parametrize('remaining', [0,2])
def test_mixed_gap_stops_before_adjustment(tmp_path,remaining):
    port = FitPort()
    port.before['rows'][1]['remaining'] = remaining
    result = invoke_fit(tmp_path,port,execute=True)
    assert result['outcome'] == 'count_fit_mixed_gap'
    assert port.calls == ['read']


def test_target_exceeds_remaining_and_limit(tmp_path):
    port = FitPort(10)
    result = execute_one(port, dict(slot=2,kind='deep',target_per_stage=11),
                         'user-reported-unreset',Journal(tmp_path),fit_remaining=True,
                         execute=True,game_day='2026-09-23')
    assert result['outcome'] == 'count_fit_exceeds_available'
    assert port.calls == ['read']


@pytest.mark.parametrize('resource', ['stamina','tickets'])
def test_insufficient_after_adjustment_restores_no_consumption(tmp_path,resource):
    port = FitPort()
    port.q.update(insufficient=resource,identity_verified=True,verified=False)
    result = invoke_fit(tmp_path,port,execute=True)
    assert result['outcome'] == 'skipped_insufficient_'+resource
    assert port.count == 3 and result['count_fit']['restored']
    assert result['home_verified'] and 'confirm' not in port.calls


def test_dishonest_adjustment_result_cannot_replace_actual_readback(tmp_path):
    port = FitPort()
    port.fit_count = lambda slot,target: dict(verified=True,observed=target)
    result = invoke_fit(tmp_path,port,execute=True)
    assert result['outcome'] == 'count_fit_readback_failed'
    assert 'quote' not in port.calls and 'submit' not in port.calls


@pytest.mark.parametrize('preview', [True,False])
def test_restore_failure_separate_from_verified_consumption(tmp_path,preview):
    port = FitPort()
    port.fail_restore = True
    result = invoke_fit(tmp_path,port,preview=preview,execute=not preview)
    assert result['outcome'] == ('count_restore_failed' if preview else 'executed_restore_failed')
    assert result['settlement_verified'] is (not preview)
    assert not result['count_fit']['restored'] and 'home' not in port.calls
    assert port.calls.count(('fit',3)) == 1
    if not preview:
        assert port.journal.events()[-1]['state'] == 'settled'


@pytest.mark.parametrize('failure', ['result','counts','exception'])
def test_unknown_consumption_no_restore_navigation_or_resend(tmp_path,failure):
    port = FitPort()
    if failure == 'result':
        port.settled = False
    elif failure == 'counts':
        port.after['rows'][0]['remaining'] = 1
    else:
        def lost(q):
            port.calls.append('submit')
            raise RuntimeError('lost')
        port.submit = lost
    result = invoke_fit(tmp_path,port,execute=True)
    assert result['outcome'].startswith('unknown_')
    assert result['count_fit']['restore_reason'] == 'unknown_consumption_preserve_scene'
    assert ('fit',3) not in port.calls and 'home' not in port.calls
    assert port.journal.events()[-1]['state'] == 'in_flight'
    assert port.journal.events()[-1]['count_fit']['original'] == 3
    again = FitPort()
    assert invoke_fit(tmp_path,again,execute=True)['outcome'] == 'blocked_in_flight'
    assert again.calls == []


def test_opt_in_requires_explicit_preview_or_execute(tmp_path):
    port = FitPort()
    assert invoke_fit(tmp_path,port)['outcome'] == 'blocked_or_failed'
    assert port.calls == []


def test_legacy_preview_never_adjusts(tmp_path):
    port = FitPort()
    result = execute_one(port,dict(slot=2,kind='deep',target_per_stage=10),
                         'user-reported-unreset',Journal(tmp_path),preview=True,game_day='2026-09-23')
    assert result['outcome'] == 'preview_complete'
    assert port.calls == ['read','quote','confirm','cancel','home']
    assert 'count_fit' not in result


def test_changed_quote_count_refused_even_when_under_goal(tmp_path):
    port = FitPort()
    quote = port.quote
    def stale(r):
        q = quote(r)
        q.update(counts=[3]*5)
        return q
    port.quote = stale
    result = invoke_fit(tmp_path,port,execute=True)
    assert result['outcome'] == 'count_fit_quote_mismatch'
    assert 'confirm' not in port.calls and port.count == 3


@pytest.mark.parametrize('mode', ['--preview-confirm','--execute'])
def test_cli_explicit_fit_flag_forwarded(tmp_path,monkeypatch,mode):
    import json
    from test_inspect_daily import config
    from pcr_maa import cli,device
    path = tmp_path/'config.json'
    path.write_text(json.dumps(config()),encoding='utf-8')
    def capture(**kwargs):
        assert kwargs['execution']['fit_remaining'] is True
        assert kwargs['execution']['preview'] is (mode == '--preview-confirm')
        return dict(outcome='count_restore_failed',home_verified=False)
    monkeypatch.setattr(device,'navigate',capture)
    assert cli.main(['execute-preset','--config',str(path),'--kind','deep',
                     '--game-day','2026-09-23','--fit-remaining',mode]) == 2


def test_cli_bare_fit_rejected_before_config_or_device(monkeypatch):
    from pcr_maa import cli,device
    monkeypatch.setattr(device,'navigate',lambda **kw: pytest.fail('device called'))
    assert cli.main(['execute-preset','--config','missing','--kind','deep',
                     '--game-day','2026-09-23','--fit-remaining']) == 1


@pytest.mark.parametrize('remaining,target,expected', [(4,10,'executed'), (0,10,'skipped_goal_met'), (2,11,'count_fit_exceeds_available')])
def test_gui_current_state_finite_count_execution(tmp_path, remaining, target, expected):
    port = FitPort(remaining)
    port.journal = Journal(tmp_path)
    report = execute_one(port, dict(slot=2,kind='deep',target_per_stage=target),
        'gui-current-state', port.journal, fit_remaining=True, execute=True, game_day='2026-09-23')
    assert report['outcome'] == expected
    assert report['count_context_source'] == 'current-game-observation'
    if expected == 'executed':
        assert report['quote']['counts'] == [remaining]*5
        assert port.count == 3 and report['count_fit']['restored']
    else:
        assert 'submit' not in port.calls


def test_gui_unknown_blocks_fresh_process_and_new_day(tmp_path):
    import os, subprocess, sys
    from pathlib import Path
    port = FitPort()
    port.settled = False
    port.journal = Journal(tmp_path)
    report = execute_one(port, dict(slot=2,kind='deep',target_per_stage=10),
        'gui-current-state', port.journal, fit_remaining=True, execute=True, game_day='2026-09-23')
    assert report['outcome'] == 'unknown_settlement'
    script = """import sys
sys.path.insert(0,sys.argv[2])
from pcr_maa.preset_execution import execute_one,Journal
class NoIO:
 def __getattr__(self,name): raise AssertionError('unexpected I/O: '+name)
r=execute_one(NoIO(),dict(slot=2,kind='deep',target_per_stage=10),'gui-current-state',Journal(sys.argv[1]),execute=True,fit_remaining=True,game_day='2099-01-01')
assert r['outcome']=='blocked_in_flight',r
print(r['outcome'])
"""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    source = Path(__file__).resolve().parents[1] / 'src'
    run = subprocess.run([sys.executable,'-B','-c',script,str(tmp_path),str(source)],
        cwd=tmp_path, capture_output=True, text=True, env=env, timeout=30)
    assert run.returncode == 0, run.stdout+run.stderr
    assert run.stdout.strip() == 'blocked_in_flight'
