"""Short event-list boundary checks through Maa, with no device or game input."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from pcr_maa import native
from pcr_maa.event_native import EventPage
from test_pixel_free_resources import native_probe


TAIL = [60, 448, 1140, 49]


@pytest.mark.parametrize('gray', [240, 244, 247, 250])
def test_complete_neutral_short_tail(gray):
    image = np.full((720, 1280, 3), gray, dtype=np.uint8)
    assert tail_hit(image, f'neutral-{gray}')


def tail_hit(image, label):
    def observe(context, argv):
        result = context.run_recognition('EventShortBlankTail', argv.image, {
            'EventShortBlankTail': {'roi': TAIL, 'count': TAIL[2] * TAIL[3]}})
        return bool(result and result.hit)
    return native_probe(image, observe, 'event-short-tail-' + label)


@pytest.mark.parametrize('fault', ['dark', 'below_range', 'above_range', 'white',
                                   'tinted', 'extra_row', 'one_dark_pixel', 'one_white_pixel'])
def test_short_tail_requires_the_entire_blank_surface(fault):
    image = np.full((720, 1280, 3), 247, dtype=np.uint8)
    if fault in ('dark', 'below_range', 'above_range', 'white'):
        image[:] = {'dark': 0, 'below_range': 239, 'above_range': 251, 'white': 255}[fault]
    elif fault == 'tinted':
        image[:] = [240, 240, 255]
    elif fault == 'extra_row':
        image[455:490, 60:1200] = 255
    else:
        image[490, 1000] = 0 if fault == 'one_dark_pixel' else 255
    assert not tail_hit(image, fault)


@pytest.mark.private_replay
@pytest.mark.parametrize('case', ['original', 'gray_240', 'gray_244', 'gray_250',
                                  'extra_row', 'clipped_tail', 'missing_title',
                                  'missing_counter', 'unchecked', 'missing_identity'])
def test_saved_story_list_keeps_row_and_boundary_guards(case):
    project = Path(os.environ.get('PCR_FEEDBACK_PROJECT', str(native.ROOT)))
    path = project / '.runtime/pilot-readiness-20261002/live-event-preview-20261002/05-checked-list/after.png'
    if not path.is_file():
        pytest.skip('Private original frame unavailable; no device fallback')
    image = native.read_image(path)
    if case.startswith('gray_'):
        image[448:497, 60:1200] = int(case.split('_')[1])
    elif case == 'extra_row':
        image[455:490, 60:1200] = 255
    elif case == 'clipped_tail':
        image[495:497, 60:1200] = 0
    elif case == 'missing_title':
        image[195:228, 65:530] = 255
    elif case == 'missing_counter':
        image[230:263, 585:650] = 255
    elif case == 'unchecked':
        image[193:269, 1098:1178] = 255
    # This synthetic identity only selects the story reader, not a live event.
    identity = dict(layout='story_event_h', period='举办时间：10/0112:00~10/0704:59')
    reader = EventPage(None if case == 'missing_identity' else identity)

    def observe(context, argv):
        argv.custom_recognition_param = json.dumps('short')
        page = reader.analyze(context, argv)
        if page is None:
            return None
        return json.loads(page.detail) if isinstance(page.detail, str) else page.detail

    page = native_probe(image, observe, 'event-short-list-' + case)
    if case == 'original' or case.startswith('gray_'):
        assert page is not None and not page['gaps']
        assert [row['title'] for row in page['rows']] == [
            '活动关卡H-3', '活动关卡H-2', '活动关卡H-1']
        assert all(row['checked'] and row['remaining'] == row['limit'] == 3
                   and row['category'] == '活动' and row['difficulty'] == '困难'
                   for row in page['rows'])
        assert page['evidence']['EventShortBlankTail']['hit']
    else:
        assert page is None


@pytest.mark.private_replay
@pytest.mark.parametrize('gray', [247, 244])
def test_saved_short_list_completes_without_input(gray):
    from pcr_maa.event_native import NativeEvent
    from pcr_maa.replay import SavedImages

    project = Path(os.environ.get('PCR_FEEDBACK_PROJECT', str(native.ROOT)))
    path = project / '.runtime/pilot-readiness-20261002/live-event-preview-20261002/05-checked-list/after.png'
    if not path.is_file():
        pytest.skip('Private original frame unavailable; no device fallback')
    image = native.read_image(path)
    image[448:497, 60:1200] = gray

    class NoInput(SavedImages):
        def forbidden(self, *args):
            pytest.fail('Short checked list reading must not send input')
        click = swipe = forbidden

    controller = NoInput([[image]])
    port = NativeEvent(native.bind(native.load_resource(), controller), start='event')
    port.event_identity = dict(layout='story_event_h', period='举办时间：10/0112:00~10/0704:59')
    result = port.read_list()
    assert result['native_ok'] and result['event_verified'] and result['list_complete']
    assert result['incomplete_reason'] is None and result['view'] == 'checked'
    assert len(result['rows']) == 3 and len(result['pages']) == 1
    assert result['pages'][0]['kind'] == 'short'
    assert not controller.clicks and not controller.swipes
