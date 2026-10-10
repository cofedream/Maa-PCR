"""Short-lived data-only preflight; imports no Maa controller or business CLI."""
from pathlib import Path
import sys
import os
import time
import json

_entry_ns = time.perf_counter_ns()
_entry_unix_ms = time.time_ns() // 1_000_000
_trace = os.environ.get('PCR_PREFLIGHT_PHASE_TRACE')
def phase(name, **fields):
    if not _trace:
        return
    # Observations only, restricted to the isolated formal diagnostic outputs.
    path = Path(_trace).resolve()
    allowed = Path(__file__).resolve().parents[1] / '.runtime/gui-start-debug-20260928'
    if not any(path.is_relative_to(allowed / name) for name in ('bounded-compare', 'formal-diff')):
        return
    try:
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(phase=name, elapsed_ms=(time.perf_counter_ns()-_entry_ns)/1_000_000,
                unix_ms=time.time_ns()//1_000_000, pid=os.getpid(), **fields))+'\n')
    except OSError:
        pass
phase('script_entry', entry_unix_ms=_entry_unix_ms)

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

if __name__ == '__main__':
    # Both explicit modes only serialize validated data and grant no input.
    # Default production preflight remains blocked; GUI review has its own v3
    # restriction and the Owner still checks production qualification afterwards.
    if len(sys.argv) != 3 or sys.argv[2] not in ('--offline-custom-controller', '--review-v3'):
        print('MuMu Start BLOCKED: live display identity/freshness and input target are unverified; fixed display config cannot authorize Start', file=sys.stderr)
        raise SystemExit(2)
    phase('imports_start')
    from pcr_maa.daily import load_config
    from pcr_maa.alpha_owner import config_snapshot
    phase('imports_end')
    try:
        phase('config_load_start')
        config = load_config(sys.argv[1])
        phase('config_load_end')
        if sys.argv[2] == '--review-v3' and not (config['version'] == 3
                or config['version'] == 4 and config['controller']['kind'] == 'adb'):
            raise ValueError('请选择 v3 MuMu 或 v4 标准 ADB 本机配置；不从旧配置推断身份或继承许可')
        phase('snapshot_start', history_read='not_in_this_path')
        snapshot = config_snapshot(config)
        phase('snapshot_end')
        print(snapshot, flush=True)
        phase('stdout_flushed')
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
