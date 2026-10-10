import pytest
from pathlib import Path
from uuid import uuid4
import os


def output_root():
    project = Path(__file__).resolve().parents[1]
    recovery = os.environ.get('PCR_RECOVERY_TEST_OUTPUT')
    if recovery:
        root = Path(recovery).resolve()
        assert root.is_relative_to(project / '.runtime/unsettled-recovery-20260929')
        return root
    dungeon = os.environ.get('PCR_DUNGEON_TEST_OUTPUT')
    if dungeon:
        root = Path(dungeon).resolve()
        assert root.is_relative_to(project / '.runtime/dungeon-result-fix-20260929')
        return root
    normal = os.environ.get('PCR_NORMAL_TEST_OUTPUT')
    if normal:
        root = Path(normal).resolve()
        assert root.is_relative_to(project / '.runtime/normal-integration-20260928')
        return root
    selected = os.environ.get('PCR_HISTORY_TEST_OUTPUT')
    if selected:
        root = Path(selected).resolve()
        assert root.is_relative_to(project / '.runtime/schedule-final-sprint-20260928/history')
        return root
    return project / '.runtime/confirmation-timing-work'


def pytest_configure(config):
    # Never reuse/clean a directory created by another process or identity.
    import pcr_maa.native as native
    session = output_root() / ("pytest-" + uuid4().hex)
    session.mkdir(parents=True)
    import tempfile
    temporary = session / 'stdlib-temp'
    temporary.mkdir()
    tempfile.tempdir = str(temporary)
    if config.option.basetemp is None:
        config.option.basetemp = str(session / "tmp")
    native.RUNTIME = session


@pytest.fixture(autouse=True)
def forbid_real_adb(monkeypatch):
    import maa.controller
    from maa.toolkit import Toolkit

    def forbidden(*args, **kwargs):
        raise AssertionError("Tests must never construct real AdbController")

    monkeypatch.setattr(maa.controller, "AdbController", forbidden)
    monkeypatch.setattr(Toolkit, 'find_adb_devices', forbidden)


@pytest.fixture(autouse=True)
def confine_test_logs(monkeypatch):
    """CLI regression tests may select their own product log location."""
    import pcr_maa.native as native
    original = native.setup
    root = output_root()
    def setup():
        if not native.RUNTIME.is_relative_to(root):
            native.RUNTIME = root / ('test-cli-' + uuid4().hex)
        original()
    monkeypatch.setattr(native, 'setup', setup)
