"""Production entry recognition on sanitized saved UI; no real controller or account state."""
import json
from pathlib import Path

import numpy as np
import pytest
from maa.custom_recognition import CustomRecognition

from pcr_maa import native
from pcr_maa.replay import SavedImages
from scripts.entry_recognition_audit import audit

ROOT = Path(__file__).resolve().parents[1]


def test_every_input_rule_and_dynamic_factory_is_accounted_for():
    report = audit()
    assert len(report['static_inputs']) >= 138
    assert len(report['dynamic_factory_inputs']) >= 35
    assert all(row['status'] == 'expanded' for row in report['factories'])
    assert report['python_construction_sites'] and report['dispatch_sites']
    for row in report['static_inputs']:
        assert row['recognition'] in ('And', 'Or', 'OCR', 'TemplateMatch', 'Custom'), row
        assert row['rules'], row
        assert not any(leaf.get('status') == 'recognition_cycle' for leaf in row['rules']), row
        for leaf in row['rules']:
            if leaf.get('method') == 'OCR':
                x, y, w, h = leaf['roi']
                assert 0 <= x < x+w <= 1280 and 0 <= y < y+h <= 720, (row, leaf)
                assert leaf['expected'], (row, leaf)
                # Historical preset-page identity uses the SDK default plus its sibling guards.
                if leaf['threshold'] is None:
                    assert leaf['name'] == 'PresetsReached', (row, leaf)
                else:
                    assert leaf['threshold'] >= .8, (row, leaf)


def test_fixed_schedule_targets_require_their_sampled_layout():
    for file, entry in [('schedule.json', 'ScheduleEnter'), ('schedule_quick.json', 'OneKeyQuickEnter')]:
        node = json.loads((native.RESOURCE / 'pipeline' / file).read_text(encoding='utf-8'))[entry]
        assert set(node['all_of']) == {'HomeDefaultTextLayout', 'ObserveHome'}
        assert node['target'] == [1017, 575] and node['max_hit'] == 1


@pytest.fixture(scope='module')
def saved_ui():
    return native.read_image(ROOT / 'tests/data/home-entry-ui.png')


def probe(image, names):
    resource = native.load_resource()
    controller = SavedImages([[image]])
    def no_input(*args):
        raise AssertionError('recognition-only replay dispatched input')
    controller.click = controller.swipe = no_input
    tasker = native.bind(resource, controller)
    values, errors = {}, []
    class Probe(CustomRecognition):
        def analyze(self, context, argv):
            try:
                for name in names:
                    result = context.run_recognition(name, argv.image)
                    values[name] = bool(result and result.hit)
            except Exception as error:
                errors.append(repr(error))
            return self.AnalyzeResult([0, 0, 1, 1], {})
    assert resource.register_custom_recognition('EntryReplay', Probe())
    assert resource.override_pipeline({'EntryReplay': dict(recognition='Custom', custom_recognition='EntryReplay',
        action='DoNothing', pre_delay=0, post_delay=0, timeout=1000)})
    try:
        assert native.run(tasker, 'EntryReplay')['native_ok'] and not errors, errors
        return values
    finally:
        native.stop_tasker(tasker)


HOME_ENTRIES = ['RewardsMissionsOpen', 'RewardsGiftsOpen', 'ScheduleEnter', 'OneKeyQuickEnter',
                'EnterAdventure', 'EventAdventure', 'AbyssFromHome']


def test_saved_ui_passes_whole_entry_rules(saved_ui):
    assert all(probe(saved_ui, HOME_ENTRIES).values())


@pytest.mark.parametrize('variant', ['blank', 'missing_home_word', 'missing_exp', 'missing_missions', 'wrong_missions', 'half_missions'])
def test_entry_missing_identity_or_label_never_grants_permission(saved_ui, variant):
    image = saved_ui.copy()
    if variant == 'blank': image[:] = 0
    elif variant == 'missing_home_word': image[594:632, 980:1060] = 0
    elif variant == 'missing_exp': image[22:62, 90:250] = 0
    elif variant == 'missing_missions': image[594:632, 1090:1150] = 0
    elif variant == 'half_missions': image[598:623, 1094:1117] = 0
    else:
        image[598:623, 1094:1138] = image[598:623, 1192:1236]
    values = probe(image, HOME_ENTRIES)
    if variant in ('missing_missions', 'wrong_missions', 'half_missions'):
        assert values['RewardsMissionsOpen'] is False
    else:
        assert not any(values.values()), values


def edge_ui(image):
    changed = image.copy()
    changed[594:632, 970:] = 0
    changed[594:632, 1012:] = image[594:632, 970:1238]
    return changed


def test_layout_and_gift_target_move_together(saved_ui):
    image = edge_ui(saved_ui)
    names = ['ObserveHome', 'RewardsGiftsOpen', 'RewardsMissionsOpen', 'ScheduleEnter', 'OneKeyQuickEnter']
    values = probe(image, names)
    assert values == dict(ObserveHome=True, RewardsGiftsOpen=True, RewardsMissionsOpen=False,
                         ScheduleEnter=False, OneKeyQuickEnter=False)


@pytest.mark.parametrize('move_gift', [True, False])
def test_mixed_home_layouts_are_rejected(saved_ui, move_gift):
    image = saved_ui.copy()
    left, right = (1188, 1280) if move_gift else (980, 1100)
    image[594:632, left:right] = 0
    width = min(right-left, 1280-left-42)
    image[594:632, left+42:left+42+width] = saved_ui[594:632, left:left+width]
    assert not any(probe(image, HOME_ENTRIES).values())


@pytest.mark.parametrize('entry,edge,box', [
    ('RewardsMissionsOpen', False, (1094, 598, 44, 25)),
    ('RewardsGiftsOpen', False, (1188, 596, 51, 30)),
    ('RewardsGiftsOpen', True, (1230, 596, 50, 30)),
])
def test_native_entry_dispatches_only_one_saved_frame_click(saved_ui, entry, edge, box):
    resource = native.load_resource()
    assert resource.override_pipeline({entry: dict(next=[], pre_delay=0, post_delay=0, timeout=1)})
    controller = SavedImages([[edge_ui(saved_ui) if edge else saved_ui]])
    tasker = native.bind(resource, controller)
    try:
        receipt = native.run(tasker, entry)
        native.require_native_input(receipt, (entry,))
        assert len(controller.clicks) == 1 and not controller.swipes
        x, y = controller.clicks[0]
        left, top, width, height = box
        assert left <= x < left+width and top <= y < top+height
    finally:
        native.stop_tasker(tasker)


def test_entry_rechecks_changed_frame_before_click(saved_ui):
    resource = native.load_resource()
    assert resource.override_pipeline({'RewardsMissionsOpenStart': dict(timeout=200, on_error=[])})
    controller = SavedImages([[saved_ui, np.zeros_like(saved_ui)]])
    tasker = native.bind(resource, controller)
    try:
        receipt = native.run(tasker, 'RewardsMissionsOpenStart')
        assert not controller.clicks and not controller.swipes
        assert not any(row['name'] == 'RewardsMissionsOpen' and row['completed'] for row in receipt['nodes'])
    finally:
        native.stop_tasker(tasker)


def test_all_static_input_rules_reject_empty_frame():
    # Custom business recognizers need their module registration and separate caller contracts.
    rows = [row for row in audit()['static_inputs']
            if all(leaf.get('method') != 'Custom' and not leaf.get('status') for leaf in row['rules'])]
    assert len(rows) >= 80
    values = probe(np.zeros((720, 1280, 3), np.uint8), [row['name'] for row in rows])
    assert not any(values.values()), [name for name, hit in values.items() if hit]
