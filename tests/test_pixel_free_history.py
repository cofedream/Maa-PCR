"""Synthetic, source-frozen historical receipts; never a real ledger write."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest

def reading(*, legacy):
    from pcr_maa.preset_scan import Page, Collector
    collector = Collector()
    for kind, indices in [('top', [0,1,2]), ('page', [2,3,4]), ('bottom', [4,5])]:
        words = []
        for position, index in enumerate(indices):
            y = 235 + 85 * position
            for text, box in [(f'测试{index+1}-1', [80,y,160,24]),
                              ('主线关卡', [90,y+28,100,22]),
                              ('困难', [240,y+28,70,22]), ('3/3', [590,y+28,50,22])]:
                words.append(dict(text=text, score=.99, box=box))
        def recognize(name, image, override=None):
            hit = name != 'ScanNoRail'
            if name in ('ScanBlueTop', 'ScanGrayBottom'):
                hit = kind == 'top'
            if name in ('ScanGrayTop', 'ScanBlueBottom'):
                hit = kind == 'bottom'
            raw = dict(filtered=words) if name == 'ScanOCR' else {}
            if name == 'ScanSlotGuard':
                raw = [dict(name='PresetsReached', algorithm='OCR', box=[500,30,100,30], detail={}),
                       dict(name='SelectedSlot1', algorithm='ColorMatch', box=[85,103,16,13], detail={})]
            if name == 'ScanCheckGlyph':
                x,y,_,_ = override[name]['roi']
                component = dict(box=[x+9,y+21,57,41], count=965)
                raw = dict(best=component, filtered=[component], all=[component])
            return SimpleNamespace(hit=hit, raw_detail=raw)
        value = Page().analyze(SimpleNamespace(run_recognition=recognize),
                     SimpleNamespace(image=None, custom_recognition_param=json.dumps(kind)))
        page = json.loads(value.detail) if isinstance(value.detail,str) else value.detail
        assert collector.run(None, SimpleNamespace(reco_detail=SimpleNamespace(raw_detail={'best': {'detail': page}})))
    assert collector.complete
    if legacy:
        # Synthetic r23 receipt shape, independently exercised against the
        # frozen r23 Page in the development RED evidence. No images or ledger.
        for page in collector.pages:
            for row in page['rows']:
                roi_y = max(230, row['ocr_box'][1] - 8)
                component = dict(box=[1106,roi_y+11,59,55], score=.96)
                raw = row['check_evidence']
                raw.clear()
                raw.update(best=component, filtered=[component], all=[component])
    nodes = [dict(name='ScanSlotReady', completed=True, action=dict(success=True), recognition={})]
    nodes += [dict(name='Scan'+page['kind'].title(), completed=True, action=dict(success=True),
                   recognition={'best': {'detail': page}}) for page in collector.pages]
    return dict(native_ok=True, tasker_inited=True, requested_slot=1, selected_slot=1, view='checked',
                nodes=nodes, pages=collector.pages, rows=collector.rows, list_complete=True)


def assert_no_native(monkeypatch):
    from pcr_maa import native
    def forbidden(*args, **kwargs):
        raise AssertionError('Historical validation must never run Maa or send input')
    for name in ('run', 'bind', 'load_resource', 'read_image'):
        monkeypatch.setattr(native, name, forbidden)


def test_bound_legacy_template_receipt_remains_read_only_valid(monkeypatch):
    from pcr_maa.preset_reconcile import scan_evidence
    old = reading(legacy=True)
    before = json.dumps(old, ensure_ascii=False, sort_keys=True).encode('utf-8')
    assert_no_native(monkeypatch)
    scan_evidence(old)
    assert json.dumps(old, ensure_ascii=False, sort_keys=True).encode('utf-8') == before


def test_new_color_receipt_uses_geometry_validation(monkeypatch):
    from pcr_maa.preset_reconcile import scan_evidence
    current = reading(legacy=False)
    before = json.dumps(current, sort_keys=True).encode('utf-8')
    assert_no_native(monkeypatch)
    scan_evidence(current)
    assert json.dumps(current, sort_keys=True).encode('utf-8') == before


def test_live_checked_helper_never_accepts_old_template_receipt():
    from pcr_maa.preset_scan import checked_glyph
    component = dict(box=[1106,241,59,55], score=.96)
    result = SimpleNamespace(hit=True, raw_detail=dict(best=component, filtered=[component], all=[component]))
    context = SimpleNamespace(run_recognition=lambda *args, **kwargs: result)
    assert checked_glyph(context, object(), [1098,230,80,76]) is False
    assert checked_glyph(context, None, [1098,230,80,76]) is False


@pytest.mark.parametrize('fault', ['low_score', 'missing_filtered', 'duplicate_match', 'unbound_box',
                                  'new_solid_blue', 'new_missing_count', 'new_bool_count'])
def test_history_cannot_substitute_unbound_or_incomplete_glyph(fault, monkeypatch):
    from pcr_maa.preset_reconcile import scan_evidence
    current = reading(legacy=not fault.startswith('new_'))
    raw = current['pages'][0]['rows'][0]['check_evidence']
    if fault == 'low_score':
        raw['best']['score'] = .89
    elif fault == 'missing_filtered':
        raw.pop('filtered')
    elif fault == 'duplicate_match':
        raw['filtered'].append(deepcopy(raw['filtered'][0]))
    elif fault == 'unbound_box':
        raw['best']['box'][0] = 1200
    elif fault == 'new_solid_blue':
        raw['best']['count'] = 57 * 41
    elif fault == 'new_missing_count':
        raw['best'].pop('count')
    else:
        raw['best']['count'] = True
    assert_no_native(monkeypatch)
    with pytest.raises(ValueError):
        scan_evidence(current)
