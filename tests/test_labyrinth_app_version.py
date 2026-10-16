from types import SimpleNamespace as NS

import pytest

from pcr_maa.labyrinth.app_version import COMMAND, parse_package_version, read_installed_version, validate_version
from pcr_maa.labyrinth.batch import BatchStopped


def package(version='11.8.0', name='com.bilibili.priconne'):
    return f'Packages:\n  Package [{name}] (abc):\n    versionCode=123\n    versionName={version}\n    flags=[ HAS_CODE ]\n'


@pytest.mark.parametrize('version',['11.7.2','11.8.0','12.0.1','0.0.0'])
def test_reads_actual_installed_package_version(version):
    assert parse_package_version(package(version))==version
    assert parse_package_version(package(version).replace('\n','\r\n'))==version


@pytest.mark.parametrize('version',[None,1180,True,'','11.8','11.8.0-beta','11.8.0\n','１１.８.０','11.8.0/abc','1.2.1234567'])
def test_protocol_header_rejects_nonliteral_versions(version):
    with pytest.raises(ValueError,match='game_version_invalid'):validate_version(version)


@pytest.mark.parametrize('output',[
    '', 'versionName=11.8.0', package(name='another.game'), package()+package(),
    package().replace('versionName=11.8.0','versionName=null'),
    package().replace('versionName=11.8.0','versionName=11.8.0\n    versionName=12.0.0'),
    package().replace('versionName=11.8.0','versionName=11.8.0\u3000'),
    package().replace('versionName=11.8.0',''), '\x00'+package(), 'x'*(1024*1024+1),
], ids=lambda value:'dump-'+str(len(value)))
def test_missing_ambiguous_or_malformed_dump_stops(output):
    with pytest.raises(ValueError,match='game_version_invalid'):parse_package_version(output)


def test_other_package_cannot_supply_the_game_version():
    assert parse_package_version(package('99.0.0','other.game')+package('11.8.0'))=='11.8.0'
    with pytest.raises(ValueError):parse_package_version(package().replace('versionName=11.8.0','')+package('99.0.0','other.game'))


def test_owner_reuses_its_controller_and_only_fixed_read_command():
    calls=[]
    def shell(command,*,timeout):
        calls.append((command,timeout));return NS(done=True,succeeded=True,get=lambda:package('11.9.0'))
    assert read_installed_version(NS(post_shell=shell),stopped=lambda:False)=='11.9.0'
    assert calls==[(COMMAND,5000)]


@pytest.mark.parametrize('fault',['failed','exception','missing'])
def test_query_failures_have_fixed_public_error_and_no_fallback(fault):
    def shell(*args,**kw):
        if fault=='exception':raise OSError('synthetic private dump')
        return NS(done=True,succeeded=fault!='failed',get=lambda:'' if fault=='missing' else package())
    with pytest.raises(BatchStopped,match='^game_version_unavailable$'):
        read_installed_version(NS(post_shell=shell),stopped=lambda:False)


def test_stop_before_query_never_sends_shell():
    def shell(*a,**kw):pytest.fail('query after Stop')
    with pytest.raises(BatchStopped,match='^stopped$'):
        read_installed_version(NS(post_shell=shell),stopped=lambda:True)


def test_stop_during_query_never_reads_or_uses_version():
    state=[False]
    def shell(*args,**kw):
        state[0]=True
        return NS(done=False,succeeded=True,get=lambda:pytest.fail('read after Stop'))
    with pytest.raises(BatchStopped,match='^stopped$'):
        read_installed_version(NS(post_shell=shell),stopped=lambda:state[0])


def test_query_has_bounded_wait(monkeypatch):
    import pcr_maa.labyrinth.app_version as module
    clock=iter([0,7]);monkeypatch.setattr(module.time,'monotonic',lambda:next(clock))
    with pytest.raises(BatchStopped,match='^game_version_unavailable$'):
        read_installed_version(NS(post_shell=lambda *a,**kw:NS(done=False)),stopped=lambda:False)


def test_installed_version_is_set_before_any_protocol_request():
    from pcr_maa.labyrinth.protocol import LabyrinthClient,ProtocolFailure
    client=LabyrinthClient('synthetic','synthetic',app_version='11.9.0')
    assert client.auth.headers['APP-VER']=='11.9.0'
    client.close()
    for value in ('garbage',1180,False,'11.9.0\n'):
        with pytest.raises(ProtocolFailure,match='^game_version_unavailable$'):
            LabyrinthClient('synthetic','synthetic',app_version=value)


def test_native_sdk_shell_result_reaches_parser_without_real_adb():
    import numpy as np
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    class Controller(SavedImages):
        def __init__(self):
            super().__init__([[np.zeros((720,1280,3),np.uint8)]])
            self.commands=[]
        def shell(self,cmd,timeout):
            self.commands.append((cmd,timeout))
            return package('12.1.3')
    controller=Controller()
    assert native.wait_job(controller.post_connection()).succeeded
    assert read_installed_version(controller,stopped=lambda:False)=='12.1.3'
    assert controller.commands==[(COMMAND,5000)]
    assert not controller.clicks and not controller.swipes
