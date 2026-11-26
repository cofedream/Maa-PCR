"""Read large historical daily reports as bounded, verified step snapshots."""

import io
import json
import os
from contextlib import contextmanager

from . import supported_daily as daily


# Historical releases wrote pretty-printed JSON with one identically formatted
# receipt per completed step. Limit the largest individual proof, not their sum.
STEP_LIMIT = 64 * 1024 * 1024
METADATA_LIMIT = 64 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('invalid_report')
        result[key] = value
    return result


def _decode(data):
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        raise ValueError('invalid_report') from None


def _identity(info):
    return info.st_dev, info.st_ino, info.st_nlink, info.st_size, info.st_mtime_ns


@contextmanager
def _open_checked(path):
    expected = _identity(daily._plain_file(path))
    with path.open('rb') as stream:
        if expected != _identity(os.fstat(stream.fileno())):
            raise ValueError('control_snapshot_changed')
        yield stream
        if expected != _identity(daily._plain_file(path)):
            raise ValueError('control_snapshot_changed')


class _HistoricalReport(io.RawIOBase):
    """Pin a file identity, not a Windows handle that would block os.replace."""

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.identity = _identity(daily._plain_file(path))
        self.position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=os.SEEK_SET):
        base = {os.SEEK_SET: 0, os.SEEK_CUR: self.position, os.SEEK_END: self.identity[3]}[whence]
        self.position = base + offset
        return self.position

    def readinto(self, buffer):
        # Each lock covers at most 1 MiB of disk I/O. JSON parsing and independent
        # receipt verification never prevent the worker from publishing its next step.
        with daily._control_lock(self.path.parent, existing_only=True):
            with _open_checked(self.path) as stream:
                if _identity(os.fstat(stream.fileno())) != self.identity:
                    raise ValueError('control_snapshot_changed')
                stream.seek(self.position)
                count = stream.readinto(memoryview(buffer)[:1024 * 1024])
        self.position += count
        return count


def _step_offsets(stream):
    """Split only the layout emitted by the historical indent=2 report writer."""
    metadata = bytearray()
    while True:
        line = stream.readline(METADATA_LIMIT + 1)
        if not line:
            raise ValueError('invalid_report')
        if line.rstrip(b'\r\n') == b'  "steps": [':
            metadata.extend(b'  "steps": [')
            break
        metadata.extend(line)
        if len(metadata) > METADATA_LIMIT:
            raise ValueError('control_file_too_large')

    spans = []
    while True:
        start = stream.tell()
        if stream.readline(32).rstrip(b'\r\n') != b'    {':
            raise ValueError('invalid_report')
        if len(spans) >= len(daily.MODULE_ORDER) + 2:
            raise ValueError('invalid_report')
        while True:
            position = stream.tell()
            remaining = STEP_LIMIT - (position - start)
            if remaining <= 0:
                raise ValueError('control_file_too_large')
            line = stream.readline(remaining + 1)
            if not line:
                raise ValueError('invalid_report')
            if len(line) > remaining:
                raise ValueError('control_file_too_large')
            delimiter = line.rstrip(b'\r\n')
            if delimiter in (b'    },', b'    }'):
                spans.append((start, position + 5 - start))   # exclude comma and newline
                break
            if not line.startswith(b'      '):
                raise ValueError('invalid_report')
        if delimiter == b'    }':
            break
    tail = stream.read(METADATA_LIMIT + 1)
    if len(metadata) + len(tail) > METADATA_LIMIT:
        raise ValueError('control_file_too_large')
    metadata.extend(tail)
    document = _decode(metadata)
    if (not isinstance(document, dict) or document.get('steps') != []
            or 'control_projection_version' in document):
        raise ValueError('invalid_report')
    return document, spans


def _check_receipt(directory, index, row, raw):
    # Never follow the stored path; it can name an old/moved installation.
    path = directory / f"{index}-{row['name']}.json"
    with _open_checked(path) as receipt:
        for line in io.BytesIO(raw):
            expected = line[4:].rstrip(b'\r\n')
            actual = receipt.readline(len(expected) + 3)
            if actual.rstrip(b'\r\n') != expected or not actual:
                raise ValueError('invalid_report')
        if receipt.read(1):
            raise ValueError('invalid_report')


def read_legacy_control(directory):
    """Verify each archived step and return only facts needed by summary/stop."""
    path = directory / 'report.json'
    source = _HistoricalReport(path)
    with io.BufferedReader(source, buffer_size=1024 * 1024) as stream:
        document, spans = _step_offsets(stream)
        snapshot = daily._report_projection(document)
        try:
            for index, (offset, size) in enumerate(spans, 1):
                stream.seek(offset)
                raw = stream.read(size)
                row = _decode(raw)
                if (not isinstance(row, dict) or row['name'] not in (*daily.MODULE_ORDER, 'event_current',
                        'event_revival', 'rewards_final') or row['status'] not in daily.STEP_STATES):
                    raise ValueError('invalid_report')
                if row['status'] not in ('not_run', 'running', 'stopped', 'failed', 'interrupted'):
                    if daily._expected_step_status(row, document, directory) != row['status']:
                        raise ValueError('invalid_report')
                    _check_receipt(directory, index, row, raw)
                elif 'receipt' in row:
                    _check_receipt(directory, index, row, raw)
                if ('result' in row and (not isinstance(row['result'], dict) or row['status'] == 'not_run')):
                    raise ValueError('invalid_report')
                snapshot['steps'].extend(daily._report_projection({'steps': [row]})['steps'])
                del raw, row
        except (KeyError, TypeError, IndexError, RecursionError, OverflowError, AttributeError):
            raise ValueError('invalid_report') from None
        if _identity(daily._plain_file(path)) != source.identity:
            raise ValueError('control_snapshot_changed')
    return daily._validate_report(directory, snapshot, legacy_projection=True)
