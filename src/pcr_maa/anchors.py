"""Shared page anchors: one definition, OCR first, icon fallback second.

A page anchor set is the sampled words of one page layout (the exact word, its
tight word box and the layout it belongs to) plus one icon row that renders with
the same pixels on every home skin. The OCR leaves are the primary proof; the
icon leaves are the working fallback for clients whose font rendering the
shipped recognizer misreads (a confident but wrong word cannot be tuned away by
ROI, threshold or scaling, and the same word can decode differently across the
home page's animated backgrounds).

Both flavours are generated here so daily and labyrinth share one definition
instead of two copies: `assets/resource/pipeline/home_text.json` must equal
`ocr_nodes()` exactly (asserted by tests), and `icon_pixel_nodes()` only adds
names that document does not declare. Images live under
`assets/resource/image/<anchor image root>/<page>/icons/<anchor>.png`; the icon
templates are the client's own button art flattened over the white plate that
sits behind it, so they match 1:1 at 1280x720 and do not depend on the animated
page background. A missing or empty file cannot match, so an unsampled icon
simply stays inert.

Activation stays explicit: this module never decides which guard a caller
trusts. Labyrinth points its own client-home guard at `anchor_guard()`; daily
keeps the shared OCR document until the game-image provenance path is opened.
"""
import re
from dataclasses import dataclass

# Relative to `assets/resource/image`; every page gets its own folder.
IMAGE_ROOT = 'anchors'
FRAME = (1280, 720)
# Icon art is composited over the white button plate, so one template covers every
# home skin. 0.85 keeps every measured home score (0.859-0.965 across three themes;
# the gift icon is the floor) while the labyrinth-lobby negative stays an order of
# magnitude below (0.14-0.26). The gift floor leaves only ~9 points of headroom: if a
# future theme pushes an icon under the threshold, add that theme's template variant
# (TemplateMatch takes a list) instead of lowering this value blindly; 0.82 is the
# measured-safe lower bound, and any change must re-run the three-theme + lobby set.
ICON_THRESHOLD = .85


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
class Icon:
    """One home-page button icon: its on-screen box and the flattened template."""
    name: str
    box: tuple
    threshold: float = ICON_THRESHOLD


@dataclass(frozen=True)
class Layout:
    name: str
    words: tuple


@dataclass(frozen=True)
class Page:
    page: str
    layouts: tuple
    anchors: tuple
    icons: tuple
    image: str = IMAGE_ROOT
    guard: str = 'HomeAnchorLayout'
    window: int = 15

    def anchor(self, name):
        return next(anchor for anchor in self.anchors if anchor.name == name)

    def icon(self, name):
        return next(icon for icon in self.icons if icon.name == name)


# 1280x720 client home, sampled from real frames. The shifted layout moves only
# the schedule and gift words; adventure keeps its box in both layouts.
HOME = Page('home',
            layouts=(Layout('Default', ('Adventure', 'Schedule', 'Gift')),
                     Layout('Edge', ('Adventure', 'EdgeSchedule', 'EdgeGift'))),
            anchors=(Anchor('Adventure', '^冒险$', (693, 685, 53, 33)),
                     Anchor('Schedule', '^日程表$', (986, 594, 68, 34)),
                     Anchor('Gift', '^礼物$', (1188, 596, 51, 30)),
                     Anchor('EdgeSchedule', '^日程表$', (1028, 598, 62, 24)),
                     Anchor('EdgeGift', '^礼物$', (1230, 596, 50, 30))),
            icons=(Icon('Schedule', (989, 549, 56, 49)),
                   Icon('Gift', (1188, 548, 53, 53)),
                   Icon('Shop', (893, 549, 54, 53)),
                   Icon('Mission', (1090, 550, 52, 52))))


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


def icon_template_path(page, icon_name):
    snake = re.sub(r'(?<!^)(?=[A-Z])', '_', icon_name).lower()
    return page.image + '/' + page.page + '/icons/' + snake + '.png'


def search_window(roi, pad, frame=FRAME):
    """Pad a word box into a small search window, clipped to the frame."""
    x, y, w, h = roi
    left = max(0, x - pad)
    top = max(0, y - pad)
    right = min(frame[0], x + w + pad)
    bottom = min(frame[1], y + h + pad)
    return [left, top, right - left, bottom - top]


def icon_pixel_nodes(page=HOME):
    """The home button icons as templates, each searched only inside its own window."""
    return {'HomeIcon' + icon.name + 'Pixels': dict(
        recognition='TemplateMatch', roi=search_window(icon.box, page.window),
        template=[icon_template_path(page, icon.name)], threshold=icon.threshold,
        **_do_nothing()) for icon in page.icons}


def layout_nodes(page=HOME):
    """Icon layout plus the OCR-first guard over the page's own layouts."""
    nodes = {'HomeIconPixelsLayout': dict(recognition='And',
        all_of=['HomeIcon' + icon.name + 'Pixels' for icon in page.icons], **_do_nothing())}
    nodes[page.guard] = dict(recognition='Or',
        any_of=['HomeTextLayout', 'HomeIconPixelsLayout'], **_do_nothing())
    return nodes


def nodes(page=HOME):
    """Everything a caller needs to observe the page with and without pixels."""
    return dict(ocr_nodes(page), **icon_pixel_nodes(page), **layout_nodes(page))


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
