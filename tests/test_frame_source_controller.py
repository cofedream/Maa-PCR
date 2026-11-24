"""Experimental latest-frame controller: pure helpers only; no real device or stream.

The core logic (source validation, frame copying, coordinate forwarding) is tested
without the native binding. The single adapter test constructs Maa's
``CustomController`` only when the binding is importable, and skips otherwise.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location(
    'frame_source_controller', ROOT / 'scripts/experimental/frame_source_controller.py')
frame_controller = module_from_spec(SPEC)
SPEC.loader.exec_module(frame_controller)


def numpy():
    return pytest.importorskip('numpy')


def frame(width=1280, height=720, channels=3):
    return numpy().zeros((height, width, channels), dtype='uint8')


def sources(**overrides):
    values = dict(click_fn=lambda x, y: True, swipe_fn=lambda *args: True,
                  start_app_fn=lambda intent: True, stop_app_fn=lambda intent: True)
    values.update(overrides)
    return values


def forwarder(**overrides):
    values = sources(**overrides)
    if 'screencap_fn' not in overrides and 'frame_source' not in overrides:
        values['screencap_fn'] = lambda: frame()
    return frame_controller.FrameForwarder(**values)


def test_exactly_one_frame_source_is_required():
    with pytest.raises(ValueError, match='exactly one'):
        frame_controller.validate_sources(**sources())
    both = sources(screencap_fn=lambda: frame(), frame_source=frame_controller.PushedFrameSource())
    with pytest.raises(ValueError, match='exactly one'):
        frame_controller.validate_sources(**both)


@pytest.mark.parametrize('name', ['click_fn', 'swipe_fn', 'start_app_fn', 'stop_app_fn'])
def test_every_required_input_source_must_be_callable(name):
    with pytest.raises(ValueError, match=f'{name} is required'):
        frame_controller.validate_sources(**sources(screencap_fn=lambda: frame(), **{name: None}))
    with pytest.raises(ValueError, match=f'{name} must be callable'):
        frame_controller.validate_sources(**sources(screencap_fn=lambda: frame(), **{name: 'not-callable'}))


@pytest.mark.parametrize('name', ['screencap_fn', 'shell_fn', 'connect_fn', 'request_uuid_fn'])
def test_optional_sources_reject_non_callables(name):
    values = sources(screencap_fn=lambda: frame())
    values[name] = object()
    with pytest.raises(ValueError, match=f'{name} must be callable'):
        frame_controller.validate_sources(**values)


def test_frame_source_object_must_expose_latest():
    with pytest.raises(ValueError, match='latest'):
        frame_controller.validate_sources(**sources(frame_source=object()))


def test_forwarder_returns_a_defensive_copy_of_the_latest_frame():
    image = frame()
    forwarder_instance = forwarder(screencap_fn=lambda: image)
    first = forwarder_instance.screencap()
    second = forwarder_instance.screencap()
    assert first is not image and second is not image and first is not second
    assert frame_controller.frame_size(first) == (1280, 720)
    first[0, 0] = [255, 0, 0]
    assert image[0, 0].tolist() == [0, 0, 0]


def test_pushed_source_streams_the_newest_frame_and_handles_no_frame_yet():
    source = frame_controller.PushedFrameSource()
    forwarder_instance = forwarder(frame_source=source)
    with pytest.raises(frame_controller.FrameUnavailable):
        source.latest()
    with pytest.raises(frame_controller.FrameUnavailable):
        forwarder_instance.screencap()
    old = frame(width=640, height=360)
    assert source.push(old) == 1
    newest = frame(width=800, height=600)
    assert source.push(newest) == 2
    assert forwarder_instance.screencap().shape == (600, 800, 3)
    with pytest.raises(ValueError):
        source.push(None)


def test_forwarder_rejects_missing_or_invalid_frames():
    with pytest.raises(frame_controller.FrameUnavailable):
        forwarder(screencap_fn=lambda: None).screencap()
    with pytest.raises(TypeError):
        frame_controller.copy_frame(object())
    with pytest.raises(ValueError, match='2D or 3D'):
        frame_controller.frame_size(object())
    with pytest.raises(ValueError, match='positive integers'):
        frame_controller.frame_size(frame(width=0))
    with pytest.raises(ValueError, match='channels'):
        frame_controller.frame_size(frame(channels=2))

    class SelfCopying:
        shape = (720, 1280, 3)

        def copy(self):
            return self

    with pytest.raises(RuntimeError, match='distinct frame'):
        forwarder(screencap_fn=SelfCopying).screencap()


def test_forwarder_forwards_click_and_swipe_coordinates_exactly():
    clicks, swipes = [], []
    forwarder_instance = forwarder(click_fn=lambda x, y: clicks.append((x, y)) or True,
                                   swipe_fn=lambda *args: swipes.append(args) or True)
    assert forwarder_instance.click(12, 34) is True
    assert forwarder_instance.swipe(1, 2, 3, 4, 500) is True
    assert clicks == [(12, 34)] and swipes == [(1, 2, 3, 4, 500)]
    for invalid in (-1, 1.5, '7', True):
        with pytest.raises(ValueError):
            forwarder_instance.click(invalid, 0)
        with pytest.raises(ValueError):
            forwarder_instance.swipe(0, 0, invalid, 0, 0)
    with pytest.raises(ValueError):
        forwarder_instance.swipe(0, 0, 1, 1, -1)


def test_forwarder_connect_uuid_start_stop_and_shell_are_injectable():
    intents = []
    forwarder_instance = forwarder(
        connect_fn=lambda: False, request_uuid_fn=lambda: 'stream-42',
        start_app_fn=lambda intent: intents.append(('start', intent)) or True,
        stop_app_fn=lambda intent: intents.append(('stop', intent)) or True,
        shell_fn=lambda cmd, timeout: f'{cmd}:{timeout}')
    assert forwarder_instance.connect() is False
    assert forwarder_instance.request_uuid() == 'stream-42'
    assert forwarder_instance.start_app('com.bilibili.priconne') is True
    assert forwarder_instance.stop_app('com.bilibili.priconne') is True
    assert intents == [('start', 'com.bilibili.priconne'), ('stop', 'com.bilibili.priconne')]
    assert forwarder_instance.shell('echo hi', 1000) == 'echo hi:1000'
    for empty in ('', None, 7):
        with pytest.raises(ValueError):
            forwarder_instance.start_app(empty)
        with pytest.raises(ValueError):
            forwarder_instance.stop_app(empty)
    with pytest.raises(ValueError):
        forwarder_instance.shell('', 1000)
    with pytest.raises(ValueError):
        forwarder_instance.shell('echo', -1)


def test_default_connect_uuid_and_shell_need_no_injection():
    image = frame()
    forwarder_instance = forwarder(screencap_fn=lambda: image)
    assert forwarder_instance.connect() is True
    assert forwarder_instance.request_uuid() == frame_controller.UUID
    assert forwarder_instance.shell('echo', 0) is None


def test_unsupported_native_input_surfaces_fail_closed():
    forwarder_instance = forwarder()
    assert forwarder_instance.touch_down(0, 1, 2, 1) is False
    assert forwarder_instance.touch_move(0, 1, 2, 1) is False
    assert forwarder_instance.touch_up(0) is False
    assert forwarder_instance.click_key(4) is False
    assert forwarder_instance.input_text('x') is False
    assert forwarder_instance.key_down(4) is False
    assert forwarder_instance.key_up(4) is False
    assert forwarder_instance.scroll(1, 1) is False
    assert forwarder_instance.relative_move(1, 1) is False


def test_adapter_validates_before_allocating_the_native_controller():
    with pytest.raises(ValueError, match='click_fn'):
        frame_controller.LatestFrameController(**sources(click_fn='not-callable',
                                                         screencap_fn=lambda: frame()))
    with pytest.raises(ValueError, match='exactly one'):
        frame_controller.LatestFrameController(**sources())
    with pytest.raises(ValueError, match='click_fn is required'):
        frame_controller.LatestFrameController(screencap_fn=lambda: frame())


def test_adapter_is_a_custom_controller_when_the_binding_is_available():
    if frame_controller.MaaCustomController is None:
        pytest.skip('maafw Python binding unavailable')
    assert issubclass(frame_controller.LatestFrameController, frame_controller.MaaCustomController)


def test_adapter_delegates_to_its_core_when_native_construction_is_available():
    if frame_controller.MaaCustomController is None:
        pytest.skip('maafw Python binding unavailable')
    clicks = []
    source = frame_controller.PushedFrameSource()
    try:
        controller = frame_controller.LatestFrameController(
            frame_source=source,
            **sources(click_fn=lambda x, y: clicks.append((x, y)) or True,
                      connect_fn=lambda: True, request_uuid_fn=lambda: 'adapter-uuid'))
    except Exception as exc:  # native allocation may fail on a bare host; never a hard block
        pytest.skip(f'maafw CustomController cannot be constructed here: {exc}')
    source.push(frame(width=64, height=32))
    assert controller.connect() is True
    assert controller.connected() is True
    assert controller.request_uuid() == 'adapter-uuid'
    assert controller.get_features() == 0
    assert controller.screencap().shape == (32, 64, 3)
    assert controller.click(5, 6) is True and clicks == [(5, 6)]
    assert controller.swipe(0, 0, 1, 1, 10) is True
    assert controller.shell('echo', 10) is None
    assert controller.touch_down(0, 0, 0, 1) is False


def test_help_exits_zero_and_documents_the_experimental_boundary(capsys):
    with pytest.raises(SystemExit) as raised:
        frame_controller.main(['--help'])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert 'Experimental' in output and 'CustomController' in output


def test_experimental_controller_is_not_referenced_by_production_code():
    offenders = [str(path.relative_to(ROOT)) for path in (ROOT / 'src').rglob('*.py')
                 if 'frame_source_controller' in path.read_text(encoding='utf-8')]
    assert offenders == []
