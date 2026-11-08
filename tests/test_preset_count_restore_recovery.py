"""A completed count restoration must not constrain later user settings."""
import json
import time

import pytest

from pcr_maa import game_day, preset_execution, result_checkpoint as cp
from pcr_maa.preset_execution import Journal
from test_game_day_policy import freeze
from test_preset_execution import Port, PRESET, reading, quote_for
from test_result_checkpoint import consumer, frame, anchors


class FitPort(Port):
    def __init__(self, count, *, verified=True):
        super().__init__(reading(0))
        self.count, self.verified = count, verified
        self.targets = []

    def read_count(self, slot):
        self.calls.append('read_count')
        return self.count

    def fit_count(self, slot, target):
        self.calls.append('restore')
        self.count = target
        self.targets.append(target)
        return dict(verified=self.verified)


def pending_fit(tmp_path):
    before = reading()
    quote = quote_for(before)
    journal = Journal(tmp_path / 'ledger')
    with journal.locked():
        journal.append(dict(state='in_flight', slot=2, game_day=game_day.current_game_day(),
            at=time.time(), quote=quote, count_fit=dict(original=1, target=3, readback=3)))
    checkpoint = cp.ResultCheckpoint(journal, kind='preset', before=before, quote=quote)
    checkpoint.capture(consumer(), frame(tmp_path), anchors())
    return journal, checkpoint


def invoke(port, journal, *, fit_remaining=False):
    port.journal = journal
    return preset_execution.execute_one(port, PRESET, 'gui-current-state', journal,
        execute=True, fit_remaining=fit_remaining, game_day=game_day.current_game_day())


@pytest.mark.parametrize('next_day', [False, True])
def test_completed_restoration_does_not_reapply_after_restart(tmp_path, monkeypatch, next_day):
    for module in (game_day, preset_execution):
        freeze(monkeypatch, module, '2026-11-07T14:00:00+08:00')
    journal, checkpoint = pending_fit(tmp_path)
    restored = FitPort(3)
    first = invoke(restored, journal)
    assert first['outcome'] == 'skipped_goal_met' and first['count_fit']['restored'] is True
    assert restored.count == 1
    original_journal, original_checkpoint = journal.path.read_bytes(), checkpoint.path.read_bytes()
    if next_day:
        for module in (game_day, preset_execution):
            freeze(monkeypatch, module, '2026-11-08T14:00:00+08:00')
    changed = FitPort(7)
    second = invoke(changed, Journal(journal.directory))
    assert second['outcome'] == 'skipped_goal_met' and second['home_verified'] is True
    assert changed.count == 7 and changed.calls == ['read', 'home']
    assert journal.path.read_bytes() == original_journal
    assert checkpoint.path.read_bytes() == original_checkpoint


def test_completed_restoration_allows_next_day_execution_with_new_count(tmp_path, monkeypatch):
    for module in (game_day, preset_execution):
        freeze(monkeypatch, module, '2026-11-07T14:00:00+08:00')
    journal, checkpoint = pending_fit(tmp_path)
    assert invoke(FitPort(3), journal)['outcome'] == 'skipped_goal_met'
    old = journal.path.read_bytes()
    marker = checkpoint.path.with_suffix('.count-restored.json').read_bytes()
    for module in (game_day, preset_execution):
        freeze(monkeypatch, module, '2026-11-08T14:00:00+08:00')
    port = FitPort(7)
    port.before = reading()
    result = invoke(port, Journal(journal.directory), fit_remaining=True)
    assert result['outcome'] == 'executed' and result['settlement_verified'] is True
    assert port.calls.count('submit') == 1 and port.targets == [3, 7] and port.count == 7
    assert journal.path.read_bytes().startswith(old)
    assert checkpoint.path.with_suffix('.count-restored.json').read_bytes() == marker


def test_unverified_restoration_keeps_old_value_guard(tmp_path):
    journal, checkpoint = pending_fit(tmp_path)
    first = invoke(FitPort(3, verified=False), journal)
    assert first['outcome'] == 'count_restore_failed' and first['count_fit']['restored'] is False
    assert not checkpoint.path.with_suffix('.count-restored.json').exists()
    changed = FitPort(7)
    second = invoke(changed, Journal(journal.directory))
    assert second['outcome'] == 'count_restore_value_changed'
    assert 'restore' not in changed.calls and 'submit' not in changed.calls


def test_failed_restoration_marker_write_stops_and_can_be_retried(tmp_path, monkeypatch):
    journal, checkpoint = pending_fit(tmp_path)
    replace = cp.os.replace
    def fail_marker(source, target):
        if str(target).endswith('.count-restored.json'):
            raise OSError('synthetic count restoration marker failure')
        return replace(source, target)
    port = FitPort(3)
    with monkeypatch.context() as patch:
        patch.setattr(cp.os, 'replace', fail_marker)
        result = invoke(port, journal)
    assert result['outcome'] == 'blocked_or_failed'
    assert port.count == 1 and 'home' not in port.calls and 'submit' not in port.calls
    assert not checkpoint.path.with_suffix('.count-restored.json').exists()
    assert not list(checkpoint.path.parent.glob('*.tmp'))
    refused = invoke(FitPort(7), Journal(journal.directory))
    assert refused['outcome'] == 'count_restore_value_changed'
    retry = invoke(FitPort(1), Journal(journal.directory))
    assert retry['outcome'] == 'skipped_goal_met'
    assert invoke(FitPort(7), Journal(journal.directory))['outcome'] == 'skipped_goal_met'


def test_restoration_marker_cannot_be_rebound_to_another_checkpoint(tmp_path):
    journal, checkpoint = pending_fit(tmp_path)
    assert invoke(FitPort(3), journal)['outcome'] == 'skipped_goal_met'
    marker = checkpoint.path.with_suffix('.count-restored.json')
    value = json.loads(marker.read_text(encoding='utf-8'))
    value['checkpoint_sha256'] = '0' * 64
    marker.write_text(json.dumps(value), encoding='utf-8')
    changed = FitPort(7)
    result = invoke(changed, Journal(journal.directory))
    assert result['outcome'] == 'blocked_or_failed'
    assert 'restore' not in changed.calls and 'submit' not in changed.calls
