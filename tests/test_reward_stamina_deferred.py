"""R30 original-frame rewards regression; native replay is offline only."""
from copy import deepcopy
from pathlib import Path
import pytest
from pcr_maa import native, rewards, supported_daily as chain
from pcr_maa.replay import SavedImages

EVIDENCE = Path(__file__).resolve().parents[2] / 'private-evidence'

@pytest.fixture
def resource(tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    monkeypatch.setattr(native, 'RUNTIME', tmp_path / 'maa')
    return native.load_resource()

def frame(name):
    path = EVIDENCE / (name + '.png')
    if not path.is_file():
        pytest.skip('private original reward frames supplied separately')
    return native.read_image(path)

def tasker_for(resource, names):
    ctrl = SavedImages([[frame(n)] for n in names])
    return native.bind(resource, ctrl), ctrl

def test_saved_post_claim_stamina_returns_home(resource):
    tasker, ctrl = tasker_for(resource, ['gifts-start-home', 'gifts-before',
        'gifts-dispatch', 'gifts-result', 'gifts-after', 'gifts-start-home'])
    report = rewards.claim_rewards(tasker, execute=True, only='gifts')
    assert report['outcome'] == 'complete', report.get('error')
    item = report['items']['gifts']
    assert item['status'] == 'deferred_stamina'
    assert item['stamina_deferred_verified'] is True
    assert item['result_verified'] is True and item['claim_attempted'] is True
    assert item['claimable_after'] is False and item['home_verified'] is True
    assert ctrl.clicks[-1] == [789, 636] and len(ctrl.clicks) == 5
    assert sum(x['entry'] == 'RewardsGiftsClaimStart' for x in item['native_receipts']) == 1
    assert 'error' not in report and 'error' not in item


def test_saved_stamina_before_claim_skips_consumer(resource):
    tasker, ctrl = tasker_for(resource, ['gifts-start-home', 'gifts-after', 'gifts-start-home'])
    report = rewards.claim_rewards(tasker, execute=True, only='gifts')
    assert report['outcome'] == 'complete', report.get('error')
    item = report['items']['gifts']
    assert item['status'] == 'deferred_stamina' and item['stamina_deferred_verified']
    assert not item['claim_attempted'] and not item['result_verified']
    assert item['claimable_before'] is False and item['claimable_after'] is False
    assert len(ctrl.clicks) == 2 and ctrl.clicks[-1] == [789, 636]
    assert not any('Claim' in r['entry'] for r in item['native_receipts'])


@pytest.mark.parametrize('name,expected', [('gifts-before', 'RewardsGiftsReady'),
                                         ('missions-before', None)])
def test_stamina_branch_does_not_mask_other_states(resource, name, expected):
    tasker, ctrl = tasker_for(resource, [name])
    receipt = native.run(tasker, 'RewardsGiftsRead')
    assert not rewards.completed(receipt, 'RewardsGiftsStaminaDeferred')
    if expected:
        assert rewards.completed(receipt, expected)
    assert ctrl.clicks == []


def deferred_report():
    from test_supported_daily import success
    report = success('rewards')
    report['items']['gifts'].update(status='deferred_stamina',
        stamina_deferred_verified=True, claimable_after=False)
    return report


@pytest.mark.parametrize('step', ['rewards', 'rewards_final'])
def test_deferred_stamina_continues_all_later_modules(tmp_path, monkeypatch, step):
    from test_supported_daily import invoke, frozen_game_clock
    frozen_game_clock.__wrapped__(monkeypatch)
    report, calls = invoke(tmp_path, monkeypatch, {step: deferred_report()})
    assert calls == list(chain.ORDER)
    assert report['outcome'] == 'complete' and report['supported_chain_complete']
    assert not report['daily_complete']
    assert all(s['status'] not in ('stopped', 'failed', 'partial') for s in report['steps'])
    text = chain.summary(report, player=True)
    assert '体力礼物暂不可领取' in text and '异常停止' not in text


@pytest.mark.parametrize('fault', ['proof', 'claimable', 'mission', 'home', 'result', 'error'])
def test_unverified_deferral_still_stops(fault):
    report = deferred_report();gift = report['items']['gifts']
    if fault == 'proof': gift.pop('stamina_deferred_verified')
    elif fault == 'claimable': gift['claimable_after'] = True
    elif fault == 'mission': report['items']['missions'] = deepcopy(gift)
    elif fault == 'home': gift['home_verified'] = False
    elif fault == 'result': gift['result_verified'] = False
    else: report['error'] = 'unknown dialog'
    assert chain._status('rewards', report) == 'stopped'


def test_shared_native_context_early_and_final_deferral(resource):
    from maa.custom_action import CustomAction
    from pcr_maa.context_backend import ContextTasker
    tasker, ctrl = tasker_for(resource, ['gifts-start-home', 'gifts-after',
        'gifts-start-home', 'gifts-after', 'gifts-start-home'])
    reports = []
    class Probe(CustomAction):
        def run(self, context, argv):
            with ContextTasker(context) as borrowed:
                reports.extend(rewards.claim_rewards(borrowed, execute=True, only='gifts') for _ in range(2))
            return all(r['outcome'] == 'complete' for r in reports)
    resource.register_custom_action('R30RewardProbe', Probe())
    resource.override_pipeline({'R30RewardProbe': {'action': 'Custom', 'custom_action': 'R30RewardProbe'}})
    receipt = native.run(tasker, 'R30RewardProbe')
    assert rewards.completed(receipt, 'R30RewardProbe')
    assert len(reports) == 2 and all(r['home_verified'] for r in reports)
    assert ctrl.clicks.count([789, 636]) == 2 and len(ctrl.clicks) == 4
def test_full_original_missions_then_stamina_gifts_producer_continues(resource, tmp_path, monkeypatch):
    from test_supported_daily import invoke, frozen_game_clock
    names = ['missions-start-home', 'missions-before', 'missions-result', 'missions-after',
             'missions-home', 'gifts-before', 'gifts-dispatch', 'gifts-result',
             'gifts-after', 'gifts-start-home']
    tasker, ctrl = tasker_for(resource, names)
    result = rewards.claim_rewards(tasker, execute=True)
    assert result['outcome'] == 'complete', result.get('error')
    assert result['items']['missions']['status'] == 'claimed'
    assert result['items']['gifts']['status'] == 'deferred_stamina'
    assert result['items']['gifts']['result_verified'] is True
    assert len(ctrl.clicks) == 9 and ctrl.clicks[-1] == [789, 636]
    frozen_game_clock.__wrapped__(monkeypatch)
    report, calls = invoke(tmp_path / 'chain', monkeypatch, {'rewards': result})
    assert calls == list(chain.ORDER) and report['supported_chain_complete']
    assert report['outcome'] == 'complete' and 'error' not in report


def test_cancel_without_verified_home_is_not_success(resource):
    tasker, ctrl = tasker_for(resource, ['gifts-start-home', 'gifts-after', 'missions-before'])
    report = rewards.claim_rewards(tasker, execute=True, only='gifts')
    assert report['outcome'] == 'unknown' and report.get('error')
    assert report['home_verified'] is False and report['items']['gifts']['home_verified'] is False
    assert not report['items']['gifts']['claim_attempted']
    assert len(ctrl.clicks) == 2 and ctrl.clicks[-1] == [789, 636]
