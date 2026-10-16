"""Non-authoritative capture evidence from this connection's native log window."""
import hashlib
import json
import os
from pathlib import Path
import re


_FAMILY = re.compile(r'maafw(?:\.bak\.\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}\.\d{1,3})?\.log\Z')
_SELECTED = re.compile(r'\[MaaNS::CtrlUnitNs::ScreencapAgent::speed_test\] The fastest method is (Encode|RawWithGzip) \[cost=\d+ms\]')
_METHODS = {'Encode': 2, 'RawWithGzip': 4}


def _files(directory):
    return sorted(path for path in directory.iterdir() if _FAMILY.fullmatch(path.name) and path.is_file())


def _identity(stat):
    if not stat.st_ino:
        raise ValueError('log_identity_unavailable')
    return stat.st_dev, stat.st_ino


def selected_backend(lines, *, requested, input_methods, serial):
    """A request mask never substitutes for the selected speed-test result."""
    if (type(requested) is not int or type(input_methods) is not int
            or (requested, input_methods) not in {(6, 1), (2, 0), (4, 0)}):
        raise ValueError('unsupported_measurement_scope')
    creates, selections, pids = [], [], set()
    for index, line in enumerate(lines):
        pid = re.search(r'\[Px([0-9]+)\]', line)
        if pid:
            pids.add(pid[1])
        if '[MaaAdbControllerCreate]' in line and '| enter' in line:
            if (pid is None or f'[address={serial}]' not in line
                    or f'[screencap_methods={requested}]' not in line
                    or f'[input_methods={input_methods}]' not in line):
                raise ValueError('connection_binding_mismatch')
            creates.append((index, pid[1]))
        if 'The fastest method is ' in line:
            match = _SELECTED.search(line)
            if pid is None or match is None:
                raise ValueError('selection_unrecognized')
            selections.append((index, pid[1], _METHODS[match[1]]))
    if len(creates) != 1 or len(selections) != 1 or pids != {creates[0][1]}:
        raise ValueError('connection_window_ambiguous')
    index, pid, method = selections[0]
    if index <= creates[0][0] or pid != creates[0][1] or not requested & method:
        raise ValueError('selected_backend_mismatch')
    return method


class CapturePerformance:
    """Collect once just after connection; never revisit the full business log."""
    def __init__(self, root, run_id, trace_path, *, options, serial, display_binding,
                 requested=6, input_methods=1):
        self.root, self.run_id, self.trace_path = Path(root), run_id, Path(trace_path)
        self.serial, self.requested, self.input_methods = serial, requested, input_methods
        self.options = json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        self.display_binding = dict(display_binding)
        self.offsets = {}
        if self.trace_path.parent.exists():
            for path in _files(self.trace_path.parent):
                stat = path.stat()
                self.offsets[_identity(stat)] = stat.st_size

    def finish(self):
        report = dict(evidence_version=2, purpose='capture_performance_only', run_id=self.run_id,
            scope='formal' if self.input_methods == 1 else 'read_only_ab',
            observation_window='connection_only', requested_screencap_methods=self.requested,
            actual_screencap_method=None, actual_backend=None,
            input_methods=self.input_methods, options_sha256=hashlib.sha256(self.options).hexdigest(),
            options_encoding='sorted_compact_utf8_json', display_binding_basis=self.display_binding['basis'],
            logical_input_display=self.display_binding['logical'],
            physical_screenshot_display=self.display_binding['physical'],
            source_manifest_sha256=None, native_log_segments=[], observation='unknown')
        try:
            manifest = self.root / 'package-manifest.json'
            if manifest.is_file():
                report['source_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
            lines, seen, total = [], set(), 0
            for path in _files(self.trace_path.parent):
                with path.open('rb') as stream:
                    stat = os.fstat(stream.fileno())
                    identity = _identity(stat)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    start = self.offsets.get(identity, 0)
                    if stat.st_size < start:
                        raise ValueError('log_truncated')
                    total += stat.st_size - start
                    if total > 32 * 1024 * 1024:
                        raise ValueError('connection_window_too_large')
                    stream.seek(start)
                    data = stream.read(stat.st_size - start)
                    # Only complete native lines belong to this observation.
                    end = data.rfind(b'\n') + 1
                    if end != len(data):
                        raise ValueError('log_line_incomplete')
                    if not data:
                        continue
                    report['native_log_segments'].append(dict(file=path.name, file_identity=list(identity),
                        start_offset=start, end_offset=start + end, sha256=hashlib.sha256(data).hexdigest()))
                    lines.extend(data.decode('utf-8').splitlines())
            report['actual_screencap_method'] = selected_backend(lines, requested=self.requested,
                input_methods=self.input_methods, serial=self.serial)
            report['actual_backend'] = 'Encode' if report['actual_screencap_method'] == 2 else 'RawWithGzip'
            report['observation'] = 'observed'
        except Exception:
            report['observation'] = 'unknown'
            report['actual_screencap_method'] = report['actual_backend'] = None
        destination = self.trace_path.parent.parent / 'capture-performance-v2.json'
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(destination)
        return report


def begin_capture_report(*args, **kwargs):
    try:
        return CapturePerformance(*args, **kwargs)
    except Exception:
        return None


def finish_capture_report(report):
    if report is not None:
        try:
            return report.finish()
        except Exception:
            pass
    return None
