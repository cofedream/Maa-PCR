"""A local console front end for the existing supported chain and control CLI."""
import argparse
import re
from pathlib import Path

from .schedule_onekey import ITEMS
from .game_day import current_game_day


CONFIG_PATH = Path(__file__).resolve().parents[2] / '.runtime/daily-config.json'
TEMPLATE = 'docs/daily-config.template.json'
CONTEXT = 'user-reported-unreset'
AUTHORIZE = 'AUTHORIZE_SUPPORTED'
CONFIRM = 'EXECUTE_SUPPORTED_ONCE'
RUN_ID = re.compile(r'run-[0-9a-f]{32}\Z')


def _ask(prompt, label):
    try:
        return prompt(label).strip()
    except EOFError:
        return None


def _config(output, path=None):
    from .daily import load_config
    from .supported_daily import validate

    try:
        # Fixed schema date checks the existing supported config without implying
        # a current game day, connection, authorization, or execution.
        return validate(load_config(CONFIG_PATH if path is None else path), execute=True, game_day='2000-01-01',
                        context=CONTEXT)
    except (ValueError, OSError) as exc:
        output('配置缺失或未通过版本、固定显示字段、三业务槽位及目标检查。')
        output(f'请对照 {TEMPLATE} 手动填写 .runtime/daily-config.json；不会生成、覆盖或展示配置。')
        output('首次配置可选择 configure；' + str(exc))
        return None


def _configure(path, prompt, output):
    from .daily import KINDS, LABELS
    from .local_config import save_config
    if path.exists():
        output('配置已存在，未读取或覆盖；如需调整，请手动编辑并运行 check。')
        return 2
    output('仅保存本机连接和稳定目标；不连接设备，不保存游戏日、执行许可、用券许可或日程申报。')
    try:
        connection = dict(adb_path=_ask(prompt, '本机已有 adb.exe 绝对路径：'),
                          serial=_ask(prompt, '唯一设备 IPv4:端口（不会扫描）：'))
        from .device import validate_connection
        validate_connection(connection)
        device = dict(input_display=int(_ask(prompt, '已核实逻辑 display（0..255）：')),
                      screenshot_display=_ask(prompt, '已核实物理 display 数字 ID：'))
        presets = []
        for kind in KINDS:
            presets.append(dict(kind=kind, slot=int(_ask(prompt, LABELS[kind] + '槽位（1..7）：')),
                label=_ask(prompt, '展示名称：'), target_per_stage=int(_ask(prompt, '每关每日目标：'))))
        value = dict(version=2, start='home', device=device, connection=connection,
                     presets=presets, count_context=None)
        from .daily import validate_config
        validate_config(value)
        if _ask(prompt, '确认只保存稳定配置，请输入 SAVE_CONFIG：') != 'SAVE_CONFIG':
            output('未保存配置，零设备操作。')
            return 2
        save_config(path, value)
    except (ValueError, TypeError, OSError) as exc:
        output('配置未保存成功；零设备操作：' + str(exc))
        return 2
    output('配置已保存；后续 check 检查，start 逐次确认后启动。daily_complete=false。')
    return 0


def _delegate(argv, delegate):
    if delegate is None:
        from .cli import main as delegate
    result = delegate(argv)
    if type(result) is not int:
        raise TypeError('downstream_cli_returned_non_integer_exit')
    return result


def _start(prompt, output, delegate, *, config_path=None, schedule_attest_current_plan=False):
    config = _config(output) if config_path is None else _config(output, config_path)
    if config is None:
        return 2
    output('顺序：活动→深域→每日任务/礼物→高难→困难；日程一键仅逐项显式可选。')
    output('深渊尚未支持、未纳入已支持链，状态 unknown/unsupported；开放状态须当次核实；daily_complete=false。')
    output('本次可能消耗体力和本次扫荡券、领取奖励；不可购买、重置、改设置或自动重试。')
    output('另开本地命令窗口可用 supported-control.bat stop --run-id <本次打印的run-id> 请求步骤边界停止；在途步骤不会即时取消。')
    output('游戏日按北京时间05:00划分，00:00–04:59填前一日期；不证明服务端已刷新，也不增加许可。动作仍须新鲜页面、剩余次数、设置和预算，未结算意图仍阻断。')
    day = _ask(prompt, '本次目标游戏日 YYYY-MM-DD：')
    if not day:
        output('缺少当前游戏日；没有启动。')
        return 2
    if day != current_game_day():
        output('游戏日与北京时间05:00边界不符；没有启动，不会自动改参。')
        return 2
    context = _ask(prompt, f'若确认本次为同游戏日未重置语境，请逐字输入 {CONTEXT}：')
    if context != CONTEXT:
        output('缺少本次计数语境；没有启动。')
        return 2
    permission = _ask(prompt, f'若本次明确许可上述已支持链的资源操作，请逐字输入 {AUTHORIZE}：')
    if permission != AUTHORIZE:
        output('缺少本次执行许可；没有启动。')
        return 2
    output('日程本次申报 yes-attested：玩家已核实当前计划仅九项免费/现有次数与券，零宝石、无购买/付费扭蛋/重置；不保存为以后许可。')
    include = _ask(prompt, '本次包含日程？yes（兼容旧门禁）、yes-attested（含本次申报）、no（回车默认）：')
    if include == 'yes-attested':
        include = 'yes'
        schedule_attest_current_plan = True
    if include not in ('', 'no', 'yes'):
        output('日程选择无效；没有启动。')
        return 2
    if schedule_attest_current_plan and include != 'yes':
        output('本次日程申报须同时选择日程一键；没有启动。')
        return 2
    authorizations = []
    if include == 'yes':
        output('日程必须有当次逐项许可；可选项目：' + ', '.join(ITEMS))
        for _ in range(len(ITEMS) + 1):
            item = _ask(prompt, '授权 ITEM（每次一个；空行结束）：')
            if item is None:
                output('日程许可未收集完整；没有启动。')
                return 2
            if item == '':
                break
            if item not in ITEMS or item in authorizations:
                output('日程许可非法或重复；没有启动。')
                return 2
            authorizations.append(item)
        if not authorizations or len(authorizations) > len(ITEMS):
            output('缺少或超出日程逐项许可；没有启动。')
            return 2

    from .supported_daily import validate
    try:
        from .device import connection_options
        connection_options(config, include_schedule_onekey=include == 'yes')
        validate(config, execute=True, game_day=day, context=context,
                 include_schedule_onekey=include == 'yes',
                 schedule_authorizations=authorizations,
                 schedule_attest_current_plan=schedule_attest_current_plan)
    except ValueError as exc:
        output('当前游戏日、设备显示或逐项许可未通过原执行门禁；没有启动。')
        output(str(exc))
        return 2
    output(f'待启动游戏日：{day}；日程许可：{", ".join(authorizations) if authorizations else "无"}。')
    output('仅一次调用原 execute-supported CLI；原组件账本及未知不重发规则继续生效。')
    if _ask(prompt, f'最终确认请逐字输入 {CONFIRM}：') != CONFIRM:
        output('最终确认缺失；没有启动。')
        return 2
    argv = ['execute-supported', '--config', str(CONFIG_PATH if config_path is None else config_path), '--game-day', day,
            '--count-context', context, '--execute']
    if include == 'yes':
        argv.append('--include-schedule-onekey')
        for item in authorizations:
            argv.extend(['--schedule-authorize', item])
        if schedule_attest_current_plan:
            argv.append('--schedule-attest-current-plan')
    code = _delegate(argv, delegate)
    output(f'原执行入口退出码={code}；保留上方 run-id 和中文结果。daily_complete=false。')
    return code


def main(argv=None, *, prompt=None, output=None, delegate=None):
    parser = argparse.ArgumentParser(description='PCR 已支持日常本地操作菜单；完整日常未交付')
    parser.add_argument('action', nargs='?', choices=['configure', 'check', 'start', 'summary', 'stop'],
                        help='省略时展示菜单；启动须逐次输入许可与最终确认')
    parser.add_argument('--run-id', help='只供 summary/stop 使用：run- 后接32位小写十六进制')
    parser.add_argument('--config', type=Path, help='显式本地配置路径；默认 .runtime/daily-config.json')
    parser.add_argument('--schedule-attest-current-plan', action='store_true',
                        help='仅本次启动申报玩家已核实日程仅免费/现有次数与券，零宝石/购买/重置；不保存')
    args = parser.parse_args(argv)
    prompt = input if prompt is None else prompt
    output = print if output is None else output
    action = args.action
    if action is None:
        output('1 配置检查　2 启动已支持链　3 查询单次摘要　4 请求步骤边界停止　5 首次配置　0 退出')
        choice = _ask(prompt, '选择一次操作：')
        if choice in (None, '0'):
            return 0
        action = {'1': 'check', '2': 'start', '3': 'summary', '4': 'stop', '5': 'configure'}.get(choice)
        if action is None:
            output('未选择有效操作；没有启动。')
            return 2
    if args.run_id is not None and action not in ('summary', 'stop'):
        output('run-id 只用于摘要或停止请求；没有启动。')
        return 2
    if args.schedule_attest_current_plan and action != 'start':
        output('日程申报仅用于本次 start；没有启动。')
        return 2
    if action == 'configure':
        return _configure(CONFIG_PATH if args.config is None else args.config, prompt, output)
    if action == 'check':
        if _config(output, args.config) is None:
            return 2
        output('配置结构检查通过；保存的 display/game-day 不证明当前绑定、页面、设置、账本或本次许可。')
        output('深渊 unknown/unsupported；daily_complete=false。')
        return 0
    if action == 'start':
        return _start(prompt, output, delegate, config_path=args.config,
                      schedule_attest_current_plan=args.schedule_attest_current_plan)
    run_id = args.run_id or _ask(prompt, '输入本次 run-id：')
    if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
        output('run-id 格式无效；没有查询或发送停止请求。')
        return 2
    if action == 'stop':
        output('停止仅为请求；在途步骤可能继续运行，以原控制回执为准。')
    code = _delegate(['supported-control', action, '--run-id', run_id], delegate)
    output(f'本地控制入口退出码={code}；daily_complete=false。')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
