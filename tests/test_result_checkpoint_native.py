"""Official CustomController OCR/action replay. Upstream consumer is synthetic."""
from pathlib import Path
import os
import pytest
from pcr_maa import native,result_checkpoint as cp
from pcr_maa.preset_execution import Journal
from pcr_maa.preset_execution_native import NativePreset
from pcr_maa.replay import SavedImages
from test_result_checkpoint import consumer,pending

I=Path(os.environ.get('PCR_CHECKPOINT_PLAYER',str(native.ROOT / '.runtime/private-fixtures/result-checkpoint')))
RESULT=I/'.runtime/preset-result-1790944440132215700.png'
SHOP=I/'.runtime/preset-limited_shop-1790944442804377600.png'
HOME=Path(os.environ.get('PCR_CHECKPOINT_HOME_FRAME', str(native.ROOT / '.runtime/private-fixtures/result-checkpoint/home.png')))


def test_actual_preset_result_survives_navigation_failure_and_shop_recovery(tmp_path):
    if not all(p.is_file() for p in [RESULT,SHOP,HOME]):pytest.skip('private original result/shop/home frames required')
    journal,checkpoint=pending(tmp_path)
    # A new actual-frame checkpoint, bound to this test intent, rather than synthetic frame.
    journal=Journal(tmp_path/'native-ledger')
    original=checkpoint.original
    with journal.locked():journal.append(original)
    checkpoint=cp.ResultCheckpoint(journal,kind='preset',before=checkpoint.before,quote=checkpoint.quote)
    home=native.read_image(HOME)
    result=native.read_image(RESULT);shop=native.read_image(SHOP)
    resource=native.load_resource()
    assert resource.override_pipeline({'ExecPostResult':{'next':['UnknownStop'],'timeout':500}})
    controller=SavedImages([[result],[shop]])
    port=NativePreset(native.bind(resource,controller));port.bind_result_checkpoint(checkpoint.capture)
    submission=port._finish_result(checkpoint.before,checkpoint.quote,dict(native_receipt=consumer()))
    assert submission['result_verified'] and not submission['preset_return_verified'],submission
    assert checkpoint.read() is not None and len(controller.clicks)==1
    # Restart on the known shop. Dedicated cancel reaches a verified home frame;
    # this test substitutes the terminal original-page recognition only.
    resource=native.load_resource()
    assert resource.override_pipeline({'ExecReturnedPreset':{'all_of':['ObserveHome']}})
    controller2=SavedImages([[shop],[home]])
    port2=NativePreset(native.bind(resource,controller2))
    class HomeReturn(NativePreset):
        def return_guard(self,quote):return ['ObserveHome']
    port2=HomeReturn(port2.tasker)
    recovery=port2.recover_result(checkpoint.read())
    assert recovery['recovery_verified'],recovery
    assert len(controller2.clicks)==1 and not controller2.swipes
    assert all(n['name']!='ExecConfirm' for n in recovery['result_evidence']['nodes'])


def test_recovery_wrong_page_zero_clicks(tmp_path):
    journal,checkpoint=pending(tmp_path)
    resource=native.load_resource()
    assert resource.override_pipeline({'ExecRecoverResultStart':{'timeout':500}})
    import numpy as np
    controller=SavedImages([[np.zeros((720,1280,3),dtype=np.uint8)]])
    recovery=NativePreset(native.bind(resource,controller)).recover_result(checkpoint.read())
    assert not recovery['recovery_verified'] and controller.clicks==[] and controller.swipes==[]
