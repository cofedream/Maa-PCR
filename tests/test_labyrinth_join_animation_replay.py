"""Private replay of the sampled 菜单→设定 frames for the join-animation fallback.

The 1280x720 frames live under `.runtime/join-animation-calibration/` and are not
distributed, so this module is marked `private_replay` and skips when they are
absent.  It pins the production `CALIBRATION` against what was actually seen on
the client: page guards, radio state, and the click-guard composition.
"""
import hashlib
from pathlib import Path

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import animation_ui
from pcr_maa.replay import SavedImages

pytestmark = pytest.mark.private_replay
ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / '.runtime/join-animation-calibration'

MAP = dict(page='map', menu=False, settings=False, state=None)
MENU = dict(page='menu', menu=True, settings=False, state=None)
PLAY = dict(page='settings', menu=False, settings=True, state='on')
STOP = dict(page='settings', menu=False, settings=True, state='off')
EXPECT = {
    'map.png': dict(MAP, clicks=dict(menu_open=True, settings_open=False,
        toggle=False, settings_close=False, menu_close=False)),
    'map_final.png': dict(MAP, clicks=dict(menu_open=True, settings_open=False,
        toggle=False, settings_close=False, menu_close=False)),
    # The panel keeps the map visible; the opener guard must miss on these frames.
    'menu_panel.png': dict(MENU, clicks=dict(menu_open=False, settings_open=True,
        toggle=False, settings_close=False, menu_close=True)),
    'panel3.png': dict(MENU, clicks=dict(menu_open=False, settings_open=True,
        toggle=False, settings_close=False, menu_close=True)),
    'panel_again.png': dict(MENU, clicks=dict(menu_open=False, settings_open=True,
        toggle=False, settings_close=False, menu_close=True)),
    'dialog_play.png': dict(PLAY, clicks=dict(menu_open=False, settings_open=False,
        toggle=True, settings_close=True, menu_close=False)),
    'dialog_off2.png': dict(STOP, clicks=dict(menu_open=False, settings_open=False,
        toggle=False, settings_close=True, menu_close=False)),
    'dialog_from_panel.png': dict(STOP, clicks=dict(menu_open=False, settings_open=False,
        toggle=False, settings_close=True, menu_close=False)),
}
CLICKS = {
    'menu_open': 'LabyJoinAnimationMenuOpenOnce',
    'settings_open': 'LabyJoinAnimationSettingsOpenOnce',
    'toggle': 'LabyJoinAnimationToggleOnce',
    'settings_close': 'LabyJoinAnimationSettingsCloseOnce',
    'menu_close': 'LabyJoinAnimationMenuCloseOnce',
}


def _require_frames():
    if not FRAMES.is_dir() or not all((FRAMES / name).is_file() for name in EXPECT):
        pytest.skip('private join-animation calibration frames are not distributed')


def _node_names(root):
    raw = root.get('recognition')
    if not isinstance(raw, list):
        return []
    return [row.get('name') for row in raw if isinstance(row, dict)
            and row.get('box') is not None]


def _completed(tasker, node):
    receipt = native.run(tasker, node)
    return (receipt.get('nodes') or [{}])[0].get('completed') is True


@pytest.mark.parametrize('frame', sorted(EXPECT))
def test_calibrated_nodes_reproduce_the_sampled_client_frames(frame):
    _require_frames()
    expected = EXPECT[frame]
    image = native.read_image(FRAMES / frame)
    resource = native.load_resource(ROOT / 'assets/resource')
    nodes = animation_ui._control_nodes(animation_ui.CALIBRATION)
    assert nodes, 'production calibration must be complete'
    for key, spec in list(nodes.items()):
        if spec.get('action') == 'Click':
            nodes[key] = dict(spec, action='DoNothing')
    assert resource.override_pipeline(nodes)
    assert resource.register_custom_recognition(
        animation_ui.MENU_OPEN_GUARD, animation_ui._MenuClosedGuard())
    tasker = native.bind(resource, SavedImages([[image]]))
    try:
        page_names = _node_names((native.run(tasker, 'LabyJoinAnimationPageObserved').get('nodes') or [{}])[0])
        if expected['page'] == 'settings':
            assert 'LabyJoinAnimationSettingsObserved' in page_names
        elif expected['page'] == 'menu':
            assert 'LabyJoinAnimationMenuObserved' in page_names
        elif expected['page'] == 'map':
            assert 'LabyMapObserved' in page_names
        state_names = _node_names((native.run(tasker, 'LabyJoinAnimationStateObserved').get('nodes') or [{}])[0])
        if expected['state'] is None:
            assert state_names == []
        else:
            want = ('LabyJoinAnimationStateOn' if expected['state'] == 'on'
                    else 'LabyJoinAnimationStateOff')
            assert state_names == [want]
        assert _completed(tasker, 'LabyJoinAnimationMenuObserved') is expected['menu']
        assert _completed(tasker, 'LabyJoinAnimationSettingsObserved') is expected['settings']
        for role, node in CLICKS.items():
            assert _completed(tasker, node) is expected['clicks'][role], (frame, role)
    finally:
        native.wait_job(tasker.post_stop(), seconds=10)


def test_distributed_radio_template_matches_the_notice():
    path = ROOT / 'assets/resource/image/labyrinth/join_animation/radio_selected.png'
    assert path.is_file()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == 'fa9e272a4b86269c730c992e5949881cbdbce2afe0a6b908ac370a7daca245dd'
