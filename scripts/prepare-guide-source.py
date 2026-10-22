"""Prepare public guide material for the existing review panel; no account or game access."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pcr_maa.guide_source import main

if __name__ == '__main__':
    main()
