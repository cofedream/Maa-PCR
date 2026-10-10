"""Offline callback fault injection: errors remain misses and retain tracebacks."""
from types import SimpleNamespace
import time

import numpy as np
import pytest

from pcr_maa import abyss_native, event_native, home_guard, native
from pcr_maa import preset_confirmation as confirmation
from pcr_maa import preset_execution_native as execution


CALLBACKS = ('AbyssHomePage', 'AbyssQuotePage', 'AbyssResultPage', 'AbyssTicketPage',
             'EventPage', 'ClosedAdventurePage', 'HomeNoKnownLargePanel',
             'ConfirmationPage', 'ConfirmationCommitGuard', 'CurrentClanPrompt',
             'CurrentResultEvidence')
FRAME = np.zeros((720, 1280, 3), dtype=np.uint8)


class Context:
    def __init__(self, failure=None):
        self.failure = failure

    def run_recognition(self, *args, **kwargs):
        if self.failure is not None:
            raise self.failure('injected_callback_failure')
        return None


class Resource:
    def __init__(self):
        self.callbacks = {}

    def override_pipeline(self, nodes):
        return True

    def register_custom_recognition(self, name, callback):
        self.callbacks[name] = callback
        return True

    def unregister_custom_recognition(self, name):
        del self.callbacks[name]
        return True


def invoke(name, context, monkeypatch):
    argv = SimpleNamespace(image=FRAME, custom_recognition_param='"short"')
    if name == 'AbyssTicketPage':
        monkeypatch.setattr(abyss_native, 'check_quote', lambda *args: 'ready')
    readers = {
        'AbyssHomePage': lambda: abyss_native.AbyssHomePage(),
        'AbyssQuotePage': lambda: abyss_native.AbyssQuotePage({}),
        'AbyssResultPage': lambda: abyss_native.AbyssResultPage({}, []),
        'AbyssTicketPage': lambda: abyss_native.AbyssTicketPage({}, {}),
        'EventPage': lambda: event_native.EventPage(),
        'ClosedAdventurePage': lambda: event_native.ClosedAdventurePage(),
        'HomeNoKnownLargePanel': lambda: home_guard.HomeNoKnownLargePanel(),
        'ConfirmationPage': lambda: confirmation.ConfirmationPage(),
    }
    if name in readers:
        return readers[name]().analyze(context, argv)

    resource = Resource()
    adapter = execution.NativePreset(SimpleNamespace(resource=resource))
    receipt = dict(native_ok=False, nodes=[])
    results = []

    def run(*args, **kwargs):
        results.append(resource.callbacks[name].analyze(context, argv))
        return receipt

    monkeypatch.setattr(native, 'run', run)
    monkeypatch.setattr(native, 'require_native_input', lambda *args: None, raising=False)
    monkeypatch.setattr(native, 'save_receipt', lambda *args: 'offline-only-receipt')
    if name == 'ConfirmationCommitGuard':
        adapter._confirmed = ({}, {'slot': 1}, {'pages': [{'kind': 'short', 'rows': []}]}, time.monotonic())
        # The outer guard must record an error even if an inner reader throws.
        adapter.confirmation_page = lambda: SimpleNamespace(
            analyze=lambda context, argv: context.run_recognition('injected', argv.image))
        adapter.submit({'slot': 1})
    else:
        if name == 'CurrentClanPrompt':
            import pcr_maa.preset_execution as business
            monkeypatch.setattr(business, 'identity_ok',
                                lambda *args: context.run_recognition('injected', FRAME))
        adapter._finish_result({}, {'slot': 1}, {'native_receipt': receipt})
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize('name', CALLBACKS)
@pytest.mark.parametrize('failure', [RuntimeError, ValueError])
def test_internal_callback_error_retains_traceback(name, failure, monkeypatch, capsys):
    assert invoke(name, Context(failure), monkeypatch) is None
    captured = capsys.readouterr()
    assert 'Traceback (most recent call last)' in captured.err
    assert f'{failure.__name__}: injected_callback_failure' in captured.err
    assert '.py' in captured.err and 'analyze' in captured.err
    assert not captured.out


@pytest.mark.parametrize('name', CALLBACKS)
def test_normal_no_match_has_no_error_output(name, monkeypatch, capsys):
    assert invoke(name, Context(), monkeypatch) is None
    captured = capsys.readouterr()
    assert not captured.err and not captured.out


def test_confirmation_collector_failure_retains_traceback(monkeypatch, capsys):
    collector = confirmation.ConfirmationCollector()
    monkeypatch.setattr(collector, '_collect', lambda *args: (_ for _ in ()).throw(
        RuntimeError('injected_collector_failure')))
    assert collector.run(None, None) is False
    assert collector.complete is False and collector.reason == 'confirmation_callback_failed'
    assert 'RuntimeError: injected_collector_failure' in capsys.readouterr().err


def test_confirmation_collector_normal_no_match_is_quiet(capsys):
    collector = confirmation.ConfirmationCollector()
    page = dict(kind='short', rows=[], gaps=True, summary={'stages': 0, 'times': 0})
    argv = SimpleNamespace(reco_detail=SimpleNamespace(raw_detail={'best': {'detail': page}}))
    assert collector.run(None, argv) is False
    assert collector.reason == 'empty_or_unreadable_rows'
    assert not capsys.readouterr().err


def test_confirmation_normal_deadline_is_quiet(monkeypatch, capsys):
    page = confirmation.ConfirmationPage()
    # Start in budget, then exhaust it in the existing same-frame reader.
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(confirmation.time, 'monotonic', lambda: next(clock))
    page.short_deadline = 1.0
    argv = SimpleNamespace(image=FRAME, custom_recognition_param='"short"')
    assert page.analyze(Context(), argv) is None
    assert page._short_signature is None and not capsys.readouterr().err


@pytest.mark.parametrize('text', ['?', '12/0', '4/3'])
def test_abyss_normal_unreadable_field_is_quiet(text, monkeypatch, capsys):
    operation = abyss_native._Words.integer if text == '?' else abyss_native._Words.fraction
    monkeypatch.setattr(abyss_native._Words, 'word', lambda self, name, override=None: text)
    def expected_rejection(self, name, override=None):
        operation(self, name)
        raise AssertionError('unreadable field unexpectedly accepted')
    monkeypatch.setattr(abyss_native._Words, 'recognize', expected_rejection)
    assert abyss_native.AbyssHomePage().analyze(Context(), SimpleNamespace(image=FRAME)) is None
    assert not capsys.readouterr().err


@pytest.mark.parametrize('kind', ['quote', 'ticket'])
@pytest.mark.parametrize('internal_error', [False, True])
def test_abyss_optional_inventory_only_ignores_expected_miss(kind, internal_error, monkeypatch, capsys):
    def word(self, name):
        if name.endswith('Title'):
            return '前哨关卡'
        if name.endswith('Mode'):
            return '深渊讨伐战'
        if name.endswith('Difficulty'):
            return list(reversed(abyss_native.DIFFICULTIES))[int(name[13]) - 1]
        return 'label'

    def integer(self, name):
        if name in ('AbyssQuoteTicketsOwned', 'AbyssTicketOwned'):
            if internal_error:
                raise ValueError('injected_inventory_failure')
            raise abyss_native._NoMatch('inventory_not_visible')
        return 3

    monkeypatch.setattr(abyss_native._Words, 'word', word)
    monkeypatch.setattr(abyss_native._Words, 'integer', integer)
    monkeypatch.setattr(abyss_native._Words, 'fraction', lambda *args: (3, 4))
    monkeypatch.setattr(abyss_native._Words, 'recognize', lambda *args: SimpleNamespace(hit=True, box=[0, 0, 1, 1]))
    monkeypatch.setattr(abyss_native, 'check_quote', lambda *args: 'ready')
    monkeypatch.setattr(abyss_native, 'check_ticket', lambda *args: 'ready')
    # Quote-row validation is independent of this optional-inventory probe.
    monkeypatch.setattr(abyss_native, '_quote_rows', lambda *args, **kwargs: [{'times': 3}])
    reading = {'abyss_identity': {}}
    page = abyss_native.AbyssQuotePage(reading) if kind == 'quote' else abyss_native.AbyssTicketPage(reading, {})
    result = page.analyze(Context(), SimpleNamespace(image=FRAME))
    captured = capsys.readouterr()
    if internal_error:
        assert result is None and 'ValueError: injected_inventory_failure' in captured.err
    else:
        assert result is not None and 'tickets_owned' not in result.detail and not captured.err


def test_confirmation_sdk_timeout_retains_traceback(capsys):
    argv = SimpleNamespace(image=FRAME, custom_recognition_param='"short"')
    assert confirmation.ConfirmationPage().analyze(Context(TimeoutError), argv) is None
    assert 'TimeoutError: injected_callback_failure' in capsys.readouterr().err
