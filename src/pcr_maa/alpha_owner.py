"""One request/reply over a GUI-owned child's redirected standard streams.

Endpoint possession is the trust boundary, not JSON or a saved configuration.
No discovery, controller construction, permission file, environment grant or retry.
The parent must bind its actual AppendTask job before answering the request.
"""
import json
import math
import queue
import threading
import time

PREFIX = 'pcr-owner-request:'
SOURCE_PREFIX = 'pcr-owner-source:'
MAX_LINE = 16384


def config_snapshot(value):
    """Validated v1-v4 data; serialization grants no live authority."""
    from .daily import validate_config
    from .device import validate_connection, ADB_PATH, SERIAL
    config = validate_config(value)
    connection = validate_connection(config.get('connection', dict(adb_path=ADB_PATH, serial=SERIAL)))
    return json.dumps(dict(config=config, connection=connection), sort_keys=True,
                      ensure_ascii=True, separators=(',', ':'), allow_nan=False)


def bind_config_snapshot(entry, snapshot):
    """Keep the original post-authorize load/validation and reject any drift."""
    expected = config_snapshot(json.loads(snapshot)['config'])
    if expected != snapshot:
        raise ValueError('invalid_config_snapshot')
    original_load = entry.load_config

    def checked_load(path):
        value = original_load(path)
        if config_snapshot(value) != expected:
            raise ValueError('config_changed_since_confirmation')
        return value

    entry.load_config = checked_load


class OwnerProvider:
    def __init__(self, incoming, outgoing, *, timeout=5.0, on_disconnect=None):
        self.incoming, self.outgoing = incoming, outgoing
        self.timeout = timeout
        self.on_disconnect = on_disconnect or self.revoke
        self._used = False
        self._lock = threading.Lock()
        self._answers = queue.Queue(maxsize=1)
        self._closed = threading.Event()
        self._source_answers = queue.Queue(maxsize=1)
        self._source_key = None
        # Start reading only after requesting: a buffered reply cannot authorize
        # anything without this provider's actual callback root and controller.

    def _read(self):
        try:
            line = self.incoming.readline(MAX_LINE + 1)
            if not line or len(line) > MAX_LINE or not line.endswith('\n'):
                raise ValueError('owner_channel_closed_or_invalid')
            self._answers.put(json.loads(line), timeout=self.timeout)
        except BaseException as exc:
            self._answers.put(exc)

    def __call__(self, context, selection):
        with self._lock:
            if self._used or self._closed.is_set():
                raise ValueError('owner_request_already_consumed')
            self._used = True
        root = context.get_task_job().job_id
        uuid = context.tasker.controller.uuid
        request = dict(root_task_id=root, controller_uuid=uuid, combination=selection)
        self.outgoing.write(PREFIX + json.dumps(request, allow_nan=False) + '\n')
        self.outgoing.flush()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            answer = self._answers.get(timeout=self.timeout)
        except queue.Empty:
            raise ValueError('owner_reply_timeout_no_retry') from None
        if isinstance(answer, BaseException):
            raise ValueError('owner_channel_closed_or_invalid') from answer
        if type(answer) is not dict or answer.get('ok') is not True:
            raise ValueError('owner_denied')
        if (answer.get('root_task_id') != root or answer.get('controller_uuid') != uuid
                or answer.get('combination') != selection):
            raise ValueError('owner_reply_binding_mismatch')
        remaining = answer.pop('remaining_seconds', None)
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or not 0 < remaining <= 1800:
            raise ValueError('owner_reply_invalid_remaining_time')
        answer.pop('ok')
        answer['deadline'] = time.monotonic() + remaining
        auth = answer.get('schedule_authorizations')
        if type(auth) is not list or any(type(item) is not str for item in auth):
            raise ValueError('owner_invalid_schedule_authorizations')
        answer['schedule_authorizations'] = tuple(auth)
        fixed_binding = answer.pop('fixed_binding', False)
        if type(fixed_binding) is not bool:
            raise ValueError('owner_invalid_fixed_binding')
        if fixed_binding:
            self._source_key = (context, root, uuid, answer['invocation_id'])
            answer['observation_source'] = self.check_source
        from .agent_entry import ExecutionContext
        grant = ExecutionContext(**answer)
        if self.on_disconnect:
            threading.Thread(target=self._watch_disconnect, daemon=True).start()
        return grant

    def check_source(self, context):
        """One bounded, non-retried check of actual objects retained by Owner."""
        from uuid import uuid4
        with self._lock:
            key = self._source_key
            if self._closed.is_set() or key is None or context is not key[0]:
                raise RuntimeError('Owner source closed or mismatched')
            if context.get_task_job().job_id != key[1] or context.tasker.controller.uuid != key[2]:
                self.revoke()
                raise RuntimeError('Owner source root/controller changed')
            request = dict(root_task_id=key[1], controller_uuid=key[2],
                           invocation_id=key[3], probe_id=uuid4().hex)
            try:
                self.outgoing.write(SOURCE_PREFIX + json.dumps(request) + '\n')
                self.outgoing.flush()
                try:
                    answer = self._source_answers.get(timeout=self.timeout)
                except queue.Empty:
                    raise RuntimeError('Owner source timeout; no retry') from None
                if (self._closed.is_set() or type(answer) is not dict
                        or answer != dict(request, ok=True)):
                    raise RuntimeError('Owner source changed or revoked')
            except BaseException:
                self.revoke()
                raise
            return True

    def revoke(self):
        self._closed.set()

    def _watch_disconnect(self):
        try:
            # No second execution grant is accepted. A fixed-binding channel
            # accepts only live source replies; invalid data/EOF revokes it.
            # Parent Stop also stops the native tasker.
            if self._source_key is None:
                self.incoming.read(1)
            else:
                # A rejected source probe revokes this execution grant, but
                # Maa still needs the active callback's response. Keep watching
                # the pipe until actual EOF/error; local revoke is not disconnect.
                while True:
                    line = self.incoming.readline(MAX_LINE + 1)
                    if not line or len(line) > MAX_LINE or not line.endswith('\n'):
                        break
                    self._source_answers.put_nowait(json.loads(line))
        except BaseException:
            pass  # Every malformed/unsolicited response revokes this channel.
        finally:
            self._closed.set()
            try:
                self._source_answers.put_nowait(None)
            except queue.Full:
                pass
            self.on_disconnect()
