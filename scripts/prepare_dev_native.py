"""Align the Windows development GUI with the locked Python MaaFramework Agent."""
import argparse
import filecmp
from importlib import metadata
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APP = ROOT / 'gui/MFAAvalonia/bin/x64/Release'


def copy_maa_native(source, destination, *, skip_identical=False):
    """Stage the wheel's native libraries, including plugins, alongside GUI dependencies."""
    for name in ('MaaFramework.dll', 'MaaAgentClient.dll', 'MaaAgentServer.dll', 'MaaToolkit.dll'):
        if not (source / name).is_file():
            raise ValueError(f'Missing native library in maafw: {source / name}')
    for path in source.rglob('*'):
        if path.is_file():
            target = destination / path.relative_to(source)
            if skip_identical and target.is_file() and filecmp.cmp(path, target, shallow=False):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)


def prepare(app):
    if (app / 'package-manifest.json').exists() or not (app / 'MFAAvalonia.exe').is_file():
        raise ValueError(f'Expected a built development GUI in {app}')
    binding = (app / 'pcr-repository.txt').read_text(encoding='utf-8-sig').strip()
    if not binding or Path(binding).resolve() != ROOT:
        raise ValueError(f'Expected pcr-repository.txt bound to {ROOT}')
    lock = (ROOT / 'requirements-runtime.lock').read_text(encoding='utf-8')
    pinned = re.search(r'^maafw==([^\s]+)\s', lock, re.MULTILINE)
    distribution = metadata.distribution('maafw')
    if not pinned or pinned[1] != distribution.version.removeprefix('v'):
        raise ValueError(f'Installed maafw {distribution.version} differs from requirements-runtime.lock')
    copy_maa_native(distribution.locate_file('maa/bin'), app / 'runtimes/win-x64/native',
                    skip_identical=True)
    print(f'Development GUI native runtime ready: maafw {distribution.version} -> {app}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=DEFAULT_APP, help='checkout-bound development GUI directory')
    app = parser.parse_args().app.resolve()
    # Mirror PcrRuntimePaths.ResolvePython so a global development interpreter cannot supply different DLLs.
    agent_python = next((exe for exe in (ROOT / 'runtime/python/python.exe', ROOT / '.venv/Scripts/python.exe')
                         if exe.is_file()), None)
    if not agent_python:
        parser.error('Install the checkout development environment before preparing the GUI for game tasks')
    if not agent_python.samefile(sys.executable):
        sys.exit(subprocess.call([str(agent_python), '-X', 'utf8', '-B', __file__, '--app', str(app)]))
    prepare(app)
