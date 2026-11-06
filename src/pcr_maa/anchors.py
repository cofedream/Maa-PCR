"""Shared page anchors: one definition, OCR first, optional pixel fallback.

A page anchor set is the sampled words of one page layout: the exact word, its
tight word box and the layout it belongs to. The OCR leaves are the primary
proof and the only thing that decides a page today; the pixel leaves are an
inert fallback for clients whose font rendering the shipped v4 recognizer
misreads (a confident but wrong word cannot be tuned away by ROI, threshold or
scaling).

Both flavours are generated here so daily and labyrinth share one definition
instead of two copies: `assets/resource/pipeline/home_text.json` must equal
`ocr_nodes()` exactly (asserted by tests), and `pixel_nodes()` only adds names
that document does not declare. Images live under
`assets/resource/image/<anchor image root>/<page>/<anchor>.png`; a missing or
empty file cannot match, so an unsampled anchor simply stays inert.

Activation stays explicit: this module never decides which guard a caller
trusts. Labyrinth points its own client-home guard at `anchor_guard()`; daily
keeps the shared OCR document until the game-image provenance path is opened.
"""
import re
from dataclasses import dataclass

# Relative to `assets/resource/image`; every page gets its own folder.
IMAGE_ROOT = 'anchors'
FRAME = (1280, 720)


def word(text, roi, threshold=.8):
    """One sampled word: exact expectation on its own tight crop."""
    return dict(recognition='OCR', roi=list(roi), expected=text, threshold=threshold,
                only_rec=True, action='DoNothing', next=[], timeout=1000,
                pre_delay=0, post_delay=0)


@dataclass(frozen=True)
class Anchor:
    name: str
    text: str
    roi: tuple
    threshold: float = .8


@dataclass(frozen=True)
class Layout:
    name: str
    words: tuple


@dataclass(frozen=True)
class Page:
    page: str
    layouts: tuple
    anchors: tuple
    image: str = IMAGE_ROOT
    guard: str = 'HomeAnchorLayout'
    template_threshold: float = .9
    window: int = 15

    def anchor(self, name):
        return next(anchor for anchor in self.anchors if anchor.name == name)


# 1280x720 client home, sampled from real frames. The shifted layout moves only
# the schedule and gift words; adventure keeps its box in both layouts.
HOME = Page('home', layouts=(Layout('Default', ('Adventure', 'Schedule', 'Gift')),
                             Layout('Edge', ('Adventure', 'EdgeSchedule', 'EdgeGift'))),
            anchors=(Anchor('Adventure', '^冒险$', (693, 685, 53, 33)),
                     Anchor('Schedule', '^日程表$', (986, 594, 68, 34)),
                     Anchor('Gift', '^礼物$', (1188, 596, 51, 30)),
                     Anchor('EdgeSchedule', '^日程表$', (1028, 598, 62, 24)),
                     Anchor('EdgeGift', '^礼物$', (1230, 596, 50, 30))))


def _do_nothing(**extra):
    return dict(action='DoNothing', next=[], timeout=1000, pre_delay=0, post_delay=0, **extra)


def ocr_nodes(page=HOME):
    """The shipped word leaves and layout composites for one page."""
    nodes = {'Home' + anchor.name + 'Text': word(anchor.text, anchor.roi, anchor.threshold)
             for anchor in page.anchors}
    for layout in page.layouts:
        nodes['Home' + layout.name + 'TextLayout'] = dict(recognition='And',
            all_of=['Home' + name + 'Text' for name in layout.words], **_do_nothing())
    nodes['HomeTextLayout'] = dict(recognition='Or',
        any_of=['Home' + layout.name + 'TextLayout' for layout in page.layouts], **_do_nothing())
    return nodes


def template_path(page, anchor_name):
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', anchor_name).lower()
    return page.image + '/' + page.page + '/' + snake + '.png'


def search_window(roi, pad, frame=FRAME):
    """Pad a word box into a small search window, clipped to the frame."""
    x, y, w, h = roi
    left = max(0, x - pad)
    top = max(0, y - pad)
    right = min(frame[0], x + w + pad)
    bottom = min(frame[1], y + h + pad)
    return [left, top, right - left, bottom - top]


def pixel_nodes(page=HOME):
    """The same words as templates, each searched only inside its own window."""
    nodes = {}
    for anchor in page.anchors:
        nodes['Home' + anchor.name + 'Pixels'] = dict(
            recognition='TemplateMatch', roi=search_window(anchor.roi, page.window),
            template=[template_path(page, anchor.name)], threshold=page.template_threshold,
            **_do_nothing())
    return nodes


def layout_nodes(page=HOME):
    """Pixel layouts plus the OCR-first guard over the page's own layouts."""
    nodes = {'Home' + layout.name + 'PixelsLayout': dict(recognition='And',
        all_of=['Home' + name + 'Pixels' for name in layout.words], **_do_nothing())
        for layout in page.layouts}
    nodes[page.guard] = dict(recognition='Or', any_of=['HomeTextLayout'] + [
        'Home' + layout.name + 'PixelsLayout' for layout in page.layouts], **_do_nothing())
    return nodes


def nodes(page=HOME):
    """Everything a caller needs to observe the page with and without pixels."""
    return dict(ocr_nodes(page), **pixel_nodes(page), **layout_nodes(page))


def anchor_guard(page=HOME):
    """The composite a page observation should trust; its first branch is OCR."""
    return page.guard


def install(resource, page=HOME):
    """Add the shared nodes to a live resource; activation stays the caller's."""
    return resource.override_pipeline(nodes(page))


def guard_override(guard, page=HOME, *, panels=('HomeNoKnownLargePanel',)):
    """Partial patch that points one existing page guard at the shared composite.

    Activation is a caller decision: the shipped document keeps the OCR-only
    layout, so a pipeline that never applies this stays unchanged.
    """
    return {guard: {'all_of': [anchor_guard(page), *panels]}}
