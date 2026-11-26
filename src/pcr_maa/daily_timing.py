"""Per-invocation metrics from the Windows Owner log and borrowed task dispatch."""
import json
import re
from datetime import datetime
from pathlib import Path

from .labyrinth.timing import NativeTimingTrace


class GuiDailyTimings(NativeTimingTrace):
    # Maa 5.16 sends Controller.Action for posted screencaps. Pipeline-internal
    # captures bypass this notification; never turn absent events into estimates.
    _event = re.compile(
        r'AgentClient::ctrl_event_sink\]\s+\[handle=true\] '
        r'\[message=Controller\.Action\.(Starting|Succeeded|Failed)\] \[details_json=')
    _header = re.compile(r'^\[([\d-]+ [\d:.]+)\]\[[A-Z]+\]\[Px(\d+)\]')

    def __init__(self, facade, timings, grant):
        self.facade = facade
        self.process_id = grant.timing_owner_pid
        self.controller_uuid = grant.controller_uuid
        self.invocation_id = grant.invocation_id
        self.captures = {}
        self.owner_seen = False
        super().__init__(timings, Path(grant.timing_owner_log))
        self.timings.observe('page_wait')
        facade.page_timings = timings

    def _record_trace_line(self, line):
        header = self._header.match(line)
        if not header or int(header[2]) != self.process_id:
            return
        self.owner_seen = True
        recognition = self.recognition_pattern.search(line)
        if recognition:
            self.timings.record('recognition', int(recognition[1]))
            return
        if '"screencap"' not in line:
            return
        event = self._event.search(line)
        if not event:
            return
        details, _ = json.JSONDecoder().raw_decode(line, event.end())
        if (details.get('action') != 'screencap' or details.get('uuid') != self.controller_uuid
                or type(details.get('ctrl_id')) is not int):
            return
        ctrl_id = details['ctrl_id']
        time = datetime.fromisoformat(header[1])
        if event[1] == 'Starting':
            self.captures[ctrl_id] = time
            return
        start = self.captures.pop(ctrl_id, None)
        if start is None or time < start:
            self.trace_issues.add('capture_interval_unavailable')
            return
        self.timings.record('screenshot', (time - start).total_seconds() * 1000,
                            outcome='succeeded' if event[1] == 'Succeeded' else 'failed')

    def finish_native_trace(self):
        super().finish_native_trace()
        if not self.owner_seen:
            self.trace_issues.add('owner_records_unavailable')
        if self.captures:
            self.trace_issues.add('unfinished_capture')
        if self.trace_issues:
            raise ValueError('gui_native_trace_incomplete')

    def close(self):
        self.facade.page_timings = None
        self.captures.clear()

    def coverage(self):
        return dict(source='gui_owner_trace_and_context_dispatch',
                    owner_invocation_id=self.invocation_id, owner_process_id=self.process_id,
                    screenshot='paired_posted_screencaps_only',
                    recognition='native_leaf_analyze_cost',
                    page_wait='borrowed_context_run_task_including_nested_work',
                    trace=self.trace_diagnostics())
