"""Read recognition metadata from a single custom-recognition callback frame."""
import ctypes
import json
from dataclasses import dataclass
from functools import lru_cache

from maa.buffer import ImageBuffer, RectBuffer, StringBuffer
from maa.context import Context
from maa.define import MaaBool, MaaRecoId, Rect
from maa.library import Library
from .recognition_metadata import supports_metadata_binding


_supported_binding = lru_cache(maxsize=1)(supports_metadata_binding)


@dataclass(frozen=True, slots=True)
class _FrameResult:
    reco_id: int
    hit: bool
    box: Rect | None
    raw_detail: object


def same_frame_context(context, image):
    if type(context) is not Context or not _supported_binding():
        return context
    return _FrameContext(context, image)


class _FrameContext:
    def __init__(self, context, image):
        self._context = context
        self._image = image
        self._buffer = None
        self.tasker = context.tasker
        self.get_node_data = context.get_node_data
        self.wait_freezes = context.wait_freezes

    def run_recognition(self, entry, image, pipeline_override=None):
        # Enlarged OCR crops and any other frame must use their own pixels.
        if image is not self._image:
            return self._context.run_recognition(entry, image, pipeline_override)
        if self._buffer is None:
            buffer = ImageBuffer()
            if not buffer.set(image):
                raise RuntimeError('Cannot load recognition frame')
            self._buffer = buffer

        api = Library.framework()
        rid = api.MaaContextRunRecognition(self._context._handle,
            *Context._gen_post_param(entry, {} if pipeline_override is None else pipeline_override),
            self._buffer._handle)
        if not rid:
            return None
        hit, box, raw = MaaBool(), RectBuffer(), StringBuffer()
        # As for task receipts, leave raw and draw images in the SDK's diagnostic cache.
        name, algorithm = StringBuffer(), StringBuffer()
        if not api.MaaTaskerGetRecognitionDetail(self.tasker._handle, MaaRecoId(rid),
                name._handle, algorithm._handle, ctypes.pointer(hit), box._handle,
                raw._handle, None, None):
            return None
        return _FrameResult(rid, bool(hit), box.get() if hit else None, json.loads(raw.get()))
