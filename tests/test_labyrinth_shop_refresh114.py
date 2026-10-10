"""Real refresh confirmation layout; saved-image native replay, never a game connection."""
from pathlib import Path
import json
import pytest
from pcr_maa.labyrinth.shop_execution import confirmation_terms
from test_labyrinth_shop_execution import setup,finish,frame,offline

ROOT=Path(__file__).resolve().parents[1]
SCREEN=ROOT/'.runtime/acceptance-after109/.runtime/labyrinth-work/runs/050a5bff02924d759ba0c92d2e817faa/acceptance-ending/screen.png'

def test_actual114_refresh_quote_reads_required300_not_balance3160_without_input(tmp_path):
    if not SCREEN.is_file():pytest.skip('private actual114 refresh evidence excluded from public source')
    path,controller,tasker,token,shop,batch,stamp=setup(tmp_path,'actual114-refresh',[frame(SCREEN)])
    try:
        value,receipt=shop._capture()
        path.mkdir(parents=True,exist_ok=True)
        (path/'refresh-native-read.json').write_text(json.dumps(dict(value=value,native=receipt),ensure_ascii=False,indent=2),encoding='utf-8')
        assert value['state']=='refresh_confirmation' and not controller.clicks and not controller.swipes
        body,price=confirmation_terms(value)
        assert price==300 and '3,160' in body
    finally:finish(tasker,token)


from pcr_maa.labyrinth.batch import BatchStopped

def row_words():
    def word(text,box):return dict(text=text,box=box,score=.99)
    return [word('要消耗阿尔法金币更新列表吗？',[480,282,322,25]),
        word('所需阿尔法金币',[360,365,155,27]),word('300',[875,365,43,22]),
        word('持有阿尔法金币',[360,405,155,27]),word('3,160',[858,405,60,22])]

@pytest.mark.parametrize('state',['confirmation','complete','refresh_confirmation'])
def test_same_frame_required_row_works_for_all_shared_quote_callers(state):
    assert confirmation_terms(dict(state=state,words={'DialogBody':row_words()}))[1]==300

@pytest.mark.parametrize('change',['missing','balance_only','wrong_currency','duplicate_label','duplicate_amount','conflicting_inline','invalid_box'])
def test_required_quote_never_uses_balance_ambiguous_or_conflicting_price(change):
    words=row_words()
    if change=='missing':words.pop(2)
    elif change=='balance_only':words[1]['text']='持有阿尔法金币'
    elif change=='wrong_currency':words[1]['text']='所需宝石'
    elif change=='duplicate_label':words.append(dict(words[1]))
    elif change=='duplicate_amount':words.append(dict(words[2],box=[780,368,35,22]))
    elif change=='conflicting_inline':words.append(dict(text='消耗阿尔法金币×301',box=[330,220,400,25],score=.99))
    else:words[1]['box']=None
    with pytest.raises(BatchStopped,match='shop_quote_unknown'):
        confirmation_terms(dict(state='refresh_confirmation',words={'DialogBody':words}))


def test_actual114_refresh_fresh_guard_checks_price_before_one_saved_image_click(tmp_path):
    if not SCREEN.is_file():pytest.skip('private actual114 refresh evidence excluded from public source')
    path,controller,tasker,token,shop,batch,stamp=setup(tmp_path,'actual114-guard',[frame(SCREEN)])
    try:
        current,_=shop._capture();box=current['words']['Confirm'][0]['box']
        for wrong in (301,3160):
            with pytest.raises(BatchStopped,match='shop_input_unknown'):
                shop._input('refresh_confirmation',box,price=wrong)
            assert not controller.clicks
        receipt=shop._input('refresh_confirmation',box,price=300)
        assert receipt['native_ok'] and len(controller.clicks)==1 and not controller.swipes
    finally:finish(tasker,token)


@pytest.mark.parametrize('balance',[2860,2861])
def test_actual_quote_then_refresh_ack_needs_independent_balance_readback(tmp_path,balance):
    if not SCREEN.is_file():pytest.skip('private actual114 refresh evidence excluded from public source')
    path,controller,tasker,token,shop,batch,stamp=setup(tmp_path,'actual114-following',[frame(SCREEN)])
    try:
        dialog,_=shop._capture()
        # Actual quote, simulated ACK and successor; no claim of real refresh spending.
        states=iter([(dialog,{}),(dict(state='shop',balance=balance,items=[]),{})]);calls=[]
        shop._wait_state=lambda *a:next(states)
        def ack(state,*a,**kw):
            assert batch.journal.events()[-1]['state']=='in_flight'
            calls.append(state);return dict(native_ok=True)
        shop._input=ack
        before=dict(balance=3160,refresh_allowed=True,words={'Refresh':[dict(text='用300更新',box=[1,1,10,10])]})
        if balance==2860:
            assert shop._refresh(batch,114,30601,before,0)['refresh_verified']
        else:
            with pytest.raises(BatchStopped,match='shop_refresh_result_unknown'):
                shop._refresh(batch,114,30601,before,0)
        settled=[e for e in batch.journal.events() if e['state']=='settled']
        assert [e['operation'] for e in settled]==['shop_refresh_proposal','shop_refresh_purchase']
        assert len({e['operation_id'] for e in settled})==2
        assert calls==['refresh','refresh_confirmation'] and not controller.clicks
    finally:finish(tasker,token)
