"""Private GUI pipe: one read-only request; Stop/EOF revokes it. No disk credentials."""
from pathlib import Path
import asyncio
import contextlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.guide_snapshot import collect, positive, selection, version, SnapshotFailure
from pcr_maa.labyrinth.owned_runtime import Discard, StopSignal, unique_object
from pcr_maa.labyrinth.owner_messages import captcha_message


def parse(value):
    if type(value) is not dict or type(value.get('version')) is not int or value['version'] not in (1, 2):
        raise SnapshotFailure('invalid_configuration')
    expected = {'version', 'account_id', 'content_version', 'credentials'}
    if value['version'] == 1:
        expected.add('unit_ids')
    if set(value) != expected:
        raise SnapshotFailure('invalid_configuration')
    positive(value['account_id'], 2**63 - 1)
    version(value['content_version'])
    if value['version'] == 1:
        selection(value['unit_ids'])
    c = value['credentials']
    if (type(c) is not dict or set(c) != {'username', 'password'}
        or type(c['username']) is not str or not 0 < len(c['username']) <= 512
        or type(c['password']) is not str or not 0 < len(c['password']) <= 4096
        or any(ch in c['username'] for ch in '\r\n\0') or '\0' in c['password']):
        raise SnapshotFailure('invalid_configuration')
    return value


def main(*, collector=collect):
    output = sys.stdout
    value = None
    stop = None
    line = None
    result = dict(kind='result', outcome='snapshot_failed')
    def captcha(url, stage):
        output.write(json.dumps(captcha_message(url, stage), separators=(',', ':')) + '\n')
        output.flush()
    try:
        line = sys.stdin.readline(16385)
        if len(line) > 16384:
            raise SnapshotFailure('invalid_configuration')
        value = parse(json.loads(line, object_pairs_hook=unique_object))
        line = None
        stop = StopSignal(sys.stdin)
        stop.start()
        with contextlib.redirect_stdout(Discard()), contextlib.redirect_stderr(Discard()):
            c = value['credentials']
            snapshot = asyncio.run(collector(c['username'], c['password'], value['account_id'],
                value['content_version'], value.get('unit_ids'), stop=stop, on_captcha=captcha))
        result = dict(kind='result', outcome='complete', snapshot=snapshot)
    except Exception as error:
        safe = {'stopped', 'content_update_required', 'account_mismatch', 'invalid_configuration', 'invalid_snapshot',
            'select_one_team', 'manual_verification_required', 'sdk_login_failed', 'sdk_login_timeout',
            'protocol_login_timeout', 'login_failed', 'protocol_version_invalid', 'tutorial_incomplete',
            'captcha_cancelled', 'captcha_timeout', 'captcha_rejected', 'captcha_network_failed', 'captcha_failed'}
        result['outcome'] = str(error) if str(error) in safe else 'snapshot_failed'
    finally:
        line = None
        if type(value) is dict:
            credentials = value.pop('credentials', None)
            if type(credentials) is dict:
                credentials.clear()
        if stop is not None:
            stop.finish()
    output.write(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
    output.flush()
    return 0 if result['outcome'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
