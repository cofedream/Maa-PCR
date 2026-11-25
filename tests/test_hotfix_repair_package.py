import hashlib
import json
import zipfile

import pytest

from scripts import build_hotfix_repair as builder


def test_repair_package_contains_independent_runtime_and_exact_registered_files(tmp_path):
    player = tmp_path / 'player'
    runtime = player / 'runtime/python'
    runtime.mkdir(parents=True)
    rows = []
    for name in builder.RUNTIME_FILES:
        content = name.encode('ascii')
        (runtime / name).write_bytes(content)
        rows.append({'path': 'runtime/python/' + name, 'sha256': hashlib.sha256(content).hexdigest()})
    (player / 'package-manifest.json').write_text(json.dumps({'files': rows}), encoding='utf-8')
    (runtime / 'unlisted.py').write_text('raise AssertionError()', encoding='utf-8')
    destination = tmp_path / 'repair.zip'
    builder.build(player, destination)
    with zipfile.ZipFile(destination) as package:
        assert package.testzip() is None
        assert package.read('python/python313._pth').splitlines() == [b'python313.zip', b'.']
        assert 'python/unlisted.py' not in package.namelist()
        assert set(builder.MODULES).issubset(package.namelist())
        data = json.loads(package.read('repair-data.json'))['baselines']
        assert {record['version']: len(record['files']) for record in data.values()} == {
            'v0.1.35': 4, 'v0.1.36': 4, 'v0.2.2': 11, 'v0.2.5': 2}
        for (revision, target), names in builder.KNOWN_FILES.items():
            files = data[builder.BASELINE_HASHES[revision]]['files']
            _, previous = builder.release_rows(revision)
            _, following = builder.release_rows(target)
            for name in names:
                row = files[name]
                assert hashlib.sha256(package.read('payload/' + row['sha256'])).hexdigest() == previous[name]
                assert row['hotfix_sha256'] == [following[name]]
    (runtime / 'python.exe').write_bytes(b'different')
    with pytest.raises(ValueError, match='repair_input_hash_mismatch'):
        builder.build(player, tmp_path / 'changed.zip')
    assert not (tmp_path / 'changed.zip').exists()
