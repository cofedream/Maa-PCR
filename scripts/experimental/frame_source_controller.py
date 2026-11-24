"""Experimental latest-frame ``CustomController`` skeleton. NEVER import from production.

This module is a design seam only. It is not wired into ``src/pcr_maa``, the GUI,
or any schedule, and it changes nothing about the production capture path. It
imports no scrcpy, PyAV, H.264 decoder, or any new dependency: a future streaming
frame source only has to implement ``FrameSource.latest()`` and be injected here.

Hard limits that do not disappear with a faster frame source:
- **Lossy frames.** Streaming sources (scrcpy/H.264) do not preserve deterministic
  pixels; template/colour thresholds, same-frame icon identity and saved-frame
  replay evidence are built on lossless capture.
- **Single display.** One stream session binds one display, while production
  capture binds a physical viewport and input binds a logical display, proven
  separately. A custom controller cannot reproduce that split.
- **Staleness.** A "latest frame" may predate the click that triggered it;
  ``ADB screencap`` samples at call time instead.
- **Evidence.** A custom controller changes what ``capture-performance`` can
  observe, so it cannot replace the formal ADB path.

Maa's Python binding exposes the C ``MaaCustomController`` callbacks as
``maa.controller.CustomController``; ``MaaCustomController`` below is that name.
"""
from __future__ import annotations

import argparse
import threading

try:  # The binding is optional for tests and for pure helper use.
    from maa.controller import CustomController as MaaCustomController
except Exception:  # pragma: no cover - native binding missing on the host
    MaaCustomController = None

UUID = 'experimental-latest-frame-source'
EXPERIMENTAL_NOTICE = (
    'Experimental LatestFrameController skeleton: design seam only, no device/network IO.\n'
    'Lossy frames, single display and stale "latest frame" semantics mean it cannot\n'
    'replace the formal ADB capture path. Not imported by production code.'
)


class FrameUnavailable(RuntimeError):
    """No frame has been produced yet."""


def validate_sources(*, screencap_fn=None, frame_source=None, click_fn=None, swipe_fn=None,
                     start_app_fn=None, stop_app_fn=None, shell_fn=None,
                     connect_fn=None, request_uuid_fn=None):
    """Validate injected sources; return them normalised. Pure, no Maa dependency."""
    if (screencap_fn is None) == (frame_source is None):
        raise ValueError('provide exactly one of screencap_fn or frame_source')
    if frame_source is not None and not callable(getattr(frame_source, 'latest', None)):
        raise ValueError('frame_source must implement latest()')
    if screencap_fn is not None and not callable(screencap_fn):
        raise ValueError('screencap_fn must be callable')
    required = {'click_fn': click_fn, 'swipe_fn': swipe_fn,
                'start_app_fn': start_app_fn, 'stop_app_fn': stop_app_fn}
    fillers = {'shell_fn': shell_fn, 'connect_fn': connect_fn,
               'request_uuid_fn': request_uuid_fn}
    for name, fn in required.items():
        if fn is None:
            raise ValueError(f'{name} is required')
    for name, fn in {**required, **fillers}.items():
        if fn is not None and not callable(fn):
            raise ValueError(f'{name} must be callable')
    return dict(screencap_fn=screencap_fn, frame_source=frame_source, click_fn=click_fn,
                swipe_fn=swipe_fn, start_app_fn=start_app_fn, stop_app_fn=stop_app_fn,
                shell_fn=shell_fn, connect_fn=connect_fn, request_uuid_fn=request_uuid_fn)


def copy_frame(frame):
    """Return a distinct defensive copy; consumers must never mutate the source frame."""
    if frame is None:
        raise FrameUnavailable('no frame has been produced yet')
    copy = getattr(frame, 'copy', None)
    if not callable(copy):
        raise TypeError('frame must provide copy()')
    duplicate = copy()
    if duplicate is None or duplicate is frame:
        raise RuntimeError('frame copy() must return a distinct frame')
    return duplicate


def frame_size(frame):
    """Validate a Maa screenshot frame and return ``(width, height)``."""
    shape = getattr(frame, 'shape', None)
    if not isinstance(shape, tuple) or len(shape) not in (2, 3):
        raise ValueError('frame must expose a 2D or 3D shape tuple')
    if len(shape) == 3 and shape[2] not in (1, 3, 4):
        raise ValueError('frame must have 1, 3 or 4 channels')
    height, width = shape[:2]
    if type(height) is not int or type(width) is not int or height <= 0 or width <= 0:
        raise ValueError('frame dimensions must be positive integers')
    return width, height


def as_coordinate(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f'{name} must be a non-negative integer')
    return value


def forward_click(click_fn, x, y):
    """Validate coordinates and forward them to the injected click source."""
    return bool(click_fn(as_coordinate(x, 'x'), as_coordinate(y, 'y')))


def forward_swipe(swipe_fn, x1, y1, x2, y2, duration):
    """Validate a swipe and forward it to the injected swipe source."""
    points = [as_coordinate(x1, 'x1'), as_coordinate(y1, 'y1'),
              as_coordinate(x2, 'x2'), as_coordinate(y2, 'y2')]
    if type(duration) is not int or duration < 0:
        raise ValueError('duration must be a non-negative integer')
    return bool(swipe_fn(*points, duration))


class FrameSource:
    """Seam for a future streaming source: implement ``latest()`` only."""

    def latest(self):
        raise NotImplementedError


class CallableFrameSource(FrameSource):
    """Frame source backed by ``screencap_fn()`` (one sample per call)."""

    def __init__(self, screencap_fn):
        if not callable(screencap_fn):
            raise ValueError('screencap_fn must be callable')
        self._screencap_fn = screencap_fn

    def latest(self):
        return self._screencap_fn()


class PushedFrameSource(FrameSource):
    """Frame buffer for a streaming producer; the producer pushes decoded frames.

    The producer owns the pushed frame and must not mutate it afterwards; the
    controller copies the newest frame before handing it to Maa.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._generation = 0

    def push(self, frame):
        if frame is None:
            raise ValueError('frame must not be None')
        with self._lock:
            self._frame = frame
            self._generation += 1
            return self._generation

    @property
    def generation(self):
        with self._lock:
            return self._generation

    def latest(self):
        with self._lock:
            frame = self._frame
        if frame is None:
            raise FrameUnavailable('no frame has been pushed yet')
        return frame


class FrameForwarder:
    """Pure controller core: source validation, frame copying, coordinate forwarding.

    It has no Maa dependency and can be unit-tested without the native binding.
    ``LatestFrameController`` is only the ``CustomController`` adapter around it.
    """

    def __init__(self, *, screencap_fn=None, frame_source=None, click_fn=None, swipe_fn=None,
                 start_app_fn=None, stop_app_fn=None, shell_fn=None,
                 connect_fn=None, request_uuid_fn=None):
        sources = validate_sources(
            screencap_fn=screencap_fn, frame_source=frame_source, click_fn=click_fn,
            swipe_fn=swipe_fn, start_app_fn=start_app_fn, stop_app_fn=stop_app_fn,
            shell_fn=shell_fn, connect_fn=connect_fn, request_uuid_fn=request_uuid_fn)
        self._frame_source = sources['frame_source']
        if self._frame_source is None:
            self._frame_source = CallableFrameSource(sources['screencap_fn'])
        self._click_fn = sources['click_fn']
        self._swipe_fn = sources['swipe_fn']
        self._start_app_fn = sources['start_app_fn']
        self._stop_app_fn = sources['stop_app_fn']
        self._shell_fn = sources['shell_fn']
        self._connect_fn = sources['connect_fn']
        self._request_uuid_fn = sources['request_uuid_fn']

    def connect(self):
        return True if self._connect_fn is None else bool(self._connect_fn())

    def request_uuid(self):
        if self._request_uuid_fn is None:
            return UUID
        value = self._request_uuid_fn()
        return value if isinstance(value, str) and value else str(value)

    def screencap(self):
        frame = copy_frame(self._frame_source.latest())
        frame_size(frame)  # fail this capture on an empty/invalid streamed frame
        return frame

    def click(self, x, y):
        return forward_click(self._click_fn, x, y)

    def swipe(self, x1, y1, x2, y2, duration):
        return forward_swipe(self._swipe_fn, x1, y1, x2, y2, duration)

    def start_app(self, intent):
        if not isinstance(intent, str) or not intent:
            raise ValueError('intent must be a non-empty string')
        return bool(self._start_app_fn(intent))

    def stop_app(self, intent):
        if not isinstance(intent, str) or not intent:
            raise ValueError('intent must be a non-empty string')
        return bool(self._stop_app_fn(intent))

    def shell(self, cmd, timeout):
        if self._shell_fn is None:
            return None
        if not isinstance(cmd, str) or not cmd:
            raise ValueError('cmd must be a non-empty string')
        if type(timeout) is not int or timeout < 0:
            raise ValueError('timeout must be a non-negative integer')
        return self._shell_fn(cmd, timeout)

    # Unsupported input surfaces fail closed; faking input would be worse than False.
    def touch_down(self, contact, x, y, pressure):
        return False

    def touch_move(self, contact, x, y, pressure):
        return False

    def touch_up(self, contact):
        return False

    def click_key(self, keycode):
        return False

    def input_text(self, text):
        return False

    def key_down(self, keycode):
        return False

    def key_up(self, keycode):
        return False

    def scroll(self, dx, dy):
        return False

    def relative_move(self, dx, dy):
        return False


class _UnavailableControllerBase:
    """Stand-in used when the maafw Python binding is absent (pure helpers still work)."""

    def __init__(self):
        raise RuntimeError('maafw Python binding unavailable: LatestFrameController cannot be constructed')


_ControllerBase = MaaCustomController if MaaCustomController is not None else _UnavailableControllerBase


class LatestFrameController(_ControllerBase):
    """Experimental Maa ``CustomController`` adapter over :class:`FrameForwarder`.

    Not a production capture path. It returns the newest produced frame and copies
    it before handing it to Maa; a future streamer only replaces its ``FrameSource``.
    """

    def __init__(self, *, screencap_fn=None, frame_source=None, click_fn=None, swipe_fn=None,
                 start_app_fn=None, stop_app_fn=None, shell_fn=None,
                 connect_fn=None, request_uuid_fn=None):
        # Validate before native allocation so bad injections never create a controller.
        try:
            core = FrameForwarder(
                screencap_fn=screencap_fn, frame_source=frame_source, click_fn=click_fn,
                swipe_fn=swipe_fn, start_app_fn=start_app_fn, stop_app_fn=stop_app_fn,
                shell_fn=shell_fn, connect_fn=connect_fn, request_uuid_fn=request_uuid_fn)
        except (TypeError, ValueError):
            # A rejected injection leaves the native handle uninitialised; make the
            # base destructor harmless instead of letting it fail on missing attributes.
            self._handle = None
            self._own = False
            raise
        super().__init__()
        self.core = core

    def connect(self):
        return self.core.connect()

    def connected(self):
        return True

    def request_uuid(self):
        return self.core.request_uuid()

    def get_features(self):
        return 0

    def start_app(self, intent):
        return self.core.start_app(intent)

    def stop_app(self, intent):
        return self.core.stop_app(intent)

    def screencap(self):
        return self.core.screencap()

    def click(self, x, y):
        return self.core.click(x, y)

    def swipe(self, x1, y1, x2, y2, duration):
        return self.core.swipe(x1, y1, x2, y2, duration)

    def shell(self, cmd, timeout):
        return self.core.shell(cmd, timeout)

    def touch_down(self, contact, x, y, pressure):
        return False

    def touch_move(self, contact, x, y, pressure):
        return False

    def touch_up(self, contact):
        return False

    def click_key(self, keycode):
        return False

    def input_text(self, text):
        return False

    def key_down(self, keycode):
        return False

    def key_up(self, keycode):
        return False

    def scroll(self, dx, dy):
        return False

    def relative_move(self, dx, dy):
        return False


def build_parser():
    return argparse.ArgumentParser(
        prog='frame_source_controller.py',
        description='Experimental latest-frame CustomController skeleton (design seam only).',
        epilog=EXPERIMENTAL_NOTICE.replace('\n', ' '))


def main(argv=None):
    """Description-only entry point: ``--help`` documents the experimental boundary."""
    build_parser().parse_args(argv)
    print(EXPERIMENTAL_NOTICE)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
