import json

import pytest

from scripts.inspect_clan_targets import cached_master_identity
from test_labyrinth_database_parity import installed


def test_season_uses_current_installation_cache_without_an_update(tmp_path):
    cache, _ = installed(tmp_path)
    pointer = json.loads(cache.metadata.read_bytes())
    path, identity = cached_master_identity(tmp_path / 'product')
    assert path == cache.directory / f'master_cn.{pointer["sha256"]}.db'
    assert identity == {'sha256': pointer['sha256'], 'content_version': '202610141448'}


@pytest.mark.parametrize('damage', ['missing_file', 'changed_file', 'bad_version', 'missing_version'])
def test_season_rejects_current_cache_damage_even_with_valid_previous(tmp_path, damage):
    cache, _ = installed(tmp_path)
    previous = json.loads(cache.metadata.read_bytes())
    current = {**previous, 'version': '202611131613', 'previous': previous}
    if damage in ('missing_file', 'changed_file'):
        current['sha256'] = 'f' * 64
        if damage == 'changed_file':
            (cache.directory / f'master_cn.{current["sha256"]}.db').write_bytes(b'broken')
    elif damage == 'bad_version':
        current['version'] = None
    else:
        del current['version']
    cache.metadata.write_text(json.dumps(current), encoding='utf-8')
    assert cache.snapshot().version == previous['version']
    with pytest.raises(ValueError, match='current CN master cache'):
        cached_master_identity(tmp_path / 'product')


def test_missing_cache_is_read_only(tmp_path):
    root = tmp_path / 'install'
    with pytest.raises(ValueError, match='current CN master cache'):
        cached_master_identity(root)
    assert not root.exists()
