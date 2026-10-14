"""Exercise the list's native stable-window wait without devices or private frames."""
import json
from pathlib import Path

import numpy as np
import pytest
from maa.define import LoggingLevelEnum
from maa.resource import Resource
from maa.tasker import Tasker

from pcr_maa import native
from pcr_maa.replay import SavedImages


PIPELINES = Path(__file__).resolve().parents[1] / 'assets/resource/pipeline'
SCAN_NODES = [
    ('preset_scan.json', 'ScanSwipe', 8, ['ScanSlotGuard', 'ScanCheckedLabel'],
     ['ScanBottom', 'ScanPage'], [567, 440], [567, 360], [50, 228, 1231, 503]),
    ('preset_execution.json', 'ExecScanSwipe', 8, ['ExecConfirmHeader'],
     ['ExecUnsafeStop', 'ExecScanBottom', 'ExecScanPage'], [570, 393], [570, 313], [55, 134, 1231, 449]),
    ('event.json', 'EventSwipe', 8, ['EventListGuard', 'EventCheckedLabel'],
     ['EventShort', 'EventBottom', 'EventPage'], [567, 430], [567, 350], [50, 188, 1235, 503]),
    ('event.json', 'EventResetTop', 2,
     ['EventListGuard', 'EventCheckedLabel', 'EventGrayTop', 'EventRailBlue', 'EventRailGray'],
     ['EventTop', 'EventResetTop'], [567, 245], [567, 460], [50, 188, 1235, 503]),
]


@pytest.mark.parametrize('filename,node,budget,guards,next_nodes,begin,end,bounds', SCAN_NODES)
def test_list_wait_preserves_input_structure(filename, node, budget, guards, next_nodes, begin, end, bounds):
    swipe = json.loads((PIPELINES / filename).read_text(encoding='utf-8'))[node]
    assert swipe['action'] == 'Swipe' and swipe['duration'] == 1000 and swipe['max_hit'] == budget
    assert swipe['all_of'] == guards and swipe['next'] == next_nodes
    assert swipe['begin'] == begin and swipe['end'] == end
    assert swipe['post_delay'] == 0
    wait = swipe['post_wait_freezes']
    assert (wait['time'], wait['rate_limit'], wait['timeout']) == (200, 100, 2500)
    x, y, w, h = wait['target']
    assert x <= bounds[0] and y <= bounds[1] and x + w >= bounds[2] and y + h >= bounds[3]


def test_preset_click_waits_unchanged():
    pipeline = json.loads((PIPELINES / 'preset_scan.json').read_text(encoding='utf-8'))
    assert all(pipeline[name]['post_delay'] == 500 for name in
               ('ScanSelectClick', 'ScanToggle', 'ScanResetToggle'))


@pytest.mark.parametrize('moving_inside_list', [False, True])
@pytest.mark.parametrize('filename,node', [(row[0], row[1]) for row in SCAN_NODES])
def test_native_wait_tracks_list_motion_and_ignores_outside_motion(tmp_path, moving_inside_list, filename, node):
    swipe = json.loads((PIPELINES / filename).read_text(encoding='utf-8'))[node]
    # Only bypass the page gate; execute the actual configured swipe and wait.
    swipe.update(recognition='DirectHit', next=[], on_error=[])
    swipe.pop('all_of')
    bundle = tmp_path / 'bundle'
    (bundle / 'pipeline').mkdir(parents=True)
    (bundle / 'pipeline/test.json').write_text(json.dumps({node: swipe}), encoding='utf-8')
    Tasker.set_log_dir(tmp_path / 'maa')
    Tasker.set_stdout_level(LoggingLevelEnum.Off)
    resource = Resource()
    assert resource.post_bundle(bundle).wait().succeeded
    parsed = resource.get_node_data(node)['post_wait_freezes']
    assert all(parsed[key] == value for key, value in swipe['post_wait_freezes'].items())

    rng = np.random.default_rng(0)
    base = rng.integers(0, 256, (720, 1280, 3), dtype=np.uint8)
    frames = []
    x, y, w, h = swipe['post_wait_freezes']['target']
    for _ in range(8):
        image = base.copy()
        region = image[y:y+h, x:x+w] if moving_inside_list else image[:y-8]
        region[:] = rng.integers(0, 256, region.shape, dtype=np.uint8)
        frames.append(image)
    controller = SavedImages([[base], frames])
    tasker = native.bind(resource, controller)
    result = native.run(tasker, node)
    assert result['native_ok']
    assert controller.swipes == [swipe['begin'] + swipe['end'] + [1000]] and not controller.clicks
    assert controller.frame >= 9 if moving_inside_list else controller.frame < 8
