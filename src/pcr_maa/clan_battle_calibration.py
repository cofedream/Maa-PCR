"""Load reviewed, bundled calibration for the requested battle capability."""
from pathlib import Path

from ._clan_calibration_pin import INDEX_SHA256
from .clan_battle_calibration_data import INDEX_PATH, _json, _path, _read, parse_index, read_profile, resolve_profile_files


def load_calibration(plan, *, root=None, capability='simulation'):
    """The optional root is a Python test dependency, never a task/config field."""
    from . import native
    if capability not in ('simulation', 'formal', 'carryover'):
        raise ValueError('clan_battle_calibration_capability_invalid')
    root = Path(native.ROOT if root is None else root).resolve()
    if not _path(root, INDEX_PATH).is_file():
        raise RuntimeError('clan_battle_calibration_missing')
    _, data = _read(root, INDEX_PATH, INDEX_SHA256, limit=1024 * 1024)
    index = parse_index(root, data)
    entry = next((row for row in index['entries'] if row['server'] == plan.get('server')
                  and row['content_version'] == plan.get('content_version')), None)
    if entry is None:
        raise RuntimeError('clan_battle_calibration_missing')
    capabilities = entry.get('capabilities', ['simulation'])
    if capability not in capabilities:
        raise RuntimeError('clan_battle_calibration_capability_missing:' + capability)
    calibration = read_profile(root, entry)
    if index['version'] == 1 or calibration['version'] == 1:
        if set(capabilities) - {'simulation'}:
            raise ValueError('clan_battle_calibration_legacy_capability_invalid')
        if plan.get('auto') is not True or plan.get('switches') != []:
            raise RuntimeError('clan_battle_calibration_auto_only')
    elif plan.get('switches') and 'timed' not in capabilities:
        raise RuntimeError('clan_battle_calibration_capability_missing:timed')
    resolve_profile_files(root, calibration, capabilities)
    from .clan_battle_native import calibration_pipeline, timing_capability
    calibration_pipeline(calibration, plan, capability=capability)
    if 'daily' in calibration or capability in ('formal', 'carryover') or 'carryover' in capabilities:
        from .clan_battle_daily_native import daily_pipeline, require_daily_execution
        if capability in ('formal', 'carryover') or 'carryover' in capabilities or calibration['daily'].get('version') == 2:
            require_daily_execution(calibration, plan, require_scope=capability in ('formal', 'carryover'),
                                    capability=capability, require_carry_pages='carryover' in capabilities)
        else:
            daily_pipeline(calibration, plan)
    if 'timed' in capabilities and timing_capability(calibration).get('validated') is not True:
        raise ValueError('clan_battle_timing_not_calibrated')
    return calibration
