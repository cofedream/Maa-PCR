"""Build the offline companion for installations carrying a known published hotfix."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

if __package__:
    from .release_baselines import BASELINE_HASHES, baseline_filename
else:
    from release_baselines import BASELINE_HASHES, baseline_filename


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'scripts/hotfix_baselines'
# Each hotfix was subsequently shipped in the second revision in the pair.
KNOWN_FILES = {
    (90, 92): (
        'assets/resource/pipeline/abyss.json', 'assets/resource/pipeline/preset_execution.json',
        'assets/resource/pipeline/schedule_onekey.json', 'assets/resource/pipeline/schedule_quick.json',
        'src/pcr_maa/abyss_native.py', 'src/pcr_maa/onekey_flow.py', 'src/pcr_maa/onekey_history.py',
        'src/pcr_maa/onekey_page.py', 'src/pcr_maa/preset_execution_native.py',
        'src/pcr_maa/schedule_onekey.py', 'src/pcr_maa/schedule_quick.py',
    ),
    (93, 94): ('src/pcr_maa/preset_execution.py', 'src/pcr_maa/preset_result_reconcile.py'),
}
# The repair CLI imports the same locking and validation code as the updater.
# Embedded Python and its stdlib extensions load independently of the old installation.
RUNTIME_FILES = (
    'python.exe', 'python3.dll', 'python313.dll', 'python313.zip', 'LICENSE.txt',
    '_bz2.pyd', '_lzma.pyd', '_hashlib.pyd', '_ssl.pyd', '_socket.pyd', '_uuid.pyd',
    'select.pyd', 'libcrypto-3.dll', 'libssl-3.dll', 'vcruntime140.dll', 'vcruntime140_1.dll',
)
MODULES = ('scripts/repair_hotfix_baseline.py', 'src/pcr_maa/__init__.py',
           'src/pcr_maa/player_maintenance.py', 'src/pcr_maa/player_update.py')


def checked_bytes(content, sha256, label):
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError(f'repair_input_hash_mismatch: {label}')
    return content


def release_rows(revision):
    path = ROOT / baseline_filename(revision)
    manifest = json.loads(checked_bytes(path.read_bytes(), BASELINE_HASHES[revision], path.name))
    return manifest['update']['version'], {row['path']: row['sha256'] for row in manifest['files']}


def repair_payload():
    payload = {}
    baselines = {}
    with zipfile.ZipFile(ASSETS / 'originals.zip') as originals:
        for (revision, hotfix_revision), names in KNOWN_FILES.items():
            version, old_rows = release_rows(revision)
            _, fixed_rows = release_rows(hotfix_revision)
            files = {}
            for name in names:
                sha, hotfix = old_rows[name], fixed_rows[name]
                if sha == hotfix:
                    raise ValueError(f'unchanged_hotfix_file: {name}')
                payload['payload/' + sha] = checked_bytes(originals.read(f'r{revision}/{name}'), sha, name)
                files[name] = {'sha256': sha, 'hotfix_sha256': [hotfix]}
            baselines[BASELINE_HASHES[revision]] = {'version': version, 'files': files}
    data = {'schema_version': 1, 'baselines': baselines}
    payload['repair-data.json'] = (json.dumps(data, indent=2) + '\n').encode('utf-8')
    return payload


def build(player, output):
    files = repair_payload()
    manifest = json.loads((player / 'package-manifest.json').read_bytes())
    hashes = {row['path']: row['sha256'] for row in manifest['files']}
    for name in RUNTIME_FILES:
        source = 'runtime/python/' + name
        files['python/' + name] = checked_bytes((player / source).read_bytes(), hashes[source], source)
    files['python/python313._pth'] = b'python313.zip\r\n.\r\n'
    for name in MODULES:
        files[name] = (ROOT / name).read_bytes()
    files['repair-update.bat'] = (ASSETS / 'repair-update.bat').read_text(encoding='utf-8').replace('\n', '\r\n').encode('utf-8')
    files['使用说明.txt'] = (ASSETS / '使用说明.txt').read_bytes()
    files['LICENSE'] = (ROOT / 'LICENSE').read_bytes()
    files['licenses/Apache-2.0.txt'] = (ROOT / 'docs/licenses/nuget/CalcBindingAva/2.5.3/LICENSE').read_bytes()
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            entry = zipfile.ZipInfo(name)
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, content)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--player', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    options = parser.parse_args()
    build(options.player, options.output)
