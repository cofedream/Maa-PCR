"""Import a public clan guide without a browser profile, account, controller, or game input."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pcr_maa.clan_guide_source import main

if __name__ == '__main__':
    main()
