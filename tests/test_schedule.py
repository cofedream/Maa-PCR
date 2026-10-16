from pcr_maa.native import ROOT, load_resource, bind, read_image, save_receipt
from pcr_maa.replay import SavedImages
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest


def frame(name):
    path = ROOT / 'tests/fixtures' / name
    if not path.is_file():
        pytest.skip('private saved-frame fixture is not distributed: ' + name)
    return read_image(path)


def test_schedule_counts_are_separate():
    from pcr_maa.schedule import inspect_schedule
    names = ['schedule-04-panel.png', 'schedule-07-notifications-middle.png',
             'schedule-08-notifications-lower.png', 'schedule-09-delegated-top.png']
    controller = SavedImages([[frame(n)] for n in names])
    result = inspect_schedule(bind(load_resource(), controller), start='schedule')
    save_receipt(result, 'schedule-top')
    assert result['inspection_complete']
    assert result['notifications']['completed'] == 4
    assert result['notifications']['total'] == 7
    assert result['delegated']['completed'] == result['delegated']['total'] == 9
    assert result['delegated']['count_shows_complete'] is True
    assert result['notifications']['count_shows_complete'] is False
    assert result['daily_complete'] is False
    assert controller.clicks == [] and len(controller.swipes) == 2


@pytest.fixture
def resource():
    return load_resource()


def replay(resource, names, start='schedule'):
    from pcr_maa.schedule import inspect_schedule
    images = [frame(n) if isinstance(n, str) else n for n in names]
    controller = SavedImages([[im] for im in images])
    result = inspect_schedule(bind(resource, controller), start=start)
    result.update(clicks=controller.clicks, swipes=controller.swipes)
    save_receipt(result, 'schedule-regression')
    for page in result['pages']:
        path = Path(page['screenshot'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == page['image_sha256']
        assert any(np.array_equal(read_image(path), im) for im in images)
    assert result['daily_complete'] is False and result['schedule_executed'] is False
    return result, controller


@pytest.mark.parametrize('start,stages,clicks', [
    ('home', [['schedule-03-home-settled.png'], ['schedule-date-change-dialog.png']], [[1017, 575]]),
    ('schedule', [['schedule-date-change-dialog.png']], []),
])
def test_real_game_day_change_stops_schedule_with_specific_reason(resource, start, stages, clicks):
    from pcr_maa.schedule import inspect_schedule
    controller = SavedImages([[frame(name) for name in stage] for stage in stages])
    result = inspect_schedule(bind(resource, controller), start=start)
    save_receipt(result | {'clicks': controller.clicks, 'swipes': controller.swipes},
                 'schedule-date-red-green')
    completed = [node['name'] for node in result['nodes'] if node['completed']]
    assert completed[:2] == (['InspectScheduleHome', 'ScheduleEnter'] if start == 'home'
                             else ['InspectScheduleOpen', 'ScheduleUnknown'])
    assert 'ScheduleUnknown' in completed and 'ScheduleDone' not in completed
    assert result['native_ok'] and not result['inspection_complete']
    assert result['incomplete_reason'] == 'game_day_changed_requires_relogin'
    assert result['schedule_executed'] is False and result['daily_complete'] is False
    assert controller.clicks == clicks and controller.swipes == []


@pytest.mark.parametrize('mask', ['both', 'title', 'body'])
def test_unverified_popup_without_both_day_change_labels_is_not_misattributed(resource, mask):
    image = frame('schedule-date-change-dialog.png')
    if mask in ('both', 'title'):
        image[170:235, 540:720] = 0
    if mask in ('both', 'body'):
        image[335:395, 530:720] = 0
    result, controller = replay(resource, [image])
    assert result['native_ok'] and not result['inspection_complete']
    assert result['incomplete_reason'] == 'page_unverified'
    assert 'ScheduleUnknown' in [n['name'] for n in result['nodes'] if n['completed']]
    assert controller.clicks == controller.swipes == []


def test_day_change_ocr_exception_stays_unknown(resource, monkeypatch):
    from pcr_maa.schedule import SchedulePage

    def failed_ocr(*args):
        raise RuntimeError('offline OCR failure')

    monkeypatch.setattr(SchedulePage, 'day_change_text', staticmethod(failed_ocr))
    result, controller = replay(resource, ['schedule-date-change-dialog.png'])
    assert result['native_ok'] and not result['inspection_complete']
    assert result['incomplete_reason'].startswith('schedule_evidence_failed: offline OCR failure')
    assert controller.clicks == controller.swipes == []


def test_day_change_receipt_maps_to_cli_exit_two(resource, monkeypatch, capsys):
    from pcr_maa.cli import main
    import pcr_maa.device as device

    result, controller = replay(resource, ['schedule-date-change-dialog.png'])
    monkeypatch.setattr(device, 'navigate', lambda *args, **kwargs: result)
    assert main(['inspect-schedule', '--start', 'schedule', '--input-display', '2',
                 '--screenshot-display', 'offline']) == 2
    output = json.loads(capsys.readouterr().out)
    assert output['incomplete_reason'] == 'game_day_changed_requires_relogin'
    assert output['inspection_complete'] is False
    assert output['schedule_executed'] is False and output['daily_complete'] is False
    assert controller.clicks == controller.swipes == []


@pytest.mark.parametrize('start_index', [0, 1, 2, 3])
def test_resume_below_top_recovers_actual_headers(resource, start_index):
    reverse = ['schedule-12-delegated-bottom.png', 'schedule-11-delegated-lower.png',
               'schedule-10-delegated-middle.png', 'schedule-09-delegated-top.png',
               'schedule-08-notifications-lower.png', 'schedule-07-notifications-middle.png',
               'schedule-06-settings-closed.png']
    r, c = replay(resource, reverse[start_index:])
    assert r['inspection_complete'] and r['notifications']['completed'] == 4
    assert r['delegated']['count_shows_complete'] is True
    assert c.clicks == [] and all(s == [667, 287, 667, 500, 1000] for s in c.swipes)
    assert r['notifications']['evidence']['screenshot'] == r['pages'][-1]['screenshot']


def test_home_entry_and_native_raw_scaling(resource):
    import cv2
    names = ['schedule-03-home-settled.png', 'schedule-04-panel.png',
             'schedule-07-notifications-middle.png', 'schedule-08-notifications-lower.png']
    images = [cv2.resize(frame(n), (1920, 1080)) for n in names]
    from pcr_maa.schedule import inspect_schedule
    c = SavedImages([[im] for im in images])
    r = inspect_schedule(bind(resource, c), start='home')
    save_receipt(r, 'schedule-raw-scaling')
    assert r['inspection_complete']
    assert c.clicks == [[1526, 863]]
    assert c.swipes == [[1001, 750, 1001, 431, 1000]] * 2


@pytest.mark.parametrize('name', ['schedule-05-settings-top.png', '05-sweep-presets.png',
                                  '02-adventure-settled.png', '04-main-map-settled.png',
                                  'schedule-03-home-settled.png'])
def test_wrong_page_zero_actions(resource, name):
    r, c = replay(resource, [name])
    assert not r['inspection_complete'] and not r['navigation_complete']
    assert r['incomplete_reason'] == 'page_unverified'
    assert not c.clicks and not c.swipes


def test_explicit_home_start_rejects_open_panel(resource):
    r, c = replay(resource, ['schedule-04-panel.png'], start='home')
    assert not r['inspection_complete'] and not c.clicks and not c.swipes


@pytest.mark.parametrize('decoration', ['quote_replaced', 'quote_occluded', 'date_changed'])
def test_synthetic_decoration_changes_preserve_counts(resource, decoration):
    import cv2
    images = []
    for name in ['schedule-04-panel.png', 'schedule-07-notifications-middle.png',
                 'schedule-08-notifications-lower.png']:
        original = frame(name)
        im = original.copy()
        if decoration == 'date_changed':
            x, y, w, h = 260, 45, 80, 35
            text = '12 / 31'
        else:
            x, y, w, h = 510, 45, 280, 43
            text = 'A different daily message'
        im[y:y+h, x:x+w] = 0 if decoration == 'quote_occluded' else (235, 240, 245)
        if decoration != 'quote_occluded':
            cv2.putText(im, text, (x+2, y+25), cv2.FONT_HERSHEY_SIMPLEX,
                        .5, (45, 45, 45), 1, cv2.LINE_AA)
        # Synthetic copies of 2026-09-22 captures, never new live/day-change evidence.
        assert not np.array_equal(im[y:y+h, x:x+w], original[y:y+h, x:x+w])
        unchanged = im.copy()
        unchanged[y:y+h, x:x+w] = original[y:y+h, x:x+w]
        assert np.array_equal(unchanged, original)
        images.append(im)
    r, c = replay(resource, images)
    r['synthetic_variant'] = decoration
    save_receipt(r, 'schedule-synthetic-decoration')
    assert r['native_ok'] and r['navigation_complete'] and r['inspection_complete']
    assert (r['notifications']['completed'], r['notifications']['total']) == (4, 7)
    assert (r['delegated']['completed'], r['delegated']['total']) == (9, 9)
    assert r['notifications']['count_shows_complete'] is False
    assert r['delegated']['count_shows_complete'] is True
    assert not c.clicks and c.swipes == [[667, 500, 667, 287, 1000]] * 2


# The quote used to be treated as a mandatory guard. Its occlusion is now a
# positive case above: decorative text is not evidence of business-page identity.
@pytest.mark.parametrize('box', [(325, 620, 100, 43), (550, 620, 130, 43)])
def test_occluded_guard_zero_actions(resource, box):
    im = frame('schedule-04-panel.png')
    x, y, w, h = box
    im[y:y+h, x:x+w] = 0  # Synthetic occlusion, never a live sample.
    r, c = replay(resource, [im])
    assert not r['inspection_complete'] and not c.clicks and not c.swipes


@pytest.mark.parametrize('box', [(970, 140, 55, 42), (530, 140, 190, 35)])
def test_missing_count_or_heading_is_unknown_without_input(resource, box):
    im = frame('schedule-04-panel.png')
    x, y, w, h = box
    im[y:y+h, x:x+w] = 0
    r, c = replay(resource, [im])
    assert not r['inspection_complete'] and r['delegated']['count_shows_complete'] is None
    assert not c.clicks and not c.swipes


def test_fully_occluded_list_zero_actions(resource):
    im = frame('schedule-04-panel.png')
    im[120:591, 255:1027] = 0  # Synthetic occlusion of the OCR list, not a live capture.
    r, c = replay(resource, [im])
    assert not r['inspection_complete'] and not c.clicks and not c.swipes


def test_notifications_alone_and_stalled_scroll(resource):
    r, c = replay(resource, ['schedule-04-panel.png'])
    assert not r['inspection_complete'] and r['notifications']['completed'] == 4
    assert r['delegated']['total'] is None and r['delegated']['count_shows_complete'] is None
    assert r['incomplete_reason'] == 'no_progress' and len(c.swipes) == 1


def test_native_scroll_limit_is_bounded(resource):
    # Adversarial saved-frame replay: movement never exposes either heading.
    r, c = replay(resource, ['schedule-10-delegated-middle.png', 'schedule-11-delegated-lower.png'] * 6)
    assert not r['inspection_complete'] and len(c.swipes) == 8 and not c.clicks
    assert r['delegated']['count_shows_complete'] is None


def words(title='交给可可萝', count='9/9'):
    return [{'text': title, 'box': [550, 200, 150, 24], 'score': .99},
            {'text': count, 'box': [980, 202, 40, 24], 'score': .99}]


@pytest.mark.parametrize('count,done,total,complete', [('0/12', 0, 12, False), ('5/5', 5, 5, True)])
def test_counts_not_hardcoded(count, done, total, complete):
    from pcr_maa.schedule import section_counts
    section = section_counts(words(count=count))['delegated']
    assert (section['completed'], section['total'], section['count_shows_complete']) == (done, total, complete)


@pytest.mark.parametrize('count', ['9/?', '10/9', '0/0', '9', '9/9 9/9'])
def test_invalid_count(count):
    from pcr_maa.schedule import section_counts
    with pytest.raises(ValueError):
        section_counts(words(count=count))


def test_unassociated_duplicate_or_low_confidence_count():
    from pcr_maa.schedule import section_counts
    detached = words(); detached[1]['box'][1] += 100
    low = words(); low[1]['score'] = .6
    for sample in [detached, low, words()+[words()[1]], words(title='竞技场')]:
        with pytest.raises(ValueError):
            section_counts(sample)


def test_conflicting_evidence_stops_collector():
    from pcr_maa.schedule import Collector, section_counts
    collector = Collector()
    for count in ['4/7', '5/7']:
        ws = words(title='来自可可萝的通知', count=count)
        page = {'sections': section_counts(ws), 'error': None, 'words': ws, 'screenshot': count}
        assert collector.run(None, SimpleNamespace(reco_detail=SimpleNamespace(raw_detail={'best': {'detail': page}})))
    assert collector.decision == 'unknown' and collector.reason == 'conflicting_section_count'


def test_schedule_action_whitelist_and_caps():
    p = json.loads((ROOT / 'assets/resource/pipeline/schedule.json').read_text(encoding='utf-8'))
    actions = {k: v for k, v in p.items() if v.get('action') in ('Click', 'Swipe')}
    assert set(actions) == {'ScheduleEnter', 'ScheduleUp', 'ScheduleDown', 'ScheduleCloseClick'}
    assert p['ScheduleCloseClick']['all_of'] == ['ScheduleCloseControl', 'ScheduleGuard']
    assert p['ScheduleCloseClick']['target'] is True and p['ScheduleCloseClick']['max_hit'] == 1
    assert p['ScheduleHomeGate']['next'] == ['ObserveHome']
    assert p['ScheduleEnter']['all_of'] == ['HomeDefaultTextLayout', 'ObserveHome']
    assert p['ScheduleEnter']['target'] == [1017, 575] and p['ScheduleEnter']['max_hit'] == 1
    for name in ('ScheduleUp', 'ScheduleDown'):
        assert p[name]['custom_recognition'] == 'ScheduleDecision'
        assert p[name]['max_hit'] == 8 and p[name]['duration'] == 1000
        assert p[name]['begin'][0] == p[name]['end'][0] == 667
    assert all(n.get('action', 'DoNothing') in {'Click', 'Swipe', 'Custom', 'DoNothing', 'StopTask'} for n in p.values())
    assert p['ScheduleRead']['max_hit'] == 17


@pytest.mark.parametrize('changed_page', ['settings', 'close_occluded', 'settings_occluded'])
def test_page_changes_before_swipe_are_rechecked(resource, changed_page):
    from pcr_maa.schedule import inspect_schedule
    changed = frame('schedule-05-settings-top.png') if changed_page == 'settings' else frame('schedule-04-panel.png')
    if changed_page == 'close_occluded':
        changed[620:663, 325:425] = 0
    elif changed_page == 'settings_occluded':
        changed[620:663, 550:680] = 0
    c = SavedImages([[frame('schedule-04-panel.png'), changed]])
    r = inspect_schedule(bind(resource, c), start='schedule')
    save_receipt(r, 'schedule-page-change')
    assert not r['inspection_complete'] and not c.clicks and not c.swipes


def test_evidence_write_failure_is_zero_input(resource, monkeypatch):
    import pcr_maa.schedule as schedule
    monkeypatch.setattr(schedule.native, 'write_png', lambda *a: False)
    r, c = replay(resource, ['schedule-04-panel.png'])
    assert not r['inspection_complete'] and not c.clicks and not c.swipes
    assert r['incomplete_reason'].startswith('schedule_evidence_failed')


def test_malformed_collector_detail_fails_closed():
    from pcr_maa.schedule import Collector
    collector = Collector()
    collector.decision = 'down'
    assert collector.run(None, SimpleNamespace(reco_detail=SimpleNamespace(raw_detail={'best': {'detail': ''}})))
    assert collector.decision == 'unknown' and collector.reason.startswith('schedule_collection_failed')


def test_cli_dispatch_and_unknown_exit(monkeypatch, capsys):
    from pcr_maa.cli import main
    import pcr_maa.device as device
    calls = []
    def fake(input_display, screenshot_display, **kwargs):
        calls.append((input_display, screenshot_display, kwargs))
        return dict(inspection_complete=False, notifications={}, delegated={}, schedule_executed=False, scope='counts')
    monkeypatch.setattr(device, 'navigate', fake)
    assert main(['inspect-schedule', '--start', 'schedule', '--input-display', '2', '--screenshot-display', '123']) == 2
    assert calls == [(2, '123', {'schedule_start': 'schedule'})]
    assert json.loads(capsys.readouterr().out)['outcome'] == 'unknown_or_failed'
