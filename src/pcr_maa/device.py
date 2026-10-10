"""Fixed, source-audited Maa ADB configuration. No discovery or command overrides."""
import os
import re
from pathlib import Path
from ipaddress import IPv4Address


def _legacy_adb_path():
    """Default CLI install only; GUI transport is explicitly selected by its Owner."""
    directory = os.environ.get('ProgramFiles')
    if (not directory or directory.startswith(('\\\\', '//'))
            or any(c in directory for c in '\r\n\x00')
            or not Path(directory).is_absolute()):
        return ''
    return (Path(directory) / 'Netease/MuMuPlayer-12.0/nx_main/adb.exe').as_posix()


ADB_PATH = _legacy_adb_path()
SERIAL = "127.0.0.1:16384"


def validate_connection(value):
    """Validate data only. Never launch ADB, discover, or connect a device."""
    if not isinstance(value, dict) or set(value) != {'adb_path', 'serial'}:
        raise ValueError('连接配置仅接受 adb_path 与唯一 serial 地址')
    path = value['adb_path']
    if (not isinstance(path, str) or not path or path.startswith(('\\\\', '//'))
            or any(c in path for c in '\r\n\x00')
            or not Path(path).is_absolute() or Path(path).name.lower() != 'adb.exe'
            or not Path(path).is_file()):
        raise ValueError('ADB 路径无效：须为本机已有 adb.exe 的绝对路径（可含中文/空格）')
    validate_serial(value['serial'])
    return dict(value)


def validate_serial(serial):
    """Explicit address syntax only; no discovery or connection."""
    if isinstance(serial, str) and re.fullmatch(r'emulator-[1-9][0-9]{0,4}', serial):
        if int(serial[9:]) <= 65535:
            return serial
        raise ValueError('设备绑定无效：模拟器串号超出范围')
    match = re.fullmatch(r'([0-9.]+):([1-9][0-9]{0,4})', serial) if isinstance(serial, str) else None
    if not match:
        raise ValueError('设备绑定无效：须指定唯一 IPv4:端口或 emulator-串号，禁止发现或扫描')
    address = IPv4Address(match[1])
    if address.is_unspecified or address.is_multicast or address.is_reserved or int(match[2]) > 65535:
        raise ValueError('设备绑定无效：须为单一有效设备地址')
    return serial


def connection_options(config, *, include_schedule_onekey=False):
    """Legacy callers retain fixed defaults; v2 callers explicitly bind transport."""
    if config['version'] == 1:
        return {}
    connection = validate_connection(config['connection'])
    return {'connection': connection}


def fixed_config(input_display, screenshot_display, *, preset_list=False):
    if (type(input_display) is not int or not 0 <= input_display <= 255
            or type(screenshot_display) not in (str, int)
            or not re.fullmatch(r"[0-9]{1,20}", str(screenshot_display))):
        raise ValueError("Require logical display 0..255 and an explicitly verified numeric physical display ID")
    base = ["{ADB}", "-s", "{ADB_SERIAL}"]
    check = base + ["get-state"]
    denied = base + ["shell", "false"]
    commands = {name: denied[:] for name in (
        "Shell", "Swipe", "ClickKey", "InputText", "StartApp", "StartActivity", "StopApp", "Devices")}
    commands.update({
        "Connect": check[:], "KillServer": check[:], "TestConnection": check[:],
        "Monitor": base + ["shell", "cat"],
        "UUID": base + ["get-serialno"],
        "ScreencapEncode": base + ["exec-out", f"screencap -p -d {screenshot_display}"],
        "Click": base + ["shell", f"input -d {input_display} tap {{X}} {{Y}}"],
    })
    if preset_list:
        commands["Swipe"] = base + ["shell", f"input -d {input_display} swipe {{X1}} {{Y1}} {{X2}} {{Y2}} {{DURATION}}"]
    return {"command": commands}


def navigate(input_display, screenshot_display, *, slot=None, schedule_start=None, daily_config=None, daily_report=None,
             execution=None, rewards=None, event=None, supported=None, house=None, onekey=None, settings_audit=None,
             connection=None):
    # Only this explicit product operation constructs AdbController. Never used by tests.
    bound = validate_connection(connection if connection is not None else dict(adb_path=ADB_PATH, serial=SERIAL))
    adb_path, serial = bound['adb_path'], bound['serial']
    if serial != SERIAL and (settings_audit is not None or house is not None):
        raise ValueError('日程组件尚未支持此设备绑定，零设备操作')
    for owner in (daily_config, supported.get('config') if isinstance(supported, dict) else None):
        if isinstance(owner, dict) and owner.get('version') == 2 and owner.get('connection') != bound:
            raise ValueError('Conflicting configured device binding')
    from maa.controller import AdbController
    from maa.define import MaaAdbInputMethodEnum, MaaAdbScreencapMethodEnum
    from maa.tasker import Tasker
    from .native import load_resource, run, inspect, wait_job, write_png, require_frame_size, stop_tasker
    import cv2
    import time

    if onekey is not None:
        from .schedule_onekey import validate_execution
        if (not isinstance(onekey, dict) or set(onekey) not in (
                {'execute', 'game_day', 'authorizations'},
                {'execute', 'game_day', 'authorizations', 'attest_current_plan'})
                or onekey['execute'] is not True
                or type(onekey.get('attest_current_plan', False)) is not bool
                or any(v is not None for v in (slot, schedule_start, daily_config, daily_report,
                                               execution, rewards, event, supported, house))):
            raise ValueError('Invalid or conflicting standalone one-key arguments')
        validate_execution(onekey['game_day'], dict(serial=serial, input_display=input_display,
            screenshot_display=screenshot_display), onekey['authorizations'], portable=True)
    if settings_audit is not None:
        from .schedule_onekey import check_day
        if (not isinstance(settings_audit, dict)
                or set(settings_audit) not in ({'audit', 'game_day'}, {'audit', 'game_day', 'start'})
                or settings_audit['audit'] is not True
                or settings_audit.get('start', 'home') not in ('home', 'schedule')
                or any(v is not None for v in (slot, schedule_start, daily_config, daily_report,
                                               execution, rewards, event, supported, house, onekey))):
            raise ValueError('Invalid or conflicting standalone settings audit arguments')
        check_day(settings_audit['game_day'])
    if house is not None:
        from datetime import date
        if (not isinstance(house, dict) or set(house) != {'execute', 'game_day'}
                or type(house['execute']) is not bool
                or any(v is not None for v in (slot, schedule_start, daily_config, daily_report,
                                               execution, rewards, event, supported))):
            raise ValueError('Invalid or conflicting standalone house arguments')
        if house['execute'] and not house['game_day']:
            raise ValueError('House execute requires explicit game day')
        if house['game_day'] is not None and date.fromisoformat(house['game_day']).isoformat() != house['game_day']:
            raise ValueError('Invalid house game day')
    if supported is not None:
        from .supported_daily import validate
        base_keys = {'config', 'execute', 'game_day', 'context', 'directory'}
        schedule_keys = {'include_schedule_onekey', 'schedule_authorizations'}
        if (not isinstance(supported, dict)
                or set(supported) not in (base_keys, base_keys | schedule_keys,
                    base_keys | schedule_keys | {'schedule_attest_current_plan'})
                or type(supported.get('schedule_attest_current_plan', False)) is not bool
                or any(v is not None for v in (slot, schedule_start, daily_config, daily_report,
                                               execution, rewards, event, house, onekey, settings_audit))):
            raise ValueError('Invalid or conflicting supported chain arguments')
        validated = validate(**{k: v for k, v in supported.items() if k != 'directory'})
        if validated['device'] != dict(input_display=input_display, screenshot_display=screenshot_display):
            raise ValueError('Conflicting supported connection arguments')
    if event is not None:
        from datetime import date
        if (set(event) != {'start', 'game_day', 'context', 'execute', 'preview'}
                or event['start'] not in ('home', 'event')
                or event['context'] not in (None, 'same-day-unreset', 'user-reported-unreset')
                or type(event['execute']) is not bool or type(event['preview']) is not bool
                or (event['execute'] and event['preview'])
                or any(v is not None for v in (slot, schedule_start, daily_config, execution, rewards))):
            raise ValueError('Invalid or conflicting event arguments')
        if date.fromisoformat(event['game_day']).isoformat() != event['game_day']:
            raise ValueError('Invalid game day')
    if rewards is not None:
        if (set(rewards) != {'execute', 'only'} or type(rewards['execute']) is not bool
                or rewards['only'] not in (None, 'missions', 'gifts')
                or any(v is not None for v in (slot, schedule_start, daily_config, execution))):
            raise ValueError('Invalid or conflicting rewards arguments')
    if slot is not None and (type(slot) is not int or not 1 <= slot <= 7):
        raise ValueError("slot must be 1..7")
    if schedule_start is not None and (schedule_start not in ('home', 'schedule') or slot is not None):
        raise ValueError("schedule_start must be home or schedule, without slot")
    if daily_config is not None:
        from .daily import validate_config
        daily_config = validate_config(daily_config)
        if slot is not None or schedule_start is not None or daily_config['device'] != {'input_display': input_display, 'screenshot_display': screenshot_display}:
            raise ValueError('Conflicting daily connection arguments')
    if execution is not None and (slot is not None or schedule_start is not None or daily_config is not None):
        raise ValueError('Conflicting execution arguments')
    if onekey is not None or isinstance(supported, dict) and supported.get('include_schedule_onekey'):
        from .onekey_history import preflight_history
        preflight_history((onekey or supported)['game_day'],
            dict(serial=serial, input_display=input_display, screenshot_display=str(screenshot_display)))
    config = fixed_config(input_display, screenshot_display, preset_list=slot is not None or schedule_start is not None or daily_config is not None or execution is not None or event is not None or supported is not None or onekey is not None or settings_audit is not None)
    resource = load_resource()
    from .native import RUNTIME
    if not Path(adb_path).is_file():
        raise RuntimeError("Fixed MuMu adb executable missing")
    controller = AdbController(adb_path, serial, MaaAdbScreencapMethodEnum.Encode,
                               MaaAdbInputMethodEnum.AdbShell, config=config)
    tasker = Tasker()
    try:
        if not controller.set_screenshot_use_raw_size(True):
            raise RuntimeError("Cannot request raw screenshot")
        if not wait_job(controller.post_connection()).succeeded:
            raise RuntimeError("Existing fixed connection unavailable")
        capture = wait_job(controller.post_screencap())
        raw = capture.get() if capture.succeeded else None
        if raw is None:
            raise RuntimeError("Fresh raw frame unavailable")
        require_frame_size(raw.shape[1], raw.shape[0])
        if not controller.set_screenshot_use_raw_size(False) or not controller.set_screenshot_target_short_side(720):
            raise RuntimeError("Cannot set 720 short side")
        if not tasker.bind(resource, controller) or not tasker.inited:
            raise RuntimeError("Native Tasker not initialized")
        if onekey is not None:
            from .schedule_onekey import schedule_onekey
            binding = dict(serial=serial, input_display=input_display, screenshot_display=str(screenshot_display))
            return schedule_onekey(tasker, **onekey, device=binding) | {'source': 'live standalone one-key'}
        if settings_audit is not None:
            from .schedule_onekey import schedule_settings_audit
            binding = dict(serial=serial, input_display=input_display, screenshot_display=str(screenshot_display))
            return schedule_settings_audit(tasker, **settings_audit, device=binding,
                                           source='live fixed-device Tasker')
        if house is not None:
            from .schedule_house import schedule_house
            binding = dict(serial=serial, input_display=input_display, screenshot_display=str(screenshot_display))
            result = schedule_house(tasker, **house, device=binding)
            result.update(source='live standalone house', device=binding)
            return result
        if supported is not None:
            from .supported_daily import execute_supported
            return execute_supported(tasker, **supported)
        if rewards is not None:
            from .rewards import claim_rewards
            return claim_rewards(tasker, **rewards)
        if event is not None:
            from .event import execute_event
            from .event_native import NativeEvent
            from .preset_execution import Journal
            from .native import ROOT
            return execute_event(NativeEvent(tasker, start=event['start']),
                                 Journal(ROOT / '.runtime/event-work/ledger'),
                                 **{k: v for k, v in event.items() if k != 'start'})
        if execution is not None:
            from .preset_execution import execute_one, Journal
            from .preset_execution_native import NativePreset
            from .native import ROOT
            return execute_one(NativePreset(tasker), execution['preset'], execution['context'],
                               Journal(ROOT / '.runtime/preset-execution-work/ledger'),
                               execute=execution['execute'], preview=execution.get('preview', False),
                               fit_remaining=execution.get('fit_remaining', False),
                               game_day=execution['game_day'])
        if daily_config is not None:
            from .daily import inspect_daily
            result = inspect_daily(tasker, daily_config, source='live daily inspection', report=daily_report)
            result['device'] = daily_config['device'] | {'serial': serial}
            return result
        if schedule_start is not None:
            from .schedule import inspect_schedule
            result = inspect_schedule(tasker, start=schedule_start, source='live schedule inspection')
            result['device'] = {'serial': serial, 'input_display': input_display,
                                'screenshot_display': str(screenshot_display)}
            return result
        if slot is None:
            result = run(tasker, "NavigatePresets")
        else:
            from .preset_scan import scan
            result = scan(tasker, slot, source="live preset inspection")
        result.update(source="live navigation" if slot is None else "live preset inspection", device={"serial": serial, "input_display": input_display,
                      "screenshot_display": str(screenshot_display)}, observation=None)
        if result["navigation_complete"]:
            capture = wait_job(controller.post_screencap())
            if not capture.succeeded:
                raise RuntimeError("Navigation ended but final screenshot failed")
            image = capture.get()
            path = RUNTIME / f"live-presets-{time.time_ns()}.png"
            if not write_png(path, image):
                raise RuntimeError("Cannot save final screenshot")
            observed = inspect(image, resource)
            result["ocr_receipt"] = observed
            result["observation"] = observed["observation"]
            result["screenshot"] = str(path)
            if not observed["native_ok"] or observed["observation"]["page"] != "presets":
                result["navigation_complete"] = False
            if slot is not None and (not result['navigation_complete']
                                     or observed['observation']['selected_slot'] != slot):
                result.update(list_complete=False, all_selected_exhausted=None,
                              incomplete_reason='final_page_or_slot_unverified', view='unknown')
        return result
    except KeyboardInterrupt:
        stop_tasker(tasker)
        raise
    finally:
        if tasker.running:
            stop_tasker(tasker)
