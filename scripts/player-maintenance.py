"""Run packaged maintenance with its own standard-library-only implementation."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='strict')

from pcr_maa.player_maintenance import main

if __name__ == '__main__':
    raise SystemExit(main())
