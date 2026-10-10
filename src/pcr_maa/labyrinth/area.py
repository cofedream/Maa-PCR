"""Read a stable map's area counter, without navigation or cursor changes.

The current number and slash-prefixed total use separate original-frame OCR
regions. Missing total stays unobserved. A task ID or caller source label is not
proof of screenshot freshness, account identity, a handoff, or a cleared run.
"""
import math
import re
from .batch import BatchStopped

LABEL_ROI = (425, 15, 65, 35)
CURRENT_ROI = (432, 43, 25, 29)
TOTAL_ROI = (460, 44, 28, 31)
THRESHOLD = .85
_MAP_WORDS = {
    'LabyHomeTitle': ((70, 0, 250, 80), '黎明界迷宫'),
    'LabyMapRegionLabel': ((425, 15, 80, 35), '区域'),
    'LabyMapMenu': ((1180, 55, 90, 55), '菜单'),
    'LabyMapRetreatLabel': ((850, 620, 110, 65), '撤退'),
    'LabyMapReturnLabel': ((1100, 620, 110, 65), '返回'),
}
_FOOTER_ROI = (810, 625, 192, 52)
_SOURCES = ('unspecified', 'saved_frame', 'live_maa')


def _ocr(expected, roi, *, only_rec=False):
    return dict(recognition='OCR', expected=expected, roi=list(roi), only_rec=only_rec,
                threshold=THRESHOLD, action='DoNothing', next=[], timeout=1000,
                pre_delay=0, post_delay=0)


def area_pipeline():
    """Scoped, input-free overrides; requires the resource's stable map guard.

    The Or fallback records absent total OCR; it never supplies a configured or
    protocol total. And evaluates map, counter and footer on the same image.
    """
    return {
        'LabyAreaLabel': _ocr('^区域$', LABEL_ROI),
        'LabyAreaCurrent': _ocr('^[1-5]$', CURRENT_ROI, only_rec=True),
        'LabyAreaTotal': _ocr('^/[1-5]$', TOTAL_ROI, only_rec=True),
        'LabyAreaTotalNotObserved': dict(recognition='DirectHit', action='DoNothing',
            next=[], pre_delay=0, post_delay=0),
        'LabyAreaTotalOptional': dict(recognition='Or',
            any_of=['LabyAreaTotal', 'LabyAreaTotalNotObserved'], action='DoNothing',
            next=[], pre_delay=0, post_delay=0),
        'LabyAreaObserved': dict(recognition='And', all_of=['LabyMapStableObserved',
            'LabyAreaLabel', 'LabyAreaCurrent', 'LabyAreaTotalOptional'],
            action='DoNothing', next=[], timeout=3000, pre_delay=0, post_delay=0),
    }


def _unknown():
    raise BatchStopped('area_readback_unknown')


def _inside(box, roi):
    return (type(box) is list and len(box) == 4 and all(type(v) is int for v in box)
        and box[2] > 0 and box[3] > 0 and box[0] >= roi[0] and box[1] >= roi[1]
        and box[0] + box[2] <= roi[0] + roi[2]
        and box[1] + box[3] <= roi[1] + roi[3])


def _rows(raw, expected, seen):
    if (type(raw) is not list or len(raw) != len(expected)
            or any(type(r) is not dict or type(r.get('name')) is not str for r in raw)
            or {r['name'] for r in raw} != set(expected)):
        _unknown()
    result = {}
    for row in raw:
        name = row['name']
        if name in seen:
            _unknown()
        seen.add(name)
        result[name] = row
    return result


def _group(row, algorithm, expected, seen):
    if row.get('algorithm') != algorithm:
        _unknown()
    return _rows(row.get('detail'), expected, seen)


def _word(row, roi, *, strict_all=False):
    detail = row.get('detail')
    raw = detail.get('filtered') if type(detail) is dict else None
    if row.get('algorithm') != 'OCR' or type(raw) is not list or len(raw) != 1:
        _unknown()
    word = raw[0]
    if type(word) is not dict:
        _unknown()
    text, score, box = word.get('text'), word.get('score'), word.get('box')
    if (type(text) is not str or not text or len(text) > 16
            or type(score) not in (int, float) or not THRESHOLD <= score <= 1
            or not math.isfinite(score) or not _inside(box, roi)
            or not _inside(row.get('box'), roi) or row['box'] != box or detail.get('best') != word
            or type(detail.get('all')) is not list
            or strict_all and detail['all'] != raw):
        _unknown()
    return text


def _stable_map(row, seen):
    stable = _group(row, 'And', ('LabyMapObserved', 'LabyMapFooterBright'), seen)
    words = _group(stable['LabyMapObserved'], 'And', tuple(_MAP_WORDS), seen)
    for name, (roi, text) in _MAP_WORDS.items():
        if _word(words[name], roi) != text:
            _unknown()
    footer = stable['LabyMapFooterBright']
    detail = footer.get('detail')
    raw = detail.get('filtered') if type(detail) is dict else None
    if (footer.get('algorithm') != 'ColorMatch' or type(raw) is not list
            or len(raw) != 1 or type(raw[0]) is not dict):
        _unknown()
    box, count = raw[0].get('box'), raw[0].get('count')
    if (not _inside(box, _FOOTER_ROI) or type(count) is not int
            or not 6000 <= count <= box[2] * box[3]
            or not _inside(footer.get('box'), _FOOTER_ROI) or footer['box'] != box
            or detail.get('best') != raw[0]):
        _unknown()


def _total(row, seen):
    raw = row.get('detail')
    if row.get('algorithm') != 'Or' or type(raw) is not list:
        _unknown()
    names = ('LabyAreaTotal',) if len(raw) == 1 else (
        'LabyAreaTotal', 'LabyAreaTotalNotObserved')
    values = _rows(raw, names, seen)
    total = values['LabyAreaTotal']
    if len(raw) == 1:
        text = _word(total, TOTAL_ROI, strict_all=True)
        if re.fullmatch(r'/[1-5]', text) is None:
            _unknown()
        return int(text[1]), 'ocr'
    # Maa Or retains failed preceding rows. A fallback must carry no successful
    # filtered total, rather than silently masking conflicting successful OCR.
    detail = total.get('detail')
    fallback = values['LabyAreaTotalNotObserved']
    if (total.get('algorithm') != 'OCR' or total.get('box') is not None
            or type(detail) is not dict or detail.get('filtered') != []
            or detail.get('best') is not None or type(detail.get('all')) is not list
            or fallback.get('algorithm') != 'DirectHit'
            or fallback.get('detail') is not None
            or not _inside(fallback.get('box'), (0, 0, 1280, 720))
            or fallback['box'] != [0, 0, 1280, 720]):
        _unknown()
    return None, 'not_observed'


def area_readback(receipt, *, expected_task_id=None, source='unspecified'):
    """Parse this one native call only. No freshness or transition authorization.

    Pass the task ID from the call being consumed to reject an older receipt.
    Even source='live_maa' is only provenance supplied by the caller; it cannot
    certify a new capture. The caller must separately bind fresh frame evidence.
    """
    if (type(receipt) is not dict or receipt.get('native_ok') is not True
            or receipt.get('tasker_inited') is not True
            or type(receipt.get('task_id')) is not int or receipt['task_id'] <= 0
            or type(source) is not str or source not in _SOURCES):
        _unknown()
    task_id = receipt['task_id']
    if expected_task_id is not None:
        if type(expected_task_id) is not int or expected_task_id <= 0:
            _unknown()
        if expected_task_id != task_id:
            raise BatchStopped('area_task_mismatch')
    nodes = receipt.get('nodes')
    if (type(nodes) is not list or len(nodes) != 1 or type(nodes[0]) is not dict
            or nodes[0].get('name') != 'LabyAreaObserved'
            or nodes[0].get('completed') is not True):
        _unknown()
    seen = {'LabyAreaObserved'}
    rows = _rows(nodes[0].get('recognition'), area_pipeline()['LabyAreaObserved']['all_of'], seen)
    _stable_map(rows['LabyMapStableObserved'], seen)
    if _word(rows['LabyAreaLabel'], LABEL_ROI, strict_all=True) != '区域':
        _unknown()
    current = _word(rows['LabyAreaCurrent'], CURRENT_ROI, strict_all=True)
    if re.fullmatch(r'[1-5]', current) is None:
        _unknown()
    total, total_source = _total(rows['LabyAreaTotalOptional'], seen)
    current = int(current)
    if total is not None and current > total:
        raise BatchStopped('area_readback_conflicting')
    return dict(current_area=current, total=total, total_source=total_source,
                task_id=task_id, source=source, fresh_verified=False)
