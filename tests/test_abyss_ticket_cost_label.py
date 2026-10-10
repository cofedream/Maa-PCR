"""Replay the private R30 player failure through the current native consumer."""
from copy import deepcopy
import os
from pathlib import Path
import pytest
from pcr_maa import native
from pcr_maa.abyss_native import AbyssTicketPage, _reader, _detail
from pcr_maa.replay import SavedImages
from test_abyss import reading, quotation


@pytest.mark.parametrize('change', ['none', 'cost', 'counts', 'shortage'])
def test_player_ticket_confirmation(change):
    path = Path(os.environ.get('PCR_ABYSS_COST_LABEL_FRAME', '.runtime/private-fixtures/abyss-cost-label.png'))
    if not path.is_file():
        pytest.skip('private player failure frame required')
    before, quote = reading(), quotation()
    for row in before['rows']:
        row['remaining'] = 4
    for row in quote['rows']:
        row.update(remaining=4, times=4)
    quote.update(total_times=12, ticket_cost=12, stamina_cost=300,
                 stamina_owned=424, tickets_owned=230729)
    if change == 'cost':
        quote['stamina_cost'] += 1
    elif change == 'counts':
        before['rows'][0]['remaining'] = 3
        quote['rows'][0].update(remaining=3, times=3)
        quote.update(total_times=11, ticket_cost=11, stamina_cost=275)
    elif change == 'shortage':
        quote['stamina_owned'] = 299
    resource = native.load_resource()
    assert resource.override_pipeline({'AbyssTicketSnapshot': {'timeout': 500}})
    ctrl = SavedImages([[native.read_image(path)]])
    tasker = native.bind(resource, ctrl)
    # The same reader guards snapshot and the final native click.
    with _reader(resource, 'AbyssTicketPage', AbyssTicketPage(before, deepcopy(quote), require_ready=True)):
        receipt = native.run(tasker, 'AbyssTicketSnapshot')
        detail = _detail(receipt, 'AbyssTicketSnapshot')
        if change != 'none':
            assert not receipt['native_ok'] and ctrl.clicks == []
            return
        assert detail and detail['status'] == 'ready', receipt
        assert (detail['stamina_cost'], detail['stamina_owned'], detail['ticket_cost']) == (300, 424, 12)
        committed = native.run(tasker, 'AbyssTicketCommit')
        assert committed['native_ok'] and len(ctrl.clicks) == 1
        assert 734 <= ctrl.clicks[0][0] <= 853 and 471 <= ctrl.clicks[0][1] <= 526
    assert not ctrl.swipes
