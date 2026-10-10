"""Fake process inspections and synthetic updates; never inspect live processes."""
import json
import os
import subprocess

import pytest
from pcr_maa import player_maintenance as maintenance
from test_player_update import assert_private, update_case


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows image path semantics')


def fake_probe(monkeypatch, values):
    def inspect(command, **options):
        assert options['timeout'] == 30
        assert '-NonInteractive' in command
        return subprocess.CompletedProcess(command, 0, json.dumps(values), '')
    monkeypatch.setattr(maintenance.subprocess, 'run', inspect)


def test_exact_install_images_are_blocked_case_insensitively(monkeypatch, tmp_path):
    player = tmp_path / 'player'
    values = [{'pid': 100 + i, 'image': str(player / image).upper(), 'error': None}
              for i, image in enumerate(('MFAAvalonia.exe', 'runtime/python/python.exe',
                  'runtime/python/pythonw.exe', 'runtime/dotnet/dotnet.exe'))]
    fake_probe(monkeypatch, values)
    assert maintenance._processes(player) == [100, 101, 102, 103]


def test_unrelated_same_names_and_similar_prefix_are_ignored(monkeypatch, tmp_path):
    player = tmp_path / 'player'
    values = [{'pid': 100 + i, 'image': str(tmp_path / image), 'error': None}
              for i, image in enumerate(('other/MFAAvalonia.exe', 'other/python.exe',
                  'player-copy/runtime/python/python.exe', 'player2/runtime/dotnet/dotnet.exe'))]
    fake_probe(monkeypatch, values)
    assert maintenance._processes(player) == []


def test_updater_itself_is_excluded_before_unavailable_path_check(monkeypatch, tmp_path):
    fake_probe(monkeypatch, [{'pid': os.getpid(), 'image': None, 'error': 'path_unavailable'}])
    assert maintenance._processes(tmp_path) == []


def fake_shell(monkeypatch, image, *, path_throws=False, query_error=False):
    """Run the emitted PowerShell with fake APIs; no Get-Process reaches the OS."""
    original = maintenance.subprocess.run
    value = json.dumps(image)
    # The process exposes no Path, while the second image API may still resolve it.
    prefix = '''
function Get-Process { [pscustomobject]@{Id=123;Path=$null} }
function Get-CimInstance { param($ClassName,$Filter) [pscustomobject]@{ExecutablePath=IMAGE} }
'''.replace('IMAGE', "'" + value.replace("'", "''") + "' | ConvertFrom-Json")
    if path_throws:
        prefix = prefix.replace('[pscustomobject]@{Id=123;Path=$null}',
            "([pscustomobject]@{Id=123} | Add-Member -MemberType ScriptProperty -Name Path -Value { throw 'fake path denied' } -PassThru)")
    if query_error:
        prefix = prefix.replace('[pscustomobject]@{ExecutablePath=', "throw 'fake query denied'; [pscustomobject]@{ExecutablePath=")
    def inspect(command, **options):
        assert options['timeout'] == 30
        assert 'CommandLine' not in command[-1]
        return original([*command[:-1], prefix + command[-1]], **options)
    monkeypatch.setattr(maintenance.subprocess, 'run', inspect)


@pytest.mark.parametrize('owned', [False, True])
@pytest.mark.parametrize('path_throws', [False, True])
def test_unavailable_first_image_uses_second_api_before_update(owned, path_throws, update_case, monkeypatch):
    player, packet, old, _, _, old_bytes = update_case
    image = (player if owned else player.parent / 'unrelated') / 'runtime/python/python.exe'
    fake_shell(monkeypatch, str(image), path_throws=path_throws)
    if owned:
        with pytest.raises(ValueError, match='player_or_agent_running'):
            maintenance.apply_update(player, packet)
        assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
        for name, data in old.items():
            assert (player / name).read_bytes() == data
    else:
        assert maintenance.apply_update(player, packet)['status'] == 'updated'
    assert_private(update_case)


@pytest.mark.parametrize('query_error', [False, True])
def test_unavailable_both_images_remains_conservative(query_error, update_case, monkeypatch):
    player, packet, old, _, _, old_bytes = update_case
    fake_shell(monkeypatch, None, query_error=query_error)
    with pytest.raises(ValueError, match='process_inspection_incomplete'):
        maintenance.apply_update(player, packet)
    assert (player / maintenance.MANIFEST).read_bytes() == old_bytes
    for name, data in old.items():
        assert (player / name).read_bytes() == data
    assert_private(update_case)