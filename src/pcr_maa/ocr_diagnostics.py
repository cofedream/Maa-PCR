"""Local terminal-failure evidence; never a settlement or retry authority."""
from collections import deque
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
from queue import Queue, Full
from threading import Lock, Thread
from uuid import uuid4

SCHEMA = 'pcr-ocr-failure-v1'
CACHE_FRAMES = 16
RUN_BYTES = 16 * 1024 * 1024
TOTAL_BYTES = 512 * 1024 * 1024


@contextmanager
def _quota_lock(path):
    # Keep the file: deleting it could split concurrent owners across inodes.
    # The OS releases this nonblocking lock even after an interrupted process.
    with path.open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def fingerprints(resource):
    rows = {}
    for folder in ('model/ocr', 'pipeline'):
        for path in sorted((Path(resource) / folder).rglob('*')):
            if path.is_file():
                rows[path.relative_to(resource).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return rows


class FailureDiagnostics:
    def __init__(self, root, run_id, owner, hashes):
        self.root, self.run_id, self.owner = Path(root), run_id, owner
        self.hashes = hashes
        self.ids = deque(maxlen=CACHE_FRAMES)
        self.pending = deque(maxlen=2)
        self.lock = Lock()
        self.sinks = []
        self.error = None

    def event(self, message, detail):
        # Native callbacks only retain scalar IDs. No SDK, image or file calls.
        if message in ('Node.Recognition.Succeeded', 'Node.Recognition.Failed'):
            with self.lock:
                self.ids.append((detail.get('task_id'), detail.get('reco_id')))

    def attach(self, tasker):
        from maa.event_sink import EventSink
        from maa.tasker import Tasker
        owner = self
        class Sink(EventSink):
            def _on_raw_notification(self, handle, message, detail):
                owner.event(message, detail)
        try:
            if not Tasker.set_reco_image_cache_limit(CACHE_FRAMES) or not Tasker.set_debug_mode(True):
                raise RuntimeError('native_image_options_unavailable')
            sink = Sink()
            sid = tasker.add_context_sink(sink)
            if sid is None:
                raise RuntimeError('native_sink_unavailable')
            self.sinks.append((tasker.remove_context_sink, sid))
            tasker._ocr_diagnostics = self
        except Exception as exc:
            self.error = type(exc).__name__
        return self

    def capture(self, tasker, task_id, entry):
        """Owner safe point after native job completion; detach from SDK memory."""
        with self.lock:
            ids = list(dict.fromkeys(row for row in self.ids if row[0] == task_id))[-2:]
        if not ids:
            ids = [(task_id, None)]
        for tid, rid in ids:
            row = dict(schema=SCHEMA, owner=self.owner, run_id=self.run_id,
                       task_id=tid, reco_id=rid, entry=entry, resource_hashes=self.hashes,
                       purpose='diagnostic_only', image_status='missing', image_reason='no_recognition_id')
            frame = None
            try:
                detail = tasker.get_recognition_detail(rid) if rid else None
                if detail is not None:
                    row.update(node=detail.name, algorithm=detail.algorithm, hit=detail.hit,
                               recognition=detail.raw_detail)
                    node = tasker.resource.get_node_data(detail.name)
                    row['pipeline'] = node
                    row['roi'] = (node or {}).get('recognition', {}).get('param', {}).get('roi') if isinstance((node or {}).get('recognition'), dict) else (node or {}).get('roi')
                    if detail.raw_image is not None and detail.raw_image.size:
                        frame = detail.raw_image.copy()
                        row.update(image_status='pending', image_reason=None)
                    else:
                        row['image_reason'] = 'native_image_evicted_or_unavailable'
                elif rid:
                    row['image_reason'] = 'native_detail_unavailable'
            except Exception as exc:
                row['image_reason'] = 'native_query_' + type(exc).__name__
            self.pending.append((row, frame))

    def finish(self, failed):
        """Only a terminal Owner failure may persist the last two observations."""
        for remove, sid in reversed(self.sinks):
            try:
                remove(sid)
            except Exception:
                pass
        self.sinks.clear()
        if not failed:
            self.pending.clear()
            return
        if not self.pending:
            self.pending.append((dict(schema=SCHEMA, owner=self.owner, run_id=self.run_id,
                purpose='diagnostic_only', resource_hashes=self.hashes, image_status='missing',
                image_reason=self.error or 'no_failed_native_task'), None))
        work = Queue(maxsize=2)
        for item in self.pending:
            try:
                work.put_nowait(item)
            except Full:
                logging.warning('OCR diagnostics queue full; image omitted')
        self.pending.clear()
        worker = Thread(target=self._write, args=(work,), daemon=True, name='ocr-failure-writer')
        worker.start()
        worker.join(timeout=.8)
        if worker.is_alive():
            logging.warning('OCR diagnostics writer still pending; process exit may leave missing evidence')

    def _write(self, work):
        import cv2
        used = 0
        while not work.empty():
            row, frame = work.get_nowait()
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                directory = self.root / self.run_id
                directory.mkdir(exist_ok=True)
                stem = uuid4().hex
                row['images'] = {}
                try:
                    with _quota_lock(self.root / '.quota-lock'):
                        total = sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
                        used = sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
                        images = {}
                        if frame is not None:
                            ok, encoded = cv2.imencode('.png', frame)
                            if not ok:
                                raise ValueError('png_encode_failed')
                            images['frame'] = encoded.tobytes()
                            roi = row.get('roi')
                            if isinstance(roi, list) and len(roi) == 4 and all(type(n) is int for n in roi):
                                x, y, w, h = roi
                                if x >= 0 and y >= 0 and w > 0 and h > 0 and x+w <= frame.shape[1] and y+h <= frame.shape[0]:
                                    ok, crop = cv2.imencode('.png', frame[y:y+h, x:x+w])
                                    if not ok:
                                        raise ValueError('crop_encode_failed')
                                    images['roi'] = crop.tobytes()
                            size = sum(map(len, images.values()))
                            if used + size > RUN_BYTES or total + size + 65536 > TOTAL_BYTES:
                                row.update(image_status='missing', image_reason='quota_exhausted')
                                images.clear()
                                logging.warning('OCR diagnostics quota exhausted; no images saved')
                        for kind, data in images.items():
                            name = stem + '-' + kind + '.png'
                            (directory / name).write_bytes(data)
                            row['images'][kind] = dict(path=name, sha256=hashlib.sha256(data).hexdigest())
                            used += len(data)
                        if images:
                            row['image_status'] = 'saved'
                except Exception as exc:
                    row.update(image_status='missing', image_reason='write_' + type(exc).__name__)
                    logging.warning('OCR diagnostics image missing: %s', type(exc).__name__)
                (directory / (stem + '.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
            except Exception as exc:
                # Even metadata IO failures must not change the task outcome.
                logging.warning('OCR diagnostics metadata missing: %s', type(exc).__name__)
