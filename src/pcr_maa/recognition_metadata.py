"""Image-free receipt fields for supported Maa bindings, never image evidence."""
import ctypes
import json
import re
import sys
from functools import lru_cache
from importlib.metadata import distributions
from pathlib import Path

from maa.buffer import RectBuffer, StringBuffer
from maa.define import MaaActId, MaaBool, MaaNodeId, MaaRecoId, TaskDetail
from maa.library import Library
from maa.tasker import Tasker

from . import player_log


_VERSION = re.compile(r'[0-9]{1,3}(?:\.[0-9]{1,3}){2}')


def _maafw_versions():
    # An in-place update can leave an old dist-info directory without METADATA.
    return {value.removeprefix('v') for package in distributions(name='maafw')
            if (value := package.version)}


def _binding_status():
    loaded = Library.version()
    try:
        versions = _maafw_versions()
        if loaded == 'v5.16.1' and versions == {'5.13.0', '5.16.1'}:
            python = Path(sys.executable).resolve().parent
            if python.name.casefold() == 'python' and python.parent.name.casefold() == 'runtime':
                # The first upgrade to this release is performed by the older updater.
                from .player_maintenance import repair_legacy_maafw_metadata
                repair_legacy_maafw_metadata(python.parent.parent)
                versions = _maafw_versions()
    except (OSError, UnicodeError):
        return loaded, None, '包元数据无法读取'
    visible = sorted(version for version in versions if _VERSION.fullmatch(version))
    if loaded not in ('v5.13.0', 'v5.16.1'):
        reason = 'DLL 版本不受支持'
    elif len(visible) != len(versions):
        reason = '包元数据含无效版本字段'
    elif not visible:
        reason = '缺少有效的包版本元数据'
    elif len(visible) > 1:
        reason = '发现多个包版本'
    elif visible[0] != loaded[1:]:
        reason = 'DLL 与包版本不一致'
    else:
        reason = None
    return loaded, visible, reason


def supports_metadata_binding():
    _, _, reason = _binding_status()
    return reason is None


@lru_cache(maxsize=1)
def _api():
    # Only _handle bridges the fixed Python binding to the public C API.
    loaded, versions, reason = _binding_status()
    if reason is not None:
        dll = loaded if isinstance(loaded, str) and _VERSION.fullmatch(loaded.removeprefix('v')) else '未知'
        packages = '未知'
        if versions is not None:
            packages = '、'.join(versions[:5]) or '无'
            if len(versions) > 5:
                packages += '等'
        player_log.text(f'MaaFramework 回执使用常规 SDK（DLL {dll}，包版本 {packages}，{reason}）。')
        return None
    return Library.framework()


def read_receipt_nodes(tasker, detail, *, check):
    """Read fresh native IDs without touching or extending the image cache.

    ``check`` is the caller's active owner/stop/deadline check. Remote calls are
    synchronous: the owning process must retain its real termination boundary.
    Return None when the optimized backend is unavailable before reading.
    Once a read starts, exceptions propagate without a partial receipt.
    """
    if type(tasker) is not Tasker or type(detail) is not TaskDetail:
        raise TypeError('Expected the fixed Maa Tasker and TaskDetail')
    api = _api()
    if api is None:
        return None
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
