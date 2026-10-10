"""Stable combinations are data, never permission or execution contexts."""
import pytest


def test_combination_plan_preserves_order_without_restoring_authority():
    from pcr_maa import gui_contract as gui
    saved = gui.make_combination(['hard', 'event'])
    assert saved == dict(version=1, selected_modules=['hard', 'event'], rewards_final=False)
    assert gui.build_plan(saved) == ('hard', 'event')
    assert gui.validate_combination(saved) == saved
    assert gui.make_combination([], rewards_final=True)['rewards_final'] is True
    assert gui.build_plan(gui.make_combination([])) == ()


@pytest.mark.parametrize('key', ['execute', 'authorization', 'schedule_authorizations',
    'game_day', 'game_day_valid', 'context', 'attestation', 'schedule_attest_current_plan',
    'include_schedule_onekey', 'device', 'name'])
def test_combination_rejects_extra_fields(key):
    from pcr_maa import gui_contract as gui
    with pytest.raises(ValueError):
        gui.validate_combination(gui.make_combination(['event']) | {key: True})


@pytest.mark.parametrize('value', [None, True, 1, 'false', [], {}])
def test_tail_is_strict_boolean(value):
    from pcr_maa import gui_contract as gui
    if value is True:
        assert gui.build_plan(gui.make_combination([], rewards_final=value)) == ('rewards_final',)
    else:
        with pytest.raises(ValueError):
            gui.make_combination(['event'], rewards_final=value)


@pytest.mark.parametrize('value', [None, 'event', {}, {'event'}, True, 1,
    ['event', 'event'], ['unknown'], [None], [True], [['event']]])
def test_bad_selection(value):
    from pcr_maa import gui_contract as gui
    with pytest.raises(ValueError):
        gui.make_combination(value)


@pytest.mark.parametrize('value', [None, [], {}, {'version': True, 'selected_modules': [], 'rewards_final': False},
    {'version': 2, 'selected_modules': [], 'rewards_final': False},
    {'version': 1, 'selected_modules': [], 'rewards_final': None}])
def test_bad_combination(value):
    from pcr_maa import gui_contract as gui
    with pytest.raises(ValueError):
        gui.validate_combination(value)


def test_combination_copies_data_and_maps_results_without_io(monkeypatch):
    from pcr_maa import gui_contract as gui, supported_daily as chain
    original = ['hard', 'rewards']
    saved = gui.make_combination(original)
    restored = gui.validate_combination(saved)
    restored['selected_modules'].append('event')
    assert original == ['hard', 'rewards'] and saved['selected_modules'] == ['hard', 'rewards']
    monkeypatch.setattr(chain, 'execute_supported', lambda *a, **kw: pytest.fail('implicit execution'))
    report = dict(steps=[dict(name='hard', status='stopped')], outcome='stopped')
    text = gui.result_text(report)
    assert text.startswith('本次任务已停止。\n') and '困难：停止' in text
    assert 'daily_complete=' not in text and 'supported_chain_complete=' not in text
