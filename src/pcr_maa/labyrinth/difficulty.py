"""Read the actual difficulty badge beside the sampled details modal.

Only the observed difficulty-5 layout is native-qualified. The grammar accepts
1..5; qualification of other layouts is separate. This parser proves a same-frame
number, not account, enter ID, capture freshness or a newly created opening.
Merge difficulty_pipeline() into the caller-owned Resource for one bounded
native.run(tasker, 'LabyDifficultyReadbackObserved'), then parse its receipt.
All nodes are read-only and have no next page or input.
"""
import math,re
from .batch import BatchStopped
from .relics import _ocr,_words,_normalized

TITLE_ROI=(490,25,300,60)
ENEMY_ROI=(355,105,300,45)
# Whole badge width, including any leading digit; never tighten to the visible 5.
NUMBER_ROI=(943,96,90,45)
NUMBER_THRESHOLD=.8


def difficulty_pipeline():
    number=_ocr(r'^[1-5]$',NUMBER_ROI)
    # Dimmed blue outlined font: measured full-width 5 confidence .842847.
    # Keep the session OCR standard .8, full width and exact numeric grammar.
    number.update(only_rec=True,threshold=NUMBER_THRESHOLD)
    nodes={
        'LabyDifficultyReadbackTitle':_ocr('^难度详情$',TITLE_ROI),
        'LabyDifficultyReadbackEnemy':_ocr('^[·•]?强敌出现$',ENEMY_ROI),
        'LabyDifficultyReadbackNumber':number,
    }
    nodes['LabyDifficultyReadbackObserved']=dict(recognition='And',all_of=list(nodes),
        action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    return nodes


def difficulty_readback(receipt):
    """Return the observed integer only; never fill it from expected settings."""
    if type(receipt) is not dict or receipt.get('native_ok') is not True:
        raise ValueError('difficulty_unknown')
    nodes=receipt.get('nodes')
    if type(nodes) is not list or any(type(n) is not dict for n in nodes):
        raise ValueError('difficulty_unknown')
    found=[n for n in nodes if n.get('name')=='LabyDifficultyReadbackObserved']
    if len(found)!=1 or found[0].get('completed') is not True:
        raise ValueError('difficulty_unknown')
    raw=found[0].get('recognition')
    names=set(difficulty_pipeline()['LabyDifficultyReadbackObserved']['all_of'])
    if (type(raw) is not list or len(raw)!=3 or any(type(r) is not dict for r in raw)
            or any(type(r.get('name')) is not str for r in raw) or {r['name'] for r in raw}!=names):
        raise ValueError('difficulty_unknown')
    rows={r['name']:r.get('detail') for r in raw}
    try:
        title=_words(rows['LabyDifficultyReadbackTitle'],TITLE_ROI)
        enemy=_words(rows['LabyDifficultyReadbackEnemy'],ENEMY_ROI)
    except BatchStopped:
        raise ValueError('difficulty_unknown') from None
    if (_normalized(title[0].text)!='难度详情'
            or _normalized(enemy[0].text) not in ('强敌出现','·强敌出现','•强敌出现')):
        raise ValueError('difficulty_unknown')
    detail=rows['LabyDifficultyReadbackNumber']
    words=detail.get('filtered') if type(detail) is dict else None
    if type(words) is not list or len(words)!=1 or type(words[0]) is not dict:
        raise ValueError('difficulty_unknown')
    word=words[0];score=word.get('score');text=word.get('text');box=word.get('box')
    # only_rec must preserve the complete configured row. A cropped receipt can
    # otherwise turn a high-confidence 15 into 5 and must not be accepted.
    if (type(score) not in (int,float) or not math.isfinite(score) or not NUMBER_THRESHOLD<=score<=1
            or type(text) is not str or type(box) is not list or box!=list(NUMBER_ROI)
            or any(type(v) is not int for v in box)
            or re.fullmatch(r'[1-5]',_normalized(text)) is None):
        raise ValueError('difficulty_unknown')
    return int(_normalized(text))
