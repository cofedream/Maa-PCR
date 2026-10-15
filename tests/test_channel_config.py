"""Channel configuration is data, never live focus or legacy ledger authority."""
from copy import deepcopy
import pytest
from pcr_maa.daily import validate_controller, validate_app_target
from pcr_maa.schedule_quick import gui_device, validate_gui_device
from pcr_maa import supported_daily
from pcr_maa.schedule_onekey import ITEMS
from test_gui_adb_schedule import config


@pytest.mark.parametrize('package,activity', [
    ('com.bilibili.priconne', '.MainActivity'),
    ('example.channel.client', '.GameActivity'),
    ('example.channel.client', 'example.sdk.GameActivity'),
    ('example.channel.client', '.game.Main$Inner'),
])
@pytest.mark.parametrize('module', ['event', 'schedule_onekey', 'abyss', 'deep', 'rewards', 'very_hard', 'hard', 'normal'])
def test_channel_is_preserved_through_existing_gui_modules(tmp_path, package, activity, module):
    cfg = config(tmp_path)
    cfg['controller'].update(app_package=package, app_activity=activity)
    checked = supported_daily.validate(cfg, execute=True, game_day='', context='gui-current-state',
        selected_modules=[module], rewards_final=False,
        schedule_authorizations=ITEMS if module == 'schedule_onekey' else ())
    assert checked['controller'] == cfg['controller']
    target = gui_device(checked)
    assert target['app_package'] == package
    validate_gui_device(target)


@pytest.mark.parametrize('field,value', [
    ('app_package', None), ('app_package', True), ('app_package', 'example'),
    ('app_package', 'example.client\n'), ('app_package', 'example.client;reboot'),
    ('app_package', 'example/.MainActivity'), ('app_package', 'a.' + 'a' * 255),
    ('app_activity', None), ('app_activity', False), ('app_activity', ''),
    ('app_activity', 'MainActivity'), ('app_activity', '.MainActivity\n'),
    ('app_activity', '.MainActivity|reboot'), ('app_activity', '.Main.*'),
])
def test_channel_rejects_malformed_or_command_like_fields(field, value):
    controller = dict(kind='adb', app_package='example.channel.client', app_activity='.GameActivity')
    controller[field] = value
    with pytest.raises(ValueError):
        validate_controller(controller)


def test_old_b_server_config_remains_byte_equivalent_data():
    controller = dict(kind='adb', app_package='com.bilibili.priconne')
    assert validate_controller(controller) == controller
    assert 'app_activity' not in controller


@pytest.mark.parametrize('identity', ['ledger_identity', 'manager_identity'])
def test_channel_cannot_inherit_b_server_history(tmp_path, identity):
    # The B-server marker is invalid for this package before any game operation.
    controller = dict(kind='adb', app_package='example.channel.client', app_activity='.GameActivity')
    if identity == 'manager_identity':
        controller[identity] = dict(source='mumu_manager_local_v1', installation=str(tmp_path),
            index=0, created_timestamp=1, app_package='com.bilibili.priconne')
    else:
        sdk = tmp_path / 'external_renderer_ipc.dll'
        sdk.write_bytes(b'SYNTHETIC NEVER LOAD')
        controller[identity] = dict(path=str(tmp_path), lib=str(sdk), index=0,
            app_cloned_index=0, app_package='com.bilibili.priconne')
    original = deepcopy(controller)
    with pytest.raises(ValueError, match='不匹配'):
        validate_controller(controller)
    assert controller == original
