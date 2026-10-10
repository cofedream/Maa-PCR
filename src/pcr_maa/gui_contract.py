"""No-IO combination contract for a future GUI/MFA named instance.

Only module choices are restored. The caller supplies fresh execute, game day,
context, device/config and schedule permissions to supported_daily separately.
This module never dispatches work, stores files, or establishes permission.
"""
from . import supported_daily


def make_combination(selected_modules, *, rewards_final=False):
    """Return independent version-1 choices preserving the player-selected order."""
    if selected_modules is None:
        raise ValueError('explicit_selected_modules_required')
    plan = supported_daily.build_plan(selected_modules=selected_modules, rewards_final=rewards_final)
    return dict(version=1, selected_modules=[n for n in plan if n != 'rewards_final'],
                rewards_final=rewards_final)


def validate_combination(value):
    """Validate restored data; reject every extra field, including authority."""
    if (type(value) is not dict or set(value) != {'version', 'selected_modules', 'rewards_final'}
            or type(value['version']) is not int or value['version'] != 1
            or type(value['selected_modules']) is not list):
        raise ValueError('invalid_combination')
    return make_combination(value['selected_modules'], rewards_final=value['rewards_final'])


def build_plan(combination):
    """Return an offline tuple of step names, including an optional final claim."""
    value = validate_combination(combination)
    return supported_daily.build_plan(selected_modules=value['selected_modules'],
                                      rewards_final=value['rewards_final'])


def result_text(report):
    """Map business reports and Agent exceptions to display text; perform no IO."""
    if 'steps' not in report and report.get('error'):
        error = str(report['error'])
        message = {
            'owner_denied': '当前启动许可或设备绑定未通过核验。',
            'owner_reply_timeout_no_retry': '等待执行程序回复超时。',
            'owner_channel_closed_or_invalid': '与执行程序的连接中断或回复无效。',
            'alpha_result_publication_failed': '本次结果记录未能正常发布。',
        }.get(error, '执行程序报告异常。')
        reason = '原因：' + error
        if report.get('source_error'):
            reason += '；原错误：' + str(report['source_error'])
        return '\n'.join(['本次执行已停止；未确认的结果不能视为成功。', message,
            '请核对所选设备和游戏当前状态并保留本次日志；结果未确认时不要直接重跑、删账或换日，请交维护端核查。',
            reason])
    return supported_daily.summary(report, player=True)
