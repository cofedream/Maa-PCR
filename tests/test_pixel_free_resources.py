"""Pixel-free candidate uses real Maa recognition, never a live device."""
import json
from pathlib import Path
import cv2
import pytest
from maa.custom_recognition import CustomRecognition

from pcr_maa import native
from pcr_maa.replay import SavedImages

PROJECT = Path(__file__).resolve().parents[1]
LIVE = PROJECT / '.runtime/private-fixtures/hermes-pcr/live'


def private_frame(path):
    if not path.is_file():
        pytest.skip('private saved-frame fixture is not distributed: ' + path.name)
    return native.read_image(path)


def test_daily_resources_have_no_game_image_templates():
    references = []
    for path in native.RESOURCE.joinpath('pipeline').glob('*.json'):
        if path.name == 'labyrinth.json':
            continue  # Labyrinth intentionally owns its separate template assets.
        def visit(value):
            if isinstance(value, dict):
                if value.get('recognition') == 'TemplateMatch' or 'template' in value:
                    references.append((path.name, value))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(json.loads(path.read_text(encoding='utf-8')))
    assert references == []
    assert all(path.name == 'labyrinth' and path.is_dir()
               for path in native.RESOURCE.joinpath('image').iterdir())


def test_native_home_navigation_without_game_templates():
    from pcr_maa.preset_scan import scan
    names = ['parent-home-before-product.png', '02-adventure-settled.png',
             '04-main-map-settled.png', '05-sweep-presets.png', '10-slot4-top.png',
             '12-slot4-list-view-toggle.png', '13-slot4-checked-bottom.png']
    controller = SavedImages([[private_frame(PROJECT / 'tests/fixtures' / n)] for n in names])
    result = scan(native.bind(native.load_resource(), controller), 4)
    native.save_receipt(dict(result, source='historical originals; no bundled game images',
                             clicks=controller.clicks, swipes=controller.swipes), 'pixel-free-navigation')
    assert result['list_complete'] and result['selected_slot'] == 4
    assert controller.clicks == [[718, 681], [753, 280], [1225, 189], [611, 110], [937, 196]]
    assert len(controller.swipes) == 1


def native_probe(image, callback, label, pipeline=None):
    class Probe(CustomRecognition):
        def __init__(self):
            super().__init__()
            self.observed = None

        def analyze(self, context, argv):
            self.observed = callback(context, argv)
            return self.AnalyzeResult([0, 0, 1, 1], {'observed': True})
    probe = Probe()
    resource = native.load_resource()
    assert resource.register_custom_recognition('PixelFreeProbe', probe)
    assert resource.override_pipeline(dict(pipeline or {}, PixelFreeProbe=dict(recognition='Custom',
        custom_recognition='PixelFreeProbe', action='DoNothing', next=[])))
    controller = SavedImages([[image]])
    result = native.run(native.bind(resource, controller), 'PixelFreeProbe')
    assert result['native_ok'] and not controller.clicks and not controller.swipes
    native.save_receipt(dict(result, observed=probe.observed, source=label,
                            clicks=controller.clicks, swipes=controller.swipes), label)
    return probe.observed


@pytest.mark.parametrize('source,y,floor', [
    ('12-slot4-list-view-toggle.png', 244, 230),
    ('13-slot4-checked-bottom.png', 260, 230),
    ('event-sweep-result-closed', 199, 188),
    ('event-sweep-after-bottom', 259, 188),
])
def test_native_checked_stroke_original(source, y, floor):
    from pcr_maa.preset_scan import checked_glyph
    path = PROJECT / 'tests/fixtures' / source if source.endswith('.png') else LIVE / (source + '.png')
    image = private_frame(path)
    def observe(context, argv):
        evidence = {}
        checked = checked_glyph(context, argv.image, [1098, max(floor, y-8), 80, 76], evidence=evidence)
        return dict(checked=checked, evidence=evidence)
    result = native_probe(image, observe, 'pixel-check-original-' + source)
    assert result['checked']
    component = result['evidence']['raw']['filtered']
    assert len(component) == 1 and 850 <= component[0]['count'] <= 1150


@pytest.mark.parametrize('event', [False, True])
@pytest.mark.parametrize('fault', ['masked', 'unchecked', 'near_color', 'solid_rectangle', 'wide_bar'])
def test_native_checked_stroke_rejects_synthetic_counterexamples(event, fault):
    from pcr_maa.preset_scan import checked_glyph
    path = LIVE / 'event-sweep-result-closed.png' if event else PROJECT / 'tests/fixtures/12-slot4-list-view-toggle.png'
    image = private_frame(path)
    y, floor, top, height = (199, 188, 206, 42) if event else (244, 230, 248, 41)
    left, width = (1108, 58) if event else (1107, 57)
    crop = image[top:top+height, left:left+width]
    if fault == 'masked':
        image[y-8:y+68, 1098:1178] = 0
    elif fault == 'unchecked':
        # Remove only saturated blue ink; the actual gray checkbox remains.
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (95,100,100), (120,255,255))
        crop[mask != 0] = [225, 225, 225]
    elif fault == 'near_color':
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (95,100,100), (120,255,255))
        hsv[mask != 0, 0] = 80
        crop[:] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    else:
        image[y-8:y+68, 1098:1178] = 225
        h = height if fault == 'solid_rectangle' else 17
        image[top:top+h, left:left+width] = [235, 145, 50]
    def observe(context, argv):
        evidence = {}
        checked = checked_glyph(context, argv.image, [1098, max(floor, y-8), 80, 76], evidence=evidence)
        return dict(checked=checked, evidence=evidence)
    result = native_probe(image, observe, 'pixel-check-synthetic-' + str(event) + '-' + fault)
    assert result['checked'] is False


def event_page(image, label):
    from pcr_maa.event_native import EventPage
    def observe(context, argv):
        argv.custom_recognition_param = json.dumps('top')
        page = EventPage().analyze(context, argv)
        if page is None:
            return None
        return json.loads(page.detail) if isinstance(page.detail, str) else page.detail
    return native_probe(image, observe, label)


def test_native_gray_exhausted_hard_original():
    page = event_page(private_frame(LIVE / 'event-sweep-result-closed.png'), 'pixel-exhausted-original')
    assert page and not page['gaps']
    assert len(page['rows']) == 3 and all(row['difficulty'] == '困难' and row['remaining'] == 0
                                         and row['limit'] == 3 and row['checked'] for row in page['rows'])
    badge = page['evidence']['EventExhaustedHard']
    assert badge['hit']
    assert {part['name'] for part in badge['raw']} == {
        'EventExhaustedHardText', 'EventExhaustedHardGray', 'EventExhaustedHardDimBadge'}


@pytest.mark.parametrize('fault', ['gray_absent', 'colored_badge', 'text_absent',
                                  'notice_absent', 'wrong_row_gray'])
def test_native_gray_exhausted_hard_needs_all_original_guards(fault):
    image = private_frame(LIVE / 'event-sweep-result-closed.png')
    y = 199
    if fault in ('gray_absent', 'wrong_row_gray'):
        image[y+45:y+55, 550:570] = 255
        if fault == 'wrong_row_gray':
            # A genuine color patch from another row is outside this row ROI.
            image[y+58:y+68, 550:570] = [198, 178, 165]
    elif fault == 'colored_badge':
        image[y+43:y+53, 239:271] = [230, 80, 230]
    elif fault == 'text_absent':
        image[y+41:y+57, 281:321] = [123, 93, 148]
    else:
        image[y+20:y+47, 276:460] = [198, 178, 165]
    page = event_page(image, 'pixel-exhausted-synthetic-' + fault)
    assert page is None or page['gaps']
    if page:
        assert page['rows'][0]['difficulty'] is None


@pytest.mark.parametrize('node,filename,roi', [
    ('EnterMain', '02-adventure-settled.png', [70,6,108,63]),
    ('EnterMain', '02-adventure-settled.png', [685,255,138,48]),
    ('OpenPresets', '04-main-map-settled.png', [76,11,150,57]),
    ('OpenPresets', '04-main-map-settled.png', [1190,153,75,81]),
])
def test_native_navigation_requires_both_original_words(node, filename, roi):
    image = private_frame(PROJECT / 'tests/fixtures' / filename)
    x,y,w,h = roi
    image[y:y+h,x:x+w] = 0
    def observe(context, argv):
        r = context.run_recognition(node, argv.image)
        return dict(hit=bool(r and r.hit), raw=r.raw_detail if r else None)
    result = native_probe(image, observe, 'pixel-navigation-mask-' + node + '-' + str(x))
    assert result['hit'] is False


@pytest.mark.parametrize('event', [False, True])
@pytest.mark.parametrize('fault', ['unchecked', 'solid_rectangle'])
def test_actual_page_collectors_stop_on_unchecked_or_solid_blue(event, fault):
    from types import SimpleNamespace
    from pcr_maa.preset_scan import Page, Collector
    from pcr_maa.event_native import EventPage, EventCollector
    path = LIVE / 'event-sweep-result-closed.png' if event else PROJECT / 'tests/fixtures/12-slot4-list-view-toggle.png'
    image = private_frame(path)
    top, left, width, height = (206,1108,58,42) if event else (248,1107,57,41)
    crop = image[top:top+height, left:left+width]
    if fault == 'unchecked':
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (95,100,100), (120,255,255))
        crop[mask != 0] = 225
    else:
        crop[:] = [235,145,50]
    def observe(context, argv):
        argv.custom_recognition_param = json.dumps('top')
        parsed = (EventPage() if event else Page()).analyze(context, argv)
        return json.loads(parsed.detail) if isinstance(parsed.detail, str) else parsed.detail
    overrides = {} if event else {'ScanSlotGuard': {'all_of': ['PresetsReached', 'SelectedSlot4']}}
    page = native_probe(image, observe, 'pixel-page-synthetic-' + str(event) + '-' + fault,
                        pipeline=overrides)
    assert page['rows'][0]['checked'] is False
    collector = EventCollector() if event else Collector()
    assert collector.run(None, SimpleNamespace(reco_detail=SimpleNamespace(raw_detail={'best': {'detail': page}}))) is False
    assert not collector.complete and collector.reason == 'empty_or_unreadable_rows'
