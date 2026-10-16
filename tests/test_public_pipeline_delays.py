"""Explicit idle-node delays; keep all input waits and readiness gates."""
import json
from pathlib import Path

import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.replay import SavedImages


PIPELINES = Path(__file__).resolve().parents[1] / 'assets/resource/pipeline'
ZERO_DELAY_NODES = {
    'presets': 'NavigatePresets PresetsReached ObserveHome ObserveMain ObserveAdventure ReadScreen ScreenOCR',
    'preset_scan': 'InspectPreset ScanSlotGate ScanSlotReady ScanChecked ScanShort ScanTop ScanBottom ScanPage',
    'preset_execution': 'ExecReadQuote ExecPresetGuardRead ExecCount ExecStaminaBefore ExecStaminaAfter ExecQuoteDone ExecReadShortage ExecStaminaShortage ExecTicketsShortage ExecNoShortage ExecSubmit ExecWaitConfirm ExecConfirmHeader ExecScanStart ExecScanShort ExecScanTop ExecScanBottom ExecScanPage ExecCancelStart ExecCancelledGate ExecCancelled ExecChallenge ExecResultStart ExecPostResult ExecReturnedPreset ExecAfterShop ExecAfterClan ExecRecoverResultStart FitRead FitReadGuard FitCount FitIncreaseStart FitDecreaseStart FitWaitUpdated FitUpdatedRead',
    'event': 'EventFromHome EventFromEvent EventEnterGate EventPageGate EventMapGate EventMapRead EventMapName EventListGate EventListReady StoryEventMapGate EventHardGate EventAfterLoginRewardGate EventUnavailable EventScanStart EventCheckedGate EventCheckedReady EventShort EventTop EventBottom EventPage EventRestoreView EventAllGate EventAllReady EventReturnHome EventHomeGate',
    'activity_navigation': 'ActivityPrepareHome ActivityHomeReady',
    'return_home': 'ReturnHome ReturnMainGate ReturnHomeGate',
    'rewards': ' '.join('Rewards' + kind + suffix for kind in ('Missions', 'Gifts')
        for suffix in ('Read', 'Ready', 'Empty', 'OpenStart', 'ClaimStart', 'ResultRead',
                       'Result', 'CloseStart', 'AfterClose', 'HomeStart')) +
        ' RewardsHomeRead RewardsGiftsAfterClaim RewardsGiftsConfirm RewardsGiftsConfirmClickStart'
        ' RewardsGiftsStaminaDeferred RewardsGiftsIncludeStaminaStart RewardsGiftsIncludeStamina'
        ' RewardsGiftsExcludeStaminaStart RewardsGiftsExcludeStamina RewardsGiftsWithStaminaClaimStart',
}
COLLECTORS = {'CollectPresetPage', 'CollectConfirmation', 'CollectEvent'}


@pytest.mark.parametrize('filename', ZERO_DELAY_NODES)
def test_idle_whitelist_has_no_input_or_implicit_delay(filename):
    pipeline = json.loads((PIPELINES / (filename + '.json')).read_text(encoding='utf-8'))
    for name in ZERO_DELAY_NODES[filename].split():
        node = pipeline[name]
        assert node.get('action', 'DoNothing') in ('DoNothing', 'Custom'), name
        if node.get('action') == 'Custom':
            assert node['custom_action'] in COLLECTORS
        assert (node['pre_delay'], node['post_delay']) == (0, 0), name


def test_native_parser_observes_whitelist_zero_delays():
    resource = native.load_resource()
    for names in ZERO_DELAY_NODES.values():
        for name in names.split():
            node = resource.get_node_data(name)
            assert node['pre_delay'] == node['post_delay'] == 0, name


@pytest.mark.parametrize('filename,post_delay', [
    ('presets', None), ('preset_scan', 500), ('preset_execution', 600),
    ('event', 600), ('activity_navigation', 650), ('return_home', 600), ('rewards', 650),
])
def test_input_transitions_keep_their_existing_delays(filename, post_delay):
    pipeline = json.loads((PIPELINES / (filename + '.json')).read_text(encoding='utf-8'))
    for name, node in pipeline.items():
        if node.get('action') in ('Click', 'Swipe'):
            # Existing implicit pre-delay is part of the input action contract.
            assert 'pre_delay' not in node, name
            if node['action'] == 'Swipe' or name == 'ExecSweepSkipFinish':
                assert node['post_delay'] == 0, name
            elif name.endswith('LevelUpDismiss') or name == 'ExecLevelUpDismiss':
                assert node['post_delay'] == 600, name
            elif name.endswith('StaminaToggle'):
                assert node['post_delay'] == 350, name
            elif post_delay is None:
                assert 'post_delay' not in node, name
            else:
                assert node['post_delay'] == post_delay, name


def test_confirmation_and_list_polling_budgets_remain():
    pipeline = json.loads((PIPELINES / 'preset_execution.json').read_text(encoding='utf-8'))
    for name in ('ExecScanStart', 'FitIncreaseStart', 'FitDecreaseStart', 'FitWaitUpdated'):
        assert (pipeline[name]['timeout'], pipeline[name]['rate_limit']) == (30000, 200)
    assert pipeline['ExecWaitConfirm']['timeout'] == 30000
    for filename, name in [('preset_scan', 'ScanSwipe'), ('preset_execution', 'ExecScanSwipe'),
                           ('event', 'EventSwipe'), ('event', 'EventResetTop')]:
        node = json.loads((PIPELINES / (filename + '.json')).read_text(encoding='utf-8'))[name]
        assert node['duration'] == 1000
        assert (node['post_wait_freezes']['time'], node['post_wait_freezes']['rate_limit'],
                node['post_wait_freezes']['timeout']) == (200, 100, 2500)


@pytest.mark.parametrize('entry,terminal', [('NavigatePresets', 'PresetsReached'),
    ('ExecReadQuote', 'ExecQuoteDone'), ('ReturnHome', 'ObserveHome')])
@pytest.mark.parametrize('gray', [0, 255])
def test_unknown_frames_never_advance_or_dispatch_input(entry, terminal, gray):
    resource = native.load_resource()
    assert resource.override_pipeline({entry: {'timeout': 250, 'rate_limit': 25}})
    controller = SavedImages([[np.full((720, 1280, 3), gray, dtype=np.uint8)]])
    receipt = native.run(native.bind(resource, controller), entry)
    assert not any(node['name'] == terminal and node['completed'] for node in receipt['nodes'])
    assert not controller.clicks and not controller.swipes


@pytest.mark.parametrize('filename,entry,terminal', [
    ('presets', 'NavigatePresets', 'PresetsReached'),
    ('return_home', 'ReturnHomeGate', 'ObserveHome'),
])
def test_zero_delay_gate_still_waits_for_a_complete_new_frame(tmp_path, filename, entry, terminal):
    from maa.resource import Resource

    # Keep the real dispatch definition; a public synthetic anchor replaces OCR here.
    gate = json.loads((PIPELINES / (filename + '.json')).read_text(encoding='utf-8'))[entry]
    assert gate['pre_delay'] == gate['post_delay'] == 0
    anchor = dict(recognition='ColorMatch', roi=[10, 10, 8, 8], method=40,
                  lower=[60, 255, 255], upper=[60, 255, 255], count=64,
                  action='DoNothing', next=[], pre_delay=0, post_delay=0)
    nodes = {entry: gate, terminal: anchor,
             'UnknownStop': dict(action='StopTask', next=[], pre_delay=0, post_delay=0)}
    for name in gate['next']:
        if name != terminal:
            nodes[name] = dict(anchor, roi=[30, 30, 8, 8])
    bundle = tmp_path / 'bundle'
    (bundle / 'pipeline').mkdir(parents=True)
    (bundle / 'pipeline/test.json').write_text(json.dumps(nodes), encoding='utf-8')
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    blank = np.zeros((720, 1280, 3), dtype=np.uint8)
    masked = blank.copy()
    masked[10:14, 10:18] = [0, 255, 0]
    ready = blank.copy()
    ready[10:18, 10:18] = [0, 255, 0]
    controller = SavedImages([[blank, masked, ready]])
    receipt = native.run(native.bind(resource, controller), entry)
    assert any(node['name'] == terminal and node['completed'] for node in receipt['nodes'])
    assert controller.frame >= 3 and not controller.clicks and not controller.swipes
