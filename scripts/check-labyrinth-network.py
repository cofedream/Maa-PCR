"""On-demand connectivity report for the labyrinth settings card.

Reads one fixed request line and writes one bounded report line. It has no
device, credential, task or player-log access.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.network_check import encode, parse_request, snapshot


def main():
    try:
        line = sys.stdin.readline(4097)
        if len(line) > 4096:
            raise ValueError('invalid_network_check_request')
        server, database = parse_request(json.loads(line))
        report = snapshot(server, database=database)
    except (ValueError, TypeError):
        return 2
    sys.stdout.write(encode(report) + '\n')
    sys.stdout.flush()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
