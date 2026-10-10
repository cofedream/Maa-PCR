"""Private GUI child entry; no CLI execute flags or saved grants."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def bind_selected_config(entry, snapshot, selected_path):
    """Bind this child's data source; the snapshot and one-shot grant still gate it."""
    from pcr_maa.alpha_owner import bind_config_snapshot
    path = Path(selected_path)
    if not path.is_absolute() or str(selected_path).startswith(('\\\\', '//')):
        raise ValueError('selected_config_requires_local_absolute_path')
    bind_config_snapshot(entry, snapshot)
    entry.CONFIG_PATH = path


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('expected_owned_identifier_and_selected_config')
    from maa.agent.agent_server import AgentServer  # select server mode first
    from pcr_maa import agent_entry
    from pcr_maa.alpha_owner import OwnerProvider
    bind_selected_config(agent_entry, sys.stdin.readline().strip(), sys.argv[2])
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    import os
    provider = OwnerProvider(sys.stdin, sys.stdout, on_disconnect=lambda: os._exit(4))
    try:
        raise SystemExit(agent_entry.main([sys.argv[1]], execution_context=provider))
    finally:
        provider.revoke()
