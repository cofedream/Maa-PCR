"""Private original receipt qualification plus synthetic continuation, no device I/O."""
from copy import deepcopy
from hashlib import sha256
import json, os
from pathlib import Path
import pytest
from pcr_maa import preset_execution as preset, supported_daily as chain
from pcr_maa.preset_result_reconcile import reconcile_closed_result, verify_result
from pcr_maa.preset_execution import Journal


@pytest.fixture
def deep_case(tmp_path):
    root = os.environ.get('PCR_ABYSS_RECOVERY_ROOT')
    if not root: pytest.skip('Private original evidence is optional; no device access')
    proof = json.loads((Path(root) / 'DEEP_RECOVERY_PROOF.json').read_text(encoding='utf-8'))
    pins = json.loads((Path(root) / 'DEEP_ORIGINAL_PINS.json').read_text(encoding='utf-8'))
    original = next(json.loads(line) for line in reversed(Path(pins['ledger_path']).read_text(
        encoding='utf-8').splitlines()) if json.loads(line)['state'] == 'in_flight')
    step = json.loads(Path(proof['files']['step']['path']).read_text(encoding='utf-8'))
    journal = Journal(tmp_path / 'cloned-deep-ledger')
    with journal.locked(): journal.append(original)
    return dict(journal=journal, prefix=journal.path.read_bytes(), original=original, step=step, proof=proof)


def apply(case, commit=True):
    return reconcile_closed_result(case['journal'], case['proof'],
        expected_sha256=sha256(case['prefix']).hexdigest(), apply=commit)


class Port:
    def __init__(self, case, zero=False):
        self.calls=[];self.before=deepcopy(case['step']['result']['reading'])
        self.quote_value=deepcopy(case['step']['result']['quote'])
        self.after=deepcopy(self.before)
        for row in self.after['rows']: row['remaining']=0
        if zero:self.before=deepcopy(self.after)
    def read(self, slot):
        self.calls.append('read');return deepcopy(self.after if 'submit' in self.calls else self.before)
    def quote(self, reading):self.calls.append('quote');return deepcopy(self.quote_value)
    def confirm(self, reading, quote):self.calls.append('confirm');return dict(verified=True)
    def submit(self, quote):
        self.calls.append('submit');return dict(consumption_dispatched=True,result_verified=True,preset_return_verified=True)
    def home(self):self.calls.append('home');return True


def run(case, monkeypatch, day='2026-10-04', zero=False):
    monkeypatch.setattr(preset,'current_game_day',lambda *args:day)
    port=Port(case,zero=zero)
    result=preset.execute_one(port,case['step']['result']['preset'],'gui-current-state',case['journal'],
        execute=True,game_day=day)
    return result,port


def test_original_deep_dry_run_and_append_preserve_unobserved_counts(deep_case):
    result=apply(deep_case,False)
    assert not result['applied'] and deep_case['journal'].path.read_bytes()==deep_case['prefix']
    assert not (deep_case['journal'].directory/'result-evidence').exists()
    result=apply(deep_case)
    assert result['applied'] and result['consumption_verified'] and not result['settlement_verified']
    event=deep_case['journal'].events()[-1]
    assert 'remaining' not in event and not event['post_counts_verified'] and not event['home_verified']
    assert deep_case['journal'].path.read_bytes().startswith(deep_case['prefix'])
    with pytest.raises(ValueError):apply(deep_case)


@pytest.fixture
def isolated_deep_log(deep_case, tmp_path):
    case = dict(deep_case, proof=deepcopy(deep_case['proof']))
    row = case['proof']['files']['native_log']
    native_log = tmp_path / 'original-native.log'
    native_log.write_bytes(Path(row['path']).read_bytes())
    row['path'] = str(native_log)
    return case


def test_deep_rotated_native_log_uses_durable_copy_in_new_day(isolated_deep_log, monkeypatch):
    case = isolated_deep_log
    apply(case)
    row = case['proof']['files']['native_log']
    pinned = case['journal'].directory / 'result-evidence' / (row['sha256'] + '.log')
    assert pinned.read_bytes() == Path(row['path']).read_bytes()
    assert case['journal'].events()[-1]['evidence'] == case['proof']
    Path(row['path']).unlink()
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'executed' and result['settlement_verified']
    assert port.calls == ['read', 'quote', 'confirm', 'submit', 'read', 'home']
    assert case['journal'].path.read_bytes().startswith(case['prefix'])


@pytest.mark.parametrize('zero,expected,calls', [
    (False, 'blocked_post_counts_unverified', ['read']),
    (True, 'skipped_goal_met', ['read', 'home'])])
def test_deep_rotated_native_log_preserves_same_day_limits(isolated_deep_log, monkeypatch,
                                                         zero, expected, calls):
    case = isolated_deep_log
    apply(case)
    Path(case['proof']['files']['native_log']['path']).unlink()
    before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch, day='2026-10-02', zero=zero)
    assert result['outcome'] == expected and port.calls == calls
    assert not result['consumption_sent'] and case['journal'].path.read_bytes() == before


@pytest.mark.parametrize('fault', ['original_changed', 'copy_changed', 'copy_missing'])
def test_deep_native_log_damage_blocks_without_navigation(isolated_deep_log, monkeypatch, fault):
    case = isolated_deep_log
    apply(case)
    row = case['proof']['files']['native_log']
    original = Path(row['path'])
    pinned = case['journal'].directory / 'result-evidence' / (row['sha256'] + '.log')
    if fault == 'original_changed': original.write_bytes(original.read_bytes() + b'changed')
    else:
        original.unlink()
        if fault == 'copy_changed': pinned.write_bytes(pinned.read_bytes() + b'changed')
        else: pinned.unlink()
    before = case['journal'].path.read_bytes()
    result, port = run(case, monkeypatch)
    assert result['outcome'] == 'blocked_or_failed' and not port.calls
    assert case['journal'].path.read_bytes() == before


@pytest.mark.parametrize('day,zero,expected,calls', [
    ('2026-10-04', False, 'executed', ['read', 'quote', 'confirm', 'submit', 'read', 'home']),
    ('2026-10-02', False, 'blocked_post_counts_unverified', ['read']),
    ('2026-10-02', True, 'skipped_goal_met', ['read', 'home'])])
def test_deep_bound_historical_log_replacement(isolated_deep_log, monkeypatch, day, zero, expected, calls):
    from test_abyss_reconcile import replace_rotated_log
    case = isolated_deep_log
    apply(case)
    before = case['journal'].path.read_bytes()
    replace_rotated_log(case)
    result, port = run(case, monkeypatch, day=day, zero=zero)
    assert result['outcome'] == expected and port.calls == calls
    assert case['journal'].path.read_bytes().startswith(before)
    if day == '2026-10-02': assert case['journal'].path.read_bytes() == before


@pytest.mark.parametrize('fault',['day','slot','quote','count_edit','commit','result_close','shop_cancel','preset_return','frame_binding','missing_result','consumer_false'])
def test_deep_missing_or_changed_evidence_cannot_resolve(deep_case,fault):
    step=deepcopy(deep_case['step']);r=step['result'];s=r['submission']
    if fault=='day':r['game_day']='2026-10-04'
    if fault=='slot':r['preset']['slot']=5
    if fault=='quote':r['quote']['counts'][0]=9
    if fault=='count_edit':r['count_fit']['adjustment']['steps']=['edit']
    if fault=='consumer_false':s['consumption_dispatched']=False
    if fault=='missing_result':s['result_images']=[]
    if fault=='frame_binding':s['result_images'][0]['sha256']='0'*64
    names={'commit':'ExecConfirm','result_close':'ExecResultConfirm','shop_cancel':'ExecLimitedShopCancel','preset_return':'ExecReturnedPreset'}
    if fault in names:
        receipt=s['native_receipt'] if fault=='commit' else s['result_evidence']
        next(n for n in receipt['nodes'] if n['name']==names[fault])['action']['success']=False
    with pytest.raises((ValueError,IndexError,KeyError)):verify_result(deep_case['original'],step)
    assert deep_case['journal'].path.read_bytes()==deep_case['prefix']


def test_deep_original_day_cannot_consume_again(deep_case,monkeypatch):
    apply(deep_case);before=deep_case['journal'].path.read_bytes()
    result,port=run(deep_case,monkeypatch,day='2026-10-02')
    assert result['outcome']=='blocked_post_counts_unverified' and port.calls==['read']
    assert deep_case['journal'].path.read_bytes()==before


def test_deep_original_day_current_complete_can_skip(deep_case,monkeypatch):
    apply(deep_case)
    result,port=run(deep_case,monkeypatch,day='2026-10-02',zero=True)
    assert result['outcome']=='skipped_goal_met' and result['home_verified']
    assert port.calls==['read','home']


def test_deep_new_day_uses_fresh_port_and_closes_new_intent(deep_case,monkeypatch):
    apply(deep_case)
    result,port=run(deep_case,monkeypatch)
    assert result['outcome']=='executed' and result['settlement_verified'] and result['home_verified']
    assert port.calls==['read','quote','confirm','submit','read','home']
    events=deep_case['journal'].events()
    assert len(events)==4 and events[2]['game_day']=='2026-10-04'
    assert events[1]['settlement_verified'] is False and events[3]['remaining']==[0]*5


def test_deep_tampered_evidence_stops_before_navigation(deep_case,monkeypatch):
    proof=deepcopy(deep_case['proof']);proof['files']['native_log']['sha256']='0'*64
    with pytest.raises(ValueError):
        reconcile_closed_result(deep_case['journal'],proof,expected_sha256=sha256(deep_case['prefix']).hexdigest(),apply=True)
    assert deep_case['journal'].path.read_bytes()==deep_case['prefix']
    apply(deep_case)
    events=deep_case['journal'].events();events[-1]['evidence']=proof
    deep_case['journal'].path.write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
    result,port=run(deep_case,monkeypatch)
    assert result['outcome']=='blocked_or_failed' and not port.calls


def test_deep_recovery_continues_hard_module(deep_case,monkeypatch,tmp_path):
    apply(deep_case);calls=[]
    monkeypatch.setattr(chain,'current_game_day',lambda *args:'2026-10-04')
    monkeypatch.setattr(chain,'CONTROL_ROOT',tmp_path/'control')
    def step(tasker,name,*args,**kwargs):
        calls.append(name)
        if name=='deep':return run(deep_case,monkeypatch)[0]
        return dict(outcome='executed',consumption_sent=True,settlement_verified=True,home_verified=True,goal_complete=True)
    monkeypatch.setattr(chain,'_run_step',step)
    cfg=dict(version=1,start='home',device=dict(input_display=2,screenshot_display='123'),count_context=None,
        presets=[dict(kind=k,slot=s,label=k,target_per_stage=n) for k,s,n in [('hard',6,3),('deep',4,10),('very_hard',2,3)]])
    result=chain.execute_supported(None,cfg,execute=True,game_day='2026-10-04',context='user-reported-unreset',
        directory=tmp_path/('run-'+'2'*32),selected_modules=['deep','hard'],rewards_final=False)
    assert calls==['deep','hard'] and result['supported_chain_complete']
