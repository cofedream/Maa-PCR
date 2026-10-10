import pytest
from pcr_maa.native import ROOT, read_image, load_resource, bind, save_receipt
from pcr_maa.replay import SavedImages


def frame(name):
    path = ROOT / 'tests/fixtures' / name
    if not path.is_file():
        pytest.skip('private saved-frame fixture is not distributed: ' + name)
    return read_image(path)


def test_checked_five_native():
    from pcr_maa.preset_scan import scan
    controller = SavedImages([[frame('12-slot4-list-view-toggle.png')],
                              [frame('13-slot4-checked-bottom.png')]])
    result = scan(bind(load_resource(), controller), 4)
    save_receipt(result, 'checked-five')
    assert result['list_complete']
    assert len(result['rows']) == 5
    assert result['all_selected_exhausted'] is True
    assert controller.clicks == []
    assert len(controller.swipes) == 1
    assert not result['daily_complete']
    assert {r['title'] for r in result['rows']} == {
        '紫冥深域6-5', '珀天深域5-7', '翠岚深域6-10', '苍波深域6-10', '红焰深域7-2'}
    swipe = next(n for n in result['nodes'] if n['name'] == 'ScanSwipe')
    assert swipe['completed'] and swipe['action']['success']


def replay(stages, slot=4, label='regression'):
    from pcr_maa.preset_scan import scan
    controller = SavedImages([[im] for im in stages])
    result = scan(bind(load_resource(), controller), slot, source='offline replay; ' + label)
    result.update(clicks=controller.clicks, swipes=controller.swipes)
    save_receipt(result, 'scan-' + label)
    return result, controller


@pytest.mark.parametrize('slot', [0, 8, -1, '4', True])
def test_invalid_slot_before_task(slot):
    from pcr_maa.preset_scan import scan
    with pytest.raises(ValueError):
        scan(None, slot)


def test_native_slot_selection_and_toggle_from_home():
    names = ['parent-home-before-product.png', '02-adventure-settled.png',
             '04-main-map-settled.png', '05-sweep-presets.png', '10-slot4-top.png',
             '12-slot4-list-view-toggle.png', '13-slot4-checked-bottom.png']
    r, c = replay([frame(n) for n in names])
    assert r['list_complete'] and r['selected_slot'] == 4
    assert c.clicks == [[718, 681], [753, 280], [1225, 189], [611, 110], [937, 196]]
    assert len(c.swipes) == 1


def test_all_view_cannot_be_aggregated():
    r, c = replay([frame('10-slot4-top.png'), frame('11-slot4-scroll1.png')])
    assert not r['list_complete'] and not r['rows']
    assert r['all_selected_exhausted'] is None
    assert c.clicks == [[937, 196]] * 3 and c.swipes == []


def test_bottom_reset_failure_is_partial():
    r, c = replay([frame('13-slot4-checked-bottom.png')])
    assert not r['list_complete']
    assert r['incomplete_reason'] == 'reset_top_unverified'
    assert c.clicks == [[937, 196]] and not c.swipes


def test_stalled_scroll_stops():
    r, c = replay([frame('12-slot4-list-view-toggle.png')])
    assert not r['list_complete'] and len(c.swipes) == 8
    assert len(r['pages']) == 9
    assert r['incomplete_reason'] == 'no_progress_without_bottom'
    assert r['action_limits']['list_swipes'] == len(c.swipes)
    assert r['all_selected_exhausted'] is None


@pytest.mark.parametrize('box', [(849,182,110,32), (585,270,65,34), (1216,237,15,261),
                               (65,240,460,33), (1100,240,80,65)])
def test_masked_evidence_cannot_complete(box):
    im = frame('12-slot4-list-view-toggle.png')
    x,y,w,h = box
    im[y:y+h,x:x+w] = 0
    r, c = replay([im, frame('13-slot4-checked-bottom.png')], label='synthetic-mask')
    assert not r['list_complete'] and r['all_selected_exhausted'] is None
    assert c.clicks == []


def checked_short():
    # Synthetic: only the view label is replaced; source 09 is NOT a checked list.
    im = frame('09-slot2-top.png')
    im[182:214,849:959] = frame('12-slot4-list-view-toggle.png')[182:214,849:959]
    return im


def test_search_to_checked_short_and_duplicate_names():
    im = checked_short()
    # Rename every tab by repeating the same label pixels; retain selected background marker.
    for i in range(7):
        im[101:123,112+i*157:169+i*157] = im[101:123,112:169].copy()
    r, c = replay([frame('09-slot2-top.png'), im], slot=2, label='synthetic-short-names')
    assert r['selected_slot'] == 2 and r['list_complete'] and len(r['rows']) == 3
    assert c.clicks == [[937,196]] and not c.swipes


def test_empty_or_unreadable_is_not_exhausted():
    im = checked_short()
    im[232:503,50:1208] = 247
    r, c = replay([im], slot=2, label='synthetic-blank-list')
    assert not r['list_complete'] and r['empty_list'] is None
    assert r['all_selected_exhausted'] is None and not c.swipes


def test_missing_main_difficulty_does_not_merge_identity():
    im = checked_short()
    im[275:302,229:369] = 255
    r, c = replay([im], slot=2, label='synthetic-missing-difficulty')
    assert not r['list_complete'] and r['all_selected_exhausted'] is None
    assert r['rows'][0]['difficulty'] is None


def test_safe_pipeline_and_fixed_swipe():
    import json
    from pcr_maa.device import fixed_config
    p = json.loads((ROOT/'assets/resource/pipeline/preset_scan.json').read_text(encoding='utf-8'))
    assert {n for n,v in p.items() if v.get('action') == 'Click'} == {'ScanSelectClick', 'ScanToggle', 'ScanResetToggle'}
    assert p['ScanToggle']['max_hit'] == 3 and p['ScanSwipe']['max_hit'] == 8
    assert p['ScanSwipe']['begin'] == [567,440] and p['ScanSwipe']['end'] == [567,360]
    assert p['ScanSwipe']['duration'] == 1000
    assert p['ScanResetToggle']['max_hit'] == 1
    assert p['ScanResetToggle']['next'] == ['ScanToggle']
    assert set(p['ScanResetToggle']['all_of']) == {
        'ScanSlotGuard', 'ScanCheckedLabel', 'ScanGrayTop', 'ScanRailBlue', 'ScanRailGray', 'ScanToggleLabel'}
    assert all(v.get('action') in (None,'Click','Swipe','Custom','StopTask') for v in p.values())
    cfg = fixed_config(2,'4619827767814508545',preset_list=True)['command']
    assert cfg['Swipe'][-1] == 'input -d 2 swipe {X1} {Y1} {X2} {Y2} {DURATION}'
    assert cfg['Shell'][-1] == 'false'


def test_native_scroll_cap_with_progress_but_no_bottom():
    import cv2
    pages = []
    for page in range(9):
        im = frame('12-slot4-list-view-toggle.png')
        for row in range(3):
            y = 244 + row * 86
            im[y-2:y+26,75:520] = 255
            cv2.putText(im, f'TEST {page*2+row+1}-1', (78,y+19),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (40,40,40), 2, cv2.LINE_AA)
        pages.append(im)
    r, c = replay(pages, label='synthetic-scroll-cap')
    assert not r['list_complete'] and r['all_selected_exhausted'] is None
    assert len(c.swipes) == 8
    assert len(r['pages']) == 9


def test_visible_remaining_cannot_complete_partial_list():
    import cv2
    im = frame('12-slot4-list-view-toggle.png')
    im[273:302,585:648] = 255
    cv2.putText(im, '1/10', (595,294), cv2.FONT_HERSHEY_SIMPLEX, 0.70,
                (40,40,40), 2, cv2.LINE_AA)
    r, c = replay([im], label='synthetic-remaining')
    assert r['has_remaining'] and not r['list_complete']
    assert r['all_selected_exhausted'] is None


@pytest.mark.parametrize('slot', range(1,8))
def test_each_slot_identity_ignores_label(slot):
    im = checked_short()
    # Synthetic selected color at each positional marker; names do not establish identity.
    for i in range(7):
        im[103:116,85+i*157:101+i*157] = [245,245,245]
    im[103:116,85+(slot-1)*157:101+(slot-1)*157] = [220,130,60]
    r, c = replay([im], slot=slot, label='synthetic-slot')
    assert r['selected_slot'] == slot and r['list_complete']
    assert not c.clicks and not c.swipes


SHORT_SCAN = ['16-slot1-reset-toggle2.png', '17-slot1-short-scroll1.png',
              '18-slot1-short-scroll2.png', '19-slot1-short-scroll3-bottom.png']


def assert_six(r):
    assert r['list_complete'], r['incomplete_reason']
    assert len(r['rows']) == 6
    assert r['has_remaining'] is True and r['all_selected_exhausted'] is False
    assert [(v['category'], v['difficulty'], v['title'][-3:], v['remaining'], v['limit'])
            for v in r['rows']][-2:] == [('外传', '困难', '1-3', 2, 5), ('外传', '困难', '1-1', 2, 5)]
    assert [v['remaining'] for v in r['rows'][:4]] == [0]*4
    assert not r['daily_complete']


def test_real_short_slow_six_rows():
    r, c = replay([frame(n) for n in SHORT_SCAN], slot=1, label='slot1-short-slow')
    assert_six(r)
    assert not c.clicks
    assert c.swipes == [[567, 440, 567, 360, 1000]] * 3


@pytest.mark.parametrize('start', [SHORT_SCAN[1], SHORT_SCAN[-1]])
def test_same_slot_middle_bottom_resets_to_top(start):
    r, c = replay([frame(start), frame('15-slot1-reset-toggle1.png')]
                  + [frame(n) for n in SHORT_SCAN], slot=1, label='slot1-reset')
    assert_six(r)
    assert c.clicks == [[937, 196]] * 2
    assert len(c.swipes) == 3
    assert r['action_limits']['view_clicks'] == 4


def test_reset_returning_to_middle_does_not_scan():
    r, c = replay([frame(SHORT_SCAN[-1]), frame('15-slot1-reset-toggle1.png'),
                   frame(SHORT_SCAN[1])], slot=1, label='reset-not-top')
    assert not r['list_complete'] and r['incomplete_reason'] == 'reset_top_unverified'
    assert c.clicks == [[937, 196]] * 2 and not c.swipes


def test_wrong_slot_after_reset_stops():
    r, c = replay([frame(SHORT_SCAN[-1]), frame('10-slot4-top.png')],
                  slot=1, label='reset-wrong-slot')
    assert not r['list_complete'] and r['all_selected_exhausted'] is None
    assert c.clicks == [[937, 196]] and not c.swipes


@pytest.mark.parametrize('box', [(849,182,110,32), (85,103,16,13), (584,38,118,37)])
def test_reset_unknown_start_zero_input(box):
    im = frame(SHORT_SCAN[-1]); x,y,w,h = box; im[y:y+h,x:x+w] = 0
    r, c = replay([im], slot=1, label='reset-masked-start')
    assert not r['list_complete'] and not c.clicks and not c.swipes


# Labelled aggregation counterexamples: no fake native/OCR success is claimed.
def collect_pages(pages):
    from types import SimpleNamespace
    from pcr_maa.preset_scan import Collector
    c = Collector()
    for page in pages:
        if not c.run(None, SimpleNamespace(reco_detail=SimpleNamespace(raw_detail={'best': {'detail': page}}))):
            break
    return c


def labelled_page(ids, ys, kind='page', count=0):
    return dict(kind=kind, gaps=False, rows=[dict(title=f'Stage {i}-1', category='test',
                difficulty='hard', remaining=count, limit=3, checked=True,
                ocr_box=[80,y,180,20]) for i,y in zip(ids,ys)])


def test_overlap_without_new_rows_but_forward_motion_continues():
    c = collect_pages([labelled_page([1,2,3], [242,329,414], 'top'),
                       labelled_page([2,3], [290,376]),
                       labelled_page([3,4,5], [250,336,422], 'bottom')])
    assert c.complete and len(c.rows) == 5


@pytest.mark.parametrize('last,reason', [
    (labelled_page([1,2,3], [242,329,414]), 'scroll_limit_or_bottom_unverified'),
    (labelled_page([4,5], [290,376]), 'no_overlap'),
    (labelled_page([2,3,4], [242,329,414], count=1), 'conflicting_rows'),
])
def test_aggregation_failure_reasons(last, reason):
    c = collect_pages([labelled_page([1,2,3], [242,329,414], 'top'), last])
    assert not c.complete and c.reason == reason


def test_bottom_evidence_with_same_rows_and_motion_can_complete():
    c = collect_pages([labelled_page([1,2,3], [242,329,414], 'top'),
                       labelled_page([2,3,4], [250,336,430]),
                       labelled_page([2,3,4], [242,328,422], 'bottom')])
    assert c.complete and len(c.rows) == 4


def test_overlap_must_be_with_adjacent_page():
    c = collect_pages([labelled_page([1,2,3], [242,329,414], 'top'),
                       labelled_page([3,4,5], [242,329,414]),
                       labelled_page([1,6,7], [242,329,414], 'bottom')])
    assert not c.complete and c.reason == 'no_overlap'



def test_native_edge_partial_and_known_rows_still_advancing():
    im = frame(SHORT_SCAN[0])
    # Labelled synthetic upward translation (15px), not a physical swipe claim.
    # The first title touches the OCR crop edge; its count is still visible.
    im[228:488,50:1208] = im[243:503,50:1208].copy()
    im[488:503,50:1208] = 247
    im[237:498,1216:1231] = frame(SHORT_SCAN[1])[237:498,1216:1231]
    r, c = replay([frame(SHORT_SCAN[0]), im] + [frame(n) for n in SHORT_SCAN[1:]],
                  slot=1, label='synthetic-edge-forward')
    assert_six(r)
    assert len(c.swipes) == 4 and not c.clicks
    assert r['pages'][1]['partial'] and not r['pages'][1]['gaps']


@pytest.mark.parametrize('box', [(585,455,65,36), (229,455,140,36), (65,424,465,31)])
def test_slot1_bottom_masked_fields_cannot_complete(box):
    im = frame(SHORT_SCAN[-1]); x,y,w,h = box; im[y:y+h,x:x+w] = 0
    r, c = replay([frame(n) for n in SHORT_SCAN[:-1]] + [im],
                  slot=1, label='slot1-bottom-masked')
    assert not r['list_complete'] and r['all_selected_exhausted'] is None
    assert not c.clicks


def test_unknown_popup_after_reset_has_no_more_input():
    import numpy as np
    r, c = replay([frame(SHORT_SCAN[-1]), np.zeros((720,1280,3), dtype=np.uint8)],
                  slot=1, label='reset-unknown-popup')
    assert not r['list_complete'] and not c.swipes
    assert c.clicks == [[937,196]]


def test_original_no_overlap_failure_keeps_five_partial_rows():
    import json
    source = ROOT/'tests/fixtures/slot1-old-no-overlap.json'
    if not source.is_file():
        pytest.skip('private saved-page fixture is not distributed')
    pages = json.loads(source.read_text(encoding='utf-8'))
    c = collect_pages(pages)
    assert not c.complete and c.reason == 'no_overlap' and len(c.rows) == 5


def test_native_identical_pages_then_new_rows():
    r, c = replay([frame(SHORT_SCAN[0])] * 4 + [frame(n) for n in SHORT_SCAN[1:]],
                  slot=1, label='repeated-top-then-new')
    assert_six(r)
    assert len(c.swipes) == 6 and not c.clicks


def test_native_same_rows_true_bottom():
    # Synthetic rail-only tail: real OCR rows unchanged, native rail evidence
    # changes to bottom. This is not a physical scroll/real tail claim.
    top = frame('12-slot4-list-view-toggle.png')
    bottom = top.copy()
    bottom[237:498, 1216:1231] = frame('13-slot4-checked-bottom.png')[237:498, 1216:1231]
    r, c = replay([top, bottom], label='synthetic-same-rows-tail')
    assert r['list_complete'] and len(r['rows']) == 3
    assert len(c.swipes) == 1 and r['pages'][-1]['kind'] == 'bottom'
