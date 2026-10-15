"""Offline Maa branch replay; synthetic scenes prove routing, optional original pixels prove OCR."""
from copy import deepcopy
import json
import os
from pathlib import Path
import time

import numpy as np
import pytest
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

from pcr_maa import native
from pcr_maa.preset_execution_native import NativePreset
from pcr_maa.replay import SavedImages


class Scene(CustomRecognition):
    def analyze(self, context, argv):
        marker, box = json.loads(argv.custom_recognition_param)
        return self.AnalyzeResult(box, {}) if int(argv.image[0, 0, 0]) == marker else None


def scene(marker):
    image = np.zeros((720, 1280, 3), np.uint8)
    image[0, 0, 0] = marker
    return image


def marker_node(marker, box=(580, 623, 110, 30)):
    return dict(recognition='Custom', custom_recognition='SweepScene',
                custom_recognition_param=[marker, list(box)])


def branch_resource():
    resource = native.load_resource()
    assert resource.register_custom_recognition('SweepScene', Scene())
    overrides = {name: marker_node(1) for name in (
        'ExecSweepSkipButton', 'ExecSweepAnimationPanel', 'ExecSweepAnimationRound', 'ExecSweepAnimationExp')}
    overrides.update({name: marker_node(2) for name in (
        'ExecResultTitle', 'ExecResultComplete', 'ExecResultButton')})
    overrides.update({
        'ExecResultStart': dict(timeout=500, rate_limit=10, post_delay=0),
        'ExecSweepSkipFinish': dict(timeout=500, rate_limit=10),
        'ExecResultConfirm': dict(post_delay=0),
        'ExecPostResult': dict(next=['ExecReturnedPreset'], timeout=500),
        'ExecReturnedPreset': dict(recognition='DirectHit', next=[], post_delay=0),
        'AbyssResultStart': dict(timeout=500, rate_limit=10, post_delay=0),
        'AbyssSweepSkipFinish': dict(timeout=500, rate_limit=10),
        'AbyssResultClose': dict(post_delay=0, next=['AbyssHomeArrived']),
        'AbyssHomeArrived': dict(recognition='DirectHit', next=[], post_delay=0),
    })
    assert resource.override_pipeline(overrides)
    return resource


def finish(port):
    # The upstream consumer is synthetic; this never authorizes a real transaction.
    return port._finish_result(dict(rows=[]), dict(slot=1), dict(native_receipt={}))


@pytest.mark.parametrize('arrival', ['immediate', 'late', 'absent'])
def test_optional_skip_and_normal_result_share_one_wait(arrival):
    stages = ([[scene(1)], [scene(2)]] if arrival == 'immediate' else
              [[scene(0), scene(0), scene(1)], [scene(2)]] if arrival == 'late' else [[scene(2)]])
    controller = SavedImages(stages)
    result = finish(NativePreset(native.bind(branch_resource(), controller)))
    assert result['result_verified'] and result['preset_return_verified']
    names = [node['name'] for node in result['result_evidence']['nodes']]
    assert names.count('ExecResultStart') == 1
    assert names.count('ExecSweepSkipFinish') == int(arrival != 'absent')
    assert len(controller.clicks) == 1 + int(arrival != 'absent')
    assert not controller.swipes and 'ExecConfirm' not in names


@pytest.mark.parametrize('fault', ['button_only', 'click_failed', 'unknown_after_skip'])
def test_skip_failure_never_verifies_result_or_resends_consumer(fault):
    resource = branch_resource()
    if fault == 'button_only':
        assert resource.override_pipeline({'ExecSweepAnimationRound': marker_node(99)})
    controller = SavedImages([[scene(1)]])
    if fault == 'click_failed':
        def fail(x, y):
            controller.clicks.append([x, y])
            return False
        controller.click = fail
    port = NativePreset(native.bind(resource, controller))
    result = finish(port)
    assert not result['result_verified'] and not result['preset_return_verified']
    assert len(controller.clicks) == int(fault != 'button_only')
    assert result['result_images'] == []
    assert all(node['name'] != 'ExecConfirm' for node in result['result_evidence']['nodes'])
    with pytest.raises(native.NativeInputNotSent):
        port.submit(dict(slot=1))
    assert len(controller.clicks) == int(fault != 'button_only')


@pytest.mark.parametrize('level_up', [False, True])
def test_two_transactions_in_borrowed_context_reset_each_skip_budget(level_up):
    from pcr_maa.agent_callbacks import AgentCallbacks
    resource = branch_resource()
    if level_up:
        assert resource.override_pipeline({
            'ExecPostResult': dict(next=['ExecReturnedPreset', 'ExecLevelUpDismiss'], timeout=500),
            'ExecReturnedPreset': marker_node(0),
            **{name: marker_node(3, (590, 480, 100, 30)) for name in (
                'ExecLevelUpTitle', 'ExecLevelUpBody', 'ExecLevelUpButton')},
            'ExecLevelUpDismiss': dict(post_delay=0),
        })
    markers = (1, 2, 3, 0) * 2 if level_up else (1, 2, 1, 2)
    controller = SavedImages([[scene(marker)] for marker in markers])
    tasker = native.bind(resource, controller)
    results, errors = [], []
    registry = AgentCallbacks()
    registry._ready = True
    for name, route in registry.recognitions.items():
        if name == 'HomeNoKnownLargePanel':
            assert resource.unregister_custom_recognition(name)
        assert resource.register_custom_recognition(name, route)

    class Borrow(CustomAction):
        def run(self, context, argv):
            try:
                with registry.scope(context, deadline=time.monotonic() + 60,
                        owner_source=lambda original: original is context) as facade:
                    for index in range(2):
                        if level_up and index:
                            # Simulate the next upstream transaction opening its animation.
                            controller.stage += 1
                            controller.frame = 0
                        results.append(finish(NativePreset(facade)))
                return True
            except BaseException as exc:
                errors.append(exc)
                return False

    assert resource.register_custom_action('SweepBorrow', Borrow())
    assert resource.override_pipeline({'SweepOuter': dict(action='Custom', custom_action='SweepBorrow', next=[], post_delay=0)})
    receipt = native.run(tasker, 'SweepOuter')
    assert not errors, errors
    assert receipt['native_ok'] and len(results) == 2
    assert all(result['result_verified'] and result['preset_return_verified'] for result in results)
    assert len(controller.clicks) == 4 + 2 * int(level_up)


@pytest.mark.parametrize('popup,returned,clicks', [(3, True, 2), (4, False, 1)])
def test_level_up_after_result_only_dismisses_verified_popup(popup, returned, clicks):
    resource = branch_resource()
    assert resource.override_pipeline({
        'ExecPostResult': dict(next=['ExecReturnedPreset', 'ExecLevelUpDismiss'], timeout=500, rate_limit=10),
        'ExecReturnedPreset': marker_node(0),
        'ExecLevelUpTitle': marker_node(3),
        'ExecLevelUpBody': marker_node(3),
        'ExecLevelUpButton': marker_node(3),
        'ExecLevelUpDismiss': dict(post_delay=0),
    })
    controller = SavedImages([[scene(2)], [scene(popup)], [scene(0)]])
    result = finish(NativePreset(native.bind(resource, controller)))
    assert result['result_verified'] and result['preset_return_verified'] is returned
    assert len(controller.clicks) == clicks and not controller.swipes
    names = [node['name'] for node in result['result_evidence']['nodes']]
    assert names.count('ExecLevelUpDismiss') == int(returned)
    assert 'ExecConfirm' not in names


@pytest.mark.parametrize('level_up', [False, True])
def test_abyss_uses_its_result_chain_after_one_consumer_only(monkeypatch, level_up):
    from pcr_maa import abyss_native
    from pcr_maa.abyss_native import NativeAbyss
    from test_abyss import reading, quotation
    resource = branch_resource()
    def synthetic_word(words, name, override=None):
        if int(words.image[0, 0, 0]) != 2:
            raise abyss_native._NoMatch('synthetic result absent')
        text = {'ExecResultTitle': '扫荡结果', 'ExecResultComplete': '扫荡完成！',
                'ExecResultButton': '确认'}[name]
        words.evidence[name] = dict(raw=dict(filtered=[dict(text=text, score=1,
            box=[580, 623, 110, 30])]))
        return text
    monkeypatch.setattr(abyss_native._Words, 'word', synthetic_word)
    # Only the upstream consumer/page return are synthetic; actual result callback stays intact.
    assert resource.override_pipeline({'AbyssTicketCommit': dict(
        recognition='DirectHit', action='Click', target=[785, 493], next=[], post_delay=0)})
    if level_up:
        assert resource.override_pipeline({
            'AbyssResultClose': dict(next=['AbyssHomeArrived', 'AbyssLevelUpDismiss']),
            'AbyssHomeArrived': marker_node(0),
            **{name: marker_node(3, (590, 480, 100, 30)) for name in (
                'ExecLevelUpTitle', 'ExecLevelUpBody', 'ExecLevelUpButton')},
            'AbyssLevelUpDismiss': dict(post_delay=0),
        })
    markers = (0, 1, 2, 3, 0) if level_up else (0, 1, 2, 0)
    controller = SavedImages([[scene(marker)] for marker in markers])
    port = NativeAbyss(native.bind(resource, controller), start='abyss')
    quote = quotation()
    port._confirmed = (reading(), quote, time.monotonic())
    result = port.submit(quote)
    assert result['consumption_dispatched'] and result['result_verified'] and result['abyss_return_verified']
    assert len(result['result_images']) == 1 and len(controller.clicks) == 3 + int(level_up)
    names = [node['name'] for node in result['result_evidence']['nodes']]
    assert names.count('AbyssSweepSkipFinish') == 1 and 'AbyssTicketCommit' not in names
    with pytest.raises(native.NativeInputNotSent):
        port.submit(quote)
    assert len(controller.clicks) == 3 + int(level_up)


@pytest.mark.parametrize('kind', ['Missions', 'Gifts'])
@pytest.mark.parametrize('matches', [True, False])
def test_rewards_close_handles_only_verified_level_up(kind, matches):
    resource = branch_resource()
    prefix = 'Rewards' + kind
    overrides = {
        prefix + 'Result': marker_node(2),
        prefix + 'Ready': marker_node(99),
        prefix + 'Empty': marker_node(0),
        prefix + 'CloseStart': dict(timeout=200),
        prefix + 'Close': dict(post_delay=0, timeout=200),
        prefix + 'AfterClose': dict(timeout=200, rate_limit=10),
        prefix + 'LevelUpDismiss': dict(post_delay=0),
        **{name: marker_node(3, (590, 480, 100, 30)) for name in (
            'ExecLevelUpTitle', 'ExecLevelUpBody', 'ExecLevelUpButton')},
    }
    if kind == 'Gifts':
        overrides['RewardsGiftsStaminaDeferred'] = marker_node(99)
    if not matches:
        overrides['ExecLevelUpTitle'] = marker_node(99)
    assert resource.override_pipeline(overrides)
    controller = SavedImages([[scene(marker)] for marker in (2, 3, 0)])
    receipt = native.run(native.bind(resource, controller), prefix + 'CloseStart')
    assert len(controller.clicks) == 1 + int(matches)
    names = [node['name'] for node in receipt['nodes']]
    assert (prefix + 'LevelUpDismiss' in names) is matches
    assert (prefix + 'Empty' in names) is matches
    assert not any('Claim' in name for name in names)


def test_result_recovery_cannot_reach_optional_skip_or_consumers():
    pipeline = {}
    for name in ('preset_execution', 'abyss', 'event', 'schedule_onekey', 'schedule_quick'):
        pipeline.update(json.loads((native.RESOURCE / f'pipeline/{name}.json').read_text(encoding='utf-8')))
    forbidden = {'ExecConfirm', 'AbyssTicketCommit', 'ExecSubmit', 'AbyssChallenge',
                 'ExecSweepSkipFinish', 'AbyssSweepSkipFinish'}
    for entry in ('ExecRecoverResultStart', 'AbyssRecoverResultStart'):
        seen, pending = set(), [entry]
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            node = pipeline.get(name, {})
            pending.extend(node.get('next', []) + node.get('on_error', []))
        assert not seen & forbidden
    from pcr_maa.event_native import NativeEvent
    assert NativeEvent._finish_result is NativePreset._finish_result


@pytest.fixture
def original_frames():
    directory = Path(os.environ.get('PCR_SWEEP_SKIP_FIXTURES', str(native.ROOT / '.runtime/private-fixtures/hermes-pcr/live')))
    paths = [directory / name for name in ('event-sweep-result.png', 'event-sweep-result-settled.png')]
    if not all(path.is_file() for path in paths):
        pytest.skip('Original event animation/result frames are optional private fixtures')
    return [native.read_image(path) for path in paths]


@pytest.mark.parametrize('fault', [None, 'button_only', 'dim_modal'])
def test_original_animation_anchors_and_negative_pages(original_frames, fault):
    animation, result = original_frames
    if fault == 'button_only':
        copy = np.zeros_like(animation)
        copy[608:668, 502:777] = animation[608:668, 502:777]
        animation = copy
    elif fault == 'dim_modal':
        animation = (animation.astype(np.float32) * .65).astype(np.uint8)
    resource = native.load_resource()
    assert resource.override_pipeline({'ExecResultStart': dict(timeout=700),
        'ExecSweepSkipFinish': dict(timeout=700), 'ExecPostResult': dict(next=['ExecReturnedPreset']),
        'ExecReturnedPreset': dict(recognition='DirectHit', next=[], post_delay=0)})
    controller = SavedImages([[animation], [result]])
    report = finish(NativePreset(native.bind(resource, controller)))
    assert report['result_verified'] is (fault is None)
    assert len(controller.clicks) == (2 if fault is None else 0)
    if fault is None:
        assert 582 <= controller.clicks[0][0] <= 691 and 623 <= controller.clicks[0][1] <= 653
        assert len(report['result_images']) == 1


@pytest.mark.parametrize('animation', [False, True])
def test_cli_explorations_optional_skip_each_then_require_result(animation):
    from pcr_maa.onekey_flow import execute_pending
    calls = []
    sequence = ['mana_confirm']
    if animation:
        sequence.append('sweep_animation')
    sequence += ['sweep_result', 'exp_confirm']
    if animation:
        sequence.append('sweep_animation')
    sequence += ['sweep_result', 'schedule']

    def send(action, page, *, targets):
        calls.append((action, tuple(targets)))
        result = dict(state=sequence.pop(0))
        assert result['state'] in targets
        return result

    result = execute_pending(dict(state='schedule'), {'mana_skip', 'exp_skip'}, send, {})
    assert result['state'] == 'schedule'
    assert [action for action, _ in calls] == (['onekey', 'mana_confirm'] +
        (['mana_skip_finish'] if animation else []) + ['mana_result', 'exp_confirm'] +
        (['exp_skip_finish'] if animation else []) + ['exp_result'])
    assert all(targets == ('sweep_result',) for action, targets in calls if action.endswith('_skip_finish'))


def test_cli_unknown_after_skip_stops_without_result_click_or_consumer_resend():
    from pcr_maa.onekey_flow import execute_pending
    sent = []
    def send(action, page, *, targets):
        sent.append(action)
        return dict(state={'onekey': 'mana_confirm', 'mana_confirm': 'sweep_animation',
                           'mana_skip_finish': 'unknown'}[action])
    with pytest.raises(ValueError, match='settlement_missing_no_resend:mana'):
        execute_pending(dict(state='schedule'), {'mana_skip'}, send, {})
    assert sent == ['onekey', 'mana_confirm', 'mana_skip_finish']


@pytest.mark.parametrize('stage', ['mana', 'exp'])
def test_cli_animation_guard_is_current_stage_once_and_disabled_in_recovery(monkeypatch, stage):
    from pcr_maa import schedule_onekey as module
    from pcr_maa.onekey_page import OneKeyPage
    page = dict(state='sweep_animation', reason=None, skip_box=[582, 623, 109, 30])
    monkeypatch.setattr(OneKeyPage, 'read', lambda self, context, image, detail: detail.update(deepcopy(page)))
    resource = branch_resource()
    assert resource.override_pipeline({'OneKeyDo_' + stage + '_skip_finish': dict(timeout=150)})
    controller = SavedImages([[scene(1)]])
    reader = OneKeyPage(module.current_game_day())
    reader.exploration_stage = stage
    reader.expected = reader.signature(page)
    reader.allowed_action = stage + '_skip_finish'
    assert resource.register_custom_recognition('OneKeyPage', reader)
    tasker = native.bind(resource, controller)
    first = native.run(tasker, 'OneKeyDo_' + stage + '_skip_finish')
    assert first['native_ok'] and len(controller.clicks) == 1
    reader.allowed_action = stage + '_skip_finish'
    reader.readonly_unresolved = True
    native.run(tasker, 'OneKeyDo_' + stage + '_skip_finish')
    assert len(controller.clicks) == 1
    reader.readonly_unresolved = False
    reader.allowed_action = stage + '_skip_finish'
    native.run(tasker, 'OneKeyDo_' + stage + '_skip_finish')
    assert len(controller.clicks) == 1


@pytest.mark.parametrize('animation', [False, True])
@pytest.mark.parametrize('level_up', [False, True])
def test_gui_two_explorations_skip_once_each_and_receipt_requires_results(tmp_path, animation, level_up):
    from pcr_maa import schedule_quick as module
    from pcr_maa.game_day import current_game_day
    from pcr_maa.preset_execution import Journal
    from pcr_maa.schedule_onekey import ITEMS
    resource = branch_resource()
    assert resource.override_pipeline({
        'OneKeyQuickPrepare': dict(recognition='DirectHit', next=['OneKeyQuickReady'], post_delay=0),
        'OneKeyQuickReady': dict(recognition='DirectHit', next=[], post_delay=0),
        'OneKeyQuickReadyGuard': marker_node(0),
        'OneKeyQuickManaGuard': marker_node(10),
        'OneKeyQuickExpGuard': marker_node(20),
        'OneKeyQuickSweepGuard': marker_node(2),
        'OneKeyQuickUnavailableGuard': marker_node(30),
        'OneKeyQuickHome': dict(recognition='DirectHit', next=[], post_delay=0),
        **{name: marker_node(3, (590, 480, 100, 30)) for name in (
            'ExecLevelUpTitle', 'ExecLevelUpBody', 'ExecLevelUpButton')},
        **{name: dict(post_delay=0, timeout=500, rate_limit=10) for name in (
            'OneKeyQuickStart', 'OneKeyQuickManaConfirm', 'OneKeyQuickExpConfirm',
            'OneKeyQuickManaSkipFinish', 'OneKeyQuickExpSkipFinish', 'OneKeyQuickSweepResult',
            'OneKeyQuickFinished', 'OneKeyQuickClose', 'OneKeyQuickLevelUpDismiss')},
    })
    markers = ([0, 10] + ([1] if animation else []) + [2] + ([3] if level_up else [])
        + [20] + ([1] if animation else []) + [2] + ([3] if level_up else []) + [30, 40])
    controller = SavedImages([[scene(marker)] for marker in markers])
    args = dict(game_day=current_game_day(), device=dict(serial='emulator-5556',
        source='gui_adb_session_v1', app_package='com.bilibili.priconne'),
        authorizations=ITEMS, invocation_id='synthetic-sweep-skip', journal=Journal(tmp_path / 'ledger'))
    report = module.schedule_quick(native.bind(resource, controller), **args)
    assert report['outcome'] == 'executed', report
    verify_args = {key: value for key, value in args.items() if key != 'journal'}
    assert module.verified(report, **verify_args)
    assert len(controller.clicks) == 6 + 2 * int(animation) + 2 * int(level_up)
    nodes = report['receipts'][-1]['native']['nodes']
    names = [node['name'] for node in nodes]
    assert names.count('OneKeyQuickSweepResult') == 2
    assert names.count('OneKeyQuickLevelUpDismiss') == 2 * int(level_up)
    if level_up:
        forged = deepcopy(report)
        forged_nodes = forged['receipts'][-1]['native']['nodes']
        first = next(node for node in forged_nodes if node['name'] == 'OneKeyQuickLevelUpDismiss')
        forged_nodes.remove(first)
        forged_nodes.insert(1, first)
        assert not module.verified(forged, **verify_args)
    for name in ('OneKeyQuickManaSkipFinish', 'OneKeyQuickExpSkipFinish'):
        assert names.count(name) == int(animation)
    if animation:
        forged = deepcopy(report)
        forged['receipts'][-1]['native']['nodes'] = [node for node in
            forged['receipts'][-1]['native']['nodes'] if node['name'] != 'OneKeyQuickSweepResult']
        assert not module.verified(forged, **verify_args)
        forged = deepcopy(report)
        forged['receipts'][-1]['native']['nodes'] = [node for node in
            forged['receipts'][-1]['native']['nodes'] if node['name'] != 'OneKeyQuickManaConfirm']
        assert not module.verified(forged, **verify_args)


@pytest.mark.parametrize('stage', ['mana', 'exp'])
def test_cli_original_animation_and_natural_result_only_after_confirmation(original_frames, stage):
    from pcr_maa.game_day import current_game_day
    from pcr_maa.onekey_page import OneKeyPage
    animation, result = original_frames
    resource = native.load_resource()
    controller = SavedImages([[animation], [result]])
    tasker = native.bind(resource, controller)
    reader = OneKeyPage(current_game_day())
    assert resource.register_custom_recognition('OneKeyPage', reader)
    native.run(tasker, 'OneKeyExecRead')
    assert reader.last['state'] != 'sweep_animation'
    reader.exploration_stage = stage
    native.run(tasker, 'OneKeyExecRead')
    assert reader.last['state'] == 'sweep_animation'
    box = reader.last['skip_box']
    assert 580 <= box[0] <= 585 and 621 <= box[1] <= 625 and 106 <= box[2] <= 112 and 27 <= box[3] <= 32
    assert not controller.clicks
    reader.expected = reader.signature(reader.last)
    reader.allowed_action = stage + '_skip_finish'
    controller.stage, controller.frame = 1, 0  # Natural completion, no input.
    ready = native.run(tasker, 'OneKeyDo_' + stage + '_skip_finish')
    assert ready['native_ok'] and any(node['name'] == 'OneKeySkipResultReady' and node['completed']
                                     for node in ready['nodes'])
    assert not controller.clicks
    assert all((node.get('action') or {}).get('raw') in (None, {}) for node in ready['nodes'])
    assert reader.last['evidence']['OneKeyGuard_sweep_result']['hit']
    reader.expected = reader.signature(reader.last)
    reader.allowed_action = stage + '_result'
    confirmed = native.run(tasker, 'OneKeyDo_' + stage + '_result')
    assert confirmed['native_ok'] and len(controller.clicks) == 1


@pytest.mark.parametrize('stage', ['mana', 'exp'])
def test_cli_natural_finish_before_skip_guard_keeps_no_input_receipt_and_settles_history(tmp_path, monkeypatch, stage):
    from pcr_maa import schedule_onekey as module
    from pcr_maa.game_day import current_game_day
    from pcr_maa.onekey_page import OneKeyPage
    from pcr_maa.onekey_history import verify_execution
    from pcr_maa.preset_execution import Journal
    pending = stage + '_skip'
    states = {0: 'home', 1: 'schedule', 2: stage + '_confirm', 3: 'sweep_animation',
              4: 'sweep_result', 5: 'item_result', 6: 'schedule', 7: 'home'}
    controller = SavedImages([[scene(marker)] for marker in (0, 1, 2, 3, 5, 6, 7)])
    def read(self, context, image, detail):
        marker = int(image[0, 0, 0])
        state = states[marker]
        detail.update(state=state, reason=None)
        if state == 'home':
            detail['resources'] = dict(ap=100, jewels=500)
        elif state == 'schedule':
            detail.update(rows={key: dict(label=module.ROW_LABELS[key],
                state='pending' if key == pending and marker == 1 else 'complete', y=index * 10)
                for index, key in enumerate(module.ITEMS)},
                sections={'delegated': dict(completed=8 if marker == 1 else 9, total=9)}, bottom=True)
        elif state == stage + '_confirm':
            detail['quote'] = dict(times=2, remaining=2, tickets=2)
        elif state == 'sweep_animation':
            detail['skip_box'] = [582, 623, 109, 30]
            # The game advances after this observation; this is no controller input.
            controller.stages[controller.stage] = [scene(4)]
        elif state == 'sweep_result':
            detail['evidence']['OneKeyGuard_sweep_result'] = dict(hit=True, raw={'synthetic': True})
    monkeypatch.setattr(OneKeyPage, 'read', read)
    resource = native.load_resource()
    pipeline = json.loads((native.RESOURCE / 'pipeline/schedule_onekey.json').read_text(encoding='utf-8'))
    assert resource.override_pipeline({name: dict(post_delay=0, timeout=500, rate_limit=10)
        for name in pipeline if name.startswith(('OneKeyDo_', 'OneKeyInput_'))})
    device = dict(serial='127.0.0.1:16384', manager=dict(source='mumu_manager_local_v1',
        installation=str(tmp_path / 'synthetic-installation'), index=0,
        created_timestamp=1, app_package='com.bilibili.priconne'))
    journal = Journal(tmp_path / 'ledger')
    report = module.schedule_onekey(native.bind(resource, controller), execute=True,
        game_day=current_game_day(), device=device, authorizations=module.ITEMS,
        journal=journal, attest_current_plan=True)
    assert report['outcome'] == 'executed', report
    assert len(controller.clicks) == 6 and not controller.swipes
    entry = 'OneKeyDo_' + stage + '_skip_finish'
    receipt = next(item for item in report['receipts'] if item['entry'] == entry)['native']
    assert receipt['native_ok'] and any(node['name'] == 'OneKeySkipResultReady' and node['completed']
                                      for node in receipt['nodes'])
    assert all((node.get('action') or {}).get('raw') in (None, {}) for node in receipt['nodes'])
    assert all(node['name'] != 'OneKeyInput_' + stage + '_skip_finish' for node in receipt['nodes'])
    nodes = [node['name'] for item in report['receipts'] for node in item['native']['nodes']]
    assert nodes.count('OneKeyInput_' + stage + '_confirm') == 1
    assert nodes.count('OneKeyInput_' + stage + '_result') == 1
    events = journal.events()
    actions = Journal(journal.directory / 'actions' / events[0]['intent_id']).events()
    skipped = next(event for event in actions if event['operation'] == stage + '_skip_finish' and event['state'] == 'settled')
    assert skipped['input_execution'] == 'not_sent'
    verify_execution(report, events[0], events[-1], actions)
    forged = deepcopy(actions)
    next(event for event in forged if event['operation'] == stage + '_skip_finish' and event['state'] == 'settled').pop('input_execution')
    with pytest.raises(ValueError):
        verify_execution(report, events[0], events[-1], forged)
    for raw in (None, dict(point=[640, 640])):
        forged_report = deepcopy(report)
        next(item for item in forged_report['receipts'] if item['entry'] == entry)['native']['nodes'][-1]['action']['raw'] = raw
        forged_end = deepcopy(events[-1]) | {'receipts': forged_report['receipts']}
        with pytest.raises(ValueError):
            verify_execution(forged_report, events[0], forged_end, actions)
    before = journal.path.read_bytes()
    again = module.schedule_onekey(native.bind(native.load_resource(), controller), execute=True,
        game_day=current_game_day(), device=device, authorizations=module.ITEMS,
        journal=journal, attest_current_plan=True)
    # A later Start can navigate for fresh state, but cannot resend these consumers.
    assert again['sent'] == ['enter'] and len(controller.clicks) == 7 and journal.path.read_bytes() == before
