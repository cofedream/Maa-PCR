import argparse
import json
import sys
from .game_day import current_game_day


def main(argv=None):
    parser = argparse.ArgumentParser(description="PCR 国服：查看扫荡预设（完整日常未交付）")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="离线真实 Maa 库/资源/Tasker 检查")
    onekey_parser = sub.add_parser('schedule-onekey', help='独立日程一键候选；默认保存帧只读预检')
    onekey_parser.add_argument('--image', help='本地保存帧；零连接、零输入，不证明当前现场')
    onekey_parser.add_argument('--game-day', help='北京时间05:00划分的游戏日 YYYY-MM-DD；不证明服务端刷新')
    onekey_parser.add_argument('--execute', action='store_true', help='本次显式授权执行；需要设备显示绑定及逐项 --authorize')
    onekey_parser.add_argument('--attest-current-plan', action='store_true',
        help='仅本次申报玩家自管当前设置：一键仅九项免费/现有次数与券，零宝石、无购买/付费扭蛋/重置；不是设置审计')
    onekey_parser.add_argument('--input-display', type=int, choices=[2])
    onekey_parser.add_argument('--screenshot-display')
    from .schedule_onekey import ITEMS
    onekey_parser.add_argument('--authorize', action='append', choices=ITEMS, default=[],
                               help='本次逐项许可；mana_skip/exp_skip 各限已见的2次2券报价')
    audit_parser = sub.add_parser('schedule-settings-audit', help='独立同会话只读设置验收；不会触发一键')
    audit_parser.add_argument('--audit', action='store_true', help='显式许可打开日程和设置并只读巡检')
    audit_parser.add_argument('--start', choices=['home', 'schedule'], default='home',
                              help='明确起点；默认从主页打开日程，schedule 仅从已核实日程页继续')
    audit_parser.add_argument('--game-day', required=True, help='北京时间05:00划分的游戏日 YYYY-MM-DD；不证明服务端刷新')
    audit_parser.add_argument('--input-display', required=True, type=int, choices=[2])
    audit_parser.add_argument('--screenshot-display', required=True)
    house_parser = sub.add_parser('schedule-house', help='独立公会之家免费领取；默认只读当前完整可见行，不滚动')
    house_parser.add_argument('--image', help='保存帧离线预览，不能配合 --execute 或设备参数')
    house_parser.add_argument('--input-display', type=int, choices=[2])
    house_parser.add_argument('--screenshot-display')
    house_parser.add_argument('--game-day', help='执行必填：父端核实的本次游戏日 YYYY-MM-DD')
    house_parser.add_argument('--execute', action='store_true')
    supported_parser = sub.add_parser('execute-supported', help='固定串联；日程仅显式逐项可选，深渊未支持，非完整日常')
    supported_parser.add_argument('--config', required=True)
    supported_parser.add_argument('--game-day', required=True, help='北京时间05:00划分；00:00–04:59填前一日期，非执行许可')
    supported_parser.add_argument('--count-context', required=True, choices=['user-reported-unreset'])
    supported_parser.add_argument('--execute', action='store_true')
    supported_parser.add_argument('--include-schedule-onekey', action='store_true',
                                  help='显式将独立日程一键放在原五步之前；仍须当次逐项授权')
    supported_parser.add_argument('--schedule-authorize', action='append', choices=ITEMS, default=[],
                                  metavar='ITEM', help='重复指定本次日程项目许可；不继承设置审计或配置授权')
    supported_parser.add_argument('--schedule-attest-current-plan', action='store_true',
                                  help='仅本次申报玩家已核实当前计划；须同时 --execute 和 --include-schedule-onekey')
    control_parser = sub.add_parser('supported-control', help='本地单次运行摘要或协作式停止请求；零设备操作')
    control_parser.add_argument('action', choices=['summary', 'stop'])
    control_parser.add_argument('--run-id', required=True, help='run- 后接32位小写十六进制ID；不接受路径')
    event_parser = sub.add_parser('event-hard', help='当前活动困难已勾选关卡各每日3次；默认观察，完整日常未交付')
    event_parser.add_argument('--start', required=True, choices=['home', 'event'])
    event_parser.add_argument('--input-display', required=True, type=int, choices=[2])
    event_parser.add_argument('--screenshot-display', required=True)
    event_parser.add_argument('--game-day', required=True, help='北京时间05:00划分；00:00–04:59填前一日期，非执行许可')
    event_parser.add_argument('--count-context', choices=['same-day-unreset', 'user-reported-unreset'])
    event_mode = event_parser.add_mutually_exclusive_group()
    event_mode.add_argument('--preview-confirm', action='store_true')
    event_mode.add_argument('--execute', action='store_true')
    rewards_parser = sub.add_parser('claim-rewards', help='从主页检查每日任务及礼物；仅 --execute 领取一次')
    rewards_parser.add_argument('--input-display', required=True, type=int, choices=[2])
    rewards_parser.add_argument('--screenshot-display', required=True)
    rewards_parser.add_argument('--execute', action='store_true')
    rewards_parser.add_argument('--only', choices=['missions', 'gifts'])
    daily_parser = sub.add_parser('inspect-daily', help='一键日常检查：只检查，不执行日常；按本地配置顺序读取')
    daily_parser.add_argument('--config', required=True, help='本地 JSON 配置路径；不会自动生成或覆盖')
    daily_parser.add_argument('--observe', action='store_true',
                              help='新版配置显式只读设备检查（导航有输入、不消费）；默认仅本地校验，零设备操作')
    execute_parser = sub.add_parser('execute-preset', help='单预设新鲜读取和报价；仅 --execute 允许提交，完整日常未实现')
    execute_parser.add_argument('--config', required=True)
    execute_parser.add_argument('--kind', required=True, choices=['deep', 'very_hard', 'hard'])
    execute_parser.add_argument('--game-day', required=True, help='北京时间05:00划分的游戏日 YYYY-MM-DD；跨刷新边界须重新观察')
    execute_parser.add_argument('--count-context', choices=['same-day-unreset', 'user-reported-unreset'],
                                help='只覆盖本次调用，不写个人配置')
    execute_parser.add_argument('--fit-remaining', action='store_true', help='显式按全组相同剩余目标临时调整本次次数并恢复；需配合预览或执行')
    execution_mode = execute_parser.add_mutually_exclusive_group()
    execution_mode.add_argument('--execute', action='store_true', help='允许单预设资源提交；未知结算不可重发')
    execution_mode.add_argument('--preview-confirm', action='store_true', help='只打开完整确认报价，取消并返回主页，绝不挑战')
    image_parser = sub.add_parser("inspect-image", help="保存图真实 Maa OCR，不连接设备")
    image_parser.add_argument("image")
    navigate_parser = sub.add_parser("navigate-presets", help="显式连接固定设备，仅导航到关卡一览即停")
    navigate_parser.add_argument("--input-display", required=True, type=int, choices=[2])
    navigate_parser.add_argument("--screenshot-display", required=True, help="本次已核实的物理 display ID，重启后须重新核对")
    scan_parser = sub.add_parser("inspect-preset", help="按槽位读取已勾选关卡；仅菜单与有限列表滚动，不消费")
    scan_parser.add_argument("--slot", required=True, type=int, choices=range(1, 8))
    scan_parser.add_argument("--input-display", required=True, type=int, choices=[2])
    scan_parser.add_argument("--screenshot-display", required=True)
    scan_parser.add_argument("--daily-target", type=int, help="显式指定本预设每个已勾选关卡的每日目标；省略为未知")
    scan_parser.add_argument("--count-context", choices=["same-day-unreset", "user-reported-unreset"],
                             help="调用者确认本次计数属于同日且未重置；省略则已用/待完成未知")
    schedule_parser = sub.add_parser("inspect-schedule", help="只读日程表两个分区计数；有限滚动，不启动或领取")
    schedule_parser.add_argument("--start", required=True, choices=['home', 'schedule'],
                                 help="明确起点：主页或已打开的日程表（顶/中/底）")
    schedule_parser.add_argument("--input-display", required=True, type=int, choices=[2])
    schedule_parser.add_argument("--screenshot-display", required=True)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    if args.command == 'supported-control':
        from .supported_daily import CONTROL_ERRORS, inspect_run, request_stop
        try:
            result = (inspect_run if args.action == 'summary' else request_stop)(args.run_id)
        except (ValueError, OSError) as exc:
            reason = str(exc) if str(exc) in CONTROL_ERRORS else 'control_unavailable'
            print(json.dumps(dict(outcome='control_unavailable', reason=reason, daily_complete=False,
                process_state='unknown', message='运行ID、报告或控制文件不可用；未确认停止。'), ensure_ascii=False))
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'execute-supported':
        from .supported_daily import NOTICE, SCHEDULE_NOTICE
        print(SCHEDULE_NOTICE if args.include_schedule_onekey else NOTICE, flush=True)
        if not args.execute:
            print('安全拒绝：必须显式 --execute；只读请使用 inspect-daily。零设备操作。')
            return 2
    from .native import load_resource, inspect, read_image, bind, run, save_receipt
    try:
        if args.command == 'schedule-settings-audit':
            from uuid import uuid4
            from . import native
            from .schedule_onekey import check_day
            from .device import fixed_config, navigate
            if not args.audit:
                print(json.dumps(dict(outcome='not_authorized', reason='explicit_audit_required',
                                      daily_complete=False, sent=[]), ensure_ascii=False))
                return 2
            check_day(args.game_day)
            fixed_config(args.input_display, args.screenshot_display)
            native.RUNTIME = (native.ROOT / '.runtime/schedule-onekey-work/settings-audit-live'
                              / ('cli-' + uuid4().hex))
            native.setup()
            settings_request = dict(audit=True, game_day=args.game_day)
            if args.start == 'schedule':
                settings_request['start'] = 'schedule'
            result = navigate(args.input_display, args.screenshot_display,
                settings_audit=settings_request)
            receipt = save_receipt(result, 'schedule-settings-audit')
            print(json.dumps(result | {'receipt': str(receipt)}, ensure_ascii=False, indent=2))
            return 0 if result['outcome'] == 'audited' and result['home_verified'] else 2
        elif args.command == 'schedule-onekey':
            from uuid import uuid4
            from . import native
            from .schedule_onekey import schedule_onekey, validate_execution
            from .device import SERIAL, navigate
            if args.attest_current_plan and (not args.execute or args.image):
                raise ValueError('--attest-current-plan requires --execute without --image')
            native.RUNTIME = native.ROOT / '.runtime/schedule-onekey-work' / ('cli-' + uuid4().hex)
            native.setup()
            device = (dict(serial=SERIAL, input_display=args.input_display, screenshot_display=args.screenshot_display)
                      if args.input_display is not None or args.screenshot_display is not None else None)
            options = dict(execute=args.execute, game_day=args.game_day,
                           authorizations=args.authorize, device=device)
            if args.execute:
                if args.image:
                    raise ValueError('--image is read-only; execution requires fresh current pages')
                validate_execution(args.game_day, device, args.authorize)
                result = navigate(args.input_display, args.screenshot_display,
                    onekey=dict(execute=True, game_day=args.game_day, authorizations=args.authorize,
                                attest_current_plan=args.attest_current_plan))
            elif device is not None or not args.image:
                result = schedule_onekey(**options)
            else:
                from .replay import SavedImages
                controller = SavedImages([[read_image(args.image)]])
                result = schedule_onekey(bind(load_resource(), controller), **options)
                result.update(source='offline saved frame; not current device evidence',
                              clicks=controller.clicks, swipes=controller.swipes)
            receipt = save_receipt(result, 'schedule-onekey')
            print(json.dumps(result | {'receipt': str(receipt)}, ensure_ascii=False, indent=2))
            return 0 if result['outcome'] in ('executed', 'already_complete', 'observed') else 2
        elif args.command == 'execute-supported':
            from uuid import uuid4
            from . import native
            from .daily import load_config
            from .device import navigate
            from .supported_daily import validate, summary
            if args.game_day != current_game_day():
                raise ValueError('current_game_day_required')
            config = validate(load_config(args.config), execute=args.execute,
                              game_day=args.game_day, context=args.count_context,
                              include_schedule_onekey=args.include_schedule_onekey,
                              schedule_authorizations=args.schedule_authorize,
                              schedule_attest_current_plan=args.schedule_attest_current_plan)
            from .device import connection_options
            transport = connection_options(config, include_schedule_onekey=args.include_schedule_onekey)
            directory = native.ROOT / '.runtime/supported-daily-work' / ('run-' + uuid4().hex)
            print('run-id=' + directory.name, flush=True)
            native.RUNTIME = directory
            native.setup()
            try:
                supported = dict(config=config, execute=True, game_day=args.game_day,
                                 context=args.count_context, directory=directory)
                if args.include_schedule_onekey:
                    supported.update(include_schedule_onekey=True,
                                     schedule_authorizations=args.schedule_authorize,
                                     schedule_attest_current_plan=args.schedule_attest_current_plan)
                result = navigate(**config['device'], **transport, supported=supported)
            except (Exception, KeyboardInterrupt) as exc:
                result = dict(outcome='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                    error=str(exc) or '用户中断', daily_complete=False, supported_chain_complete=False, steps=[])
            receipt = save_receipt(result, 'execute-supported')
            print(summary(result))
            print('结构化报告：' + str(receipt))
            return 130 if result['outcome'] == 'interrupted' else (0 if result['supported_chain_complete'] else 2)
        elif args.command == 'schedule-house':
            from datetime import date
            from uuid import uuid4
            from . import native
            from .schedule_house import schedule_house
            if args.image and (args.execute or args.input_display is not None or args.screenshot_display is not None):
                raise ValueError('--image is offline preview only; no execute/device arguments')
            if args.execute and not args.game_day:
                raise ValueError('--execute requires an explicit --game-day')
            if args.game_day and date.fromisoformat(args.game_day).isoformat() != args.game_day:
                raise ValueError('game-day must be YYYY-MM-DD')
            if not args.image:
                from .device import fixed_config
                fixed_config(args.input_display, args.screenshot_display)
            native.RUNTIME = native.ROOT / '.runtime/schedule-house-work' / ('cli-' + uuid4().hex)
            native.setup()
            if args.image:
                from .replay import SavedImages
                controller = SavedImages([[read_image(args.image)]])
                result = schedule_house(bind(load_resource(), controller), game_day=args.game_day)
                result.update(source='offline saved frame', clicks=controller.clicks, swipes=controller.swipes)
            else:
                from .device import navigate
                result = navigate(args.input_display, args.screenshot_display,
                                  house=dict(execute=args.execute, game_day=args.game_day))
            receipt = save_receipt(result, 'schedule-house')
            print(json.dumps(result | {'receipt': str(receipt)}, ensure_ascii=False, indent=2))
            return 0 if result['outcome'] in ('ready_not_executed', 'already_claimed_not_executed',
                                              'already_recorded_not_executed', 'claimed') else 2
        elif args.command == 'event-hard':
            from datetime import date
            from uuid import uuid4
            import time
            from . import native
            from .device import navigate
            if date.fromisoformat(args.game_day).isoformat() != args.game_day:
                raise ValueError('game-day must be YYYY-MM-DD')
            if args.game_day != current_game_day():
                raise ValueError('current_game_day_required')
            native.RUNTIME = native.ROOT / '.runtime/event-work' / ('cli-' + uuid4().hex)
            native.setup()
            token = native.RUN_DEADLINE.set(time.monotonic() + 300)
            try:
                result = navigate(args.input_display, args.screenshot_display, event=dict(
                    start=args.start, game_day=args.game_day, context=args.count_context,
                    execute=args.execute, preview=args.preview_confirm))
            finally:
                native.RUN_DEADLINE.reset(token)
            receipt = save_receipt(result, 'event-hard')
            print(json.dumps(result | {'receipt': str(receipt)}, ensure_ascii=False, indent=2))
            ok = result['outcome'] == 'ready_not_executed' or (result['outcome'] in (
                'preview_complete', 'executed', 'skipped_goal_met', 'skipped_insufficient_stamina',
                'skipped_insufficient_tickets') and result.get('home_verified') is True)
            return 0 if ok else 2
        elif args.command == 'claim-rewards':
            from .device import navigate
            from . import native
            from uuid import uuid4
            import time
            native.RUNTIME = native.ROOT / '.runtime/rewards-work' / ('cli-' + uuid4().hex)
            native.setup()
            token = native.RUN_DEADLINE.set(time.monotonic() + 180)
            try:
                result = navigate(args.input_display, args.screenshot_display,
                                  rewards=dict(execute=args.execute, only=args.only))
            finally:
                native.RUN_DEADLINE.reset(token)
            receipt = save_receipt(result, 'claim-rewards')
            print(json.dumps(result | {'receipt': str(receipt)}, ensure_ascii=False, indent=2))
            return 0 if result['outcome'] == 'complete' and result['home_verified'] else 2
        elif args.command == 'execute-preset':
            from datetime import date
            from .daily import load_config
            from .device import navigate
            from . import native
            from uuid import uuid4
            if args.fit_remaining and not (args.execute or args.preview_confirm):
                raise ValueError('--fit-remaining requires --preview-confirm or --execute')
            config = load_config(args.config)
            from .device import connection_options
            transport = connection_options(config)
            if config['version'] == 2 and not (args.execute or args.preview_confirm):
                print('安全拒绝：新版单预设须显式 --preview-confirm 或 --execute；零设备操作。')
                return 2
            if date.fromisoformat(args.game_day).isoformat() != args.game_day:
                raise ValueError('game-day must be YYYY-MM-DD')
            if args.game_day != current_game_day():
                raise ValueError('current_game_day_required')
            native.RUNTIME = native.ROOT / '.runtime/preset-count-fit-work' / ('cli-' + uuid4().hex)
            native.setup()
            import time
            token = native.RUN_DEADLINE.set(time.monotonic() + 600)
            try:
                result = navigate(**config['device'], **transport, execution=dict(
                    preset=next(p for p in config['presets'] if p['kind'] == args.kind),
                    context=args.count_context or config['count_context'],
                    execute=args.execute, preview=args.preview_confirm, fit_remaining=args.fit_remaining, game_day=args.game_day))
            finally:
                native.RUN_DEADLINE.reset(token)
            receipt = save_receipt(result, 'execute-preset')
            print(json.dumps(result | {'receipt': str(receipt)}, ensure_ascii=False, indent=2))
            ok = result['outcome'] == 'ready_not_executed' or (
                result['outcome'] in ('preview_complete', 'executed', 'skipped_goal_met',
                                      'skipped_insufficient_stamina', 'skipped_insufficient_tickets')
                and result.get('home_verified') is True)
            return 0 if ok else 2
        elif args.command == 'inspect-daily':
            from .daily import load_config, new_report, summary
            from .device import navigate
            from .native import setup, RUN_DEADLINE
            import time
            config = load_config(args.config)  # Validate everything before any connection.
            from .device import connection_options
            transport = connection_options(config)
            if config['version'] == 2 and not args.observe:
                print('配置结构检查通过；默认零设备操作。只读现场检查须显式 --observe；daily_complete=false。')
                return 0
            setup()
            result = new_report(config, 'live daily inspection')
            deadline_token = RUN_DEADLINE.set(time.monotonic() + 600)
            try:
                result = navigate(**config['device'], **transport, daily_config=config, daily_report=result)
            except (Exception, KeyboardInterrupt) as exc:
                result.update(outcome='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                              error=str(exc) or '用户中断')
            finally:
                RUN_DEADLINE.reset(deadline_token)
            receipt = save_receipt(result, 'inspect-daily')
            print(summary(result))
            print('结构化报告：' + str(receipt))
            return 130 if result['outcome'] == 'interrupted' else (0 if result['inspection_complete'] else 2)
        elif args.command == "doctor":
            import numpy as np
            from maa.library import Library
            from .replay import SavedImages
            resource = load_resource()
            controller = SavedImages([[np.zeros((720, 1280, 3), dtype=np.uint8)]])
            result = run(bind(resource, controller), "Doctor")
            result.update(source="offline replay", maa_version=Library.version(), observation=None)
            # Load the actual OCR model as well as the JSON bundle.
            from .native import ROOT
            probe = inspect(read_image(ROOT / "tests/fixtures/05-sweep-presets.png"), resource)
            result["ocr_probe"] = probe
            ok = result["native_ok"] and probe["native_ok"] and probe["observation"]["page"] == "presets"
        elif args.command == "inspect-image":
            result = inspect(read_image(args.image))
            result["input_image"] = str(args.image)
            ok = result["native_ok"] and result["observation"]["page"] != "unknown"
        elif args.command == 'inspect-schedule':
            from .device import navigate
            result = navigate(args.input_display, args.screenshot_display, schedule_start=args.start)
            ok = result['inspection_complete']
        else:
            from .device import navigate
            result = navigate(args.input_display, args.screenshot_display,
                              slot=args.slot if args.command == "inspect-preset" else None)
            ok = result["list_complete"] if args.command == "inspect-preset" else result["navigation_complete"]
        result = dict(result)
        if args.command == "inspect-preset":
            from .daily_goal import evaluate_daily_goal
            result["daily_goal"] = evaluate_daily_goal(result, args.daily_target, count_context=args.count_context)
        result["outcome"] = "ok" if ok else "unknown_or_failed"
        receipt = save_receipt(result, args.command)
        print(json.dumps({k: result.get(k) for k in ("native_ok", "source", "observation", "navigation_complete", "daily_complete", "outcome",
                                                   "selected_slot", "view", "rows", "list_complete", "incomplete_reason", "all_selected_exhausted")}
                         | ({"daily_goal": result["daily_goal"]} if "daily_goal" in result else {})
                         | ({k: result[k] for k in ('notifications', 'delegated', 'inspection_complete',
                                                    'schedule_executed', 'scope')} if args.command == 'inspect-schedule' else {})
                         | {"receipt": str(receipt)}, ensure_ascii=False, indent=2))
        return 0 if ok else 2
    except KeyboardInterrupt:
        print(json.dumps({"outcome": "interrupted", "native_ok": False, "navigation_complete": False, "daily_complete": False}))
        return 2 if args.command == 'claim-rewards' else 130
    except Exception as exc:
        if args.command == 'execute-supported':
            from .supported_daily import summary, NOTICE, SCHEDULE_NOTICE
            print(summary(dict(scope=SCHEDULE_NOTICE if args.include_schedule_onekey else NOTICE,
                               supported_chain_complete=False, steps=[])))
        print(json.dumps({"outcome": "failed", "error": str(exc), "native_ok": False,
                          "navigation_complete": False, "daily_complete": False}, ensure_ascii=False), file=sys.stderr)
        return 2 if args.command == 'claim-rewards' else 1


if __name__ == "__main__":
    raise SystemExit(main())
