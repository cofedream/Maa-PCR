"""Real saved frames, official CustomController, no live controller construction."""
import json
import hashlib
import subprocess
import sys
from types import SimpleNamespace

import pytest
from maa.define import AlgorithmEnum

from pcr_maa import native
from pcr_maa.replay import SavedImages

# Historical pixels + explicitly mocked 2027 clock: offline control-flow proof only.
SETTINGS_FRAMES = [f"scope-settings{i}" for i in range(21)]


def require_private_files(paths):
    if not all(path.is_file() for path in paths):
        pytest.skip('private saved-frame fixtures are not distributed')


def private_frame(path):
    require_private_files([path])
    return native.read_image(path)


@pytest.mark.parametrize('action', ['up', 'down'])
def test_navigation_real_capture_y_jitter_once(resource, monkeypatch, action):
    """Unmodified capture 7/10 pixels, real Maa OCR, official offline controller."""
    from datetime import datetime, timezone, timedelta
    import pcr_maa.schedule_onekey as module
    monkeypatch.setattr(module, 'now_cn', lambda: datetime(
        2026, 9, 25, 23, 47, tzinfo=timezone(timedelta(hours=8))))
    directory = native.ROOT / '.runtime/schedule-onekey-work/cli-8547fcec0ee24d948a3fbaf66edba7d4'
    paths = [directory / ('onekey-exec-' + token + '.png') for token in (
        'b33e6aa093d342bb9925442d1248dfa4', '520850e973a247ea9d7a3311ceb1f44f')]
    controller = SavedImages([[private_frame(path)] for path in paths])
    tasker = native.bind(resource, controller)
    reader = module.OneKeyPage('2026-09-25')
    assert resource.register_custom_recognition('OneKeyPage', reader)
    try:
        native.run(tasker, 'OneKeyExecRead')
        before = reader.last
        assert before['state'] == 'schedule', before.get('reason')
        assert before['rows']['dungeon_skip']['y'] == 550
        reader.expected = reader.signature(before)
        reader.allowed_action = action
        controller.stage = 1
        first = native.run(tasker, 'OneKeyDo_' + action)
        after = reader.last
        assert after['state'] == 'schedule', after.get('reason')
        assert after['rows']['dungeon_skip']['y'] == 551
        assert reader.signature(before) != reader.signature(after)
        expected = reader.signature(before)
        actual = reader.signature(after)
        assert {**expected, 'rows': {
            key: row | {'y': 551} if key == 'dungeon_skip' else row
            for key, row in expected['rows'].items()}} == actual
        second = native.run(tasker, 'OneKeyDo_' + action)
        native.save_receipt(dict(action=action, before=before, after=after,
            source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
            first=first, repeated=second, clicks=controller.clicks,
            swipes=controller.swipes), 'navigation-real-y-jitter')
        assert len(controller.swipes) == 1, 'same-page 1px OCR jitter must permit one navigation swipe'
        assert controller.clicks == [] and reader.allowed_action is None
        assert any(n['name'] == 'OneKeyInput_' + action and n['completed'] for n in first['nodes'])
        assert not any(n['name'] == 'OneKeyInput_' + action and n['completed'] for n in second['nodes'])
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


def test_navigation_real_adjacent_ocr_missing_row_close_once(resource, monkeypatch):
    """10/2 adjacent original frames, real OCR and formal read→close dispatch offline."""
    from datetime import datetime
    import pcr_maa.schedule_onekey as module
    monkeypatch.setattr(module, 'now_cn', lambda: datetime.fromisoformat('2026-10-02T19:55:00+08:00'))
    directory = native.ROOT / '.runtime/pilot-readiness-20261002/schedule-live-r13/continuation-03/native'
    paths = [directory / ('onekey-exec-' + token + '.png') for token in (
        '2fa2f5d0166e4459884cfe309605c12a', '9ade29391d8c427a823f1925cd89dd80')]
    require_private_files(paths)
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    assert hashes == [
        'edaa31be506e03a765a5a245d26c39151dea75c3b1ffdc6ecb6c6143918a00c3',
        '0850a6eb52a17decfcaec699ed4b720ab5853f78f571d1797fd01977539469b9']
    controller = SavedImages([[private_frame(path)] for path in paths])
    tasker = native.bind(resource, controller)
    reader = module.OneKeyPage('2026-10-02')
    assert resource.register_custom_recognition('OneKeyPage', reader)
    try:
        read_receipt = native.run(tasker, 'OneKeyExecRead')
        before = reader.last
        assert before['state'] == 'schedule', before.get('reason')
        assert {key: row['y'] for key, row in before['rows'].items()} == dict(
            exp_skip=187, normal_gacha_free=277, guild_like=369, memorial_rewards=550)
        assert all(row['state'] == 'complete' for row in before['rows'].values())
        assert before['sections'] == {} and before['bottom'] is True
        reader.expected = reader.signature(before)
        reader.allowed_action = 'close'
        controller.stage = 1
        first = native.run(tasker, 'OneKeyDo_close')
        after = reader.last
        assert after['state'] == 'schedule', after.get('reason')
        assert set(after['rows']) == set(before['rows']) - {'normal_gacha_free'}
        assert reader.signature(before) != reader.signature(after)
        repeated = native.run(tasker, 'OneKeyDo_close')
        receipt_path = native.save_receipt(dict(
            source_sha256=dict(zip(map(str, paths), hashes)), read=read_receipt,
            before=before, after=after, first=first, repeated=repeated,
            clicks=controller.clicks, swipes=controller.swipes), 'navigation-real-missing-row-close')
        assert len(controller.clicks) == 1 and controller.swipes == []
        assert reader.allowed_action is None
        assert any(n['name'] == 'OneKeyInput_close' and n['completed'] for n in first['nodes'])
        assert not any(n['name'] == 'OneKeyInput_close' and n['completed'] for n in repeated['nodes'])
        print('navigation_real_positive_receipt=' + str(receipt_path))
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


def navigation_page():
    from pcr_maa.schedule_onekey import ROW_LABELS
    return dict(state='schedule', reason=None, rows={
        key: dict(label=ROW_LABELS[key], state='pending', y=y)
        for key, y in zip(('arena_coins', 'princess_arena_coins', 'dungeon_skip'), (371, 460, 550))},
        sections={'delegated': dict(completed=0, total=9)}, bottom=True)


@pytest.fixture
def synthetic_guard_resource():
    resource = native.load_resource()
    actions = ('down', 'close', 'onekey', 'dungeon_confirm', 'mana_confirm',
               'exp_confirm', 'enter', 'settings_down')
    # Synthetic mismatch probes do not need the production animation wait.
    assert resource.override_pipeline({
        'OneKeyDo_' + action: {'timeout': 250} for action in actions})
    return resource


@pytest.mark.parametrize('action', ['down', 'close'])
def test_synthetic_guard_timeout_still_runs_callback_and_single_input(
        synthetic_guard_resource, monkeypatch, authorized, action):
    from copy import deepcopy
    import numpy as np
    import pcr_maa.schedule_onekey as module
    resource = synthetic_guard_resource
    page, calls = navigation_page(), []
    def read(self, context, image, detail):
        calls.append(1)
        detail.update(deepcopy(page))
    monkeypatch.setattr(module.OneKeyPage, 'read', read)
    controller = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    tasker = native.bind(resource, controller)
    reader = module.OneKeyPage(authorized['game_day'])
    reader.expected = reader.signature(page)
    reader.allowed_action = action
    assert resource.register_custom_recognition('OneKeyPage', reader)
    try:
        receipt = native.run(tasker, 'OneKeyDo_' + action)
        assert calls and receipt['native_ok']
        assert any(node['name'] == 'OneKeyInput_' + action and node['completed']
                   for node in receipt['nodes'])
        assert len(controller.clicks) + len(controller.swipes) == 1
        assert reader.allowed_action is None
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


@pytest.mark.parametrize('delta', [-2, -1, 0, 1, 2])
def test_navigation_bounded_y_comparison_preserves_full_signature(delta):
    from copy import deepcopy
    from pcr_maa.schedule_onekey import OneKeyPage
    before = OneKeyPage.signature(navigation_page())
    saved = deepcopy(before)
    after = deepcopy(before)
    after['rows']['dungeon_skip']['y'] += delta
    assert OneKeyPage.navigation_matches(before, after)
    assert before == saved  # No normalization/mutation of the global signature.
    assert (before == after) is (delta == 0)


@pytest.mark.parametrize('change', ['missing', 'added', 'both_empty'])
def test_navigation_ocr_row_set_change_requires_stable_shared_semantics(change):
    from copy import deepcopy
    from pcr_maa.schedule_onekey import OneKeyPage, ROW_LABELS
    before = OneKeyPage.signature(navigation_page())
    after = deepcopy(before)
    if change == 'missing':
        del after['rows']['dungeon_skip']
        after['rows']['arena_coins']['y'] += 2
    elif change == 'added':
        after['rows']['mana_skip'] = dict(label=ROW_LABELS['mana_skip'], state='pending', y=640)
    else:
        before['rows'] = after['rows'] = {}
    saved_before, saved_after = deepcopy(before), deepcopy(after)
    assert OneKeyPage.navigation_matches(before, after)
    assert before == saved_before and after == saved_after
    # Other signature fields remain mandatory even when neither OCR reading has rows.
    after['bottom'] = False
    assert not OneKeyPage.navigation_matches(before, after)


@pytest.mark.parametrize('action,change', [
    ('down', change) for change in (
        'state', 'row_state', 'identity', 'label', 'no_common', 'one_empty', 'extra_row',
        'order', 'tie', 'offset3', 'offset_minus3', 'offset100', 'sections',
        'bottom', 'quote', 'resources', 'settings', 'settings_top', 'unknown',
        'paid', 'reset')
] + [('close', change) for change in (
    'row_state', 'offset3', 'sections', 'bottom', 'no_common', 'order',
    'missing_conflict', 'missing_offset3')])
def test_navigation_changed_guard_zero_input_no_retry(synthetic_guard_resource, monkeypatch, authorized, action, change):
    """Synthetic negative receipts; native dispatch and official controller are real."""
    from copy import deepcopy
    import numpy as np
    import pcr_maa.schedule_onekey as module
    resource = synthetic_guard_resource
    calls = []
    before = navigation_page()
    after = deepcopy(before)
    rows = after['rows']
    if change in ('state', 'unknown'):
        after['state'] = 'home' if change == 'state' else 'unknown'
    elif change == 'row_state':
        rows['dungeon_skip']['state'] = 'complete'
    elif change == 'identity':
        rows['mana_skip'] = rows.pop('dungeon_skip')
    elif change == 'label':
        rows['dungeon_skip']['label'] = 'different row'
    elif change == 'no_common':
        rows.clear()
        rows['guild_like'] = dict(label=module.ROW_LABELS['guild_like'], state='pending', y=550)
    elif change == 'one_empty':
        rows.clear()
    elif change in ('missing_conflict', 'missing_offset3'):
        del rows['dungeon_skip']
        if change == 'missing_conflict':
            rows['arena_coins']['state'] = 'complete'
        else:
            rows['arena_coins']['y'] += 3
    elif change == 'extra_row':
        rows['unexpected'] = dict(label='执行', state='pending', y=555)
    elif change in ('order', 'tie'):
        # Crossing/tie inside the 2px bound must still fail independently of offset.
        before['rows']['arena_coins']['y'] = 459
        rows['arena_coins']['y'] = 461 if change == 'order' else 460
    elif change.startswith('offset'):
        rows['dungeon_skip']['y'] += {'offset3': 3, 'offset_minus3': -3, 'offset100': 100}[change]
    elif change == 'sections':
        after['sections']['delegated']['completed'] = 1
    elif change == 'bottom':
        after['bottom'] = False
    elif change in ('quote', 'resources', 'settings', 'settings_top'):
        after[change] = {'changed': True}

    original_read = module.OneKeyPage.read
    def read(self, context, image, detail):
        calls.append(int(image[0, 0, 0]))
        if int(image[0, 0, 0]) == 1 and change in ('paid', 'reset'):
            # Model a paid/reset modal obscuring the schedule panel.
            # Its caption alone is deliberately not a page-wide veto.
            label = '付费扭蛋' if change == 'paid' else '重置'
            context = DateContext([date_word(label, [500, 300, 100, 25])], None,
                                  image=image, blocked='OneKeyPanelBright')
            return original_read(self, context, image, detail)
        detail.update(deepcopy(after if int(image[0, 0, 0]) == 1 else before))
    monkeypatch.setattr(module.OneKeyPage, 'read', read)
    controller = SavedImages([[np.full((720, 1280, 3), i, dtype=np.uint8)] for i in range(3)])
    tasker = native.bind(resource, controller)
    reader = module.OneKeyPage(authorized['game_day'])
    assert resource.register_custom_recognition('OneKeyPage', reader)
    try:
        native.run(tasker, 'OneKeyExecRead')
        reader.expected = reader.signature(reader.last)
        reader.allowed_action = action
        controller.stage = 1
        initial_calls = len(calls)
        first = native.run(tasker, 'OneKeyDo_' + action)
        assert len(calls) > initial_calls and 1 in calls
        assert reader.allowed_action is None
        if change in ('paid', 'reset'):
            assert reader.last['state'] == 'unknown'
            assert reader.last['reason'] == 'unverified:OneKeyPanelBright'
        controller.stage = 2  # Even restoring the old page cannot re-arm a failed dispatch.
        first_calls = len(calls)
        repeated = native.run(tasker, 'OneKeyDo_' + action)
        assert len(calls) > first_calls and 2 in calls
        assert reader.allowed_action is None
        assert controller.clicks == controller.swipes == []
        assert all(not any(n['name'] == 'OneKeyInput_' + action and n['completed']
                           for n in receipt['nodes']) for receipt in (first, repeated))
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


@pytest.mark.parametrize('action', ['onekey', 'dungeon_confirm', 'mana_confirm', 'exp_confirm', 'enter', 'settings_down'])
@pytest.mark.parametrize('change', ['jitter', 'missing_row'])
def test_navigation_tolerance_never_applies_to_other_guards(synthetic_guard_resource, monkeypatch, authorized, action, change):
    """Synthetic jitter/row omission remains unequal at every consuming or other guard."""
    from copy import deepcopy
    import numpy as np
    import pcr_maa.schedule_onekey as module
    resource = synthetic_guard_resource
    calls, restored = [], False
    before = navigation_page()
    if action in ('dungeon_confirm', 'mana_confirm', 'exp_confirm'):
        before.update(state=action, quote=dict(times=2, remaining=2, tickets=2))
    elif action == 'enter':
        before['state'] = 'home'
    elif action == 'settings_down':
        before['state'] = 'settings'
    def read(self, context, image, detail):
        calls.append(1)
        detail.update(deepcopy(before))
        if restored:
            return
        if change == 'jitter':
            detail['rows']['dungeon_skip']['y'] += 1
        else:
            del detail['rows']['dungeon_skip']
    monkeypatch.setattr(module.OneKeyPage, 'read', read)
    controller = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    tasker = native.bind(resource, controller)
    reader = module.OneKeyPage(authorized['game_day'], attest_current_plan=True)
    reader.expected = reader.signature(before)
    reader.allowed_action = action
    assert resource.register_custom_recognition('OneKeyPage', reader)
    try:
        first = native.run(tasker, 'OneKeyDo_' + action)
        assert calls
        assert controller.clicks == controller.swipes == []
        assert reader.allowed_action is None
        first_calls = len(calls)
        restored = True
        repeated = native.run(tasker, 'OneKeyDo_' + action)
        assert len(calls) > first_calls and reader.allowed_action is None
        assert controller.clicks == controller.swipes == []
        assert all(not any(node['name'] == 'OneKeyInput_' + action and node['completed']
                           for node in receipt['nodes']) for receipt in (first, repeated))
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


def attested_controller(resource, monkeypatch, *, pending=None, fault=None):
    """Synthetic business pages through official CustomController/native guards."""
    import numpy as np
    import pcr_maa.schedule_onekey as module
    pending = set(module.ITEMS if pending is None else pending)
    states = ['home', 'schedule']
    if pending:
        for item, stage in [('dungeon_skip', 'dungeon'), ('mana_skip', 'mana'), ('exp_skip', 'exp')]:
            if item in pending:
                states += [stage + '_confirm', 'sweep_result']
        if 'guild_like' in pending:
            states += ['like_result']
        states += ['item_result', 'schedule']
    states += ['home']

    def read(self, context, image, detail):
        index = int(image[0, 0, 0])
        state = states[index]
        detail.update(state=state, reason=None)
        if state == 'home':
            detail['resources'] = dict(ap=100 + (10 if index and 'guild_like' in pending else 0),
                                       jewels=499 if index and fault == 'jewels' else 500)
        elif state == 'schedule':
            todo = pending if index == 1 else set()
            detail.update(rows={k: dict(state='pending' if k in todo else 'complete', y=i*10)
                                       for i, k in enumerate(module.ITEMS)},
                          sections={'delegated': dict(completed=9-len(todo), total=9)}, bottom=True)
        elif state in ('mana_confirm', 'exp_confirm'):
            detail['quote'] = dict(times=2, remaining=2, tickets=2)
        elif state == 'like_result':
            detail['quote'] = dict(ap_before=100, ap_after=110, ap_delta=10)
        if (fault == 'unknown' and index == 2) or (fault == 'resources' and index == 0):
            raise ValueError('synthetic_unverified_page_or_resources')

    monkeypatch.setattr(module.OneKeyPage, 'read', read)
    controller = SavedImages([[np.full((720, 1280, 3), i, dtype=np.uint8)] for i in range(len(states))])
    return native.bind(resource, controller), controller


def test_navigation_close_proof_uses_same_non_consuming_comparison(resource, monkeypatch, authorized):
    """Actual native producer receipts, synthetic pages/temporary ledgers, no historical reads."""
    from copy import deepcopy
    import pcr_maa.schedule_onekey as module
    from pcr_maa.onekey_history import verify_execution
    tasker, controller = attested_controller(resource, monkeypatch, pending=('arena_coins',))
    original_read, original_run = module.OneKeyPage.read, native.run
    def read(self, context, image, detail):
        original_read(self, context, image, detail)
        if detail.get('state') == 'schedule':
            for key, row in detail['rows'].items():
                row['label'] = module.ROW_LABELS[key]
            if int(image[2, 2, 0]) == 77:
                del detail['rows']['guild_like']
    def run(t, entry):
        if entry == 'OneKeyDo_close':
            controller.stages[controller.stage][0][2, 2, 0] = 77
        return original_run(t, entry)
    monkeypatch.setattr(module.OneKeyPage, 'read', read)
    monkeypatch.setattr(native, 'run', run)
    report = module.schedule_onekey(tasker, **authorized, attest_current_plan=True)
    assert report['outcome'] == 'executed', report.get('reason')
    assert report['home_verified'] and report['settlement_verified'] and report['nine_rows_verified']
    assert report['sent'] == ['enter', 'onekey', 'item_result', 'close']
    proof = report['journal_evidence']
    verify_execution(report, proof['start'], proof['end'], proof['actions'])
    close_index = next(i for i, receipt in enumerate(report['receipts'])
                       if receipt['entry'] == 'OneKeyDo_close')
    before = report['receipts'][close_index-1]['reading']
    guard = report['receipts'][close_index]['reading']
    assert set(guard['rows']) == set(before['rows']) - {'guild_like'}
    assert module.OneKeyPage.signature(before) != module.OneKeyPage.signature(guard)
    native.save_receipt(report | dict(clicks=controller.clicks), 'navigation-close-proof')
    for fault in ('common_state', 'common_y3', 'bottom', 'no_common', 'consumer_row_missing'):
        changed = deepcopy(report)
        changed_proof = changed['journal_evidence']
        changed_proof['end']['receipts'] = changed['receipts']
        page = changed['receipts'][close_index]['reading']
        if fault == 'common_state':
            page['rows']['arena_coins']['state'] = 'pending'
        elif fault == 'common_y3':
            page['rows']['arena_coins']['y'] += 3
        elif fault == 'bottom':
            page['bottom'] = False
        elif fault == 'no_common':
            page['rows'] = {}
        else:
            consumer = next(receipt['reading'] for receipt in changed['receipts']
                            if receipt['entry'] == 'OneKeyDo_onekey')
            del consumer['rows']['guild_like']
        with pytest.raises(ValueError, match='execution_evidence_unverified'):
            verify_execution(changed, changed_proof['start'], changed_proof['end'], changed_proof['actions'])


def test_attested_current_plan_sends_once_with_specific_results(resource, monkeypatch, authorized):
    import pcr_maa.schedule_onekey as module
    from pcr_maa.preset_execution import Journal
    tasker, controller = attested_controller(resource, monkeypatch)
    report = module.schedule_onekey(tasker, **authorized, attest_current_plan=True)
    assert report['outcome'] == 'executed', report.get('reason')
    assert report['settings_assurance'] == 'user_attested_current_plan'
    assert 'settings_audit' not in report and not report['daily_complete']
    assert report['sent'] == ['enter', 'onekey', 'dungeon_confirm', 'dungeon_result',
                              'mana_confirm', 'mana_result', 'exp_confirm', 'exp_result',
                              'like_result', 'item_result', 'close']
    assert controller.clicks.count([880, 640]) == 1 and not controller.swipes
    assert report['resources']['jewels_delta'] == 0
    root = authorized['journal'].events()[0]
    assert root['settings_assurance'] == 'user_attested_current_plan'
    actions = Journal(authorized['journal'].directory / 'actions' / root['intent_id']).events()
    intent = next(e for e in actions if e['operation'] == 'onekey' and e['state'] == 'in_flight')
    assert intent['settings_assurance'] == 'user_attested_current_plan'
    native.save_receipt(report | dict(clicks=controller.clicks), 'attested-synthetic-once')


@pytest.mark.parametrize('fault', ['unknown', 'jewels', 'resources', 'unauthorized'])
def test_attested_uncertainty_keeps_intent_and_never_resends(resource, monkeypatch, authorized, fault):
    import pcr_maa.schedule_onekey as module
    tasker, controller = attested_controller(resource, monkeypatch, pending=('memorial_rewards',), fault=fault)
    options = authorized | dict(attest_current_plan=True)
    if fault == 'unauthorized':
        options['authorizations'] = ['arena_coins']
    report = module.schedule_onekey(tasker, **options)
    assert report['outcome'] == 'unknown' and not report['daily_complete']
    assert not report['schedule_executed'] and not report['home_verified']
    assert report['sent'].count('onekey') == (1 if fault in ('unknown', 'jewels') else 0)
    if fault == 'unknown':
        assert report['sent'] == ['enter', 'onekey']
    if fault == 'jewels':
        assert report['reason'] == 'jewel_balance_changed_parent_reconciliation'
    clicks = list(controller.clicks)
    repeated = module.schedule_onekey(tasker, **options)
    assert repeated['sent'] == [] and controller.clicks == clicks
    if fault in ('unknown', 'jewels'):
        assert repeated['reason'] == 'unsettled_intent_any_day_no_resend'
    else:
        assert not authorized['journal'].events()


@pytest.mark.parametrize('change', [dict(game_day=None), dict(game_day='2026-09-25'),
    dict(authorizations=[]), dict(authorizations=['paid_gacha']), dict(device=None)])
def test_attested_missing_day_or_authority_zero_native(authorized, change):
    from pcr_maa.schedule_onekey import schedule_onekey
    report = schedule_onekey(object(), **(authorized | change), attest_current_plan=True)
    assert report['outcome'] == 'not_authorized' and report['receipts'] == report['sent'] == []
    assert not authorized['journal'].directory.exists()


@pytest.mark.parametrize('value', [1, 'true', None])
def test_attested_flag_is_strict_boolean(authorized, value):
    from pcr_maa.schedule_onekey import schedule_onekey
    with pytest.raises(ValueError, match='attestation_requires_explicit_execute_boolean'):
        schedule_onekey(object(), **authorized, attest_current_plan=value)


def test_attested_completed_rows_skipped_and_confirmation_not_inherited(resource, monkeypatch, authorized):
    import pcr_maa.schedule_onekey as module
    from pcr_maa.preset_execution import Journal
    options = authorized | dict(authorizations=['memorial_rewards'])
    tasker, controller = attested_controller(resource, monkeypatch, pending=('memorial_rewards',))
    report = module.schedule_onekey(tasker, **options, attest_current_plan=True)
    assert report['outcome'] == 'executed'
    assert report['sent'] == ['enter', 'onekey', 'item_result', 'close']
    tasker, controller = attested_controller(resource, monkeypatch, pending=())
    report = module.schedule_onekey(tasker, **options)
    assert report['outcome'] == 'already_complete' and report['sent'] == ['enter', 'close']
    assert report['settings_assurance'] == 'unattested'
    # The next independent invocation gets no permission from the former flag.
    tasker, controller = attested_controller(resource, monkeypatch, pending=('memorial_rewards',))
    options['journal'] = Journal(authorized['journal'].directory.parent / 'independent-synthetic-ledger')
    report = module.schedule_onekey(tasker, **options)
    assert report['reason'] == module.ONEKEY_SCOPE_BLOCKER and report['sent'] == ['enter']


@pytest.mark.parametrize('failed_first', [False, True])
def test_attested_native_guard_cannot_be_rearmed(resource, monkeypatch, authorized, failed_first):
    import pcr_maa.schedule_onekey as module
    tasker, controller = attested_controller(resource, monkeypatch, pending=('memorial_rewards',))
    controller.stage = 1
    reader = module.OneKeyPage(authorized['game_day'], attest_current_plan=True)
    assert resource.register_custom_recognition('OneKeyPage', reader)
    try:
        native.run(tasker, 'OneKeyExecRead')
        reader.expected = {} if failed_first else reader.signature(reader.last)
        reader.allowed_action = 'onekey'
        native.run(tasker, 'OneKeyDo_onekey')
        assert len(controller.clicks) == (0 if failed_first else 1)
        controller.stage = 1
        native.run(tasker, 'OneKeyExecRead')
        reader.expected = reader.signature(reader.last)
        reader.allowed_action = 'onekey'
        native.run(tasker, 'OneKeyDo_onekey')
        assert len(controller.clicks) == (0 if failed_first else 1)
        assert reader.allowed_action is None
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


@pytest.mark.parametrize('label', ['购买玛那', '付费扭蛋', '重置', '宝石', '确认购买', '执行'])
def test_attested_real_reader_still_rejects_paid_reset_extra_rows(tmp_path, monkeypatch, authorized, label):
    import numpy as np
    import pcr_maa.schedule_onekey as module
    monkeypatch.setattr(native, 'RUNTIME', tmp_path)
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    reader = module.OneKeyPage(authorized['game_day'], attest_current_plan=True)
    reader.allowed_action = 'onekey'
    reader.expected = {}
    argv = SimpleNamespace(image=image, custom_recognition_param='"onekey"')
    # Unsupported row identity plus its actual executable control, not a stray caption.
    words = [date_word(label, [500, 300, 170, 25]), date_word('执行', [940, 300, 70, 25])]
    context = DateContext(words, None, image=image)
    assert reader.analyze(context, argv) is None
    assert reader.last['state'] == 'unknown' and reader.allowed_action is None
    assert reader.last['reason'] == 'unrecognized_executable_row'


def test_attested_cli_forwarding_and_readonly_conflicts(monkeypatch, capsys, authorized):
    from pcr_maa.cli import main
    from pcr_maa import device
    calls = []
    monkeypatch.setattr(device, 'navigate', lambda *a, **kw: calls.append(kw) or dict(outcome='unknown'))
    args = ['schedule-onekey', '--attest-current-plan', '--execute', '--game-day', authorized['game_day'],
            '--input-display', '2', '--screenshot-display', '123', '--authorize', 'memorial_rewards']
    assert main(args) == 2
    assert calls == [dict(onekey=dict(execute=True, game_day=authorized['game_day'],
                                     authorizations=['memorial_rewards'], attest_current_plan=True))]
    calls.clear()
    assert main([arg for arg in args if arg != '--execute']) == 1
    assert main(args + ['--image', 'never-read.png']) == 1
    assert calls == []
    capsys.readouterr()


@pytest.mark.parametrize('value,valid', [(True, True), (False, True), (1, False), ('true', False)])
def test_attested_device_validates_before_resource(tmp_path, monkeypatch, authorized, value, valid):
    from pcr_maa import device
    adb = tmp_path / 'adb.exe'
    adb.write_bytes(b'never executed')
    connection = device.validate_connection(dict(adb_path=str(adb), serial=device.SERIAL))
    monkeypatch.setattr(native, 'ROOT', authorized['journal'].directory.parent)
    class ReachedValidatedResource(Exception):
        pass
    def stop():
        raise ReachedValidatedResource()
    monkeypatch.setattr(native, 'load_resource', stop)
    options = {k: authorized[k] for k in ('execute', 'game_day', 'authorizations')}
    with pytest.raises(ReachedValidatedResource if valid else ValueError,
                       match=None if valid else '^Invalid or conflicting standalone one-key arguments$'):
        device.navigate(2, '123', onekey=options | dict(attest_current_plan=value), connection=connection)


@pytest.mark.parametrize('kind', ['mana_confirm', 'exp_confirm'])
@pytest.mark.parametrize('change', [dict(times='1'), dict(remaining='0'), dict(tickets='3'),
                                   dict(risk='扫荡券不足'), dict(risk='重置'), dict(risk='宝石')])
def test_attested_quote_and_resource_limits_still_apply(kind, change):
    detail, error = read_synthetic_quote(kind, attest_current_plan=True, **change)
    assert error and detail.get('state') != kind


DATE_ROI = [260, 45, 85, 36]


@pytest.mark.parametrize('words,result', [([], None),
    ([dict(text='9_', box=[267,51,20,24], score=.761)], None)])
def test_state_first_date_absence_is_observation_only(words, result):
    detail, error, _ = read_date(words, result)
    assert error is None and detail['state'] == 'schedule'
    assert detail['date'] is None


@pytest.mark.parametrize('words', [
    [dict(text='9/25',box=[267,51,62,24],score=.99)]*2,
    [dict(text='9_',box=[267,51,20,24],score=.99),dict(text='25',box=[303,51,25,24],score=.999)],
])
def test_state_first_nonconflicting_tokenization_is_not_risk(words):
    detail,error,_=read_date(words,date_native())
    assert error is None and detail['state']=='schedule' and detail['date']==[9,25]


def test_state_first_signature_ignores_only_optional_date():
    from pcr_maa.schedule_onekey import OneKeyPage
    page = dict(state='schedule', date=[9,25], rows={'arena_coins': {'state':'complete'}},
                sections={'delegated':{'completed':1,'total':9}})
    assert OneKeyPage.signature(page) == OneKeyPage.signature(page | {'date':None})
    assert OneKeyPage.signature(page) != OneKeyPage.signature(page | {'rows':{}})


def state_first_history(authorized, states, *, action_pending=False):
    """Legacy zero-consumer observation. Pending today can follow yesterday's
    completed observation, never a fabricated partially successful root."""
    from datetime import date, timedelta
    from pcr_maa.preset_execution import Journal
    journal = authorized['journal']
    intent = 'a'*32
    day=authorized['game_day']
    if 'pending' in states.values():
        day=(date.fromisoformat(day)-timedelta(days=1)).isoformat()
        states=dict.fromkeys(states,'complete')
    common = dict(operation='schedule_onekey', game_day=day,
                  device=authorized['device'], intent_id=intent)
    with journal.locked():
        journal.append(common | dict(state='in_flight'))
        journal.append(common | dict(state='settled', outcome='already_complete', rows=states))
    action = Journal(journal.directory / 'actions' / intent)
    with action.locked():
        action.append(dict(state='in_flight', operation='enter', game_day=day))
        action.append(dict(state='settled', operation='enter', game_day=day))
        action.append(dict(state='in_flight', operation='close', game_day=day))
        if not action_pending:
            action.append(dict(state='settled', operation='close', game_day=day))


def test_integration_executed_history_requires_consumer(authorized):
    from pcr_maa.onekey_history import read_history
    from pcr_maa.schedule_onekey import ITEMS
    state_first_history(authorized, dict.fromkeys(ITEMS, 'complete'))
    events = authorized['journal'].events()
    events[-1]['outcome'] = 'executed'
    authorized['journal'].path.write_text(''.join(json.dumps(e)+'\n' for e in events), encoding='utf-8')
    with authorized['journal'].locked(), pytest.raises(ValueError):
        read_history(authorized['journal'], authorized['game_day'], authorized['device'])


def test_integration_native_guard_fresh_pixels_and_navigation_transition(resource, authorized, monkeypatch):
    """Actual native receipts: signatures remain valid while fresh pixels differ."""
    import numpy as np
    import pcr_maa.schedule_onekey as module
    tasker, controller = attested_controller(resource, monkeypatch, pending=('arena_coins',))
    # Rebind an official SavedImages controller with a fresh guard capture and
    # a safe transient read after close; no synthetic native.run return values.
    stages = [[np.full((720,1280,3), i, dtype=np.uint8)] for i in range(5)]
    guard = stages[1][0].copy(); guard[2,2,0] = 77
    original = module.OneKeyPage.read
    def read(self, context, image, detail):
        if image[0,0,0] == 17:
            detail['words']=[]
            raise ValueError('unverified:ScheduleGuard')
        return original(self,context,image,detail)
    monkeypatch.setattr(module.OneKeyPage,'read',read)
    controller=SavedImages(stages)
    tasker=native.bind(resource,controller)
    original_run=native.run
    transition=[False]
    def run(t,entry):
        if entry=='OneKeyDo_onekey':
            controller.stages[1]=[guard]
        transient = entry=='OneKeyExecRead' and controller.stage==4 and not transition[0]
        if transient:
            controller.stages[4]=[np.full((720,1280,3),17,dtype=np.uint8)]
        receipt=original_run(t,entry)
        if transient:
            transition[0]=True
            controller.stages[4]=[np.full((720,1280,3),4,dtype=np.uint8)]
        return receipt
    monkeypatch.setattr(native,'run',run)
    result=module.schedule_onekey(tasker,**authorized,attest_current_plan=True)
    assert result['outcome']=='executed', result.get('reason')
    from pcr_maa.onekey_history import read_history
    with authorized['journal'].locked():
        assert read_history(authorized['journal'],authorized['game_day'],authorized['device'])[0] == set(module.ITEMS)


def test_player_managed_pending_scope_blocks_before_consumer_intent(resource, monkeypatch, authorized):
    import pcr_maa.schedule_onekey as module
    from pcr_maa.preset_execution import Journal
    def forbidden(*args):
        pytest.fail('normal path opened settings (including 黎明界迷宫设定)')
    monkeypatch.setattr(module, 'collect_settings', forbidden)
    tasker, controller = state_first_controller(resource, monkeypatch, pending=module.ITEMS)
    result = module.schedule_onekey(tasker, **authorized)
    assert result['reason'] == 'onekey_hidden_paid_scope_unproven'
    assert result['before_rows'] == dict.fromkeys(module.ITEMS, 'pending')
    assert result['sent'] == ['enter'] and len(controller.clicks) == 1
    assert all(r['entry'] != 'OneKeyDo_onekey' for r in result['receipts'])
    assert controller.swipes == [] and not result['one_key_attempted']
    assert not result['daily_complete'] and 'settings_audit' not in result
    assert not authorized['journal'].events()
    assert result['consumer_state'] == 'not_started'
    assert module.schedule_onekey(tasker, **authorized)['sent'] == []
    assert len(controller.clicks) == 1


def test_player_managed_native_guard_rejects_legacy_settings_attestation(resource, monkeypatch, authorized):
    import pcr_maa.schedule_onekey as module
    tasker, controller = state_first_controller(resource, monkeypatch, pending=module.ITEMS)
    controller.stage = 1
    reader = module.OneKeyPage(authorized['game_day'])
    assert resource.register_custom_recognition('OneKeyPage', reader)
    try:
        native.run(tasker, 'OneKeyExecRead')
        reader.expected = reader.signature(reader.last)
        reader.settings_verified = True  # A former/handwritten audit cannot grant scope.
        reader.allowed_action = 'onekey'
        native.run(tasker, 'OneKeyDo_onekey')
        assert controller.clicks == controller.swipes == []
        assert reader.allowed_action is None
        assert reader.last['reason'] == 'onekey_hidden_paid_scope_unproven'
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


@pytest.mark.parametrize('label', ['购买玛那', '付费扭蛋', '重置', '执行'])
def test_player_managed_extra_executable_or_paid_page_rejected(label):
    words = [date_word(label, [500,300,170,25]), date_word('执行', [940,300,70,25])]
    _, error, _ = read_date(words, None)
    assert error == 'unrecognized_executable_row'


def test_player_managed_unknown_return_keeps_intent_no_resend(resource, monkeypatch, authorized):
    import pcr_maa.schedule_onekey as module
    tasker, controller = state_first_controller(resource, monkeypatch, fail=6)
    result = module.schedule_onekey(tasker, **authorized)
    assert result['outcome'] == 'unknown' and not result['home_verified']
    assert result['sent'] == ['enter', 'close'] and len(controller.clicks) == 2
    assert not result['one_key_attempted'] and not result['daily_complete']
    assert not authorized['journal'].events()
    assert result['consumer_state'] == 'not_started'
    assert module.schedule_onekey(tasker, **authorized)['sent'] == []
    assert len(controller.clicks) == 2


def state_first_controller(resource, monkeypatch, *, pending=(), fail=None, audit=False):
    """Official CustomController; synthetic page receipts, no historical pixels."""
    import numpy as np
    import pcr_maa.schedule_onekey as module
    def read(self, context, image, detail):
        stage = int(image[0,0,0])
        if stage in (1,6):
            detail.update(state='home', resources=dict(ap=100, jewels=500))
        elif stage == 3:
            detail.update(state='settings', settings={k:v[1] for k,v in module.SETTINGS.items()}, settings_top=True)
        else:
            todo = set(pending) if stage < 5 else set()
            detail.update(state='schedule', date=None if stage==4 else [9,24],
                rows={k:dict(state='pending' if k in todo else 'complete',y=i*10) for i,k in enumerate(module.ITEMS)},
                sections={'delegated':dict(completed=9-len(todo),total=9)}, bottom=True)
        if fail == stage:
            raise ValueError('unsafe_or_changed_page')
        detail['reason'] = None
    monkeypatch.setattr(module.OneKeyPage,'read',read)
    stages = [1,2,3,4,6] if audit else [1,2,6]
    controller = SavedImages([[np.full((720,1280,3),n,dtype=np.uint8)] for n in stages])
    return native.bind(resource,controller), controller


@pytest.mark.parametrize('pending', [(), ('memorial_rewards',)])
def test_state_first_settled_same_or_next_day_reobserves_and_only_pending(resource, monkeypatch, authorized, pending):
    import pcr_maa.schedule_onekey as module
    prior = {k:'pending' if k in pending else 'complete' for k in module.ITEMS}
    state_first_history(authorized, prior)
    tasker, controller = state_first_controller(resource,monkeypatch,pending=pending)
    result = module.schedule_onekey(tasker, **(authorized | dict(authorizations=pending or ('arena_coins',))))
    assert result['outcome'] == ('unknown' if pending else 'already_complete'), result.get('reason')
    assert result['sent'] == ['enter'] + ([] if pending else ['close'])
    assert len(controller.clicks) == (1 if pending else 2) and controller.swipes == []
    assert result['home_verified'] is (not pending)
    assert 'settings_audit' not in result
    if pending:
        assert result['reason'] == 'onekey_hidden_paid_scope_unproven'
        assert set(result['before_rows']) == set(module.ITEMS)
        assert not result['one_key_attempted']


def test_state_first_completed_row_rollback_never_consumes(resource, monkeypatch, authorized):
    import pcr_maa.schedule_onekey as module
    state_first_history(authorized, dict.fromkeys(module.ITEMS,'complete'))
    tasker, controller = state_first_controller(resource,monkeypatch,pending=('memorial_rewards',))
    result = module.schedule_onekey(tasker,**authorized)
    assert result['outcome']=='unknown' and result['reason']=='same_day_completed_row_regressed'
    assert result['sent']==['enter'] and len(controller.clicks)==1
    assert authorized['journal'].events()[-1]['state']=='settled'
    assert result['consumer_state'] == 'not_started'
    assert module.schedule_onekey(object(),**authorized)['sent']==[]


def test_state_first_unsettled_child_blocks_even_settled_parent(authorized):
    import pcr_maa.schedule_onekey as module
    state_first_history(authorized, dict.fromkeys(module.ITEMS,'complete'), action_pending=True)
    result = module.schedule_onekey(object(),**authorized)
    assert result['outcome']=='unknown' and result['reason']=='unsettled_action_any_day_no_resend'
    assert result['sent']==result['receipts']==[]


@pytest.mark.parametrize('bad', ['日期已变更', '重新登录', '购买', '宝石', '重置', '通信错误'])
def test_state_first_missing_date_never_hides_risk(bad):
    # These cases represent an obstructing modal/connection, not decoration text.
    blocked = 'HomeDataConnection' if bad == '通信错误' else 'OneKeyPanelBright'
    detail, error, _ = read_date([date_word(bad,[500,300,170,25])], None, blocked=blocked)
    assert error and detail.get('state') != 'schedule'


@pytest.mark.parametrize('day_text,expected', [(None,'schedule'), ('9/25','schedule'), ('9/26','unknown')])
def test_state_first_snapshot_date_optional_without_permission(tmp_path,monkeypatch,day_text,expected):
    import numpy as np
    from pcr_maa.schedule_onekey import Snapshot
    monkeypatch.setattr(native,'RUNTIME',tmp_path)
    image=np.zeros((720,1280,3),dtype=np.uint8)
    class Context(DateContext):
        def run_recognition(self,name,current,override=None):
            if name == 'HomeDataConnection':
                return SimpleNamespace(hit=False, raw_detail={}, reco_id=1)
            result=super().run_recognition(name,current,override)
            if result is not None: result.reco_id=1
            return result
    reader=Snapshot('2027-09-25')
    reader.analyze(Context([],date_native(day_text) if day_text else None,image),SimpleNamespace(image=image))
    assert reader.observation['page']==expected
    assert reader.observation.get('visible_month_day') == ([9,25] if day_text=='9/25' else None)


@pytest.mark.parametrize('fault', ['risk','count_unknown','unauthorized','unknown_page'])
def test_state_first_reentry_no_input_after_uncertainty(resource, monkeypatch, authorized, fault):
    import pcr_maa.schedule_onekey as module
    pending=('memorial_rewards',)
    state_first_history(authorized,{k:'pending' if k in pending else 'complete' for k in module.ITEMS})
    tasker,c=state_first_controller(resource,monkeypatch,pending=pending,fail=2 if fault=='risk' else None)
    base=module.OneKeyPage.read
    def changed(self,context,image,detail):
        base(self,context,image,detail)
        if fault=='unknown_page' and image[0,0,0]==2:
            detail.update(state='unknown', reason='unknown_result')
        if fault=='count_unknown' and image[0,0,0]==2:
            detail['sections']['delegated']['completed']=7
    monkeypatch.setattr(module.OneKeyPage,'read',changed)
    args=authorized | (dict(authorizations=['arena_coins']) if fault=='unauthorized' else {})
    result=module.schedule_onekey(tasker,**args)
    assert result['outcome']=='unknown'
    assert result['reason'] == {
        'risk': 'unsafe_or_changed_page',
        'count_unknown': 'row_marks_counter_disagree',
        'unauthorized': ('pending_items_not_authorized:memorial_rewards'
            '；游戏原生一键不能跳过这些未授权项目。请补充本次对应项目的授权，'
            '或先在游戏内完成它们后重新开始；本次未发送一键。'),
        'unknown_page': 'unknown_result',
    }[fault]
    assert 'onekey' not in result['sent']
    assert 'close' not in result['sent']
    count=len(c.clicks)
    again=module.schedule_onekey(tasker,**args)
    assert again['sent']==[] and len(c.clicks)==count
    assert again['input_evidence']=='unknown'


def test_state_first_unsettled_child_cannot_be_cleared_by_clock(authorized,monkeypatch):
    from datetime import datetime, timezone, timedelta
    import pcr_maa.schedule_onekey as module
    state_first_history(authorized,dict.fromkeys(module.ITEMS,'complete'),action_pending=True)
    monkeypatch.setattr(module,'now_cn',lambda:datetime(2027,9,25,14,tzinfo=timezone(timedelta(hours=8))))
    result=module.schedule_onekey(object(),**(authorized|dict(game_day='2027-09-25')))
    assert result['reason']=='unsettled_action_any_day_no_resend' and result['sent']==[]


def test_state_first_date_optional_settings_audit_roundtrip(resource,monkeypatch,authorized):
    import pcr_maa.schedule_onekey as module
    tasker,c=state_first_controller(resource,monkeypatch,audit=True)
    report=module.schedule_settings_audit(tasker,audit=True,game_day=authorized['game_day'],device=authorized['device'])
    assert report['outcome']=='audited' and report['home_verified']
    assert report['sent']==['enter','settings_open','settings_close','close']
    assert len(c.clicks)==4 and c.swipes==[] and not report['one_key_attempted']


def test_state_first_missing_date_guard_accepts_once(tmp_path, monkeypatch):
    import numpy as np
    from datetime import datetime,timezone,timedelta
    import pcr_maa.schedule_onekey as module
    monkeypatch.setattr(module,'now_cn',lambda:datetime(2027,9,25,15,tzinfo=timezone(timedelta(hours=8))))
    monkeypatch.setattr(native,'RUNTIME',tmp_path)
    image=np.zeros((720,1280,3),dtype=np.uint8)
    context=DateContext([],date_native(),image)
    reader=module.OneKeyPage('2027-09-25')
    args=SimpleNamespace(image=image,custom_recognition_param='"observe"')
    reader.analyze(context,args)
    reader.expected=reader.signature(reader.last)
    reader.allowed_action='close'
    context.result=None
    args.custom_recognition_param='"close"'
    assert reader.analyze(context,args) is not None
    assert reader.last['date'] is None and reader.allowed_action is None
    assert reader.analyze(context,args) is None


@pytest.mark.parametrize('fault',['missing_rows','device','orphan','action_binding'])
def test_state_first_incomplete_history_is_not_permission(authorized,fault):
    import pcr_maa.schedule_onekey as module
    from pcr_maa.preset_execution import Journal
    state_first_history(authorized,dict.fromkeys(module.ITEMS,'complete'))
    journal=authorized['journal']
    if fault=='orphan':
        extra=Journal(journal.directory/'actions'/('b'*32))
        with extra.locked():
            extra.append(dict(state='in_flight',operation='onekey',game_day='2026-09-23'))
    elif fault=='action_binding':
        extra=Journal(journal.directory/'actions'/('a'*32))
        with extra.locked():
            extra.append(dict(state='in_flight',operation='onekey',game_day=authorized['game_day']))
            extra.append(dict(state='settled',operation='close',game_day=authorized['game_day']))
    else:
        records=journal.events()
        if fault=='missing_rows': records[-1].pop('rows')
        else: records[-1]['device']={}
        journal.path.write_text(''.join(json.dumps(r)+'\n' for r in records),encoding='utf-8')
    report=module.schedule_onekey(object(),**authorized)
    assert report['outcome']=='unknown' and report['sent']==report['receipts']==[]


def date_word(text, box, score=.999):
    return dict(text=text, box=box, score=score)


def date_native(text='9 / 25', score=.855716):
    word = date_word(text, DATE_ROI.copy(), score)
    return SimpleNamespace(algorithm=AlgorithmEnum.OCR, hit=True,
                           raw_detail=dict(all=[word], filtered=[word], best=word))


class DateContext:
    def __init__(self, words, result, image=None, blocked=None):
        self.words, self.result, self.image, self.blocked = words, result, image, blocked
        self.calls = []

    def run_recognition(self, name, image, override=None):
        if name == 'HomeDataConnection':
            return SimpleNamespace(hit=self.blocked == name, raw_detail={})
        assert image is self.image  # Never fetch/replace the frame for fallback.
        self.calls.append(name)
        if name == 'OneKeyOCR':
            return SimpleNamespace(algorithm=AlgorithmEnum.OCR, hit=True,
                raw_detail={'all':[*self.words, date_word('关闭',[1050,610,50,25])]})
        if name == 'OneKeyDate':
            return self.result
        assert name in ('ScheduleGuard','OneKeyLabel','OneKeyPanelBright','OneKeyBottom')
        return SimpleNamespace(hit=name not in (self.blocked, 'OneKeyBottom'), raw_detail={})


def read_date(words, result, *, blocked=None):
    from pcr_maa.schedule_onekey import OneKeyPage
    context = DateContext(words, result, blocked=blocked)
    detail = dict(evidence={})
    try:
        OneKeyPage('2027-09-25').read(context, None, detail)
        error = None
    except ValueError as exc:
        error = str(exc)
    return detail, error, context.calls


@pytest.mark.parametrize('words', [
    [date_word('9_', [267,51,20,24], .761), date_word('25',[303,51,25,24])],
    [date_word('9', [267,51,12,24], .998), date_word('25',[303,51,25,24])],
    [],
])
def test_date_roi_fallback_uses_same_image_for_anonymous_fragments(words):
    detail, error, calls = read_date(words, date_native())
    assert error is None and detail['date']==[9,25] and detail['state']=='schedule'
    assert calls.count('OneKeyDate')==1


@pytest.mark.parametrize('words', [
    [date_word('9/25',[267,51,62,24], .984)],
    [date_word('9',[267,51,12,24]),date_word('/25',[290,51,40,24])],
])
def test_date_roi_keeps_readable_full_frame_path(words):
    detail, error, calls = read_date(words, None)
    assert error is None and detail['date']==[9,25]
    assert 'OneKeyDate' not in calls


@pytest.mark.parametrize('words', [
    [date_word('9/26',[267,51,62,24])],
    [date_word('8',[267,51,12,24]), date_word('25',[303,51,25,24])],
    [date_word('9',[267,51,12,24]), date_word('26',[303,51,25,24])],
    [date_word('9/25',[267,51,62,24]),date_word('9/26',[267,51,62,24])],
])
def test_date_roi_never_overrides_high_confidence_conflict_or_duplicates(words):
    detail, error, _ = read_date(words, date_native())
    assert error is not None and detail.get('state') != 'schedule'


@pytest.mark.parametrize('fault', ['missing','wrong_algorithm','miss','low','nan','two',
    'no_slash','wrong_day','invalid_date','wrong_box','missing_best','bad_filtered','bad_all','bad_score'])
def test_date_roi_rejects_bad_native_receipt(fault):
    result = date_native()
    if fault=='missing': result=None
    elif fault=='wrong_algorithm': result.algorithm=AlgorithmEnum.ColorMatch
    elif fault=='miss': result.hit=False
    elif fault in ('low','nan','bad_score'):
        result.raw_detail['best']['score'] = {'low':.849, 'nan':float('nan'), 'bad_score':True}[fault]
    elif fault=='two': result.raw_detail['all'] *= 2
    elif fault in ('no_slash','wrong_day','invalid_date'):
        result.raw_detail['best']['text']={'no_slash':'9 25','wrong_day':'9/26','invalid_date':'9/31'}[fault]
    elif fault=='wrong_box': result.raw_detail['best']['box']=[261,45,85,36]
    elif fault=='missing_best': result.raw_detail.pop('best')
    elif fault=='bad_filtered': result.raw_detail['filtered']=[]
    elif fault=='bad_all': result.raw_detail['all']=None
    detail, error, _ = read_date([],result)
    if fault == 'wrong_day':
        assert error == 'game_day_mismatch'
    else:
        assert error is None and detail['state']=='schedule' and detail['date'] is None


@pytest.mark.parametrize('block', ['ScheduleGuard','OneKeyLabel','OneKeyPanelBright',
                                   '购买','宝石','重置','正在进行数据连接'])
def test_date_roi_is_not_a_page_or_risk_bypass(block):
    blocked = block if block in ('ScheduleGuard','OneKeyLabel','OneKeyPanelBright') else (
        'HomeDataConnection' if block == '正在进行数据连接' else 'OneKeyPanelBright')
    words = [date_word(block,[500,300,160,25])]
    detail, error, calls = read_date(words,date_native(),blocked=blocked)
    assert error is not None and detail.get('state')!='schedule'
    assert 'OneKeyDate' not in calls


@pytest.mark.parametrize('failed_first', [False, True])
def test_date_roi_guard_consumes_dispatch_once(tmp_path, monkeypatch, failed_first):
    import numpy as np
    from datetime import datetime, timezone, timedelta
    import pcr_maa.schedule_onekey as module
    monkeypatch.setattr(module,'now_cn',lambda:datetime(2027,9,25,15,tzinfo=timezone(timedelta(hours=8))))
    monkeypatch.setattr(native,'RUNTIME',tmp_path)
    image=np.zeros((720,1280,3),dtype=np.uint8)
    words=[date_word('9',[267,51,12,24]),date_word('25',[303,51,25,24])]
    context=DateContext(words,date_native(),image)
    reader=module.OneKeyPage('2027-09-25')
    args=SimpleNamespace(image=image,custom_recognition_param='"observe"')
    reader.analyze(context,args)
    assert reader.last['state']=='schedule'
    reader.expected=reader.signature(reader.last)
    reader.allowed_action='close'
    if failed_first: context.result=date_native('9/26')
    args.custom_recognition_param='"close"'
    result=reader.analyze(context,args)
    assert (result is None)==failed_first
    assert reader.allowed_action is None
    context.result=date_native()
    assert reader.analyze(context,args) is None


def test_date_roi_existing_native_node_contract_on_synthetic_pixels(resource):
    import cv2
    import numpy as np
    image=np.full((720,1280,3),255,dtype=np.uint8)
    cv2.putText(image,'9 / 25',(264,69),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,0,0),1,cv2.LINE_AA)
    controller=SavedImages([[image,image]])
    receipt=native.run(native.bind(resource,controller),'OneKeyDate')
    assert receipt['native_ok']
    raw=receipt['nodes'][0]['recognition']
    assert raw['all']==raw['filtered']==[raw['best']]
    assert raw['best']['box']==DATE_ROI and raw['best']['score']>=.85
    assert ''.join(raw['best']['text'].split())=='9/25'
    assert controller.clicks==controller.swipes==[]


@pytest.mark.parametrize('failed_first', [False, True])
def test_date_roi_native_fallback_and_guard_no_resend(resource, monkeypatch, failed_first):
    import cv2
    import numpy as np
    from datetime import datetime, timezone, timedelta
    import pcr_maa.schedule_onekey as module
    monkeypatch.setattr(module,'now_cn',lambda:datetime(2027,9,25,15,tzinfo=timezone(timedelta(hours=8))))
    image=np.full((720,1280,3),255,dtype=np.uint8)
    cv2.putText(image,'9 / 25',(264,69),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,0,0),1,cv2.LINE_AA)
    class Reader(module.OneKeyPage):
        fail=False
        def read(self, context, current, detail):
            reader=self
            synthetic=DateContext([date_word('9',[267,51,12,24]),date_word('25',[303,51,25,24])],None,current)
            class Context:
                def run_recognition(self, name, same_image, override=None):
                    if name == 'HomeDataConnection':
                        return SimpleNamespace(hit=False, raw_detail={})
                    assert same_image is current
                    if name=='OneKeyDate':
                        return date_native('9/26') if reader.fail else context.run_recognition(name,same_image,override)
                    return synthetic.run_recognition(name,same_image,override)
            super().read(Context(),current,detail)
    reader=Reader('2027-09-25')
    assert resource.register_custom_recognition('OneKeyPage',reader)
    controller=SavedImages([[image,image]])
    tasker=native.bind(resource,controller)
    try:
        assert native.run(tasker,'OneKeyExecRead')['native_ok']
        assert reader.last['state']=='schedule' and reader.last['date']==[9,25]
        assert controller.clicks==controller.swipes==[]
        reader.expected=reader.signature(reader.last)
        reader.allowed_action='close'
        reader.fail=failed_first
        native.run(tasker,'OneKeyDo_close')
        assert reader.allowed_action is None
        assert len(controller.clicks)==(0 if failed_first else 1)
        reader.fail=False
        native.run(tasker,'OneKeyDo_close')
        assert len(controller.clicks)==(0 if failed_first else 1) and controller.swipes==[]
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


def read_synthetic_home(mana_words, *, extra=(), ap='107/413', jewels='575,164', modal=False):
    from pcr_maa.schedule_onekey import OneKeyPage
    def word(label, box, score=.99):
        return dict(text=label, box=box, score=score)
    words = [word('日程表', [989, 598, 59, 24]), word('礼物', [1193, 602, 41, 18]),
             *([word(ap, [333, 11, 69, 19])] if ap is not None else []),
             *([word(jewels, [341, 74, 68, 18])] if jewels is not None else []),
             *mana_words, *extra]
    class Context:
        def run_recognition(self, name, image, override=None):
            if name == 'HomeDataConnection':
                return SimpleNamespace(hit=False, raw_detail={})
            if name == 'OneKeyOCR':
                return SimpleNamespace(algorithm=AlgorithmEnum.OCR, raw_detail={'all': words}, hit=True)
            if name == 'OneKeyHomeDialogBlue':
                return SimpleNamespace(hit=modal, raw_detail={})
            assert name == 'ObserveHome'
            return SimpleNamespace(hit=True, raw_detail={})
    detail = {'evidence': {}}
    try:
        OneKeyPage('2027-09-24').read(Context(), None, detail)
    except ValueError as exc:
        return detail, str(exc)
    return detail, None


def read_synthetic_quote(kind, *, times='2', remaining='2', tickets='2',
                         owned=None, owned_score=.99, risk=None, decoration=None, attest_current_plan=False):
    from pcr_maa.schedule_onekey import OneKeyPage

    title = {'mana_confirm': '玛那关卡跳过确认',
             'exp_confirm': '经验值关卡跳过确认'}[kind]
    def word(label, box, score=.99):
        return dict(text=label, box=box, score=score)
    words = [word(title, [420, 200, 200, 30]),
             word(times, [600, 337, 40, 30]),
             word(remaining, [895, 337, 40, 30]),
             word(tickets, [600, 373, 40, 30])]
    # Match the existing JSON AND contract, including the actual consumption body.
    # A paid/reset modal replaces that body; unrelated page copy does not.
    import json, re
    pipeline = json.loads((native.ROOT / 'assets/resource/pipeline/schedule_onekey.json').read_text(encoding='utf8'))
    body = '消耗扫荡券和挑战次数，攻略' + ('玛那' if kind == 'mana_confirm' else '经验值') + '关卡。确认吗？'
    if risk == '重置': body = '重置挑战次数。确认吗？'
    elif risk == '宝石': body = '消耗宝石购买挑战次数。确认吗？'
    words.extend([word(body, [355, 260, 560, 30]),
                  word('挑战次数', [340, 330, 100, 30]),
                  word('消耗扫荡券', [340, 375, 100, 30]),
                  word('确认', [720, 475, 80, 30]), word('取消', [440, 475, 80, 30])])
    if decoration:
        words.append(word(decoration, [20, 550, 200, 30]))
    if owned is not None:
        words.append(word(owned, [830, 373, 104, 30], owned_score))
    if risk is not None:
        words.extend(word(label, [500 + 160 * index, 300, 160, 25])
                     for index, label in enumerate((risk,) if isinstance(risk, str) else risk))
    class Context:
        def run_recognition(self, name, image, override=None):
            if name == 'HomeDataConnection':
                return SimpleNamespace(hit=False, raw_detail={})
            if name in ('OneKeyOCR', 'OneKeyQuoteNumber'):
                return SimpleNamespace(algorithm=AlgorithmEnum.OCR,
                                       raw_detail={'all': words}, hit=True)
            assert name == 'OneKeyGuard_' + kind
            checks = [pipeline[n] for n in pipeline[name]['all_of']]
            def matches(check):
                x, y, width, height = check['roi']
                return any(w['score'] >= check['threshold'] and re.search(check['expected'], w['text'])
                           and x <= w['box'][0] and y <= w['box'][1]
                           and w['box'][0]+w['box'][2] <= x+width
                           and w['box'][1]+w['box'][3] <= y+height for w in words)
            return SimpleNamespace(hit=all(matches(c) for c in checks), raw_detail={})
    detail = {'evidence': {}}
    try:
        OneKeyPage('2027-09-24', attest_current_plan=attest_current_plan).read(Context(), None, detail)
    except ValueError as exc:
        return detail, str(exc)
    return detail, None


@pytest.mark.parametrize('kind', ['mana_confirm', 'exp_confirm'])
@pytest.mark.parametrize('owned,score', [(None, .99), ('?', .99), ('1', .2),
                                         ('0', .99), ('99999', .99)])
def test_confirmation_owned_stock_is_optional(kind, owned, score):
    detail, error = read_synthetic_quote(kind, owned=owned, owned_score=score)
    assert error is None and detail['state'] == kind
    assert detail['quote'] == dict(times=2, remaining=2, tickets=2)


@pytest.mark.parametrize('field', ['times', 'remaining', 'tickets'])
def test_confirmation_current_quote_still_requires_two(field):
    detail, error = read_synthetic_quote('mana_confirm', **{field: '1'}, owned='99999')
    assert error == 'unseen_or_insufficient_two_times_two_tickets_quote'
    assert detail.get('state') != 'mana_confirm'


@pytest.mark.parametrize('risk', ['扫荡券不足', ('扫荡券', '不足')])
def test_confirmation_explicit_ticket_shortage_stops(risk):
    detail, error = read_synthetic_quote('mana_confirm', owned='99999', risk=risk)
    assert error == 'insufficient_sweep_tickets' and detail.get('state') != 'mana_confirm'


def install_animation_reader(monkeypatch, *, risk=False):
    import pcr_maa.schedule_onekey as module
    original_read = module.OneKeyPage.read
    def transition_read(self, context, image, detail):
        if image[0, 0, 0] == 17:
            detail['words'] = ([dict(text='确认购买', box=[500, 300, 90, 30], score=.99)]
                               if risk else [])
            raise ValueError('unexpected_home_dialog_control' if risk else 'unverified:ScheduleGuard')
        return original_read(self, context, image, detail)
    monkeypatch.setattr(module.OneKeyPage, 'read', transition_read)


def test_execution_home_omits_mana_even_when_readable():
    mana = dict(text='876,695.353', box=[134, 74, 104, 18], score=.868053)
    detail, error = read_synthetic_home([mana])
    assert error is None and detail['state'] == 'home'
    assert detail['resources'] == dict(ap=107, jewels=575164)


def test_execution_home_mana_is_optional_but_ap_jewels_and_purchase_risk_remain_required():
    mana = dict(text='876,695.353', box=[134, 74, 104, 18], score=.868053)
    cases = [[], [mana | {'score': .79}], [mana | {'text': '?@!'}],
             [mana, mana | {'text': '876,695.354'}],
             [mana | {'box': [134, 74, 109, 18]}],
             [mana | {'text': '987,654,321'}]]
    for words in cases:
        detail, error = read_synthetic_home(words)
        assert error is None and detail['state'] == 'home'
        assert detail['resources'] == dict(ap=107, jewels=575164)
    banner = dict(text='购买宝石！', box=[470, 300, 101, 27], score=.99)
    for kwargs, expected in ((dict(ap=None), 'unreadable_home_resource'),
                             (dict(jewels=None), 'unreadable_home_resource'),
                             (dict(extra=[banner], modal=True), 'unexpected_home_dialog_control')):
        detail, error = read_synthetic_home([], **kwargs)
        assert error == expected and detail.get('state') != 'home'
    detail, error = read_synthetic_home([], extra=[banner])
    assert error is None and detail['state'] == 'home'


def test_execution_enter_transition_reads_fresh_page_without_resend(resource, authorized, monkeypatch):
    import numpy as np
    import pcr_maa.schedule_onekey as module
    install_animation_reader(monkeypatch)
    animation = np.full((720, 1280, 3), 17, dtype=np.uint8)
    c = SavedImages([[frame('home-before')],
                     [animation, animation, frame('pre-top')],
                     [animation] * 4])
    result = module.schedule_onekey(native.bind(resource, c), **authorized)
    assert result['outcome'] == 'unknown'
    assert result['sent'] == ['enter', 'down:0']
    assert c.clicks == [[1017, 575]] and len(c.swipes) == 1
    assert not result['one_key_attempted']
    assert not authorized['journal'].events()
    assert result['consumer_state'] == 'not_started'


def test_execution_enter_transition_risk_stops_before_settings(resource, authorized, monkeypatch):
    import numpy as np
    import pcr_maa.schedule_onekey as module
    install_animation_reader(monkeypatch, risk=True)
    animation = np.full((720, 1280, 3), 17, dtype=np.uint8)
    # Native post-wait-freezes also captures frames before the Python observation.
    # Keep the modal visible through those captures; do not reveal a safe page early.
    c = SavedImages([[frame('home-before')], [animation] * 8 + [frame('pre-top')]])
    result = module.schedule_onekey(native.bind(resource, c), **authorized)
    assert result['outcome'] == 'unknown'
    assert result['reason'] == 'unexpected_home_dialog_control'
    assert result['sent'] == ['enter']
    assert c.clicks == [[1017, 575]] and c.swipes == []
    assert not result['one_key_attempted']
    assert not authorized['journal'].events()
    assert result['consumer_state'] == 'not_started'


def test_nonconsuming_transitions_observe_without_extra_inputs(resource, authorized, monkeypatch):
    import numpy as np
    import pcr_maa.schedule_onekey as module
    install_animation_reader(monkeypatch)
    animation = np.full((720, 1280, 3), 17, dtype=np.uint8)
    names = ['home', 'delegated',
             'post-anchor', 'post-middle', 'post-lower', 'post-bottom', 'home']
    stages = [[frame(name)] for name in names]
    for index in (1, 2, 3, 4, 5, 6):
        stages[index] = [animation, animation, stages[index][0]]
    c = SavedImages(stages)
    result = module.schedule_onekey(native.bind(resource, c), **authorized)
    assert result['outcome'] == 'already_complete', result.get('reason')
    assert 'onekey' not in result['sent'] and not result['one_key_attempted']
    assert result['home_verified'] and not result['daily_complete']
    assert result['sent'] == ['enter', *[f'down:{i}' for i in range(4)], 'close']
    assert c.clicks == [[1017, 575], [370, 640]]
    assert len(c.swipes) == 4 and 'settings_audit' not in result
    assert sum(r['entry'] == 'OneKeyExecRead' for r in result['receipts']) > len(result['sent']) + 1
    assert [e['state'] for e in authorized['journal'].events()] == ['in_flight', 'settled']


def test_future_clock_historical_frames_full_chain_once(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    import pcr_maa.schedule_onekey as module
    from pcr_maa.preset_execution import Journal
    monkeypatch.setattr(module, 'now_cn', lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))), raising=False)
    names = ['home-before', 'pre-top', 'pre-anchor', 'pre-middle', 'pre-lower', 'pre-bottom',
             'dungeon-prompt', 'dungeon-result', 'mana-prompt', 'mana-result',
             'exp-prompt', 'exp-result', 'like-result', 'item-result',
             'delegated', 'post-anchor', 'post-middle', 'post-lower', 'post-bottom', 'home']
    controller = SavedImages([[private_frame(native.ROOT /
        f'tests/fixtures/schedule-onekey-{name}.png')] for name in names])
    journal = Journal(tmp_path / 'ledger')
    result = module.schedule_onekey(native.bind(native.load_resource(), controller), execute=True,
        game_day='2027-09-24', authorizations=module.ITEMS,
        device=dict(serial='127.0.0.1:16384', input_display=2, screenshot_display='123'), journal=journal,
        attest_current_plan=True)
    native.save_receipt(result | dict(clicks=controller.clicks, swipes=controller.swipes), 'corrected-full-chain')
    assert result['outcome'] == 'executed', result.get('reason')
    assert result['schedule_executed'] and result['nine_rows_verified'] and result['home_verified']
    assert not result['daily_complete']
    assert result['sent'].count('onekey') == 1
    assert result['settings_assurance'] == 'user_attested_current_plan' and 'settings_audit' not in result
    assert result['sent'] == ['enter', 'down:0', 'down:1', 'down:2', 'down:3', 'onekey',
        'dungeon_confirm', 'dungeon_result', 'mana_confirm', 'mana_result',
        'exp_confirm', 'exp_result', 'like_result', 'item_result',
        'down:4', 'down:5', 'down:6', 'down:7', 'close']
    assert len(controller.clicks) == 11 and len(controller.swipes) == 8
    assert result['resources']['before']['jewels'] == result['resources']['after']['jewels'] == 574504
    assert result['resources']['before']['ap'] == 310 and result['resources']['after']['ap'] == 992
    assert [e['state'] for e in journal.events()] == ['in_flight', 'settled']
    actions=Journal(journal.directory/'actions'/journal.events()[0]['intent_id']).events()
    intent=next(e for e in actions if e['operation']=='onekey' and e['state']=='in_flight')
    assert intent['settings_assurance']==result['settings_assurance']


def test_real_preclick_frame_is_observation_not_permission():
    from pcr_maa.schedule_onekey import schedule_onekey
    controller = SavedImages([[private_frame(
        native.ROOT / 'tests/fixtures/schedule-onekey-preclick.png')]])
    result = schedule_onekey(native.bind(native.load_resource(), controller), game_day='2026-09-24')
    native.save_receipt(result, 'onekey-preclick')
    assert result['observation']['page'] == 'schedule'
    assert result['observation']['visible_month_day'] == [9, 24]
    assert result['observation']['sections']['delegated']['completed'] == 0
    assert result['executability'] == 'NOT_EXECUTABLE'
    assert result['business_facts']['normal_gacha_free'] is True
    assert result['business_facts']['grants_execution_permission'] is False
    assert 'normal_gacha_free_cost_unproven' not in result['blockers']
    assert not result['schedule_executed'] and not result['daily_complete']
    assert controller.clicks == controller.swipes == []


@pytest.fixture(scope='module')
def resource():
    return native.load_resource()


@pytest.mark.parametrize('name,day,reason', [
    ('preclick', '2026-09-25', 'game_day_mismatch'),
    ('settings', '2026-09-24', 'schedule_page_unverified'),
    ('dungeon-prompt', '2026-09-24', 'schedule_page_unverified'),
    ('mana-prompt', '2026-09-24', 'schedule_page_unverified'),
    ('home', '2026-09-24', 'schedule_page_unverified'),
])
def test_saved_wrong_day_settings_dialogs_stop_without_input(resource, name, day, reason):
    from pcr_maa.schedule_onekey import schedule_onekey
    controller = SavedImages([[private_frame(
        native.ROOT / f'tests/fixtures/schedule-onekey-{name}.png')]])
    result = schedule_onekey(native.bind(resource, controller), game_day=day)
    native.save_receipt(result, 'onekey-' + name)
    assert result['observation']['page'] == 'unknown'
    assert result['observation']['reason'] == reason
    assert not result['observation_complete']
    assert not result['schedule_executed'] and not result['nine_rows_verified']
    assert controller.clicks == controller.swipes == []


@pytest.mark.parametrize('name', ['delegated', 'notifications', 'gacha-completed'])
def test_completed_historical_pages_never_prove_execution_or_cost(resource, name):
    from pcr_maa.schedule_onekey import schedule_onekey
    controller = SavedImages([[private_frame(
        native.ROOT / f'tests/fixtures/schedule-onekey-{name}.png')]])
    result = schedule_onekey(native.bind(resource, controller), game_day='2026-09-24')
    native.save_receipt(result, 'onekey-' + name)
    assert result['observation_complete']
    sections = result['observation']['sections']
    if name == 'delegated':
        assert sections['delegated']['completed'] == sections['delegated']['total'] == 9
    if name == 'notifications':
        assert sections['notifications']['completed'] == 1
        assert sections['notifications']['total'] == 5
        assert 'delegated' not in sections
    assert result['executability'] == 'NOT_EXECUTABLE'
    assert not result['nine_rows_verified'] and not result['settlement_verified']
    assert result['resources']['normal_gacha_cost'] is None
    assert all(value == 'unknown' for value in result['row_states'].values())
    assert controller.clicks == controller.swipes == []


@pytest.mark.parametrize('game_day', [None, 'invalid', '2026-09-24', '2026-09-25', '2099-01-01'])
def test_missing_device_binding_refuses_before_tasker_or_journal_access(game_day):
    from pcr_maa.schedule_onekey import ITEMS, schedule_onekey
    class Untouchable:
        def __getattribute__(self, name):
            pytest.fail('No execution dependency may be accessed: ' + name)
    result = schedule_onekey(Untouchable(), execute=True, game_day=game_day,
                            authorizations=ITEMS, device={'input_display': 2, 'screenshot_display': '123'})
    assert result['outcome'] == 'not_authorized'
    assert result['receipts'] == []
    assert result['sent'] == []
    assert not result['schedule_executed'] and not result['daily_complete']


def test_cli_execution_and_no_image_never_construct_controller(monkeypatch, capsys):
    from pcr_maa.cli import main
    import pcr_maa.replay as replay
    import pcr_maa.device as device
    def forbidden(*a, **k):
        pytest.fail('No image/resource/device/controller access on rejection')
    monkeypatch.setattr(native, 'load_resource', forbidden)
    monkeypatch.setattr(native, 'read_image', forbidden)
    monkeypatch.setattr(replay, 'SavedImages', forbidden)
    monkeypatch.setattr(device, 'navigate', forbidden)
    for args in [[], ['--input-display', '2', '--screenshot-display', '123']]:
        assert main(['schedule-onekey', *args]) == 2
        report = json.loads(capsys.readouterr().out)
        assert report['receipts'] == [] and not report['schedule_executed']
    for args in [['--execute'], ['--execute', '--game-day', '2099-01-01',
                 '--image', 'missing.png', '--authorize', 'normal_gacha_free']]:
        assert main(['schedule-onekey', *args]) == 1
        assert json.loads(capsys.readouterr().err)['outcome'] == 'failed'



def test_cli_saved_frame_reports_observation_and_nonexecutable(capsys):
    from pcr_maa.cli import main
    require_private_files([native.ROOT / 'tests/fixtures/schedule-onekey-preclick.png'])
    assert main(['schedule-onekey', '--image', str(native.ROOT /
        'tests/fixtures/schedule-onekey-preclick.png'), '--game-day', '2026-09-24']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['observation_complete'] and result['executability'] == 'NOT_EXECUTABLE'
    assert result['clicks'] == result['swipes'] == []


def test_missing_capture_does_not_retry_or_close(resource):
    from pcr_maa.schedule_onekey import schedule_onekey
    import numpy as np
    controller = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
    result = schedule_onekey(native.bind(resource, controller))
    assert not result['observation_complete']
    assert controller.clicks == controller.swipes == []


def test_native_disconnect_exception_is_unknown_without_cleanup(resource, monkeypatch):
    from pcr_maa.schedule_onekey import schedule_onekey
    controller = SavedImages([[private_frame(native.ROOT /
        'tests/fixtures/schedule-onekey-preclick.png')]])
    tasker = native.bind(resource, controller)
    def disconnected(*a, **k):
        raise RuntimeError('offline simulated disconnected')
    monkeypatch.setattr(native, 'run', disconnected)
    result = schedule_onekey(tasker)
    assert not result['observation_complete']
    assert 'disconnected' in result['observation']['reason']
    assert controller.clicks == controller.swipes == []


def test_process_restart_without_authorization_cannot_execute():
    # Actual fresh processes; missing current invocation permission rejects before dispatch.
    script = ('from pcr_maa.schedule_onekey import schedule_onekey; '
              'import json; print(json.dumps(schedule_onekey(execute=True)))')
    import os
    env = dict(os.environ, PYTHONPATH=str(native.ROOT / 'src'), PYTHONDONTWRITEBYTECODE='1')
    for _ in range(2):
        process = subprocess.run([sys.executable, '-B', '-c', script], env=env,
                                 capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr
        report = json.loads(process.stdout)
        assert not report['schedule_executed'] and report['receipts'] == []


def test_pipeline_inputs_are_one_shot_native_guards_and_default_readonly():
    pipeline = json.loads((native.RESOURCE / 'pipeline/schedule_onekey.json').read_text(encoding='utf-8'))
    inputs = {k: v for k, v in pipeline.items() if v.get('action') in ('Click', 'Swipe')}
    assert len(inputs) == 17
    for name, node in inputs.items():
        assert name.startswith('OneKeyInput_')
        assert node['recognition'] == 'Custom' and node['custom_recognition'] == 'OneKeyPage'
        assert node['max_hit'] == 1 and not node.get('next')
    assert pipeline['OneKeyPreflight']['next'] == ['OneKeyRead']
    assert pipeline['OneKeyRead']['action'] == 'DoNothing'
    assert pipeline['OneKeyInput_enter']['target'] == [1017, 575]  # Outside left banner x=30..270.
    from pcr_maa.supported_daily import ORDER
    assert ORDER == ('event', 'deep', 'rewards', 'very_hard', 'hard', 'rewards_final')


def test_saved_private_fixture_hashes():
    inventory = native.ROOT / 'tests/fixtures/schedule-onekey-sources.json'
    require_private_files([inventory])
    sources = json.loads(inventory.read_text(encoding='utf-8'))
    require_private_files([native.ROOT / source['path'] for source in sources])
    for source in sources:
        assert hashlib.sha256((native.ROOT / source['path']).read_bytes()).hexdigest() == source['sha256']


@pytest.fixture
def authorized(monkeypatch, tmp_path):
    from datetime import datetime, timezone, timedelta
    import pcr_maa.schedule_onekey as module
    from pcr_maa.preset_execution import Journal
    monkeypatch.setattr(module, 'now_cn', lambda: datetime(2027, 9, 24, 14, tzinfo=timezone(timedelta(hours=8))))
    return dict(execute=True, game_day='2027-09-24', authorizations=module.ITEMS,
        device=dict(serial='127.0.0.1:16384', input_display=2, screenshot_display='123'),
        journal=Journal(tmp_path / 'ledger'))


def frame(name):
    return private_frame(native.ROOT / f'tests/fixtures/schedule-onekey-{name}.png')


@pytest.mark.parametrize('change', [dict(game_day='2026-09-25'), dict(authorizations=[]),
    dict(authorizations=['guild_like', 'guild_like']), dict(authorizations=['paid_gacha']),
    dict(device=dict(serial='other',input_display=2,screenshot_display='123'))])
def test_invalid_invocation_zero_native(authorized, change):
    from pcr_maa.schedule_onekey import schedule_onekey
    result = schedule_onekey(object(), **(authorized | change))
    assert result['outcome'] == 'not_authorized' and result['sent'] == []
    assert not authorized['journal'].path.exists()


def test_unsettled_any_day_and_restart_zero_resend(authorized):
    journal = authorized['journal']
    with journal.locked():
        journal.append(dict(state='in_flight',operation='schedule_onekey',game_day='2026-09-23'))
    from pcr_maa.schedule_onekey import schedule_onekey
    for _ in range(2):
        result = schedule_onekey(object(), **authorized)
        assert result['reason'] == 'unsettled_intent_any_day_no_resend' and result['sent'] == []
    # New interpreter reloads exactly the persisted unfinished journal.
    script = """import json,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pcr_maa.schedule_onekey as m
from pcr_maa.preset_execution import Journal
m.now_cn=lambda:datetime(2027,9,24,14,tzinfo=timezone(timedelta(hours=8)))
print(json.dumps(m.schedule_onekey(object(),execute=True,game_day='2027-09-24',
 authorizations=m.ITEMS,device=dict(serial='127.0.0.1:16384',input_display=2,screenshot_display='123'),journal=Journal(Path(sys.argv[1])))))
"""
    import os
    result = subprocess.run([sys.executable,'-B','-c',script,str(journal.directory)],
        env=dict(os.environ,PYTHONPATH=str(native.ROOT/'src')),capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['reason'] == 'unsettled_intent_any_day_no_resend' and report['sent'] == []


def test_lock_prevents_concurrent_dispatch(authorized):
    from pcr_maa.schedule_onekey import schedule_onekey
    with authorized['journal'].locked():
        report = schedule_onekey(object(), **authorized)
    assert report['outcome'] == 'unknown' and report['sent'] == []


@pytest.mark.parametrize('label', ['purchase', 'reset', 'busy', 'settings'])
def test_native_changed_guard_never_clicks(resource, authorized, label):
    import numpy as np
    from pcr_maa.schedule_onekey import OneKeyPage
    c = SavedImages([[frame('pre-bottom')]])
    t = native.bind(resource,c); r = OneKeyPage('2027-09-24', attest_current_plan=True)
    resource.register_custom_recognition('OneKeyPage',r)
    try:
        native.run(t,'OneKeyExecRead')
        assert r.last['state']=='schedule'
        # Current attestation cannot override a changed native risk frame.
        r.expected=r.signature(r.last);r.allowed_action='onekey'
        # The old busy image only pasted a caption onto an otherwise valid page.
        # Use the existing connection-overlay pixels for the actual busy contract.
        changed = (private_frame(native.ROOT / 'tests/fixtures/home-connection-busy.png')
                   if label == 'busy' else frame('synthetic-'+label))
        c.stages=[[changed]]
        receipt=native.run(t,'OneKeyDo_onekey')
        native.save_receipt(dict(native=receipt,reading=r.last,clicks=c.clicks),'onekey-risk')
        assert c.clicks==c.swipes==[] and r.allowed_action is None
        if label == 'busy':
            assert r.last['reason'] == 'page_connection_in_progress'
    finally:
        resource.unregister_custom_recognition('OneKeyPage')


def test_result_loss_keeps_intent_and_stops_no_cleanup(resource, authorized):
    from pcr_maa.schedule_onekey import schedule_onekey
    import numpy as np
    names=['home-before','pre-top','pre-anchor','pre-middle','pre-lower','pre-bottom','dungeon-prompt']
    c=SavedImages([[frame(n)] for n in names]+[[np.zeros((720,1280,3),dtype=np.uint8)]])
    t=native.bind(resource,c)
    result=schedule_onekey(t,**authorized,attest_current_plan=True)
    native.save_receipt(result | dict(clicks=c.clicks,swipes=c.swipes),'onekey-result-loss')
    assert result['outcome']=='unknown' and result['sent'][-1]=='dungeon_confirm'
    assert result['sent'].count('onekey')==1 and len(c.clicks)==3
    assert authorized['journal'].events()[-1]['state']=='in_flight'
    repeated=schedule_onekey(object(),**authorized)
    assert repeated['sent']==[] and repeated['reason']=='unsettled_intent_any_day_no_resend'


def test_already_complete_skips_all_consumption(resource, authorized):
    from pcr_maa.schedule_onekey import schedule_onekey
    names=['home','delegated','post-anchor','post-middle','post-lower','post-bottom','home']
    stages=[[frame(n)] for n in names]
    for index in (0, len(stages)-1):
        stages[index][0][65:95, 125:242] = 0  # Synthetic missing mana OCR, AP/jewels untouched.
    c=SavedImages(stages)
    result=schedule_onekey(native.bind(resource,c),**authorized)
    native.save_receipt(result | dict(clicks=c.clicks),'onekey-already-complete')
    assert result['outcome']=='already_complete',result.get('reason')
    assert result['nine_rows_verified'] and result['home_verified'] and not result['schedule_executed']
    assert all(set(result['resources'][phase]) == {'ap', 'jewels'} for phase in ('before', 'after'))
    assert len(c.clicks)==2 and 'onekey' not in result['sent']
    assert schedule_onekey(object(),**authorized)['outcome']=='unknown'  # A fresh Tasker is required, never skip by run marker.


def test_cli_authorized_dispatch_is_explicit_only(monkeypatch,capsys,authorized):
    from pcr_maa.cli import main
    import pcr_maa.device as device
    seen=[]
    def routed(*args,**kw):
        seen.append((args,kw));return dict(outcome='executed',daily_complete=False)
    monkeypatch.setattr(device,'navigate',routed)
    args=['schedule-onekey','--execute','--game-day','2027-09-24',
          '--input-display','2','--screenshot-display','123']
    for item in authorized['authorizations']:args+=['--authorize',item]
    assert main(args)==0
    assert seen[0][1]['onekey']['authorizations']==list(authorized['authorizations'])
    assert not json.loads(capsys.readouterr().out)['daily_complete']


def test_partial_eight_complete_only_remaining_reward_authorized(resource,authorized):
    """Synthetic partial state built from historical rows; not another live run."""
    import cv2
    import numpy as np
    from pcr_maa.schedule_onekey import schedule_onekey
    def counter(name,y):
        img=frame(name)
        img[y-2:y+25,975:1021]=(240,243,247)
        cv2.putText(img,'8/9',(978,y+17),cv2.FONT_HERSHEY_SIMPLEX,.65,(65,72,90),1,cv2.LINE_AA)
        return img
    bottom=frame('post-bottom');pending=frame('pre-bottom')
    bottom[503:585,250:1025]=pending[503:585,250:1025]
    images=[frame('home'),counter('delegated',473),counter('post-anchor',260),
        frame('post-middle'),frame('post-lower'),bottom,frame('item-result')]
    images += [frame(n) for n in ['delegated','post-anchor','post-middle','post-lower','post-bottom','home']]
    c=SavedImages([[img] for img in images])
    result=schedule_onekey(native.bind(resource,c),**(authorized|dict(authorizations=['memorial_rewards'])),
                           attest_current_plan=True)
    native.save_receipt(result|dict(clicks=c.clicks),'onekey-partial-synthetic')
    assert result['outcome']=='executed',result.get('reason')
    assert result['before_rows']['memorial_rewards']=='pending'
    assert sum(s=='complete' for s in result['before_rows'].values())==8
    assert result['sent'].count('onekey')==1
    assert not any(s.endswith('_confirm') for s in result['sent'])
    assert len(c.clicks)==4 and result['nine_rows_verified'] and result['home_verified']


@pytest.mark.parametrize('fault',['disconnect','day_change'])
def test_fault_before_first_input_no_action_and_no_consumer_intent(resource,authorized,monkeypatch,fault):
    import pcr_maa.schedule_onekey as module
    from datetime import datetime,timezone,timedelta
    c=SavedImages([[frame('home-before')]])
    t=native.bind(resource,c)
    original=native.run
    def disrupted(tasker,entry):
        if entry=='OneKeyDo_enter':
            if fault=='day_change':
                monkeypatch.setattr(module,'now_cn',lambda:datetime(2027,9,25,14,tzinfo=timezone(timedelta(hours=8))))
            else:raise RuntimeError('offline simulated disconnected')
        return original(tasker,entry)
    monkeypatch.setattr(native,'run',disrupted)
    result=module.schedule_onekey(t,**authorized)
    assert result['outcome']=='unknown' and c.clicks==c.swipes==[]
    assert not authorized['journal'].events()
    assert result['consumer_state'] == 'not_started'


def test_device_onekey_conflicts_rejected_before_resource(tmp_path,monkeypatch,authorized):
    import pcr_maa.device as device
    adb = tmp_path / 'adb.exe'
    adb.write_bytes(b'never executed')
    connection = device.validate_connection(dict(adb_path=str(adb), serial=device.SERIAL))
    def forbidden(*a,**k):pytest.fail('invalid invocation reached resource/device')
    monkeypatch.setattr(native,'load_resource',forbidden)
    with pytest.raises(ValueError, match='^Invalid or conflicting standalone one-key arguments$'):
        device.navigate(2,'123',slot=1,onekey={k:authorized[k] for k in ('execute','game_day','authorizations')},
                        connection=connection)


@pytest.mark.parametrize('variant',['unknown_popup','changed_row','quote_changed'])
def test_native_fresh_page_or_quote_change_consumes_guard(resource,authorized,variant):
    from pcr_maa.schedule_onekey import OneKeyPage
    import numpy as np
    name='mana-prompt' if variant=='quote_changed' else 'pre-bottom'
    action='mana_confirm' if variant=='quote_changed' else 'onekey'
    c=SavedImages([[frame(name)]])
    t=native.bind(resource,c);reader=OneKeyPage('2027-09-24', attest_current_plan=True)
    resource.register_custom_recognition('OneKeyPage',reader)
    try:
        native.run(t,'OneKeyExecRead')
        assert reader.last['state'] in ('schedule','mana_confirm')
        # A valid current attestation still requires the exact fresh page/quote.
        reader.expected=reader.signature(reader.last);reader.allowed_action=action
        altered=frame(name)
        if variant=='unknown_popup':altered[:]=0
        elif variant=='changed_row':altered[550:584,260:650]=100
        else:altered[337:367,600:640]=100
        c.stages=[[altered]]
        native.run(t,'OneKeyDo_'+action)
        assert c.clicks==c.swipes==[] and reader.allowed_action is None
        # A later valid page is insufficient: same dispatch stays consumed.
        c.stages=[[frame(name)]]
        native.run(t,'OneKeyDo_'+action)
        assert c.clicks==c.swipes==[]
    finally:resource.unregister_custom_recognition('OneKeyPage')


def test_state_first_old_calendar_marker_is_not_execution_proof(monkeypatch):
    from datetime import datetime, timezone, timedelta
    import pcr_maa.schedule_onekey as module
    monkeypatch.setattr(module, 'now_cn', lambda: datetime(2026,9,24,14,tzinfo=timezone(timedelta(hours=8))))
    module.validate_execution('2026-09-24',
        dict(serial='127.0.0.1:16384',input_display=2,screenshot_display='123'), module.ITEMS)
    # Validation alone has no Tasker, images or ledger and cannot execute.


def test_missing_current_attestation_blocks_before_onekey(resource, tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    import numpy as np
    import pcr_maa.schedule_onekey as module
    from pcr_maa.preset_execution import Journal
    monkeypatch.setattr(module,'now_cn',lambda:datetime(2027,9,24,14,tzinfo=timezone(timedelta(hours=8))))
    c=SavedImages([[frame(n)] for n in ['home-before','pre-top','pre-anchor','pre-middle','pre-lower','pre-bottom']]
        +[[np.zeros((720,1280,3),dtype=np.uint8)]])
    result=module.schedule_onekey(native.bind(resource,c),execute=True,game_day='2027-09-24',
        authorizations=module.ITEMS,device=dict(serial='127.0.0.1:16384',input_display=2,screenshot_display='123'),journal=Journal(tmp_path/'ledger'))
    assert result['outcome']=='unknown'
    assert 'onekey' not in result['sent']
    assert result['sent'][-1]=='down:3' and result['reason']==module.ONEKEY_SCOPE_BLOCKER
    assert not any(s.startswith('settings_') for s in result['sent'])


@pytest.mark.parametrize('variant,index,setting', [
    ('gacha-reset',13,'普通扭蛋详细设定'),
    ('dungeon-display',6,'地下城设定'),
    ('mana-purchase',16,'玛那购买设定')])
def test_explicit_audit_unsafe_settings_zero_onekey(resource,authorized,variant,index,setting):
    from pcr_maa.schedule_onekey import schedule_settings_audit
    names=['home-before','pre-top',*SETTINGS_FRAMES[:index], 'synthetic-settings-'+variant]
    c=SavedImages([[frame(n)] for n in names])
    result=schedule_settings_audit(native.bind(resource,c),audit=True,
        game_day=authorized['game_day'],device=authorized['device'])
    native.save_receipt(result|dict(clicks=c.clicks,swipes=c.swipes,synthetic=True),'unsafe-settings-'+variant)
    assert result['outcome']=='unknown' and result['reason'].startswith('unsafe_setting:'+setting)
    assert 'onekey' not in result['sent'] and not result['one_key_attempted']
    assert c.clicks==[[1017,575],[612,640]]
    assert not authorized['journal'].directory.exists()
    assert result['settings_audit']['grants_execution_permission'] is False


def test_historical_settings_frame_is_not_current_session_permission(resource,authorized):
    from pcr_maa.schedule_onekey import schedule_onekey
    c=SavedImages([[frame('scope-settings13')]])
    result=schedule_onekey(native.bind(resource,c),**authorized)
    assert result['reason']=='verified_home_start_required' and c.clicks==c.swipes==[]
    # No API accepts a previous report/handwritten attestation as a settings gate.
    with pytest.raises(TypeError):
        schedule_onekey(object(),**authorized,settings_audit={'verified':True,'game_day':'2026-09-24'})


def test_known_completed_day_cli_and_device_zero_construction(tmp_path,monkeypatch,capsys,authorized):
    from pcr_maa.cli import main
    import pcr_maa.device as device
    adb = tmp_path / 'adb.exe'
    adb.write_bytes(b'never executed')
    connection = device.validate_connection(dict(adb_path=str(adb), serial=device.SERIAL))
    def forbidden(*a,**kw): pytest.fail('noncurrent game day accessed device/resource')
    monkeypatch.setattr(native,'load_resource',forbidden)
    # Test direct device entry before mocking the router for CLI.
    with pytest.raises(ValueError,match='^current_game_day_required$'):
        device.navigate(2,'123',onekey=dict(execute=True,game_day='2026-09-24',authorizations=list(authorized['authorizations'])),
                        connection=connection)
    monkeypatch.setattr(device,'navigate',forbidden)
    args=['schedule-onekey','--execute','--game-day','2026-09-24','--input-display','2','--screenshot-display','123']
    for item in authorized['authorizations']: args+=['--authorize',item]
    assert main(args)==1
    assert 'current_game_day_required' in capsys.readouterr().err


@pytest.mark.parametrize('fault',['frozen','changed_return'])
def test_explicit_audit_missing_progress_or_changed_return_never_onekey(resource,authorized,fault):
    from pcr_maa.schedule_onekey import schedule_settings_audit
    settings=['scope-settings0'] if fault=='frozen' else SETTINGS_FRAMES+['pre-anchor']
    c=SavedImages([[frame(n)] for n in ['home-before','pre-top',*settings]])
    report=schedule_settings_audit(native.bind(resource,c),audit=True,
        game_day=authorized['game_day'],device=authorized['device'])
    # SavedImages supplies fresh capture identities with unchanged pixels.
    expected='audit_transition_read_budget_exhausted' if fault=='frozen' else 'schedule_changed_after_settings_return'
    assert report['reason']==expected
    assert 'onekey' not in report['sent'] and not authorized['journal'].directory.exists()
    assert report['settings_audit']['grants_execution_permission'] is False
    if fault == 'frozen':
        assert report['sent'] == ['enter', 'settings_open', 'settings_down:0']
        assert len(c.swipes) == 1 and c.clicks == [[1017, 575], [612, 640]]


def test_native_onekey_cannot_reuse_nine_rows_without_current_attestation(resource,authorized):
    from pcr_maa.schedule_onekey import OneKeyPage
    c=SavedImages([[frame('pre-bottom')]])
    t=native.bind(resource,c);reader=OneKeyPage(authorized['game_day'])
    resource.register_custom_recognition('OneKeyPage',reader)
    try:
        native.run(t,'OneKeyExecRead')
        assert reader.last['state']=='schedule'
        reader.expected=reader.signature(reader.last);reader.allowed_action='onekey'
        native.run(t,'OneKeyDo_onekey')
        assert c.clicks==c.swipes==[] and reader.allowed_action is None
    finally:resource.unregister_custom_recognition('OneKeyPage')


def test_future_cli_cannot_execute_historical_image(monkeypatch,capsys,authorized):
    from pcr_maa.cli import main
    import pcr_maa.device as device
    def forbidden(*a,**kw):pytest.fail('historical image reached execution dependency')
    monkeypatch.setattr(device,'navigate',forbidden)
    monkeypatch.setattr(native,'read_image',forbidden)
    args=['schedule-onekey','--execute','--game-day',authorized['game_day'],'--input-display','2',
          '--screenshot-display','123','--image',str(native.ROOT/'tests/fixtures/schedule-onekey-scope-settings13.png')]
    for item in authorized['authorizations']:args+=['--authorize',item]
    assert main(args)==1
    assert json.loads(capsys.readouterr().err)['outcome']=='failed'


@pytest.mark.parametrize('fault', ['recognition_failed', 'ack_missing'])
def test_preconsumer_navigation_abort_can_fresh_restart(resource, monkeypatch, authorized, fault):
    import pcr_maa.schedule_onekey as module
    tasker, controller = attested_controller(resource, monkeypatch, pending=())
    original = native.run
    def interrupted(tasker, entry):
        if entry == 'OneKeyDo_enter':
            if fault == 'ack_missing':
                original(tasker, entry)  # Navigation input happened; ACK was lost.
                raise OSError('synthetic navigation ACK missing')
            # Actual native recognition failure, no input node.
            controller.stage = 1
        return original(tasker, entry)
    with monkeypatch.context() as patch:
        patch.setattr(native, 'run', interrupted)
        report = module.schedule_onekey(tasker, **authorized, attest_current_plan=True)
    assert report['outcome'] == 'unknown'
    assert report['consumer_state'] == 'not_started'
    assert report['phase'] == 'navigation'
    assert report['sent'] == ['enter'] and not report['one_key_attempted']
    assert not authorized['journal'].events()
    assert not (authorized['journal'].directory / 'actions').exists()
    assert len(controller.clicks) == (fault == 'ack_missing')
    tasker, controller = attested_controller(resource, monkeypatch, pending=())
    again = module.schedule_onekey(tasker, **authorized)
    assert again['outcome'] == 'already_complete', again.get('reason')
    assert again['consumer_state'] == 'not_started'
    assert again['sent'] == ['enter', 'close']


@pytest.mark.parametrize('boundary', ['root_persisted', 'consumer_persisted', 'consumer_ack', 'confirm_persisted'])
def test_consumer_crash_boundaries_never_resend(resource, monkeypatch, authorized, boundary):
    import pcr_maa.schedule_onekey as module
    from pcr_maa.preset_execution import Journal
    tasker, controller = attested_controller(resource, monkeypatch)
    append, run = Journal.append, native.run
    def crash_append(self, event):
        append(self, event)
        match = (self.path == authorized['journal'].path if boundary == 'root_persisted'
                 else event['operation'] == ('dungeon_confirm' if boundary == 'confirm_persisted' else 'onekey'))
        if boundary != 'consumer_ack' and event['state'] == 'in_flight' and match:
            raise OSError('synthetic crash after fsync')
    def crash_run(tasker, entry):
        result = run(tasker, entry)
        if boundary == 'consumer_ack' and entry == 'OneKeyDo_onekey':
            raise OSError('synthetic consumer ACK missing')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(Journal, 'append', crash_append)
        patch.setattr(native, 'run', crash_run)
        result = module.schedule_onekey(tasker, **authorized, attest_current_plan=True)
    assert result['outcome'] == 'unknown' and result['consumer_state'] == 'in_flight'
    assert result['native_input_state'] == ('unknown' if boundary == 'consumer_ack' else 'not_started')
    assert authorized['journal'].events()[-1]['state'] == 'in_flight'
    before = authorized['journal'].path.read_bytes()
    again = module.schedule_onekey(object(), **authorized, attest_current_plan=True)
    assert again['reason'] == 'unsettled_intent_any_day_no_resend'
    assert again['sent'] == again['receipts'] == []
    assert authorized['journal'].path.read_bytes() == before
    expected = 0 if boundary in ('root_persisted', 'consumer_persisted') else 1
    assert controller.clicks.count([880, 640]) == expected


def test_normal_history_does_not_import_incident_reconciler(resource, monkeypatch, authorized):
    import builtins
    import pcr_maa.schedule_onekey as module
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        if 'onekey_reconcile' in name:
            raise AssertionError('ordinary run imported historical incident verifier')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', guarded)
    for _ in range(2):
        tasker, controller = attested_controller(resource, monkeypatch, pending=())
        result = module.schedule_onekey(tasker, **authorized)
        assert result['outcome'] == 'already_complete', result.get('reason')
        assert result['consumer_state'] == 'not_started'


@pytest.mark.parametrize('consumer_started', [False, True])
def test_navigation_failure_phase_never_clears_consumption(resource, monkeypatch, authorized, consumer_started):
    import pcr_maa.schedule_onekey as module
    tasker, controller = attested_controller(resource, monkeypatch, pending=('memorial_rewards',) if consumer_started else ())
    original = native.run
    def failed(tasker, entry):
        if entry == 'OneKeyDo_close':
            raise OSError('synthetic navigation ACK unknown')
        return original(tasker, entry)
    monkeypatch.setattr(native, 'run', failed)
    result = module.schedule_onekey(tasker, **authorized, attest_current_plan=True)
    assert result['outcome'] == 'unknown' and result['sent'][-1] == 'close'
    assert result['consumer_state'] == ('in_flight' if consumer_started else 'not_started')
    assert result['native_input_state'] == 'unknown' and result['input_evidence'] == 'unknown'
    assert not result['daily_complete']
    if consumer_started:
        assert authorized['journal'].events()[-1]['state'] == 'in_flight'
        again = module.schedule_onekey(object(), **authorized, attest_current_plan=True)
        assert again['reason'] == 'unsettled_intent_any_day_no_resend'
    else:
        assert not authorized['journal'].events()


def test_navigation_holds_root_lock_before_consumer_intent(resource, monkeypatch, authorized):
    import pcr_maa.schedule_onekey as module
    tasker, controller = attested_controller(resource, monkeypatch, pending=())
    original = native.run
    attempts = []
    def contended(tasker, entry):
        if entry == 'OneKeyDo_enter':
            assert not authorized['journal'].events()
            attempts.append(module.schedule_onekey(object(), **authorized, attest_current_plan=True))
        return original(tasker, entry)
    monkeypatch.setattr(native, 'run', contended)
    result = module.schedule_onekey(tasker, **authorized)
    assert result['outcome'] == 'already_complete'
    assert len(attempts) == 1 and attempts[0]['outcome'] == 'unknown'
    assert attempts[0]['sent'] == attempts[0]['receipts'] == []
    assert len(controller.clicks) == 2


def test_old_navigation_inflight_precedes_legacy_dispatch(authorized, monkeypatch):
    import pcr_maa.schedule_onekey as module
    from pcr_maa import onekey_reconcile as recovery
    from pcr_maa.preset_execution import Journal
    root = authorized['journal'].directory.parent
    journal = Journal(root / '.runtime/schedule-onekey-work/ledger')
    authorized = authorized | dict(journal=journal)
    with journal.locked():
        for disposition in ('verified_pre_input_abort', 'verified_pre_consumer_abort', 'verified_settings_navigation_abort'):
            journal.append(dict(state='in_flight', operation='schedule_onekey', game_day='2026-09-25'))
            journal.append(dict(state='settled', operation='schedule_onekey', game_day='2026-09-25', disposition=disposition))
        journal.append(dict(state='in_flight', operation='schedule_onekey', game_day='2026-09-25', intent_id='d'*32))
    actions = Journal(journal.directory / 'actions' / ('d'*32))
    with actions.locked():
        actions.append(dict(state='in_flight', operation='down:2', game_day='2026-09-25'))
    before = journal.path.read_bytes(), actions.path.read_bytes()
    # An already blocked start may attach the existing read-only explanation.
    # Legacy proof/retry/closure and all input/write paths must still stay unused.
    def forbidden(*args, **kwargs):
        pytest.fail('unresolved history entered legacy proof, retry, write, or native input')
    for name in ('retry_authorized', 'consumer_retry_authorized', 'verify_closed_prefix',
                 'reconcile', 'reconcile_no_consumer', 'reconcile_settings_abort',
                 '_reconcile_consumer', '_readonly_abort_proof'):
        monkeypatch.setattr(recovery, name, forbidden)
    monkeypatch.setattr(Journal, 'append', forbidden)
    monkeypatch.setattr(native, 'run', forbidden)
    result = module.schedule_onekey(object(), **authorized, attest_current_plan=True)
    assert result['reason'] == 'unsettled_intent_any_day_no_resend'
    assert result['sent'] == result['receipts'] == []
    assert result['recovery']['execution_permitted'] is False
    assert result['recovery']['ledger_written'] is False
    assert result['recovery']['current_input_state'] == 'not_sent'
    assert before == (journal.path.read_bytes(), actions.path.read_bytes())


@pytest.mark.parametrize('kind', ['mana_confirm', 'exp_confirm'])
@pytest.mark.parametrize('decoration', ['重置', '宝石', '购买充值宝石重置'])
def test_confirmation_unrelated_copy_is_not_a_keyword_veto(kind, decoration):
    detail, error = read_synthetic_quote(kind, decoration=decoration, attest_current_plan=True)
    assert error is None and detail['state'] == kind
    assert detail['quote'] == dict(times=2, remaining=2, tickets=2)
