"""In-run 菜单→设定 fallback for the Labyrinth character-join animation.

The client has no API for this page and no real frames are bundled, so the
fallback must stay inert until it is calibrated; these tests drive the state
machine with synthetic receipts and never touch a device.
"""
from types import SimpleNamespace as NS
from pathlib import Path

import pytest

from pcr_maa import native
from pcr_maa.labyrinth import animation_ui, autoplay, game_port
from pcr_maa.labyrinth.batch import BatchStopped
from pcr_maa.labyrinth.scene import Scene
from test_labyrinth_autoplay import receipt as scene_receipt
from test_labyrinth_reward_shop_upstream import harness

ROOT = Path(__file__).resolve().parents[1]


PAGE_CHILDREN = ['LabyMapObserved', 'LabyJoinAnimationMenuObserved',
    'LabyJoinAnimationSettingsObserved', 'LabyDateChangedObserved',
    'LabyDisconnect6002Observed']
STATE_CHILDREN = ['LabyJoinAnimationStateOn', 'LabyJoinAnimationStateOff']


def calibration():
    return {
        'menu_guard': (),
        'settings_guard': (),
        'settings_anchors': {},
        'menu_entry': {'expected': '^设定$', 'roi': [900, 300, 150, 40]},
        'option': {'expected': '^角色加入动画$', 'roi': [500, 300, 300, 40]},
        'state_on': {'expected': '^播放$', 'roi': [800, 300, 100, 40]},
        'state_off': {'expected': '^不播放$', 'roi': [800, 300, 100, 40]},
        'settings_close': {'expected': '^关闭$', 'roi': [600, 600, 100, 40]},
        'menu_close': {'target': [1225, 82, 1, 1]},
        'targets': {'menu': [1225, 82, 1, 1], 'menu_entry': [950, 320, 1, 1],
                    'toggle': [900, 320, 1, 1], 'settings_close': [650, 620, 1, 1]},
        'timeout_seconds': 20,
    }


class Device:
    """Synthetic 1280x720 client: one page, one option state, slow tasks."""

    def __init__(self, *, start='map', state='on', close_to='menu',
                 unsent_moves=None, toggle_no_effect=False, unknown_frames=0):
        self.page = start
        self.state = state
        self.close_to = close_to
        self.unsent_moves = dict(unsent_moves or {})
        self.toggle_no_effect = toggle_no_effect
        self.unknown_frames = unknown_frames
        self.clock = [0.0]
        self.tasks = 0
        self.inputs = []

    # -- native.run -----------------------------------------------------
    def receipt(self, node, recognition=None, action=None, completed=True):
        self.tasks += 1
        self.clock[0] += 1.0
        return dict(native_ok=True, tasker_inited=True, task_id=self.tasks,
            nodes=[dict(name=node, completed=completed, action=action,
                        recognition=recognition)])

    @staticmethod
    def rows(names, matched):
        return [dict(name=name, box=[0, 0, 1, 1] if name in matched else None)
                for name in names]

    def guard_read(self, node, matched):
        return self.receipt(node, self.rows([node], [node] if matched else []))

    def run(self, tasker, node, reset_hits=()):
        if node == 'LabyJoinAnimationPageObserved':
            matched = {'map': 'LabyMapObserved',
                       'menu': 'LabyJoinAnimationMenuObserved',
                       'settings': 'LabyJoinAnimationSettingsObserved'}.get(self.page)
            if matched is None:
                return self.receipt(node, completed=False)
            if self.unknown_frames and self.page != 'map':
                self.unknown_frames -= 1
                return self.receipt(node, completed=False)
            return self.receipt(node, self.rows(PAGE_CHILDREN, [matched]))
        if node == 'LabyJoinAnimationStateObserved':
            matched = 'LabyJoinAnimationStateOn' if self.state == 'on' else 'LabyJoinAnimationStateOff'
            return self.receipt(node, self.rows(STATE_CHILDREN, [matched]))
        if node == 'LabyMapObserved':
            return self.guard_read(node, self.page == 'map')
        if node in _CLICK_STEPS:
            page = _CLICK_STEPS[node]
            if self.page != page:
                return self.receipt(node)  # recognition miss, no action started
            if node in self.unsent_moves:
                target = self.unsent_moves.pop(node)
                self.page = target
                raise native.NativeInputNotSent(dict(input_execution='not_sent',
                    native_evidence=dict(entry=node, finished=True,
                        recognition_failed=True, action_started=False)))
            self.inputs.append(node)
            self._apply(node)
            return self.receipt(node, action=dict(name=node, success=True))
        raise AssertionError('unexpected node ' + node)

    def _apply(self, node):
        if node == 'LabyJoinAnimationMenuOpenOnce':
            self.page = 'menu'
        elif node == 'LabyJoinAnimationSettingsOpenOnce':
            self.page = 'settings'
        elif node == 'LabyJoinAnimationToggleOnce':
            if not self.toggle_no_effect:
                self.state = 'off'
        elif node == 'LabyJoinAnimationSettingsCloseOnce':
            self.page = self.close_to
        elif node == 'LabyJoinAnimationMenuCloseOnce':
            self.page = 'map'


_CLICK_STEPS = {
    'LabyJoinAnimationMenuOpenOnce': 'map',
    'LabyJoinAnimationSettingsOpenOnce': 'menu',
    'LabyJoinAnimationToggleOnce': 'settings',
    'LabyJoinAnimationSettingsCloseOnce': 'settings',
    'LabyJoinAnimationMenuCloseOnce': 'menu',
}


def install(monkeypatch, device):
    batch, stamp = harness()
    tasker = NS(resource=NS(override_pipeline=lambda nodes: True,
        register_custom_recognition=lambda name, callback: True), stopping=False)
    monkeypatch.setattr(native, 'run', device.run)
    monkeypatch.setattr(native, 'active_now', lambda: device.clock[0])
    monkeypatch.setattr(autoplay, '_poll_pause',
        lambda *args: device.clock.__setitem__(0, device.clock[0] + .5))
    return batch, stamp, tasker


def settled(batch, operation):
    return [event for event in batch.journal.events()
            if event.get('state') == 'settled' and event.get('operation') == operation]


def test_fallback_status_matrix():
    for status in ('no_root', 'unknown', 'write_failed', 'error',
                   'stop_unverified', 'stop_failed'):
        assert animation_ui.needs_ui_fallback(status) is True
    for status in ('already_off', 'fixed', 'stopped', 'adb_missing', None, ''):
        assert animation_ui.needs_ui_fallback(status) is False


def test_production_calibration_is_complete_and_distributed():
    assert animation_ui.available() is True
    calibration = animation_ui.CALIBRATION
    assert calibration['targets']['menu'] == [1217, 71, 1, 1]
    for name, roi in (('menu', [920, 20, 180, 60]), ('settings', [560, 170, 160, 55])):
        assert 0 <= roi[0] and 0 <= roi[1] and roi[0] + roi[2] <= 1280 and roi[1] + roi[3] <= 720
    template = ROOT / ('assets/resource/image/'
                       + calibration['state_on']['template'])
    assert template.is_file() and template.stat().st_size > 0
    nodes = animation_ui._control_nodes(calibration)
    assert nodes
    for key, target in calibration['targets'].items():
        assert 0 <= target[0] < 1280 and 0 <= target[1] < 720, key


def test_uncalibrated_fallback_never_touches_the_device(monkeypatch):
    device = Device()
    batch, stamp, tasker = install(monkeypatch, device)
    monkeypatch.setattr(animation_ui, 'CALIBRATION', None)
    assert animation_ui.available() is False
    assert animation_ui.MaaJoinAnimationSetting(tasker, stamp).ensure_off(
        batch, enter_id=210) == 'skipped_uncalibrated'
    assert device.tasks == 0 and device.inputs == [] and not batch.journal.events()


def test_calibrated_flow_toggles_off_reads_back_and_returns_to_map(monkeypatch):
    device = Device()
    batch, stamp, tasker = install(monkeypatch, device)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    assert adapter.ensure_off(batch, enter_id=210) == 'toggled'
    assert device.inputs == ['LabyJoinAnimationMenuOpenOnce',
        'LabyJoinAnimationSettingsOpenOnce', 'LabyJoinAnimationToggleOnce',
        'LabyJoinAnimationSettingsCloseOnce', 'LabyJoinAnimationMenuCloseOnce']
    assert device.state == 'off' and device.page == 'map'
    assert [event['operation'] for event in batch.journal.events()
            if event.get('state') == 'settled'] == [
        'join_animation_menu_open', 'join_animation_settings_open',
        'join_animation_toggle', 'join_animation_settings_close',
        'join_animation_menu_close']
    assert all(event['enter_id'] == 210 for event in batch.journal.events())


def test_calibrated_flow_leaves_an_already_off_option_alone(monkeypatch):
    device = Device(state='off')
    batch, stamp, tasker = install(monkeypatch, device)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    assert adapter.ensure_off(batch, enter_id=210) == 'already_off'
    assert 'LabyJoinAnimationToggleOnce' not in device.inputs
    assert not settled(batch, 'join_animation_toggle')


def test_settings_close_can_return_straight_to_the_map(monkeypatch):
    device = Device(close_to='map')
    batch, stamp, tasker = install(monkeypatch, device)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    assert adapter.ensure_off(batch, enter_id=210) == 'toggled'
    assert device.inputs == ['LabyJoinAnimationMenuOpenOnce',
        'LabyJoinAnimationSettingsOpenOnce', 'LabyJoinAnimationToggleOnce',
        'LabyJoinAnimationSettingsCloseOnce']
    assert device.page == 'map'


def test_toggle_readback_mismatch_stops_instead_of_continuing(monkeypatch):
    device = Device(toggle_no_effect=True)
    batch, stamp, tasker = install(monkeypatch, device)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    with pytest.raises(BatchStopped, match='^join_animation_setting_readback_unknown$'):
        adapter.ensure_off(batch, enter_id=210)
    assert 'LabyJoinAnimationToggleOnce' in device.inputs
    assert 'LabyJoinAnimationSettingsCloseOnce' not in device.inputs


def test_tap_that_is_proven_unsent_never_repeats(monkeypatch):
    # The menu appeared between the guard read and the tap: no duplicate input.
    device = Device(unsent_moves={'LabyJoinAnimationMenuOpenOnce': 'menu'})
    batch, stamp, tasker = install(monkeypatch, device)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    assert adapter.ensure_off(batch, enter_id=210) == 'toggled'
    assert device.inputs == ['LabyJoinAnimationSettingsOpenOnce',
        'LabyJoinAnimationToggleOnce', 'LabyJoinAnimationSettingsCloseOnce',
        'LabyJoinAnimationMenuCloseOnce']
    assert len(settled(batch, 'join_animation_menu_open')) == 1
    assert settled(batch, 'join_animation_menu_open')[0]['result']['input_execution'] == 'not_sent'


def test_unknown_transition_frames_are_waited_through_without_input(monkeypatch):
    # Two unreadable frames after the menu tap: wait, never tap again.
    device = Device(unknown_frames=2)
    batch, stamp, tasker = install(monkeypatch, device)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    assert adapter.ensure_off(batch, enter_id=210) == 'toggled'
    assert device.inputs == ['LabyJoinAnimationMenuOpenOnce',
        'LabyJoinAnimationSettingsOpenOnce', 'LabyJoinAnimationToggleOnce',
        'LabyJoinAnimationSettingsCloseOnce', 'LabyJoinAnimationMenuCloseOnce']


def test_pipeline_builds_full_guards_and_calibrated_targets():
    nodes = animation_ui._control_nodes(calibration())
    menu_open = nodes['LabyJoinAnimationMenuOpenOnce']
    assert menu_open['recognition'] == 'Custom'
    assert menu_open['custom_recognition'] == animation_ui.MENU_OPEN_GUARD
    assert menu_open['action'] == 'Click' and menu_open['target'] is True
    assert menu_open['custom_recognition_param'] == {'target': [1225, 82, 1, 1]}
    assert nodes['LabyJoinAnimationSettingsOpenOnce']['all_of'] == [
        'LabyJoinAnimationMenuEntry']
    assert nodes['LabyJoinAnimationToggleOnce']['all_of'] == [
        'LabyJoinAnimationOption', 'LabyJoinAnimationStateOn']
    assert nodes['LabyJoinAnimationMenuCloseOnce']['target'] == [1225, 82, 1, 1]
    page_children = nodes['LabyJoinAnimationPageObserved']['any_of']
    assert page_children.index('LabyJoinAnimationSettingsObserved') < page_children.index('LabyMapObserved')
    assert page_children.index('LabyJoinAnimationMenuObserved') < page_children.index('LabyMapObserved')
    assert 'LabyJoinAnimationDate' in page_children
    assert 'LabyJoinAnimationDisconnect' in page_children
    for name in ('LabyJoinAnimationMenuEntry', 'LabyJoinAnimationOption',
                 'LabyJoinAnimationStateOn', 'LabyJoinAnimationStateOff',
                 'LabyJoinAnimationSettingsClose'):
        assert nodes[name]['action'] == 'DoNothing' and nodes[name]['next'] == []


class GuardFrame:
    """Synthetic provider for one input frame: names that matched."""

    def __init__(self, matched):
        self.matched = set(matched)

    def run_recognition(self, node, image):
        return NS(hit=node in self.matched)


def guard_analyze(matched, target=(1225, 82, 1, 1)):
    param = {'target': list(target)} if target is not None else {}
    return animation_ui._MenuClosedGuard().analyze(GuardFrame(matched),
        NS(image=None, custom_recognition_param=param))


def test_menu_open_guard_misses_when_a_panel_is_already_open():
    # The opened panel keeps the map visible; tapping 菜单 again would close it.
    for panel in ('LabyJoinAnimationMenuObserved', 'LabyJoinAnimationSettingsObserved'):
        assert guard_analyze({'LabyMapObserved', panel}) is None


def test_menu_open_guard_misses_on_an_unreadable_frame():
    assert guard_analyze(set()) is None


def test_menu_open_guard_needs_a_calibrated_target():
    assert guard_analyze({'LabyMapObserved'}, target=None) is None


def test_menu_open_guard_hits_only_a_closed_map():
    result = guard_analyze({'LabyMapObserved'})
    assert result is not None and result.box == [1225, 82, 1, 1]


def test_menu_open_guard_is_registered_once_per_resource(monkeypatch):
    device = Device()
    batch, stamp, tasker = install(monkeypatch, device)
    registered = []
    tasker.resource.register_custom_recognition = lambda name, callback: (
        registered.append(name) or True)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    adapter._install_guard()
    adapter._install_guard()
    assert registered == [animation_ui.MENU_OPEN_GUARD]


def test_menu_open_guard_belongs_to_bound_native_resource():
    import gc
    from weakref import ref

    registered = []

    class Resource:
        def register_custom_recognition(self, name, callback):
            registered.append((name, ref(callback)))
            return True

    class Tasker:
        def __init__(self):
            self._resource_holder = Resource()

        @property
        def resource(self):
            return Resource()

    tasker = Tasker()
    animation_ui.MaaJoinAnimationSetting(tasker, stamp=None)._install_guard()
    gc.collect()

    assert len(registered) == 1
    name, callback_ref = registered[0]
    assert name == animation_ui.MENU_OPEN_GUARD
    assert callback_ref() is not None
    assert callback_ref() is tasker._resource_holder._labyrinth_join_animation_guard

    animation_ui.MaaJoinAnimationSetting(tasker, stamp=None)._install_guard()
    assert len(registered) == 1


def test_wrong_start_page_sends_no_input(monkeypatch):
    device = Device(start='menu')
    batch, stamp, tasker = install(monkeypatch, device)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    with pytest.raises(BatchStopped, match='^join_animation_start_page_unknown$'):
        adapter.ensure_off(batch, enter_id=210)
    assert device.inputs == [] and not batch.journal.events()


def test_recover_to_map_only_touches_recognised_pages(monkeypatch):
    device = Device(start='settings')
    batch, stamp, tasker = install(monkeypatch, device)
    adapter = animation_ui.MaaJoinAnimationSetting(tasker, stamp,
        calibration=calibration())
    assert adapter.recover_to_map(batch, enter_id=210) is True
    assert device.inputs == ['LabyJoinAnimationSettingsCloseOnce',
        'LabyJoinAnimationMenuCloseOnce'] and device.page == 'map'
    device.page = 'unknown'
    assert adapter.recover_to_map(batch, enter_id=210) is False


def runner(monkeypatch, *, status, calibration_value, adapter_result='toggled'):
    value = autoplay.AutoplayRunner.__new__(autoplay.AutoplayRunner)
    value.tasker = NS()
    value.stamp = lambda **fields: fields
    value.animation_setting_status = status
    value.join_animation_setting_state = None
    calls = []
    class Adapter:
        def __init__(self, tasker, stamp, *, timings=None):
            calls.append('init')
        def available(self):
            return calibration_value
        def ensure_off(self, batch, *, enter_id):
            calls.append(('ensure_off', enter_id))
            return adapter_result
        def recover_to_map(self, batch, *, enter_id):
            calls.append('recover')
            return False
    monkeypatch.setattr(animation_ui, 'MaaJoinAnimationSetting', Adapter)
    scene = Scene('map', scene_receipt(), {'area': 1})
    batch = NS(timings=object(), emit_log=lambda text: None,
               port=NS(game=NS(animation_setting_status=status)),
               checkpoint=lambda: None)
    observed = [Scene('map', scene_receipt(), {'area': 1})]
    value._observe = lambda *args, **kwargs: observed.pop(0)
    return value, batch, scene, calls


@pytest.mark.parametrize('status', ['already_off', 'fixed', None])
def test_runner_skips_the_ui_when_the_pref_path_confirmed_off(monkeypatch, status):
    value, batch, scene, calls = runner(monkeypatch, status=status, calibration_value=True)
    assert value._prepare_join_animation_setting(batch, NS(enter_id=210), scene) is scene
    assert calls == []


def test_runner_logs_uncalibrated_fallback_and_keeps_the_map(monkeypatch):
    value, batch, scene, calls = runner(monkeypatch, status='no_root',
        calibration_value=False)
    assert value._prepare_join_animation_setting(batch, NS(enter_id=210), scene) is scene
    assert calls == ['init']
    assert value.join_animation_setting_state == 'uncalibrated'


def test_runner_runs_the_ui_once_and_keeps_the_client_state_for_later_runs(monkeypatch):
    value, batch, scene, calls = runner(monkeypatch, status='no_root',
        calibration_value=True)
    fresh = value._prepare_join_animation_setting(batch, NS(enter_id=210), scene)
    assert fresh.kind == 'map' and fresh is not scene
    assert calls == ['init', ('ensure_off', 210)]
    assert value.join_animation_setting_state == 'toggled'
    assert batch.port.game.animation_setting_status == 'already_off'
    assert batch.animation_setting_off is True


def test_game_port_forwards_the_pref_status():
    port = game_port.MaaGamePort(None, lambda **fields: fields,
        animation_setting='no_root')
    assert port.animation_setting_status == 'no_root'
