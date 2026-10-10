"""Private player failures and conservative cross-page recovery; offline only."""
from copy import deepcopy
import json, os
from pathlib import Path
from types import SimpleNamespace
import pytest
from maa.custom_action import CustomAction
from pcr_maa import native, abyss
from pcr_maa.preset_scan import Page, Collector
from pcr_maa.replay import SavedImages
from test_abyss import reading, quotation


def feed(collector,page):
    return collector.run(None,SimpleNamespace(reco_detail=SimpleNamespace(raw_detail={'best':{'detail':page}})))


@pytest.fixture
def evidence():
    root=Path(os.environ.get('PCR_R35_EVIDENCE','.runtime/private-fixtures/r35'))
    if not (root/'scan-pages-original.json').is_file():pytest.skip('private player evidence required')
    return root


def recognize_page(image,kind='page'):
    resource=native.load_resource();ctrl=SavedImages([[image]]);tasker=native.bind(resource,ctrl);found=[]
    assert resource.register_custom_recognition('PresetPage',Page())
    class Read(CustomAction):
        def run(self,context,argv):
            reco=context.run_recognition('ScanPage',image,{'ScanPage':{'custom_recognition_param':kind}})
            if reco and reco.hit:
                d=reco.raw_detail['best']['detail'];found.append(json.loads(d) if isinstance(d,str)else d)
            return True
    assert resource.register_custom_action('R35ReadPage',Read())
    assert resource.override_pipeline({'R35ReadPage':dict(action='Custom',custom_action='R35ReadPage',next=[],post_delay=0)})
    assert native.run(tasker,'R35ReadPage')['native_ok']
    assert not ctrl.clicks and not ctrl.swipes
    return found[0] if found else None


def test_original_clipped_title_continues_only_from_verified_predecessor(evidence):
    pages=json.loads((evidence/'scan-pages-original.json').read_text(encoding='utf-8'))
    frame=native.read_image(evidence/'debug/on_error/2026.10.06-23.52.56.795_ScanPage.png')
    current=recognize_page(frame)
    assert current and not current['gaps'] and current['top_edge_counts']
    collector=Collector()
    for page in pages[:-1]:assert feed(collector,page)
    assert feed(collector,current),collector.reason
    assert not collector.complete and len(collector.rows)==7
    assert any(r['title']=='红焰深域8-1' for r in collector.rows)
    bad=deepcopy(current);bad['top_edge_counts'][0]['remaining']=9
    other=Collector()
    for page in pages[:-1]:assert feed(other,page)
    assert not feed(other,bad) and other.reason=='unverified_top_edge_row'
    missing=Collector();assert not feed(missing,current) and not missing.complete
    frame[300:333,65:530]=0
    broken=recognize_page(frame)
    assert broken and broken['gaps']


@pytest.mark.parametrize('remaining',[(0,0,4),(4,0,3),(0,2,0)])
def test_partial_abyss_targets_only_remaining_difficulties(remaining):
    before=reading();quote=quotation()
    for row,count in zip(before['rows'],remaining):row['remaining']=count
    quote['rows']=[dict(row,title='前哨关卡',mode='深渊讨伐战',times=row['remaining']) for row in before['rows'] if row['remaining']]
    total=sum(remaining);quote.update(stages=len(quote['rows']),total_times=total,ticket_cost=total,stamina_cost=total*25)
    assert abyss.assess_home(before)=='needs_execution'
    assert abyss.check_quote(quote,before)=='ready'
    extra=deepcopy(quote);extra['rows'].append(dict(quotation()['rows'][0]))
    assert abyss.check_quote(extra,before)=='quote_identity_mismatch'


@pytest.mark.parametrize('indices,offset', [([0], 0), ([1], 30), ([2, 0], 0), ([2, 1, 0], 0)])
def test_dynamic_native_outpost_rows(indices, offset):
    from pcr_maa.abyss_native import _Words, _quote_rows, _NoMatch
    root = os.environ.get('PCR_ABYSS_FRAME_ROOT')
    if not root:
        pytest.skip('private original preview required')
    original = native.read_image(Path(root)/'07-abyss-preview/after.png')
    image = original.copy()
    image[132:405, 57:1209] = original[412, 57:1209]
    before = reading()
    chosen = {abyss.DIFFICULTIES[i] for i in indices}
    for row in before['rows']:
        if row['difficulty'] not in chosen: row['remaining'] = 0
    for position, index in enumerate(sorted(indices, reverse=True)):
        source_y = 132 + (2-index)*86
        target_y = 132 + position*86 + offset
        image[target_y:target_y+82, 57:1209] = original[source_y:source_y+82, 57:1209]
    resource=native.load_resource();ctrl=SavedImages([[image]]);tasker=native.bind(resource,ctrl)
    observed=[]; errors=[]
    class Read(CustomAction):
        def run(self, context, argv):
            try:
                return self.read(context)
            except Exception as error:
                errors.append(repr(error)); return False
        def read(self, context):
            for ticket in (False, True):
                observed.append(_quote_rows(_Words(context,image), before, ticket=ticket))
            changed=deepcopy(before)
            active=next(row for row in changed['rows'] if row['remaining'])
            active['remaining']=0
            with pytest.raises(_NoMatch): _quote_rows(_Words(context,image),changed)
            return True
    assert resource.register_custom_action('R35Rows',Read())
    assert resource.override_pipeline({'R35Rows':dict(action='Custom',custom_action='R35Rows',next=[],post_delay=0)})
    assert native.run(tasker,'R35Rows')['native_ok']
    assert not errors
    assert len(observed)==2
    for rows in observed:
        assert [r['difficulty'] for r in rows] == [r['difficulty'] for r in before['rows'] if r['remaining']]
        assert all(r['times']==3 for r in rows)
    assert not ctrl.clicks and not ctrl.swipes



def test_original_partial_home_and_unknown_dispatch_do_not_retry(evidence, tmp_path):
    from pcr_maa.abyss_native import read_abyss_home
    from pcr_maa.preset_execution import Journal
    from test_abyss import Port, run
    frame=native.read_image(evidence/'debug/on_error/2026.10.06-23.43.43.571_PCRAlphaEntry.png')
    ctrl=SavedImages([[frame]])
    before=read_abyss_home(native.bind(native.load_resource(),ctrl))
    assert [row['remaining'] for row in before['rows']] == [0,0,4]
    assert before['outcome']=='needs_execution' and not ctrl.clicks
    quote=quotation()
    quote['rows']=[dict(before['rows'][2], title='前哨关卡',mode='深渊讨伐战',times=4)]
    quote.update(stages=1,total_times=4,ticket_cost=4,stamina_cost=100,abyss_identity=deepcopy(before['abyss_identity']))
    class Partial(Port):
        def read(self):
            self.calls.append('read');return deepcopy(before)
    port=Partial(quote);journal=Journal(tmp_path/'unknown-partial')
    device=dict(serial='127.0.0.1:16384',manager=dict(source='mumu_manager_local_v1',
        installation=str(tmp_path), index=0, created_timestamp=123,app_package='com.bilibili.priconne'))
    result=run(port,journal,device,execute=True)
    assert result['outcome']=='unknown_settlement', result
    assert port.calls==['read','quote','confirm','submit']
    content=journal.path.read_bytes()
    assert run(port,journal,device,execute=True)['outcome']=='blocked_in_flight'
    assert journal.path.read_bytes()==content and port.calls==['read','quote','confirm','submit']


@pytest.mark.parametrize('change', ['none', 'count', 'missing_predecessor'])
def test_top_edge_requires_known_adjacent_row_without_private_frames(change):
    from test_preset_scan import labelled_page
    first=labelled_page([1,2,3],[242,329,414], 'top')
    last=labelled_page([2,3,4],[250,336,422], 'bottom')
    last['top_edge_counts']=[dict(category='test',difficulty='hard',remaining=0,limit=3)]
    if change=='count': last['top_edge_counts'][0]['remaining']=1
    if change=='missing_predecessor': first['rows'].pop(0)
    collector=Collector();assert feed(collector,first)
    assert feed(collector,last) == (change=='none')
    assert collector.complete == (change=='none')
    if change!='none': assert collector.reason=='unverified_top_edge_row'
