from copy import deepcopy
from types import MappingProxyType

import pytest

from pcr_maa.labyrinth.config import Strategy
from pcr_maa.labyrinth.database import DATABASE_SNAPSHOT, DatabaseSnapshot
from pcr_maa.labyrinth.owned_runtime import parse_request, ConfigurationError
from pcr_maa.labyrinth.roles import configured_optimizer, load_optimizer
from pcr_maa.labyrinth.servers import SERVERS


def request(server='cn-bilibili'):
    return dict(version=1,settings=dict(version=1,goals=[dict(guild_id=1,clears=1)],
        strategy=dict(auto_database_update=False,response_interval_ms=500)),
        account_id=123,server=server,connection=dict(adb_path='fixture-adb.exe',serial='127.0.0.1:65530'),
        credentials=dict(username='fixture-uid',password='fixture-access-key'))


@pytest.mark.parametrize('cpu_option', [{}, {'cpu_only': True}])
@pytest.mark.parametrize('server',list(SERVERS))
def test_owner_server_and_strategy_snapshot_are_bound_without_retaining_input_credentials(server,tmp_path,cpu_option):
    value=request(server)
    value.update(cpu_option)
    adb=tmp_path/'adb.exe'
    adb.write_bytes(b'offline fixture, never executed')
    value['connection']['adb_path']=str(adb.resolve())
    credentials=value['credentials']
    parsed=parse_request(value)
    try:
        assert parsed.server==server
        assert parsed.cpu_only is cpu_option.get('cpu_only', False)
        assert parsed.settings.strategy.response_interval_ms==500
        assert parsed.settings.strategy.auto_database_update is False
        assert credentials=={} and 'credentials' not in value
        assert parsed.username=='fixture-uid'
    finally:parsed.clear()
    assert parsed.username==parsed.password==''


@pytest.mark.parametrize('server',['unknown','../../cn-bilibili',None,123,True])
def test_owner_never_defaults_an_invalid_server_to_bilibili(server):
    value=request(server)
    credentials=value['credentials']
    with pytest.raises(ConfigurationError):parse_request(value)
    assert credentials=={}


def test_optimizer_uses_immutable_run_database_then_restores_catalog():
    base=load_optimizer()
    original=deepcopy(base.profiles['1001'].data)
    snapshot=DatabaseSnapshot('202610141448','a'*64,
        MappingProxyType({'1001':MappingProxyType(dict(position=987,damageType='MAGIC'))}))
    strategy=Strategy.parse(dict(personal_scores={'1001':99,'999999':45.5}))
    token=DATABASE_SNAPSHOT.set(snapshot)
    try:
        optimizer=configured_optimizer(strategy)
        assert optimizer.profiles['1001'].position==987
        assert optimizer.profiles['1001'].data['damageType']=='MAGIC'
        assert optimizer.profiles['1001'].rating==99
        assert optimizer.profiles['1001'].data['databaseVersion']=='202610141448'
        assert '999999' not in optimizer.profiles
    finally:DATABASE_SNAPSHOT.reset(token)
    assert base.profiles['1001'].data==original
    assert configured_optimizer().profiles['1001'].data==original
    assert strategy.personal_scores['999999']==45.5


@pytest.mark.parametrize('battles',[{},[{}],[None],['NORMAL',True]])
def test_special_equipment_parser_rejects_untrusted_structures(battles):
    with pytest.raises(ValueError):Strategy.parse(dict(special_equipment_battles=battles))


def test_gui_disabled_equipment_with_empty_kinds_remains_a_valid_noop():
    value=Strategy.parse(dict(special_equipment_mode='off',special_equipment_battles=[]))
    assert value.special_equipment_mode=='off' and value.special_equipment_battles==()
    assert Strategy.parse(value.payload())==value
