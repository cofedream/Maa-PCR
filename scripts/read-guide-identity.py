"""Offline source audit; no credentials, controller or game input."""
import json
from pathlib import Path
import sys
import argparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pcr_maa.guide_identity import read_source, SOURCE_HASHES

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=tuple(SOURCE_HASHES), default='红焰深域 7-3')
    print(json.dumps(read_source(parser.parse_args().stage), ensure_ascii=False, indent=2))
