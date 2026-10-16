"""Image-free receipt fields for the pinned Maa 5.13 binding, never image evidence."""
import ctypes
import json
from functools import lru_cache
from importlib.metadata import version

from maa.buffer import RectBuffer, StringBuffer
from maa.define import MaaActId, MaaBool, MaaNodeId, MaaRecoId, TaskDetail
from maa.library import Library
from maa.tasker import Tasker


@lru_cache(maxsize=1)
def _api():
    # Only _handle bridges the fixed Python binding to the public C API.
    if Library.version() != 'v5.13.0' or version('maafw') != '5.13.0':
        raise RuntimeError('Unsupported Maa metadata binding; no retry')
    return Library.framework()


def read_receipt_nodes(tasker, detail, *, check):
    """Read fresh native IDs without touching or extending the image cache.

    ``check`` is the caller's active owner/stop/deadline check. Remote calls are
    synchronous: the owning process must retain its real termination boundary.
    Exceptions propagate, with no SDK fallback, dispatch, or partial receipt.
    """
    if type(tasker) is not Tasker or type(detail) is not TaskDetail:
        raise TypeError('Expected the fixed Maa Tasker and TaskDetail')
    api = _api()
    result = []
    for node_id in detail.node_id_list:
        if type(node_id) is not int or node_id <= 0:
            raise RuntimeError('Invalid native node identity; no retry')
        check()
        name, rid, aid, completed = StringBuffer(), MaaRecoId(), MaaActId(), MaaBool()
        ok = api.MaaTaskerGetNodeDetail(tasker._handle, MaaNodeId(node_id),
            name._handle, ctypes.pointer(rid), ctypes.pointer(aid), ctypes.pointer(completed))
        check()
        if not ok:
            raise RuntimeError('Missing native node detail; no retry')
        raw = None
        if rid.value:
            check()
            reco_name, algorithm, raw_json = StringBuffer(), StringBuffer(), StringBuffer()
            hit, box = MaaBool(), RectBuffer()
            ok = api.MaaTaskerGetRecognitionDetail(tasker._handle, rid,
                reco_name._handle, algorithm._handle, ctypes.pointer(hit), box._handle,
                raw_json._handle, None, None)
            check()
            if not ok:
                raise RuntimeError('Missing native recognition detail; no retry')
            # DirectHit is null and And/Or is a list; preserve SDK raw JSON.
            raw = json.loads(raw_json.get())
        action = None
        if aid.value:
            check()
            action = tasker.get_action_detail(aid.value)
            check()
            if action is None:
                raise RuntimeError('Missing native action detail; no retry')
        result.append(dict(name=name.get(), completed=bool(completed),
            action=dict(name=action.name, success=action.success, raw=action.raw_detail) if action else None,
            recognition=raw))
    check()
    return result
