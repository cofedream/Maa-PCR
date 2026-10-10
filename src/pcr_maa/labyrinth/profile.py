"""Independent Maa observation of the player's own editable profile and ID.

All six labels are recognized in one native And frame. The general numeric OCR
never receives an expected account; comparison belongs to the caller after
profile_player_id parses the actual readback. No input or handoff is provided.
"""
import math,re,time
from .. import native
from .batch import BatchStopped
from .interruptions import date_changed_pipeline
from .session import reconnect_pipeline

PROFILE_NODE="LabyClientProfileObserved"
PROFILE_WAIT="LabyClientProfileWait"
_UID="LabyClientProfilePlayerId"
_LABELS={
    "LabyClientProfileTitle":([80,0,135,76],r"^简介$"),
    "LabyClientProfileGenerate":([1030,14,220,55],r"^生成简介$"),
    "LabyClientProfileChangeRole":([650,285,132,53],r"^变更角色$"),
    "LabyClientProfileIdLabel":([645,555,137,40],r"^玩家\s*ID$"),
    _UID:([795,554,230,41],r"^\s*[0-9][0-9\s]*$"),
    "LabyClientProfileIdCopy":([1040,550,183,51],r"^ID\s*复制$"),
}


def profile_pipeline(*,wait_ms=10000):
    if type(wait_ms) is not int or not 1<=wait_ms<=10000:raise ValueError("profile_wait_invalid")
    nodes=reconnect_pipeline();nodes.pop("LabyDisconnectReturnTitleOnce")
    readonly=date_changed_pipeline();readonly.pop("LabyDateChangedConfirmOnce");nodes.update(readonly)
    for name,(roi,pattern) in _LABELS.items():
        nodes[name]=dict(recognition="OCR",expected=pattern,roi=list(roi),threshold=.8,
            action="DoNothing",next=[],timeout=1000,pre_delay=0,post_delay=0)
    nodes[PROFILE_NODE]=dict(recognition="And",all_of=list(_LABELS),action="DoNothing",next=[],
        timeout=wait_ms,rate_limit=250,pre_delay=0,post_delay=0)
    nodes[PROFILE_WAIT]=dict(recognition="Or",any_of=["LabyDateChangedObserved","LabyDisconnect6002Observed",PROFILE_NODE],
        action="DoNothing",next=[],timeout=wait_ms,rate_limit=250,pre_delay=0,post_delay=0)
    return nodes


def _unknown():raise ValueError("game_account_unknown")


def _inside(box,roi):
    return (type(box) is list and len(box)==4 and all(type(n) is int for n in box)
        and box[2]>0 and box[3]>0 and roi[0]<=box[0] and roi[1]<=box[1]
        and box[0]+box[2]<=roi[0]+roi[2] and box[1]+box[3]<=roi[1]+roi[3])


def _root(receipt,name):
    if (type(receipt) is not dict or receipt.get("native_ok") is not True
        or receipt.get("tasker_inited") is not True or type(receipt.get("task_id")) is not int
        or receipt["task_id"]<=0):_unknown()
    nodes=receipt.get("nodes")
    if (type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict
        or nodes[0].get("name")!=name or nodes[0].get("completed") is not True):_unknown()
    action=nodes[0].get("action")
    if (type(action) is not dict or action.get("name")!=name or action.get("success") is not True
        or action.get("raw")!={}):_unknown()
    return nodes[0]


def _word(row,name,roi,pattern):
    if type(row) is not dict or row.get("name")!=name or row.get("algorithm")!="OCR":_unknown()
    detail=row.get("detail");words=detail.get("filtered") if type(detail) is dict else None
    if (type(words) is not list or len(words)!=1 or type(words[0]) is not dict
        or detail.get("all")!=words or detail.get("best")!=words[0]
        or row.get("box")!=words[0].get("box")):_unknown()
    word=words[0];text=word.get("text");score=word.get("score")
    if type(text) is not str or not 1<=len(text)<=64 or not _inside(word.get("box"),roi):_unknown()
    if type(score) not in (int,float):_unknown()
    try:valid=math.isfinite(score) and .8<=score<=1
    except (OverflowError,ValueError):valid=False
    if not valid or re.fullmatch(pattern,text) is None:_unknown()
    return word


def profile_player_id(receipt):
    """Actual positive signed-64 player ID from one complete own-profile root."""
    raw=_root(receipt,PROFILE_NODE).get("recognition")
    names=list(_LABELS)
    if (type(raw) is not list or len(raw)!=len(names)
        or any(type(row) is not dict for row in raw)
        or [row.get("name") for row in raw]!=names):_unknown()
    actual=None
    for row,(name,(roi,pattern)) in zip(raw,_LABELS.items()):
        word=_word(row,name,roi,pattern)
        if name==_UID:
            digits=re.sub(r"\s+","",word["text"])
            if not 1<=len(digits)<=19:_unknown()
            actual=int(digits)
    if type(actual) is not int or not 0<actual<=2**63-1:_unknown()
    return actual


def _wait_page(receipt):
    raw=_root(receipt,PROFILE_WAIT).get("recognition")
    names=["LabyDateChangedObserved","LabyDisconnect6002Observed",PROFILE_NODE]
    if (type(raw) is not list or not 1<=len(raw)<=3
        or any(type(row) is not dict or row.get("algorithm")!="And" for row in raw)
        or [row.get("name") for row in raw]!=names[:len(raw)]
        or any(row.get("box") is not None for row in raw[:-1])
        or not _inside(raw[-1].get("box"),[0,0,1280,720])):_unknown()
    if raw[-1]["name"]=="LabyDateChangedObserved":raise BatchStopped("date_changed")
    if raw[-1]["name"]=="LabyDisconnect6002Observed":raise BatchStopped("disconnected")


def observe_profile(tasker,*,stopped=None,timeout=10):
    """Bounded zero-input wait, followed by a separate fresh native profile root.

    Native waits may include OCR/callback time. Borrowed Context uses synchronous
    SDK dispatch, so its deadline is checked before and after the SDK returns.
    """
    if stopped is not None and not callable(stopped):raise ValueError("profile_stop_invalid")
    if type(timeout) not in (int,float):raise ValueError("profile_wait_invalid")
    try:valid=math.isfinite(timeout) and 0<timeout<=10
    except (OverflowError,ValueError):valid=False
    if not valid:raise ValueError("profile_wait_invalid")
    def checkpoint():
        if (stopped is not None and stopped()) or getattr(tasker,"stopping",False):raise BatchStopped("stopped")
    checkpoint()
    token=native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(),time.monotonic()+timeout))
    try:
        if not tasker.resource.override_pipeline(profile_pipeline(wait_ms=math.ceil(timeout*1000))):
            raise BatchStopped("profile_pipeline_unavailable")
        checkpoint();waited=native.run(tasker,PROFILE_WAIT);checkpoint();_wait_page(waited)
        # Do not rename an OR leaf or reuse its UID. This is a new completed
        # native task using a fresh frame and the original six-label And root.
        actual=native.run(tasker,PROFILE_NODE);checkpoint();profile_player_id(actual)
        return actual
    finally:native.RUN_DEADLINE.reset(token)
