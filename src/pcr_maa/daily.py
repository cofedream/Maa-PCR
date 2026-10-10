"""One sequential read-only inspection; no scheduling or execution engine."""
import copy
import json
import time
from pathlib import Path

KINDS = ('deep', 'very_hard', 'hard')
LABELS = {'deep': '深域', 'very_hard': '高难', 'hard': '困难'}


def validate_mumu(value, *, bounded_index=True):
    """Stable configuration data only; never proves a live display/input target."""
    if (type(value) is not dict or set(value) !=
            {'path', 'lib', 'index', 'app_package', 'app_cloned_index'}):
        raise ValueError('配置错误：mumu 稳定身份字段缺失或含未知字段')
    if value['app_package'] != 'com.bilibili.priconne':
        raise ValueError('配置错误：MuMu package mismatch')
    for key in ('index', 'app_cloned_index'):
        if (type(value[key]) is not int or value[key] < 0
                or bounded_index and value[key] > 2147483647):
            raise ValueError('配置错误：MuMu instance/clone invalid')
    for key in ('path', 'lib'):
        text = value[key]
        if (type(text) is not str or not text or text.startswith(('\\\\', '//'))
                or any(c in text for c in '\r\n\x00') or not Path(text).is_absolute()):
            raise ValueError('配置错误：MuMu local absolute path required')
    if (not Path(value['path']).is_dir() or not Path(value['lib']).is_file()
            or Path(value['lib']).name != 'external_renderer_ipc.dll'):
        raise ValueError('配置错误：MuMu installation/SDK missing')
    return copy.deepcopy(value)


def validate_manager_identity(value):
    """Local single-instance continuity marker, not a clone/import identifier."""
    if (type(value) is not dict or set(value) !=
            {'source', 'installation', 'index', 'created_timestamp', 'app_package'}
            or value['source'] != 'mumu_manager_local_v1'
            or value['app_package'] != 'com.bilibili.priconne'
            or type(value['index']) is not int or not 0 <= value['index'] <= 2147483647
            or type(value['created_timestamp']) is not int
            or not 0 < value['created_timestamp'] <= 9223372036854775807):
        raise ValueError('schedule_manager_identity_invalid_or_unsupported')
    path = value['installation']
    if (type(path) is not str or not path or path.startswith(('\\\\', '//'))
            or any(c in path for c in '\r\n\x00') or not Path(path).is_absolute()):
        raise ValueError('schedule_manager_installation_invalid')
    return copy.deepcopy(value)


def validate_controller(value):
    """Validate a v4 static controller identity, not a discovered live target."""
    if type(value) is not dict or type(value.get('kind')) is not str:
        raise ValueError('配置错误：v4 controller 类型无效')
    if value['kind'] == 'adb':
        if (not {'kind', 'app_package'} <= set(value) <= {'kind', 'app_package', 'ledger_identity', 'manager_identity'}
                or value['app_package'] != 'com.bilibili.priconne'):
            raise ValueError('配置错误：v4 adb controller 字段或 package 无效')
        if 'ledger_identity' in value:
            validate_mumu(value['ledger_identity'], bounded_index=False)
        if 'manager_identity' in value:
            validate_manager_identity(value['manager_identity'])
    elif value['kind'] == 'mumu_extras':
        validate_mumu({key: item for key, item in value.items() if key != 'kind'},
                      bounded_index=False)
    else:
        raise ValueError('配置错误：不支持的 v4 controller kind')
    return copy.deepcopy(value)


def validate_config(value):
    def require(ok, message):
        if not ok:
            raise ValueError('配置错误：' + message)
    require(isinstance(value, dict), '根节点必须为对象')
    require(type(value.get('version')) is int and value['version'] in (1, 2, 3, 4), 'version 必须为 1、2、3 或 4')
    stable = value['version'] == 3
    controller_v4 = value['version'] == 4
    portable = value['version'] in (2, 3, 4)
    if controller_v4:
        require(set(value) - {'collect_gift_stamina'} == {'version', 'start', 'presets', 'count_context', 'connection', 'controller'},
                'v4 顶层字段无效；仅可额外设置 collect_gift_stamina')
        require(type(value.get('collect_gift_stamina', False)) is bool,
                'collect_gift_stamina 必须为布尔值')
    else:
        require(set(value) <= {'version', 'start', 'presets', 'count_context'} |
                ({'mumu'} if stable else {'device'}) | ({'connection'} if portable else set()), '存在未知字段；不得保存本次许可或游戏日')
    if portable:
        from .device import validate_connection
        validate_connection(value.get('connection'))
        require(value.get('count_context') is None, '新版配置不保存本次计数语境，请启动时申报')
    require(value.get('start') in ('home', 'schedule'), 'start 必须为 home 或 schedule')
    if controller_v4:
        validate_controller(value['controller'])
        require(value['start'] == 'home', 'v4 仅支持 Alpha 主页非日程选集')
    elif stable:
        validate_mumu(value.get('mumu'))
        require(value['connection']['serial'].startswith('127.0.0.1:'), 'v3 仅接受本机 MuMu serial')
        require(value['start'] == 'home', 'v3 仅支持 Alpha 主页非日程选集')
    else:
        device = value.get('device')
        require(isinstance(device, dict) and set(device) == {'input_display', 'screenshot_display'}, 'device 仅接受固定显示绑定字段')
        require(type(device['input_display']) is int and (portable or device['input_display'] == 2), '旧版 input_display 必须为 2')
        require(isinstance(device['screenshot_display'], str), 'screenshot_display 必须为数字字符串')
        from .device import fixed_config
        try:
            fixed_config(**device)
        except ValueError as exc:
            raise ValueError('配置错误：须填写本次核实的物理 display ID；存储值不是永久绑定证明') from exc
    require(value.get('count_context') in (None, 'same-day-unreset', 'user-reported-unreset'), 'count_context 只能为 null、same-day-unreset 或 user-reported-unreset')
    presets = value.get('presets')
    require(isinstance(presets, list) and len(presets) in (3, 4), 'presets 必须配置 deep、very_hard、hard，可选 normal')
    for item in presets:
        require(isinstance(item, dict) and {'kind', 'slot', 'label', 'target_per_stage'} <= set(item)
                and set(item) <= {'kind', 'slot', 'label', 'target_per_stage', 'max_stamina'}, '预设字段须为 kind/slot/label/target_per_stage，可选 max_stamina')
        if 'max_stamina' in item:
            require(type(item['max_stamina']) is int and 0 <= item['max_stamina'] <= 999999, 'max_stamina 必须为 0..999999 整数')
        require(isinstance(item['kind'], str) and item['kind'] in (*KINDS, 'normal'), '未知业务类型')
        require((item['slot'] is None and (stable or controller_v4 or item['kind'] == 'normal') and item['target_per_stage'] == 0)
                or type(item['slot']) is int and 1 <= item['slot'] <= 7,
                'slot 必须为 1..7 整数；v3/v4 的 0 次项可留空')
        require(isinstance(item['label'], str) and len(item['label']) <= 100, 'label 必须为最多 100 字符的展示名称')
        require(type(item['target_per_stage']) is int and 0 <= item['target_per_stage'] <= 999, '每关每日目标必须为 0..999 整数')
    require({p['kind'] for p in presets} in (set(KINDS), {*KINDS, 'normal'})
            and len({p['kind'] for p in presets}) == len(presets), '业务类型缺失或重复')
    slots = [p['slot'] for p in presets if p['slot'] is not None]
    require(len(set(slots)) == len(slots), '同一槽位不能重复映射业务')
    result = copy.deepcopy(value)
    result['count_context'] = value.get('count_context')
    result['presets'].sort(key=lambda p: (*KINDS, 'normal').index(p['kind']))
    return result


def load_config(path):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('重复 JSON 字段：' + key)
            obj[key] = value
        return obj
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique)
    except (OSError, ValueError) as exc:
        raise ValueError(f'配置无法读取：{path}；请按 docs/INSPECT_DAILY.md 将模板手动复制到 .runtime/daily-config.json 并填写，不会自动生成或覆盖。原因：{exc}') from exc
    return validate_config(data)


def new_report(config, source):
    return dict(mode='inspect-daily', scope='只检查，不执行日常', source=source,
                inspection_complete=False, native_ok=False, daily_complete=False,
                schedule_executed=False, business_goal_met=None, outcome='not_run',
                count_context=config['count_context'],
                count_context_source=('user-reported' if config['count_context'] == 'user-reported-unreset' else 'caller_assertion') if config['count_context'] else 'unconfirmed',
                binding_note='配置仅为本次连接参数；不能独立证明物理显示绑定仍有效',
                not_implemented=['活动', '深渊', '扫荡', '领奖', '结算'],
                steps=[dict(name=n, status='not_run', **({'config': copy.deepcopy(next(p for p in config['presets'] if p['kind'] == n))} if n in KINDS else {}))
                       for n in ('schedule', 'close_schedule', 'navigate_presets', *KINDS, 'return_home')])


def inspect_daily(tasker, config, *, source='offline replay', report=None):
    from . import native
    from .schedule import inspect_schedule
    from .preset_scan import scan
    from .daily_goal import evaluate_daily_goal
    config = validate_config(config)
    report = report if report is not None else new_report(config, source)
    report['source'] = source
    started = time.monotonic()
    deadline = min(started + 600, native.RUN_DEADLINE.get())
    token = native.RUN_DEADLINE.set(deadline)
    step = None
    try:
        for step in report['steps']:
            if time.monotonic() >= deadline:
                raise TimeoutError('总检查超过 600 秒')
            step['status'] = 'running'
            name = step['name']
            if name == 'schedule':
                result = inspect_schedule(tasker, start=config['start'], source=source)
                step['result'] = result
                ok = result['native_ok'] and result['inspection_complete']
            elif name in ('close_schedule', 'navigate_presets', 'return_home'):
                result = native.run(tasker, {'close_schedule': 'CloseSchedule', 'navigate_presets': 'NavigatePresets', 'return_home': 'ReturnHome'}[name])
                step['result'] = result
                ok = result['native_ok'] and (any(n['name'] == 'ObserveHome' and n['completed'] for n in result['nodes'])
                                              if name in ('close_schedule', 'return_home') else result['navigation_complete'])
            else:
                preset = next(p for p in config['presets'] if p['kind'] == name)
                step['config'] = preset
                if preset['slot'] is None:
                    step['result'] = {'daily_goal': {'preset_goal_met': None}}
                    step['status'] = 'skipped_disabled'
                    continue
                result = scan(tasker, preset['slot'], source=source)
                step['result'] = result
                ok = result['native_ok'] and result['list_complete']
                if ok:
                    observed = native.observe_tasker(tasker)
                    result['ocr_receipt'] = observed
                    result['observation'] = observed['observation']
                    ok = (observed['native_ok'] and observed['observation']['page'] == 'presets'
                          and observed['observation']['selected_slot'] == preset['slot'])
                    if not ok:
                        step['error'] = '最终页面或槽位未验证'
                result['daily_goal'] = evaluate_daily_goal(result, preset['target_per_stage'], count_context=config['count_context'])
            step['status'] = 'ok' if ok else 'failed'
            if not ok:
                step.setdefault('error', result.get('incomplete_reason') or '原生执行、页面或结果未验证')
                report['outcome'] = 'unknown_or_failed'
                break
        else:
            report.update(inspection_complete=True, native_ok=True, outcome='ok')
            goals = [s['result']['daily_goal']['preset_goal_met'] for s in report['steps'][3:6]]
            report['business_goal_met'] = None if any(g is None for g in goals) else all(goals)
    except (Exception, KeyboardInterrupt) as exc:
        if step is not None:
            step.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', error=str(exc) or '用户中断')
        report['outcome'] = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'
        report['error'] = str(exc) or '用户中断'
        report['native_stop'] = native.stop_tasker(tasker)
    finally:
        native.RUN_DEADLINE.reset(token)
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return report


def summary(report):
    lines = ['【一键日常检查：只检查，不执行日常】',
             '读取流程：' + ('完成' if report['inspection_complete'] else '未完成（后续步骤不再输入）')]
    if any(s['name'] == 'return_home' and s['status'] in ('failed', 'interrupted') for s in report['steps']):
        lines.append('检查未完整收尾：返回主页未验证；三组已读取结果保留。')
    for step in report['steps']:
        result = step.get('result', {})
        if step.get('error'):
            lines.append(f"{step['name']}：{step['status']}，{step['error']}")
        if step['name'] == 'schedule' and result:
            for key, title in [('notifications', '来自可可萝的通知'), ('delegated', '交给可可萝')]:
                section = result.get(key, {})
                lines.append(f"{title}：{section.get('completed')}/{section.get('total')}（{section.get('status')}）")
        elif step['name'] in KINDS:
            goal = result.get('daily_goal', {}).get('preset_goal_met')
            state = '未知' if goal is None else ('达成' if goal else '未达成')
            preset = step.get('config', {})
            lines.append(f"{LABELS[step['name']]}（槽{preset.get('slot', '?')}，{preset.get('label', '')}）：每日目标：每关{preset.get('target_per_stage', '?')}次；读取结果：{step['status']}，{len(result.get('rows', []))}行；目标达成：{state}")
            reasons = result.get('daily_goal', {}).get('unknown_reasons', [])
            if goal is None:
                reason = '计数语境未确认' if 'count_context_unconfirmed' in reasons else ''
                details = '、'.join(reasons) or step.get('error') or ('尚未读取' if step['status'] == 'not_run' else '读取或目标评估未完成')
                lines.append('  未知原因：' + (reason + '；' if reason else '') + details)
            for row in result.get('rows', []):
                lines.append(f"  {row.get('title', '?')}：剩余{row.get('remaining', '?')}/{row.get('limit', '?')}")
        elif step['name'] == 'return_home':
            lines.append('返回主页：' + ('已核实 ObserveHome' if step['status'] == 'ok' else step['status']))
    lines.append('计数语境：' + ('用户申报通常不重置（user-reported），程序未独立验证'
                              if report['count_context'] == 'user-reported-unreset' else
                              '调用者假设同游戏日未重置，程序未独立验证' if report['count_context'] else '未确认，目标状态 unknown'))
    if report.get('error'):
        lines.append('错误：' + report['error'])
    lines.append('活动/深渊、扫荡、领奖、结算：not_implemented；完整日常未完成。')
    return '\n'.join(lines)
