from types import SimpleNamespace

import pytest

from scripts import prepare_dev_native as native


@pytest.fixture
def dev_gui(tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    (tmp_path / 'requirements-runtime.lock').write_text('maafw==5.16.1 --hash=example\n', encoding='utf-8')
    app = tmp_path / 'gui'
    app.mkdir()
    (app / 'MFAAvalonia.exe').touch()
    (app / 'pcr-repository.txt').write_text(str(tmp_path), encoding='utf-8-sig')
    site = tmp_path / 'site'
    wheel = site / 'maa/bin'
    (wheel / 'plugins').mkdir(parents=True)
    for name in ('MaaFramework.dll', 'MaaAgentClient.dll', 'MaaAgentServer.dll', 'MaaToolkit.dll',
                 'plugins/sample.dll'):
        (wheel / name).write_bytes(name.encode())
    dist = SimpleNamespace(version='v5.16.1', locate_file=lambda name: site / name)
    monkeypatch.setattr(native.metadata, 'distribution', lambda name: dist)
    return app, wheel, dist


def test_prepare_aligns_nested_libraries_after_build_and_preserves_gui_files(dev_gui, monkeypatch):
    app, wheel, _ = dev_gui
    output = app / 'runtimes/win-x64/native'
    output.mkdir(parents=True)
    (output / 'MaaFramework.dll').write_bytes(b'old version')
    (output / 'other.dll').write_bytes(b'GUI')
    native.prepare(app)
    assert all((output / path.relative_to(wheel)).read_bytes() == path.read_bytes()
               for path in wheel.rglob('*.dll'))
    assert (output / 'other.dll').read_bytes() == b'GUI'
    monkeypatch.setattr(native.shutil, 'copyfile', lambda *_: pytest.fail('identical file recopied'))
    native.prepare(app)


@pytest.mark.parametrize('problem', ['player', 'wrong checkout', 'wrong version', 'missing core'])
def test_prepare_errors_before_copy(dev_gui, problem):
    app, wheel, dist = dev_gui
    if problem == 'player':
        (app / 'package-manifest.json').touch()
    elif problem == 'wrong checkout':
        (app / 'pcr-repository.txt').write_text(str(wheel), encoding='utf-8')
    elif problem == 'wrong version':
        dist.version = 'v5.12.3'
    else:
        (wheel / 'MaaAgentClient.dll').unlink()
    with pytest.raises(ValueError):
        native.prepare(app)
    assert not (app / 'runtimes').exists()
