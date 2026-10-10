"""Date-change interruption observation and explicit one-shot diagnostic input.

Production batches must stop on this readback. Confirming the dedicated notice
is diagnostic navigation only; it never resumes or repeats a prior game action.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json,math,re
from maa.custom_recognition import CustomRecognition
from maa.define import AlgorithmEnum
from .batch import BatchStopped

THRESHOLD=.85
PANEL_ROI=(325,175,630,365)
_PAGE={
    'Title':((510,175,260,48),r'^日期变更$'),
    'Message':((490,325,310,65),r'^日期已变更[。.]$'),
    'Confirm':((505,461,270,66),r'^确认$'),
}
_CUSTOM='LabyDateChangedDiagnosticConfirm'
_CONFIRM='LabyDateChangedConfirmOnce'


@dataclass(frozen=True)
class DateChanged:
    message: str
    confirm_box: tuple[int,int,int,int]
    reason: str='date_changed'
    batch_must_stop: bool=True


def _unknown():raise BatchStopped('date_changed_unknown')


def _inside(box,roi):
    return (type(box) in (list,tuple) and len(box)==4 and all(type(v) is int for v in box)
        and box[2]>0 and box[3]>0 and box[0]>=roi[0] and box[1]>=roi[1]
        and box[0]+box[2]<=roi[0]+roi[2] and box[1]+box[3]<=roi[1]+roi[3])


def _ocr(pattern,roi):
    return dict(recognition='OCR',expected=pattern,roi=list(roi),threshold=THRESHOLD,
        action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)


def date_changed_pipeline():
    nodes={'LabyDateChanged'+name:_ocr(pattern,roi) for name,(roi,pattern) in _PAGE.items()}
    nodes['LabyDateChangedWhole']=_ocr('.+',PANEL_ROI)
    nodes['LabyDateChangedObserved']=dict(recognition='And',
        all_of=['LabyDateChanged'+name for name in _PAGE]+['LabyDateChangedWhole'],
        action='DoNothing',next=[],timeout=1500,pre_delay=0,post_delay=0)
    nodes[_CONFIRM]=dict(recognition='Custom',custom_recognition=_CUSTOM,
        custom_recognition_param=dict(diagnostic=False),action='Click',target=True,
        max_hit=1,next=[],timeout=1500,rate_limit=250,pre_delay=0,post_delay=0)
    return nodes


def _words(row,roi,count):
    if type(row) is not dict or row.get('algorithm')!='OCR':_unknown()
    detail=row.get('detail')
    if type(detail) is not dict:_unknown()
    words=detail.get('filtered')
    if (type(words) is not list or len(words)!=count or any(type(w) is not dict for w in words)
        or detail.get('all')!=words
        or detail.get('best')!=words[0] or row.get('box')!=words[0].get('box')):_unknown()
    result=[]
    for word in words:
        if type(word) is not dict:_unknown()
        text,box,score=word.get('text'),word.get('box'),word.get('score')
        if (type(text) is not str or not text or len(text)>24 or any(ord(c)<32 for c in text)
            or type(box) is not list or not _inside(box,roi) or type(score) not in (int,float)
            or not THRESHOLD<=score<=1 or not math.isfinite(score)):_unknown()
        result.append((text,tuple(box)))
    if len({box for _,box in result})!=len(result):_unknown()
    return tuple(result)


def _rows_readback(raw):
    names=date_changed_pipeline()['LabyDateChangedObserved']['all_of']
    if (type(raw) is not list or len(raw)!=len(names)
        or any(type(row) is not dict or type(row.get('name')) is not str for row in raw)
        or {row['name'] for row in raw}!=set(names)):_unknown()
    rows={row['name']:row for row in raw};labels={}
    for name,(roi,pattern) in _PAGE.items():
        word=_words(rows['LabyDateChanged'+name],roi,1)[0]
        if re.fullmatch(pattern,word[0]) is None:_unknown()
        labels[name]=word
    # Every word in the full dedicated modal must belong to exactly one label;
    # extra cancel/confirm/text stops instead of generalising a 确认 button.
    whole=_words(rows['LabyDateChangedWhole'],PANEL_ROI,3)
    used=set()
    for text,box in whole:
        matching=[name for name,(roi,pattern) in _PAGE.items()
                  if re.fullmatch(pattern,text) is not None and _inside(box,roi)]
        if len(matching)!=1 or matching[0] in used:_unknown()
        name=matching[0];used.add(name)
        if text!=labels[name][0]:_unknown()
    if used!=set(_PAGE):_unknown()
    return DateChanged(labels['Message'][0],labels['Confirm'][1])


def date_changed_readback(receipt):
    """An interruption only: caller must end the batch, with no resumed input."""
    if (type(receipt) is not dict or receipt.get('native_ok') is not True
        or receipt.get('tasker_inited') is not True or type(receipt.get('task_id')) is not int
        or receipt['task_id']<=0):_unknown()
    nodes=receipt.get('nodes')
    if (type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict
        or nodes[0].get('name')!='LabyDateChangedObserved' or nodes[0].get('completed') is not True):_unknown()
    return _rows_readback(nodes[0].get('recognition'))


def _validated(value):
    if (type(value) is not DateChanged or type(value.message) is not str
        or re.fullmatch(_PAGE['Message'][1],value.message) is None
        or type(value.confirm_box) is not tuple or not _inside(value.confirm_box,_PAGE['Confirm'][0])
        or value.reason!='date_changed' or value.batch_must_stop is not True):_unknown()
    return value


def register_interruption_recognition(resource):
    if not resource.register_custom_recognition(_CUSTOM,DateChangedConfirmRecognition()):
        raise BatchStopped('date_changed_registration_failed')


class DateChangedConfirmRecognition(CustomRecognition):
    def analyze(self,context,argv):
        try:
            params=json.loads(argv.custom_recognition_param)
            if (type(params) is not dict or set(params)!={'diagnostic','message'}
                or params['diagnostic'] is not True or context.tasker.stopping):return None
            result=context.run_recognition('LabyDateChangedObserved',argv.image)
            if result is None or result.algorithm!=AlgorithmEnum.And or result.hit is not True:return None
            value=_rows_readback(result.raw_detail)
            if params['message']!=value.message or context.tasker.stopping:return None
            return self.AnalyzeResult(list(value.confirm_box),dict(reason='date_changed',
                diagnostic_only=True,batch_must_stop=True))
        except Exception:return None


@contextmanager
def date_changed_guard(tasker,value):
    """Diagnostic-only confirmation; no hit reset, resume, movement or battle."""
    value=_validated(value);resource=tasker.resource;original={_CONFIRM:resource.get_node_data(_CONFIRM)}
    if not original[_CONFIRM] or not resource.override_pipeline({_CONFIRM:dict(
            custom_recognition_param=dict(diagnostic=True,message=value.message))}):
        raise BatchStopped('date_changed_guard_failed')
    try:yield
    except BaseException:
        try:resource.override_pipeline(original)
        except Exception:pass
        raise
    else:
        if not resource.override_pipeline(original):raise BatchStopped('date_changed_guard_restore_failed')
