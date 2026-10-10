"""R31 ticket OCR and completed opening-action regression; offline only."""
import json
import os
from copy import deepcopy
from pathlib import Path
import pytest
from pcr_maa import native
from pcr_maa.abyss_native import NativeAbyss, AbyssTicketPage, _reader, _detail
from pcr_maa.replay import SavedImages
from test_abyss import reading, quotation


def test_opening_click_is_reported_when_following_snapshot_fails(monkeypatch):
    before, quote = reading(), quotation()
    receipt = dict(native_ok=False, tasker_inited=True, nodes=[dict(name='AbyssChallenge',
        completed=True, action=dict(name='AbyssChallenge', success=True))])
    class Resource:
        def register_custom_recognition(self, *args): return True
        def unregister_custom_recognition(self, *args): return True
    class Tasker:
        resource = Resource()
    port = NativeAbyss(Tasker()); port.identity = before['abyss_identity']
    monkeypatch.setattr(port, '_quote_read', lambda *args: deepcopy(quote))
    monkeypatch.setattr(native, 'run', lambda *args, **kwargs: receipt)
    result = port.confirm(before, quote)
    assert result['challenge_opened'] is True
    assert result['verified'] is False and port._confirmed is None


@pytest.fixture
def original():
    root = Path(os.environ.get('PCR_ABYSS_TICKET_EVIDENCE', '.runtime/private-fixtures/abyss-ticket'))
    if not (root / 'ticket.png').is_file(): pytest.skip('private original frame required')
    return (native.read_image(root/'ticket.png'),
            json.loads((root/'reading.json').read_text(encoding='utf-8')),
            json.loads((root/'quote.json').read_text(encoding='utf-8')))


def test_original_ticket_native_snapshot_and_final_guard(original):
    image, before, quote = original
    resource = native.load_resource(); ctrl = SavedImages([[image]])
    tasker = native.bind(resource, ctrl)
    with _reader(resource, 'AbyssTicketPage', AbyssTicketPage(before, quote, require_ready=True)):
        receipt = native.run(tasker, 'AbyssTicketSnapshot')
        detail = _detail(receipt, 'AbyssTicketSnapshot')
        assert detail is not None, receipt
        assert detail['status'] == 'ready'
        assert (detail['stamina_owned'], detail['stamina_cost'], detail['ticket_cost']) == (718,300,12)
        assert detail['evidence']['AbyssTicketStaminaOwned']['raw']['best']['score'] >= .9
        # Actual final native consumer, directed only to an offline controller.
        committed = native.run(tasker, 'AbyssTicketCommit')
        assert committed['native_ok'] and len(ctrl.clicks) == 1
        assert 734 <= ctrl.clicks[0][0] <= 853 and 471 <= ctrl.clicks[0][1] <= 526
    assert not ctrl.swipes


@pytest.mark.parametrize('change', ['cost','counts','shortage'])
def test_original_ticket_rejects_changed_quote_or_shortage(original, change):
    image, before, quote = original
    if change == 'cost': quote['stamina_cost'] += 1
    elif change == 'counts':
        before['rows'][0]['remaining'] = 3
        quote['rows'][0].update(remaining=3,times=3)
        quote['total_times'] = quote['ticket_cost'] = 11
    else: quote['stamina_owned'] = 0
    resource = native.load_resource(); ctrl = SavedImages([[image]])
    tasker = native.bind(resource, ctrl)
    with _reader(resource, 'AbyssTicketPage', AbyssTicketPage(before, quote, require_ready=True)):
        receipt = native.run(tasker, 'AbyssTicketSnapshot')
    assert not receipt['native_ok'] and ctrl.clicks == []


def test_original_ticket_inside_borrowed_agent_context(original):
    import time
    from maa.custom_action import CustomAction
    from pcr_maa.agent_callbacks import AgentCallbacks
    image, before, quote = original
    resource = native.load_resource(); ctrl = SavedImages([[image]])
    tasker = native.bind(resource, ctrl); registry = AgentCallbacks(); registry._ready = True
    for name, route in registry.recognitions.items():
        if name == 'HomeNoKnownLargePanel': resource.unregister_custom_recognition(name)
        assert resource.register_custom_recognition(name, route)
    details = []
    class Borrow(CustomAction):
        def run(self, context, argv):
            with registry.scope(context, deadline=time.monotonic()+60,
                                owner_source=lambda original: original is context) as facade:
                with _reader(facade.resource, 'AbyssTicketPage', AbyssTicketPage(before, quote, require_ready=True)):
                    for _ in range(2):
                        details.append(_detail(native.run(facade, 'AbyssTicketSnapshot'), 'AbyssTicketSnapshot'))
                    assert native.run(facade, 'AbyssTicketCommit')['native_ok']
            return True
    assert resource.register_custom_action('R31Borrow', Borrow())
    assert resource.override_pipeline({'R31Borrow':dict(action='Custom',custom_action='R31Borrow',next=[],post_delay=0)})
    assert native.run(tasker, 'R31Borrow')['native_ok']
    assert len(details)==2 and all(d and d['status']=='ready' for d in details)
    assert len(ctrl.clicks)==1 and not ctrl.swipes
