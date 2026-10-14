"""One real-image preview flow, unknown dispatch, and business boundaries.

Private frames are optional; native tests never discover or connect a device.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import time

import pytest
from pcr_maa import abyss, native
from pcr_maa.abyss import execute_abyss, assess_home, check_quote, same_quote
from pcr_maa.abyss_native import NativeAbyss
from pcr_maa.game_day import current_game_day
from pcr_maa.preset_execution import Journal
from pcr_maa.replay import SavedImages
from maa.custom_action import CustomAction


@pytest.fixture
def device(tmp_path):
    # A private static manager marker for offline business binding, not a live
    # qualification. It does not inspect the installed emulator or real ledger.
    return dict(serial='127.0.0.1:16384', manager=dict(source='mumu_manager_local_v1',
        installation=str(tmp_path), index=0, created_timestamp=123,
        app_package='com.bilibili.priconne'))


def reading():
    return dict(native_ok=True, abyss_page_verified=True, list_complete=True,
        abyss_identity=dict(mode='深渊讨伐战', event_name='轰流深渊',
                            period='举办时间:10/0212:00~10/0704:59'),
        rows=[dict(difficulty=d, remaining=3, limit=4, fields_verified=True) for d in abyss.DIFFICULTIES])


def quotation():
    r = reading()
    return dict(scope=abyss.SCOPE, verified=True, identity_verified=True,
        abyss_identity=r['abyss_identity'],
        rows=[dict(row, title='前哨关卡', mode='深渊讨伐战', times=3) for row in r['rows']],
        stages=3, total_times=9, stamina_cost=225, stamina_owned=245,
        ticket_cost=9, tickets_owned=206495)


class Port:
    def __init__(self, q=None, stop=False):
        self.calls = []
        self.q, self.stop = q or quotation(), stop

    def read(self):
        self.calls.append('read')
        return reading()

    def quote(self, r):
        self.calls.append('quote')
        return self.q

    def confirm(self, r, q):
        self.calls.append('confirm')
        return dict(verified=True)

    def cancel_confirmation(self):
        self.calls.append('cancel')
        return True

    def home(self):
        self.calls.append('home')
        return True

    def submit(self, q):
        self.calls.append('submit')
        if self.stop:
            raise KeyboardInterrupt()
        return dict(consumption_dispatched=True, result_verified=False)


def run(port, journal, device, **kwargs):
    return execute_abyss(port, journal, game_day=current_game_day(),
                         context='gui-current-state', device=device, **kwargs)


@pytest.mark.parametrize('ocr_period', [
    None,
    '',
    '举办时间:13/0212:00~10/0704:59',
    '举办时间:02/3012:00~10/0704:59',
    '举办时间:10/0225:00~10/0704:59',
    '举办时间:10/0212:00~10/0704:60',
])
def test_invalid_period_stops_before_quote_or_intent(tmp_path, device, ocr_period):
    # The native page adapter normalizes OCR fields before business validation.
    # A failed normalization must never become an executable activity identity.
    period = abyss.normalize_period(ocr_period)
    assert period is None
    before, quote = reading(), quotation()
    before['abyss_identity']['period'] = period
    quote['abyss_identity']['period'] = period

    class UnverifiedPeriodPort(Port):
        def read(self):
            self.calls.append('read')
            return deepcopy(before)

    port, journal = UnverifiedPeriodPort(quote), Journal(tmp_path / 'private-invalid-period-ledger')
    result = run(port, journal, device, execute=True)
    assert result['outcome'] == 'reading_unknown'
    assert port.calls == ['read']
    assert not result['consumption_sent'] and journal.events() == []
    assert assess_home(before) == 'reading_unknown'
    assert check_quote(quote, before) == 'quote_identity_mismatch'


def test_current_observation_has_four_limit_without_event_assumptions():
    r, q = reading(), quotation()
    assert assess_home(r) == 'needs_execution'
    assert check_quote(q, r) == 'ready'
    assert check_quote(dict(q, ticket_cost=3), r) == 'quote_contradiction'
    assert check_quote(dict(q, stamina_owned=224), r) == 'skipped_insufficient_stamina'
    assert check_quote(dict(q, tickets_owned=8), r) == 'skipped_insufficient_tickets'
    q.pop('tickets_owned')
    assert check_quote(q, r) == 'ready'
    assert same_quote(q, dict(q, stamina_owned=246), r)
    r['rows'][0]['remaining'] = 0
    assert assess_home(r) == 'needs_execution'
    assert check_quote(q, r) == 'quote_identity_mismatch'
    r['rows'][0]['remaining'] = 3
    r['rows'][0]['limit'] = 5
    assert assess_home(r) == 'reading_unknown'
    r['rows'][0]['limit'] = 0
    assert assess_home(r) == 'reading_unknown'


@pytest.mark.parametrize('resource,value,expected', [
    ('stamina_owned', 224, 'skipped_insufficient_stamina'),
    ('tickets_owned', 8, 'skipped_insufficient_tickets'),
    ('ticket_cost', 8, 'quote_contradiction')])
def test_shortage_or_changed_quote_cancels_without_intent(tmp_path, device, resource, value, expected):
    port = Port(dict(quotation(), **{resource: value}))
    journal = Journal(tmp_path / 'private-ledger')
    result = run(port, journal, device, execute=True)
    assert result['outcome'] == expected and result['home_verified']
    assert not result['consumption_sent'] and journal.events() == []
    assert port.calls == ['read', 'quote', 'cancel', 'home']


@pytest.mark.parametrize('stop', [False, True])
def test_unknown_or_stop_preserves_intent_and_blocks_restart(tmp_path, device, stop):
    port, journal = Port(stop=stop), Journal(tmp_path / 'private-ledger')
    result = run(port, journal, device, execute=True)
    before = journal.path.read_bytes()
    assert result['outcome'] == 'unknown_settlement' and result['consumption_sent']
    assert not result['settlement_verified'] and not result['home_verified']
    assert [event['state'] for event in journal.events()] == ['in_flight']
    assert port.calls == ['read', 'quote', 'confirm', 'submit']
    restarted = Port()
    again = run(restarted, journal, device, execute=True)
    assert again['outcome'] == 'blocked_in_flight' and not again['retry_permitted']
    assert restarted.calls == [] and journal.path.read_bytes() == before
    # A different cycle or stable instance cannot turn an unknown into a retry.
    changed = deepcopy(device)
    changed['manager']['created_timestamp'] += 1
    crossed = execute_abyss(restarted, journal, game_day='2026-10-03',
        context='gui-current-state', execute=True, device=changed)
    assert crossed['outcome'] == 'blocked_in_flight' and restarted.calls == []
    assert journal.path.read_bytes() == before


@pytest.fixture
def frames():
    value = os.environ.get('PCR_ABYSS_FRAME_ROOT')
    if not value:
        pytest.skip('Private real frames are optional; no live device access')
    root = Path(value)
    def load(name):
        return native.read_image(root / name / 'after.png')
    return {name: load(name) for name in ('03-adventure', '05-abyss-page',
        '07-abyss-preview', '08-abyss-cancel', '09-abyss-home', '13-abyss-current-execute')}


def build_port(frames, unknown=False):
    names = ['09-abyss-home', '03-adventure', '05-abyss-page', '07-abyss-preview',
             '13-abyss-current-execute', '09-abyss-home'] if unknown else [
             '09-abyss-home', '03-adventure', '05-abyss-page', '07-abyss-preview',
             '13-abyss-current-execute', '07-abyss-preview', '08-abyss-cancel', '09-abyss-home']
    stages = [[frames[name]] for name in names]
    if not unknown:
        # Extend this same full preview with two native waiting cycles. The
        # old 1-second previous-node budget could not reach the final frame.
        stages[2] = [frames['03-adventure'], frames['03-adventure'], frames['05-abyss-page']]
    controller = SavedImages(stages)
    tasker = native.bind(native.load_resource(), controller)
    return NativeAbyss(tasker), controller


def save_result(tmp_path, label, report, controller):
    (tmp_path / (label + '.json')).write_text(json.dumps(dict(report=report,
        clicks=controller.clicks, swipes=controller.swipes), ensure_ascii=False, indent=2), encoding='utf-8')


def borrowed_run(tasker, journal, device, *, execute=False, preview=False):
    # Use the same registered callback scope and borrowed Context as the Agent.
    # The outer Resource/Tasker remain owned here by official SavedImages only.
    from pcr_maa.agent_callbacks import AgentCallbacks
    registry = AgentCallbacks()
    registry._ready = True
    resource = tasker.resource
    for name, route in registry.recognitions.items():
        if name == 'HomeNoKnownLargePanel':
            # load_resource already owns this callback. Register the formal
            # route exactly once rather than asking Maa to replace a duplicate.
            assert resource.unregister_custom_recognition(name)
        assert resource.register_custom_recognition(name, route)
    reports, errors = [], []

    class Borrow(CustomAction):
        def run(self, context, argv):
            try:
                with registry.scope(context, deadline=time.monotonic() + 120,
                                    owner_source=lambda original: original is context) as facade:
                    reports.append(run(NativeAbyss(facade), journal, device,
                                       execute=execute, preview=preview))
                return True
            except BaseException as exc:
                errors.append(str(exc))
                return False

    assert resource.register_custom_action('AbyssTestBorrow', Borrow())
    assert resource.override_pipeline({'AbyssTestOuter': dict(action='Custom',
        custom_action='AbyssTestBorrow', next=[], post_delay=0)})
    outer = native.run(tasker, 'AbyssTestOuter')
    result = reports[0] if reports else dict(outcome='callback_failed', errors=errors)
    result['outer_receipt'] = outer
    result['callback_errors'] = errors
    return result


def test_native_full_preview_current_three_rows_cancel_return_home(frames, tmp_path, device):
    port, controller = build_port(frames)
    journal = Journal(tmp_path / 'private-preview-ledger')
    result = borrowed_run(port.tasker, journal, device, preview=True)
    save_result(tmp_path, 'full-preview', result, controller)
    assert result['outer_receipt']['native_ok'] and result['callback_errors'] == []
    assert result['outcome'] == 'preview_complete', result
    assert result['home_verified'] and result['cancel_verified']
    assert not result['consumption_sent'] and not result['settlement_verified']
    assert result['reading']['rows'] == reading()['rows']
    q = result['quote']
    assert [(row['difficulty'], row['times']) for row in q['rows']] == [('普通', 3), ('困难', 3), ('高难', 3)]
    assert (q['stages'], q['total_times'], q['stamina_cost'], q['stamina_owned'], q['ticket_cost']) == (3, 9, 225, 245, 9)
    assert len(controller.clicks) == 7 and not controller.swipes and journal.events() == []
    # Challenge only opens the final ticket modal. Both cancel layers are used;
    # the small modal's final consumer Confirm is never clicked in preview.
    assert result['challenge_opened']
    assert 445 <= controller.clicks[4][0] <= 555 and 471 <= controller.clicks[4][1] <= 526
    assert 448 <= controller.clicks[5][0] <= 547 and 610 <= controller.clicks[5][1] <= 667
    assert all(not (734 <= x <= 853 and 471 <= y <= 526) for x, y in controller.clicks)


def test_native_unknown_result_no_close_no_navigation_no_resend(frames, tmp_path, device):
    port, controller = build_port(frames, unknown=True)
    # Bound the static negative fixture only; production waits 15 seconds for
    # a real animation. All guards and once-only actions are unchanged.
    assert port.tasker.resource.override_pipeline({'AbyssResultStart': {'timeout': 500}})
    journal = Journal(tmp_path / 'private-unknown-ledger')
    result = run(port, journal, device, execute=True)
    save_result(tmp_path, 'unknown-result', result, controller)
    assert result['outcome'] == 'unknown_settlement', result
    assert result['consumption_sent'] and not result['settlement_verified'] and not result['home_verified']
    assert result['submission']['consumption_dispatched']
    assert len(controller.clicks) == 5 and not controller.swipes
    assert 734 <= controller.clicks[-1][0] <= 853 and 471 <= controller.clicks[-1][1] <= 526
    data = journal.path.read_bytes()
    assert [event['state'] for event in journal.events()] == ['in_flight']
    repeated = run(port, journal, device, execute=True)
    assert repeated['outcome'] == 'blocked_in_flight'
    assert len(controller.clicks) == 5 and journal.path.read_bytes() == data
    with pytest.raises(native.NativeInputNotSent) as rejected:
        port.submit(result['quote'])
    assert rejected.value.receipt['input_execution'] == 'not_sent' and len(controller.clicks) == 5


def zero_home(source):
    # Test-only counter synthesis; every other homepage pixel is original.
    # This is not evidence that the live abyss transaction has settled.
    import cv2
    image = source.copy()
    for y in (307, 407, 505):
        image[y:y + 31, 267:331] = (255, 255, 255)
        cv2.putText(image, '0/4', (276, y + 23), cv2.FONT_HERSHEY_SIMPLEX,
                    .65, (50, 50, 50), 2, cv2.LINE_AA)
    return image


@pytest.mark.parametrize('fault', [None, 'post_counts', 'home'])
def test_native_same_kind_result_complete_transaction_and_boundaries(frames, tmp_path, device, fault):
    result_path = native.ROOT / '.runtime/private-fixtures/hermes-pcr/live/event-sweep-result-settled.png'
    if fault is None and os.environ.get('PCR_ABYSS_REAL_RESULT_FRAME'):
        result_path = Path(os.environ['PCR_ABYSS_REAL_RESULT_FRAME'])
    if not result_path.is_file():
        pytest.skip('Historical same-kind sweep result frame is private and optional')
    after = frames['05-abyss-page'] if fault == 'post_counts' else zero_home(frames['05-abyss-page'])
    home = frames['03-adventure'] if fault == 'home' else frames['09-abyss-home']
    controller = SavedImages([[image] for image in (frames['09-abyss-home'],
        frames['03-adventure'], frames['05-abyss-page'], frames['07-abyss-preview'],
        frames['13-abyss-current-execute'], native.read_image(result_path), after, home)])
    tasker = native.bind(native.load_resource(), controller)
    journal = Journal(tmp_path / 'private-same-kind-ledger')
    if fault is None:
        result = borrowed_run(tasker, journal, device, execute=True)
        assert result['outer_receipt']['native_ok'] and result['callback_errors'] == []
    else:
        result = run(NativeAbyss(tasker), journal, device, execute=True)
    result['fixture_source'] = ('Current abyss original entry/quote + actual abyss result + synthetic 0/4 post-counters; not all-original full replay'
        if fault is None and os.environ.get('PCR_ABYSS_REAL_RESULT_FRAME') else
        'Current abyss original entry/quote + historical event result + synthetic 0/4 post-counters; not live abyss qualification')
    save_result(tmp_path, 'same-kind-' + str(fault), result, controller)
    assert result['submission']['result_verified'] and result['submission']['abyss_return_verified'], result
    images = result['submission']['result_images']
    assert len(images) == 1 and Path(images[0]['path']).is_file()
    assert len(controller.clicks) == (6 if fault == 'post_counts' else 7)
    assert not controller.swipes
    if fault == 'post_counts':
        assert result['outcome'] == 'result_verified_followup_incomplete' and not result['settlement_verified']
        assert [event['state'] for event in journal.events()] == ['in_flight']
        before = journal.path.read_bytes()
        again = run(NativeAbyss(tasker), journal, device, execute=True)
        assert again['outcome'] == 'blocked_post_counts_unverified' and journal.path.read_bytes().startswith(before)
        assert journal.events()[-1]['consumption_result_verified'] and len(controller.clicks) == 6
    else:
        assert result['outcome'] == ('executed_home_failed' if fault == 'home' else 'executed')
        assert result['consumption_sent'] and result['settlement_verified'] and result['goal_complete']
        assert result['home_verified'] is (fault is None)
        assert [row['remaining'] for row in result['after']['rows']] == [0, 0, 0]
        assert [event['state'] for event in journal.events()] == ['in_flight', 'settled']


def test_native_changed_final_price_cancels_two_layers_before_any_intent(frames, tmp_path, device):
    import cv2
    ticket = frames['13-abyss-current-execute'].copy()
    ticket[355:386, 588:640] = (255, 255, 255)
    cv2.putText(ticket, '226', (590, 379), cv2.FONT_HERSHEY_SIMPLEX, .65,
                (50, 50, 50), 2, cv2.LINE_AA)
    controller = SavedImages([[image] for image in (frames['09-abyss-home'],
        frames['03-adventure'], frames['05-abyss-page'], frames['07-abyss-preview'],
        ticket, frames['07-abyss-preview'], frames['08-abyss-cancel'], frames['09-abyss-home'])])
    tasker = native.bind(native.load_resource(), controller)
    journal = Journal(tmp_path / 'private-final-changed-ledger')
    result = borrowed_run(tasker, journal, device, execute=True)
    save_result(tmp_path, 'changed-final-price', result, controller)
    assert result['confirmation']['status'] == 'final_quote_changed', result
    assert not result['consumption_sent'] and not result['settlement_verified']
    assert result['cancel_verified'] and result['home_verified']
    assert len(controller.clicks) == 7 and journal.events() == []
    assert all(not (734 <= x <= 853 and 471 <= y <= 526) for x, y in controller.clicks)


def test_native_continue_original_final_intent_no_new_intent_no_challenge(frames, tmp_path, device):
    result_path = native.ROOT / '.runtime/private-fixtures/hermes-pcr/live/event-sweep-result-settled.png'
    if not result_path.is_file():
        pytest.skip('Historical same-kind result frame is private and optional')
    before, quote = reading(), quotation()
    opening_controller = SavedImages([[frames['07-abyss-preview']], [frames['13-abyss-current-execute']]])
    opening_tasker = native.bind(native.load_resource(), opening_controller)
    opening_port = NativeAbyss(opening_tasker, start='abyss')
    opening_port.identity, opening_port.reading = before['abyss_identity'], before
    opened = opening_port.confirm(before, quote)
    assert opened['verified'] and len(opening_controller.clicks) == 1
    intent = dict(state='in_flight', scope=abyss.SCOPE, operation='abyss_outpost',
        intent_id='1' * 32, game_day=current_game_day(), device=device,
        abyss_identity=before['abyss_identity'], quote=quote)
    journal = Journal(tmp_path / 'private-original-intent-ledger')
    with journal.locked():
        journal.append(intent)
    prefix = journal.path.read_bytes()
    controller = SavedImages([[image] for image in (frames['13-abyss-current-execute'],
        native.read_image(result_path), zero_home(frames['05-abyss-page']), frames['09-abyss-home'])])
    tasker = native.bind(native.load_resource(), controller)
    port = NativeAbyss(tasker, start='abyss')
    with journal.locked():
        result = port.continue_open_final_intent(intent, before, opened['opening_receipt'], device=device)
    save_result(tmp_path, 'original-intent-continuation', result, controller)
    assert result['outcome'] == 'executed' and result['settlement_verified'] and result['home_verified'], result
    assert result['consumption_sent'] and result['consumption_dispatched']
    assert not result['new_intent_created'] and not result['challenge_dispatched']
    assert len(controller.clicks) == 3 and not controller.swipes
    assert 734 <= controller.clicks[0][0] <= 853 and 471 <= controller.clicks[0][1] <= 526
    assert journal.path.read_bytes() == prefix
    assert [event['state'] for event in journal.events()] == ['in_flight']
    with pytest.raises(native.NativeInputNotSent) as rejected:
        port.submit(quote)
    assert rejected.value.receipt['input_execution'] == 'not_sent' and len(controller.clicks) == 3
