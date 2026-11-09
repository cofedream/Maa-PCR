"""Shared geometry and facts for joined-page readers.

Identity is decided in portrait_cache._fullpack_joined_readback from the shared
full icon pack plus a native-name cross-check. This module only carries the
measured page geometry, the OCR node helper and the Arrival fact type shared by
the pipeline builders; it never resolves a name on its own.
"""
from dataclasses import dataclass

TITLE_ROI = (520, 94, 250, 46)
MESSAGE_ROI = (485, 150, 335, 53)
NAME_ROIS = tuple((415, y, 440, 53) for y in (238, 332, 426))
CLOSE_ROI = (500, 547, 278, 66)
THRESHOLD = .85


@dataclass(frozen=True)
class Arrival:
    slot: int
    character_id: str
    raw_text: str
    canonical_name: str
    alias_source: str
    box: tuple[int, int, int, int]
    score: float


def _ocr(expected, roi):
    return dict(recognition='OCR', expected=expected, roi=list(roi), threshold=THRESHOLD,
        action='DoNothing', next=[], timeout=1500, pre_delay=0, post_delay=0)
