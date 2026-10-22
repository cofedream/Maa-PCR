"""Fixed Alpha action. Authority comes only from a trusted, per-invocation owner.

Importing this module never starts work or selects AgentServer SDK mode. The
standalone launcher has no execution-context provider and therefore fails closed.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from maa.custom_action import CustomAction
from . import gui_contract, supported_daily
from .daily import load_config

ACTION = 'PCRAlphaExecuteSupported'
ENTRY = 'PCRAlphaEntry'
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / '.runtime/daily-config.json'
LOG_ROOT = ROOT / '.runtime/alpha-agent'


def parse_selection(raw, *, context=None):
    """Read fixed PI data nodes in this invocation; return only canonical data."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate_selection_field')
            result[key] = value
        return result

    def invalid(value):
        raise ValueError('non_finite_selection_value')

    if type(raw) is not str or len(raw) > 4096:
        raise ValueError('invalid_selection_json')
    value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)
    if type(value) is dict and 'selection_mode' in value:
        if (value.get('selection_mode') != 'pi_nodes_v1' or context is None
                or set(value) not in ({'selection_mode'}, {'selection_mode', 'selected_modules'})):
            raise ValueError('invalid_pi_selection_mode')
        # Fixed PI data slots, never a caller-supplied registry or node path.
        nodes = ('PCRAlphaSelectSchedule', 'PCRAlphaSelectEvent', 'PCRAlphaSelectAbyss', 'PCRAlphaSelectDeep',
                 'PCRAlphaSelectRewards', 'PCRAlphaSelectVeryHard', 'PCRAlphaSelectHard',
                 'PCRAlphaSelectNormal', 'PCRAlphaSelectGuideTrial', 'PCRAlphaSelectClanBattleSimulation', 'PCRAlphaSelectClanBattleDaily', 'PCRAlphaSelectRewardsFinal')
        flags = []
        for name in nodes:
            node = context.get_node_data(name)
            if name in ('PCRAlphaSelectAbyss', 'PCRAlphaSelectGuideTrial', 'PCRAlphaSelectClanBattleSimulation', 'PCRAlphaSelectClanBattleDaily') and node is None:
                flags.append(False)  # Older bundles do not contain the new opt-in slot.
                continue
            if (type(node) is not dict or node.get('enabled') is not False
                    or node.get('action') != {'type': 'DoNothing', 'param': {}}
                    or node.get('next') != [] or node.get('on_error') != []):
                raise ValueError('invalid_pi_selection_node')
            data = node.get('attach')
            if (type(data) is not dict or set(data) != {'selected'}
                    or type(data['selected']) is not bool):
                raise ValueError('invalid_pi_selection_data')
            flags.append(data['selected'])
        selected = [name for name, enabled in zip(supported_daily.MODULE_ORDER, flags[:-1]) if enabled]
        if 'selected_modules' in value:
            if type(value['selected_modules']) is not list:
                raise ValueError('invalid_pi_selection_order')
            ordered = gui_contract.make_combination(value['selected_modules'], rewards_final=flags[-1])
            if set(ordered['selected_modules']) != set(selected):
                raise ValueError('pi_selection_order_mismatch')
            selected = ordered['selected_modules']
        value = dict(version=1, selected_modules=selected, rewards_final=flags[-1])
    if type(value) is dict and 'module_flags' in value:
        flags = value['module_flags']
        if (set(value) != {'version', 'module_flags', 'rewards_final'}
                or type(flags) is not dict
                or set(flags) - {'guide_trial', 'clan_battle_simulation', 'clan_battle_daily'} not in (set(supported_daily.MODULE_ORDER) - {'guide_trial', 'clan_battle_simulation', 'clan_battle_daily'},
                    set(supported_daily.MODULE_ORDER) - {'guide_trial', 'clan_battle_simulation', 'clan_battle_daily', 'abyss'},
                    set(supported_daily.MODULE_ORDER) - {'guide_trial', 'clan_battle_simulation', 'clan_battle_daily', 'abyss', 'normal'})
                or any(type(enabled) is not bool for enabled in flags.values())):
            raise ValueError('invalid_module_flags')
        value = dict(version=value['version'],
                     selected_modules=[name for name in supported_daily.MODULE_ORDER if flags.get(name, False)],
                     rewards_final=value['rewards_final'])
    return gui_contract.validate_combination(value)


@dataclass(frozen=True)
class ExecutionContext:
    """Owner integration dependency, NOT a PI/JSON/environment permission schema.

    A trusted provider must atomically consume fresh authorization, verify the
    owner's actual controller/display binding and bind it to this root and exact
    selection. Stored local_config or PI_CONTROLLER alone cannot establish this.
    The current launcher supplies no provider. Never populate this from task
    parameters, saved instances, old audit images or unverified display values.
    """
    invocation_id: str
    root_task_id: int
    controller_uuid: str
    serial: str
    combination: dict
    execute: bool
    game_day: str
    count_context: str
    deadline: float
    input_display: int | None = None
    screenshot_display: str | None = None
    schedule_authorizations: tuple = ()
    schedule_attest_current_plan: bool = False
    schedule_confirm_new_cycle: bool = False
    mumu: dict | None = None
    controller: dict | None = None
    observation_source: object = None  # Trusted live callable, never saved/PI data.


class AlphaEntry(CustomAction):
    def __init__(self, registry, *, execution_context=None):
        super().__init__()
        self.registry = registry
        self.execution_context = execution_context
        self._lock = threading.Lock()
        self._roots = set()
        self._invocations = set()
        self.last_result = None

    def _authorize(self, context, task_id, selection):
        if self.execution_context is None:
            raise ValueError('trusted_owner_execution_context_required')
        # A Python dependency installed by trusted owner code, never task JSON.
        grant = self.execution_context(context, gui_contract.validate_combination(selection))
        if type(grant) is not ExecutionContext:
            raise ValueError('invalid_owner_execution_context')
        if (type(grant.invocation_id) is not str
                or not re.fullmatch(r'[0-9a-f]{32}', grant.invocation_id)
                or grant.invocation_id in self._invocations):
            raise ValueError('invalid_or_consumed_owner_invocation')
        self._invocations.add(grant.invocation_id)  # consume even on later failure
        if (type(grant.root_task_id) is not int or grant.root_task_id != task_id
                or grant.execute is not True
                or gui_contract.validate_combination(grant.combination) != selection):
            raise ValueError('owner_authority_does_not_match_invocation')
        if (type(grant.deadline) not in (int, float) or not math.isfinite(grant.deadline)
                or grant.deadline <= time.monotonic()):
            raise ValueError('finite_future_monotonic_deadline_required')
        if (type(grant.controller_uuid) is not str or not grant.controller_uuid
                or type(grant.serial) is not str or not grant.serial
                or type(grant.schedule_authorizations) is not tuple
                or any(type(n) is not str for n in grant.schedule_authorizations)
                or type(grant.schedule_attest_current_plan) is not bool
                or type(grant.schedule_confirm_new_cycle) is not bool):
            raise ValueError('invalid_owner_binding_or_permissions')
        owner = context.tasker
        if not owner.inited or owner.stopping:
            raise RuntimeError('owner_stopped_or_uninitialized_outcome_unknown')
        # Read only the already-bound controller; no discovery/connection/input.
        if owner.controller.uuid != grant.controller_uuid:
            raise ValueError('owner_controller_binding_mismatch')
        config = load_config(CONFIG_PATH)
        from .device import SERIAL
        if config['version'] == 4:
            from .daily import validate_controller
            try:
                validate_controller(grant.controller)
            except ValueError as exc:
                raise ValueError('invalid_owner_binding_or_permissions') from exc
            binding_matches = (type(grant.controller) is dict
                and grant.controller == config['controller'] and grant.mumu is None
                and grant.input_display is None and grant.screenshot_display is None)
        elif config['version'] == 3:
            from .daily import validate_mumu
            try:
                validate_mumu(grant.mumu)
            except ValueError as exc:
                raise ValueError('invalid_owner_binding_or_permissions') from exc
            binding_matches = (grant.controller is None and type(grant.mumu) is dict and grant.mumu == config['mumu']
                               and grant.input_display is None and grant.screenshot_display is None)
        else:
            binding_matches = (grant.controller is None and grant.mumu is None and type(grant.input_display) is int
                and type(grant.screenshot_display) is str and grant.screenshot_display.isdecimal()
                and config['device'] == dict(input_display=grant.input_display,
                                             screenshot_display=grant.screenshot_display))
        if (not binding_matches or config.get('connection', {}).get('serial', SERIAL) != grant.serial):
            raise ValueError('local_config_owner_binding_mismatch')
        supported_daily.validate(config, execute=grant.execute, game_day=grant.game_day,
            context=grant.count_context, schedule_authorizations=grant.schedule_authorizations,
            schedule_attest_current_plan=grant.schedule_attest_current_plan,
            selected_modules=selection['selected_modules'], rewards_final=selection['rewards_final'])
        return grant, config

    def _publish(self, result):
        self.last_result = result
        if result['report_path'] and not Path(result['report_path']).is_file():
            result['report_path'] = None
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        receipt = LOG_ROOT / ('entry-' + uuid4().hex + '.json')
        result['entry_receipt'] = str(receipt)
        with receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        # MFA AgentHelper handles the existing "info:" protocol in its run log.
        print('info: PCR Alpha ' + json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)

    def run(self, context, argv):
        """SDK boundary: explicit failure for every exception, no retry or wait."""
        acquired = False
        report = None
        result = dict(outcome='unknown', action_success=False, daily_complete=False,
                      supported_chain_complete=False, report_path=None)
        try:
            acquired = self._lock.acquire(blocking=False)
            if not acquired:
                raise RuntimeError('concurrent_alpha_entry_rejected')
            if argv.custom_action_name != ACTION:
                raise ValueError('unexpected_alpha_action')
            task_id = context.get_task_job().job_id
            if (type(task_id) is not int or task_id <= 0 or argv.task_detail.task_id != task_id
                    or task_id in self._roots):
                raise ValueError('invalid_or_repeated_root_no_retry')
            self._roots.add(task_id)
            selection = parse_selection(argv.custom_action_param, context=context)
            result['combination'] = selection
            if not gui_contract.build_plan(selection):
                raise ValueError('empty_selection')
            grant, config = self._authorize(context, task_id, selection)
            directory = supported_daily.CONTROL_ROOT / ('run-' + uuid4().hex)
            result['report_path'] = str(directory / 'report.json')
            from . import native
            token = native.RUN_DEADLINE.set(min(grant.deadline, native.RUN_DEADLINE.get()))
            try:
                with self.registry.scope(context, deadline=native.RUN_DEADLINE.get(),
                                         owner_source=grant.observation_source) as facade:
                    if selection['selected_modules'] == ['guide_trial']:
                        from .guide_trial import run_owned
                        report = run_owned(facade, config, directory)
                    elif selection['selected_modules'] == ['clan_battle_simulation']:
                        from .clan_battle import run_owned
                        report = run_owned(facade, config, directory)
                    elif selection['selected_modules'] == ['clan_battle_daily']:
                        from .clan_battle_batch import run_owned
                        report = run_owned(facade, config, directory)
                    else:
                        report = supported_daily.execute_supported(facade, config,
                            execute=grant.execute, game_day=grant.game_day, context=grant.count_context,
                            directory=directory, source='Alpha Agent owner-bound invocation',
                            schedule_authorizations=grant.schedule_authorizations,
                            schedule_attest_current_plan=grant.schedule_attest_current_plan,
                            **(dict(schedule_confirm_new_cycle=True) if grant.schedule_confirm_new_cycle else {}),
                            selected_modules=selection['selected_modules'], rewards_final=selection['rewards_final'])
                # Scope exit must succeed before interpreting a business report.
            finally:
                native.RUN_DEADLINE.reset(token)
            ok = (report.get('outcome') == 'complete'
                  and report.get('supported_chain_complete') is True
                  and report.get('daily_complete') is False)
            result.update(outcome=report.get('outcome', 'unknown'), action_success=ok,
                          supported_chain_complete=ok, summary=gui_contract.result_text(report))
        except BaseException as exc:
            result.update(outcome='unknown', action_success=False, supported_chain_complete=False,
                          error=str(exc) or type(exc).__name__, exception_type=type(exc).__name__)
            result['summary'] = gui_contract.result_text(result)
            # A returned failed report remains useful when scope exit fails.
            # Preserve only its display text; the SDK result above stays unknown.
            try:
                failed_states = ('stopped', 'failed', 'interrupted')
                if (type(report) is dict and report.get('outcome') in failed_states
                        and report.get('supported_chain_complete') is False
                        and report.get('daily_complete') is False
                        and type(report.get('steps')) is list
                        and any(type(step) is dict and step.get('status') in failed_states
                                for step in report['steps'])):
                    failed_summary = gui_contract.result_text(report)
                    if isinstance(failed_summary, str) and failed_summary.strip():
                        result['summary'] = failed_summary + '\n执行边界异常：' + result['error']
            except BaseException:
                # Malformed display data must not replace the original failure.
                pass
        finally:
            try:
                self._publish(result)
            except BaseException:
                if result.get('error'):
                    result['source_error'] = result['error']
                result.update(outcome='unknown', action_success=False, supported_chain_complete=False,
                              error='alpha_result_publication_failed')
                result['summary'] = gui_contract.result_text(result)
                self.last_result = result
            if acquired:
                self._lock.release()
        return result['action_success'] is True


def install_action(server, *, execution_context=None):
    from .agent_callbacks import AgentCallbacks
    registry = AgentCallbacks()
    registry.install()
    action = AlphaEntry(registry, execution_context=execution_context)
    if not server.register_custom_action(ACTION, action):
        raise RuntimeError('cannot_register_alpha_entry')
    return action


def main(argv=None, *, execution_context=None):
    """Official server lifecycle; owner integration can supply a trusted provider."""
    server = None
    started = False
    code = 1
    try:
        # MUST precede SDK initialization, including log configuration.
        from maa.agent.agent_server import AgentServer
        server = AgentServer
        import sys
        args = sys.argv[1:] if argv is None else argv
        if len(args) != 1 or not isinstance(args[0], str) or not args[0].strip():
            raise ValueError('expected_one_agent_identifier')
        from maa.tasker import Tasker
        from maa.define import LoggingLevelEnum
        Tasker.set_log_dir(LOG_ROOT / ('server-' + uuid4().hex))
        Tasker.set_stdout_level(LoggingLevelEnum.Off)
        action = install_action(server, execution_context=execution_context)
        if not server.start_up(args[0]):
            raise RuntimeError('alpha_agent_startup_failed')
        started = True
        server.join()  # main thread only; never inside a business callback
        code = 0
    except BaseException as exc:
        try:
            print('error: PCR Alpha server failed: ' + type(exc).__name__, flush=True)
        except BaseException:
            pass
    finally:
        if started:
            try:
                server.shut_down()
            except BaseException:
                code = 1
    return code
