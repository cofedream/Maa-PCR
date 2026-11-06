"""Display-only events and bounded player pictures; never an execution receipt."""
import hashlib
import json
from pathlib import Path
import re
import threading
import queue
import time
from uuid import uuid4

PREFIX = 'pcr-player-log:'
QUOTA = 16 * 1024 * 1024
_state = None
_lock = threading.RLock()
_output_lock = threading.Lock()
_private_page = re.compile(r'login|captcha|password|authenticat|profile|playe?r?identity|title', re.I)


def configure(root, run_id, function, emit=None):
    global _state
    if not re.fullmatch(r'[0-9a-f]{32}', run_id) or function not in ('daily', 'clan', 'labyrinth'):
        raise ValueError('invalid_player_log_identity')
    close()
    root = Path(root).resolve()
    state = dict(root=root, run_id=run_id, function=function, emit=emit, node=None, pending=queue.Queue(maxsize=128))
    def pump():
        while True:
            try: row = state['pending'].get(timeout=.1)
            except queue.Empty:
                if state.get('closed'): return
                continue
            try:
                if row is None: return
                if state['emit'] is not None:
                    state['emit'](dict(kind='player_log', **row))
                else:
                    import sys
                    write_protocol(sys.stdout, PREFIX + json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
            except Exception:
                pass
            finally:
                state['pending'].task_done()
    _state = state
    threading.Thread(target=pump, daemon=True, name='player-log-output').start()


def write_protocol(output, line):
    with _output_lock:
        output.write(line)
        output.flush()


def flush(timeout=.5):
    state = _state
    end = time.monotonic() + timeout
    while state is not None and state['pending'].unfinished_tasks and time.monotonic() < end:
        time.sleep(.005)


def close():
    global _state
    state = _state
    if state is None: return
    flush()
    state['closed'] = True
    _state = None
    try: state['pending'].put_nowait(None)
    except queue.Full: pass


def _emit(event, **values):
    state = _state
    if state is None:
        return
    row = dict(run_id=state['run_id'], function=state['function'], event=event, **values)
    try:
        state['pending'].put_nowait(row)
    except queue.Full:
        if event != 'native':
            try:
                state['pending'].get_nowait(); state['pending'].task_done()
                state['pending'].put_nowait(row)
            except (queue.Empty, queue.Full): pass


def text(message):
    try:
        with _lock:
            _emit('text', text=str(message)[:1200])
    except Exception:
        pass


def observe(receipt, entry):
    try:
        with _lock:
            if _state is None:
                return
            rows = receipt.get('nodes', [])
            node = rows[-1].get('name', entry) if rows else entry
            if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', node or '') is None:
                return
            _state['node'] = node if receipt.get('native_ok') is True else None
            _emit('native', text=f"task={receipt.get('task_id')} node={node} "
                f"success={receipt.get('native_ok') is True}{_action_output(rows)}")
    except Exception:
        pass


def _action_output(rows):
    """Keep the command's own words: a failed action often ACKs anyway."""
    try:
        action = rows[-1].get('action') if rows else None
        raw = action.get('raw') if type(action) is dict else None
        output = raw.get('output') if type(raw) is dict else None
        if type(output) is not str or not output:
            return ''
        return ' output=' + re.sub(r'\s+', ' ', output)[:300]
    except Exception:
        return ''


def capture(tasker, outcome):
    """Only the existing cached frame, at the executor's safe boundary."""
    try:
        with _lock:
            state = _state
            if state is None:
                return
            node = state.get('node')
            if not node or _private_page.search(node):
                _emit('text', text='截图未保存：没有可分享的业务画面。')
                return
            cached = tasker.controller.cached_image
            frame = cached.copy() if cached is not None else None
            if frame is None or frame.shape != (720, 1280, 3):
                _emit('text', text='截图未保存：当前运行没有有效的缓存画面。')
                return
            import cv2
            ok, encoded = cv2.imencode('.png', frame)
            if not ok:
                _emit('text', text='截图未保存：PNG 编码失败。')
                return
            data = encoded.tobytes()
            if len(data) > 8 * 1024 * 1024:
                _emit('text', text='截图未保存：单张 PNG 超过诊断缓存允许的大小。')
                return
            area = state['root'] / '.runtime/player-logs' / state['function']
            directory = area / state['run_id']
            directory.mkdir(parents=True, exist_ok=True)
            if directory.resolve() != directory or any(p.is_symlink() for p in (area, directory, area.parent)):
                return
            pictures = sorted((p for p in area.glob('*/image-*.png')
                               if re.fullmatch(r'[0-9a-f]{32}', p.parent.name)
                               and p.resolve() == p and p.is_file()), key=lambda p: p.stat().st_mtime)
            owned = lambda: sum(p.stat().st_size for d in area.iterdir()
                if d.is_dir() and re.fullmatch(r'[0-9a-f]{32}', d.name) and d.resolve() == d
                for p in d.iterdir() if p.is_file() and p.resolve() == p
                and (p.name in ('events.log', 'native.log', 'manifest.json')
                     or re.fullmatch(r'image-[0-9a-f]{32}\.png', p.name)))
            for old in pictures:
                if owned() + len(data) <= QUOTA:
                    break
                old.unlink()
            if owned() + len(data) > QUOTA:
                _emit('text', text='截图未保存：用户日志缓存已达到 16 MiB。')
                return
            path = directory / ('image-' + uuid4().hex + '.png')
            temporary = path.with_suffix('.png.tmp')
            with temporary.open('xb') as output:
                output.write(data)
            temporary.replace(path)
            _emit('image', name=path.name, sha256=hashlib.sha256(data).hexdigest(), outcome=outcome)
    except Exception:
        text('截图未保存：缓存画面或诊断目录暂不可用。')
