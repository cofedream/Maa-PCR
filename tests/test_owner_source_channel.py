"""Private live source checks; no native construction or device access."""
from types import SimpleNamespace
import json
import queue
import time

import pytest
from pcr_maa.alpha_owner import OwnerProvider, PREFIX, SOURCE_PREFIX


@pytest.mark.parametrize('fault', [None, 'deny', 'nonce', 'timeout', 'revoke', 'root'])
def test_live_source_channel_has_no_stale_grant_or_retry(fault):
    incoming = queue.Queue()
    sent = []
    context = SimpleNamespace(get_task_job=lambda: SimpleNamespace(job_id=3),
        tasker=SimpleNamespace(controller=SimpleNamespace(uuid='same-uuid')))
    selection = dict(version=1, selected_modules=['rewards'], rewards_final=False)
    answer = dict(ok=True, fixed_binding=True, invocation_id='a'*32, root_task_id=3,
        controller_uuid='same-uuid', serial='127.0.0.1:16384', combination=selection,
        execute=True, game_day='2026-09-30', count_context='gui-current-state', remaining_seconds=30,
        input_display=2, screenshot_display='123', schedule_authorizations=[], schedule_attest_current_plan=False)
    class Input:
        def readline(self, limit): return incoming.get(timeout=2)
    class Output:
        def write(self, line):
            sent.append(line)
            if line.startswith(PREFIX): incoming.put(json.dumps(answer)+'\n')
            else:
                assert line.startswith(SOURCE_PREFIX)
                reply = dict(json.loads(line[len(SOURCE_PREFIX):]), ok=True)
                if fault == 'deny': reply['ok'] = False
                if fault == 'nonce': reply['probe_id'] = 'old'
                if fault != 'timeout': incoming.put(json.dumps(reply)+'\n')
        def flush(self): pass
    provider = OwnerProvider(Input(), Output(), timeout=.05, on_disconnect=lambda: None)
    try:
        grant = provider(context, selection)
        assert callable(grant.observation_source)
        if fault == 'revoke': provider.revoke()
        if fault == 'root': context.get_task_job = lambda: SimpleNamespace(job_id=4)
        if fault is None:
            assert grant.observation_source(context) is True
            assert grant.observation_source(context) is True
            assert len(sent) == 3
        else:
            with pytest.raises(RuntimeError): grant.observation_source(context)
            previous = list(sent)
            with pytest.raises(RuntimeError): grant.observation_source(context)
            assert sent == previous
        assert sum(line.startswith(PREFIX) for line in sent) == 1
    finally:
        incoming.put('')


def test_local_source_revocation_keeps_channel_until_actual_eof():
    """A denied probe must return to Maa before channel teardown can exit Python."""
    import threading
    incoming = queue.Queue()
    release_watcher = threading.Event()
    watcher_advanced = threading.Event()
    disconnected = threading.Event()
    disconnects = []
    sent = []
    reads = []
    context = SimpleNamespace(get_task_job=lambda: SimpleNamespace(job_id=3),
        tasker=SimpleNamespace(controller=SimpleNamespace(uuid='same-uuid')))

    class Input:
        def readline(self, limit):
            reads.append(True)
            if len(reads) > 1:
                watcher_advanced.set()
            return incoming.get(timeout=2)

    class Output:
        def write(self, line):
            sent.append(line)
            request = json.loads(line[len(SOURCE_PREFIX):])
            incoming.put(json.dumps(dict(request, ok=False))+'\n')
        def flush(self): pass

    def on_disconnect():
        disconnects.append(True)
        disconnected.set()
        watcher_advanced.set()

    class PausedDelivery(queue.Queue):
        def put_nowait(self, answer):
            super().put_nowait(answer)
            if isinstance(answer, dict):
                assert release_watcher.wait(2)

    provider = OwnerProvider(Input(), Output(), timeout=1, on_disconnect=on_disconnect)
    provider._source_key = (context, 3, 'same-uuid', 'a'*32)
    provider._source_answers = PausedDelivery(maxsize=1)
    watcher = threading.Thread(target=provider._watch_disconnect, daemon=True)
    watcher.start()
    try:
        with pytest.raises(RuntimeError, match='changed or revoked'):
            provider.check_source(context)
        assert provider._closed.is_set()
        release_watcher.set()
        assert watcher_advanced.wait(2)
        assert not disconnected.is_set(), 'normal_revocation_must_not_disconnect_before_callback_returns'
        with pytest.raises(RuntimeError, match='closed or mismatched'):
            provider.check_source(context)
        assert len(sent) == 1  # Revocation cannot send another probe or grant.
        incoming.put('')
        assert disconnected.wait(2), 'actual_EOF_still_disconnects'
        assert len(disconnects) == 1
    finally:
        release_watcher.set()
        incoming.put('')
        watcher.join(2)
        assert not watcher.is_alive()
