"""Compatibility and shared day-policy boundaries after recognition extraction."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from pcr_maa import native, onekey_page, schedule_onekey


def test_legacy_imports_share_the_same_recognizer_and_constants():
    for name in ('OneKeyPage', 'ITEMS', 'ONEKEY_SCOPE_BLOCKER', 'ROW_LABELS',
                 'HEADERS', 'SETTINGS', 'SETTINGS_PASSIVE'):
        assert getattr(schedule_onekey, name) is getattr(onekey_page, name)


@pytest.mark.parametrize('historical_day', [None, '2020-09-24'])
def test_recognizer_uses_existing_clock_and_history_context(monkeypatch, tmp_path, historical_day):
    now = datetime.fromisoformat('2027-09-24T14:00:00+08:00')
    monkeypatch.setattr(schedule_onekey, 'now_cn', lambda: now)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path)
    monkeypatch.setattr(native, 'write_png', lambda path, image: bool(path.write_bytes(b'synthetic-frame')))
    monkeypatch.setattr(onekey_page.OneKeyPage, 'read',
                        lambda self, context, image, detail: detail.update(state='home', reason=None))
    reader = onekey_page.OneKeyPage(historical_day or '2027-09-24')
    token = schedule_onekey._HISTORY_DAY.set(historical_day)
    try:
        result = reader.analyze(object(), SimpleNamespace(custom_recognition_param='"observe"', image=None))
        assert result is not None
        assert reader.last['state'] == 'home'
        assert reader.last['captured_at'] == now.isoformat()
        assert reader.last['capture_index'] == 1
    finally:
        schedule_onekey._HISTORY_DAY.reset(token)


def test_wrong_day_still_consumes_dispatch_before_any_frame(monkeypatch):
    now = datetime.fromisoformat('2027-09-24T14:00:00+08:00')
    monkeypatch.setattr(schedule_onekey, 'now_cn', lambda: now)
    monkeypatch.setattr(native, 'write_png', lambda *args: pytest.fail('wrong day must not capture'))
    reader = onekey_page.OneKeyPage('2027-09-23', attest_current_plan=True)
    reader.allowed_action = 'onekey'
    token = schedule_onekey._HISTORY_DAY.set(None)
    try:
        result = reader.analyze(object(), SimpleNamespace(custom_recognition_param='"onekey"', image=None))
        assert result is None
        assert reader.last['reason'] == 'current_game_day_required'
        assert reader.allowed_action is None and reader.onekey_guard_used
    finally:
        schedule_onekey._HISTORY_DAY.reset(token)
