"""Display-only events and bounded player pictures; never an execution receipt."""
from datetime import datetime, timezone
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
_business_scenes = frozenset({
    'map', 'shop', 'shop_confirmation', 'shop_complete', 'shop_exit_confirmation',
    'team', 'battle', 'normal_challenge', 'ex_challenge', 'boss_challenge', 'move_confirmation',
    'battle_victory', 'battle_defeat', 'boss_summary', 'score_result', 'final_clear', 'chest',
    'joined', 'item_reward', 'role_reward', 'free_role_reward', 'link_reward', 'relic_reward', 'event',
})


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


def observe(receipt, entry, recognition_frames=()):
    try:
        with _lock:
            if _state is None:
                return
            rows = receipt.get('nodes', [])
            matched = rows[-1].get('name') if rows else None
            node = matched or entry
            success = receipt.get('native_ok') is True
            task_id = receipt.get('task_id')
            _state['node'] = None
            _state.pop('failure_picture', None)
            after_popup = entry == 'LabyGameSessionAfterTitleObserved' and 'startup_picture' in _state
            if success or not after_popup:
                _state.pop('startup_picture', None)
            if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', node or '') is None:
                return
            if success and matched:
                _state['node'] = node
                if _state['function'] == 'labyrinth' and node == 'LabySceneObserved':
                    kind = _business_scene_kind(receipt) if entry == node else None
                    if kind is None:
                        _state['node'] = None
                    else:
                        _remember_business_picture(_state, receipt, entry, recognition_frames, kind)
                if node in ('LabyClientNoticeCloseOnce', 'LabyClientAnnouncementCloseOnce'):
                    # These recognitions prove the popup before the click changes the screen.
                    _state['startup_picture'] = (node, None)
                    for row, frame in reversed(recognition_frames):
                        if (row.get('task_id') == task_id and row.get('node') == node
                                and row.get('hit') is True and frame is not None):
                            _state['startup_picture'] = (node, frame)
                            break
            elif (not success and _state['function'] == 'labyrinth' and type(task_id) is int and task_id > 0
                  and (after_popup or not _private_page.search(entry) and not _private_page.search(node))):
                # Only the last recognition in this task can identify the failure frame.
                latest = next(((row, frame) for row, frame in reversed(recognition_frames)
                               if row.get('task_id') == task_id and row.get('entry') == entry), None)
                caption = f'识别失败原帧（{entry}, task={task_id}'
                frame = None
                if latest:
                    row, original = latest
                    if (row.get('node') == entry and row.get('hit') is False
                            and type(row.get('reco_id')) is int and row['reco_id'] > 0):
                        caption += f", reco={row['reco_id']}"
                        frame = original
                _state['failure_picture'] = (caption + '）', frame)
            stopped = (receipt.get('native_evidence') or {}).get('stop_action')
            stop_note = f' stop={stopped}' if isinstance(stopped, str) and re.fullmatch(
                r'[A-Za-z][A-Za-z0-9_]{0,127}', stopped) else ''
            _emit('native', text=f"task={task_id} node={node} "
                f"success={success}{stop_note}{_action_output(rows)}")
    except Exception:
        pass


def _business_scene_kind(receipt):
    from .labyrinth.batch import BatchStopped
    from .labyrinth.scene import scene_readback
    try:
        kind = scene_readback(receipt).kind
    except BatchStopped:
        return None
    return kind if kind in _business_scenes else None


def _remember_business_picture(state, receipt, entry, recognition_frames, kind):
    task_id = receipt.get('task_id')
    latest = next(((row, frame) for row, frame in reversed(recognition_frames)
                   if row.get('task_id') == task_id and row.get('entry') == entry
                   and row.get('node') == entry), None)
    if latest is None:
        return
    row, frame = latest
    reco_id = row.get('reco_id')
    if row.get('hit') is not True or type(reco_id) is not int or reco_id <= 0 or frame is None or not frame.size:
        return
    # The recognition diagnostic owns a detached copy; keep just one frame.
    timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    caption = f'黎明界前序业务画面（{kind}，{entry}，task={task_id}，reco={reco_id}，记录于 {timestamp}）'
    state['business_picture'] = (caption, frame)


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
    """Use existing run evidence; never request a new screenshot during teardown."""
    try:
        with _lock:
            state = _state
            if state is None:
                return
            popup = state.get('startup_picture')
            failure = state.get('failure_picture')
            previous = state.get('business_picture') if outcome != 'complete' else None
            saved = False
            if popup:
                source, frame = popup
                saved = _save_picture(state, frame, outcome, f'弹窗关闭前（{source}）', '未取得弹窗识别原帧')
            if failure and outcome != 'complete':
                caption, frame = failure
                saved = _save_picture(state, frame, outcome, caption, '未取得本次识别的失败原帧') or saved
            if popup or failure:
                if not saved and previous:
                    caption, frame = previous
                    _save_picture(state, frame, outcome, caption, '前序业务画面不可用')
                return
            node = state.get('node')
            frame = None
            reason = ('本次黎明界运行没有可分享的业务画面' if state['function'] == 'labyrinth'
                      else '没有可分享的业务画面')
            if node and not _private_page.search(node):
                try:
                    frame = tasker.controller.cached_image
                    reason = '控制器截图缓存为空'
                except Exception:
                    reason = '无法读取控制器截图缓存'
            if previous and (frame is None or not frame.size):
                caption, frame = previous
                _save_picture(state, frame, outcome, caption, '前序业务画面不可用')
                return
            caption = ''
            if state['function'] == 'labyrinth' and frame is not None and frame.size:
                timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
                caption = f'收尾读取的控制器缓存画面（供对照，最后节点 {node}，读取于 {timestamp}）'
            _save_picture(state, frame, outcome, caption, reason)
    except Exception:
        text('截图未保存：缓存画面或诊断目录暂不可用。')


def _save_picture(state, frame, outcome, caption, reason):
    try:
        if frame is None or not frame.size:
            _emit('text', text=f'{caption}截图未保存：{reason}。')
            return
        if frame.ndim != 3 or frame.shape[2] not in (3, 4) or frame.dtype != 'uint8':
            _emit('text', text=f'截图未保存：不支持的缓存画面格式 {frame.shape} {frame.dtype}。')
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
        if caption:
            _emit('text', text=f'{caption}：{path.name}')
        _emit('image', name=path.name, sha256=hashlib.sha256(data).hexdigest(), outcome=outcome)
        return True
    except Exception:
        text('截图未保存：缓存画面或诊断目录暂不可用。')
