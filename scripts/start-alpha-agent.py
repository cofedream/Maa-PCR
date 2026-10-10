"""MFA launches this file with the official Agent identifier as its last arg."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

if __name__ == '__main__':
    code = 1
    try:
        from maa.agent.agent_server import AgentServer  # server mode before SDK init
        from pcr_maa.agent_entry import main
        code = main()
    except BaseException as exc:
        try:
            print('error: PCR Alpha launch failed: ' + type(exc).__name__, flush=True)
        except BaseException:
            pass
    raise SystemExit(code)
