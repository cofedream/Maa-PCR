import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

helper = Path(__file__).with_name('build_r24_source_delivery.py')
if not helper.exists():
    helper = Path(__file__).resolve().parents[1] / 'scripts' / 'build_r24_source_delivery.py'
spec = importlib.util.spec_from_file_location('source_delivery', helper)
delivery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(delivery)
gate_spec = importlib.util.spec_from_file_location('source_gate',
    Path(__file__).resolve().parents[1] / 'scripts' / 'source_gate.py')
source_gate = importlib.util.module_from_spec(gate_spec)
gate_spec.loader.exec_module(source_gate)


class SourceDeliveryTests(unittest.TestCase):
    def test_paths_and_retired_resources_are_rejected(self):
        for value in ('../secret', '/secret', 'C:/secret', 'a\\b', 'a/../b',
                      'gui/bin/test.txt', '.runtime/companion/gui-source-companion.zip',
                      'src/__pycache__/x.pyc', 'assets/resource/image/home_schedule.png',
                      'gui/MFAAvalonia/docs/images/preview.png'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                delivery.relative(value)
        delivery.relative('docs/daily-config.template.json')
        delivery.relative('src/pcr_maa/player_maintenance.py')

    def test_duplicate_case_and_hash_rows(self):
        row = {'path': 'README.md', 'size': 0, 'sha256': '0' * 64}
        with self.assertRaises(ValueError):
            delivery.row_index([row, dict(row, path='readme.md')])
        with self.assertRaises(ValueError):
            delivery.row_index([dict(row, sha256='wrong')])

    def test_signed_url_and_private_path_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder, 'notice.md')
            fixtures = ['https://host/release?' + key + '=fixture'
                        for key in ('sig', 'X-Amz-Signature')]
            fixtures.append('/'.join(('C:', 'Users', 'private-user', 'Projects', 'fixture')))
            for data in fixtures:
                path.write_text(data, encoding='utf-8')
                with self.assertRaises(ValueError):
                    delivery.privacy_scan(path, 'notice.md')
            path.write_text('https://github.com/example/project/tree/fixed\n', encoding='utf-8')
            delivery.privacy_scan(path, 'notice.md')

    def test_pack_is_exact_and_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder, 'prepared')
            (root / 'source' / 'src').mkdir(parents=True)
            data = b'print("not executed")\n'
            path = root / 'source' / 'src' / 'example.py'
            path.write_bytes(data)
            row = {'path': 'src/example.py', 'size': len(data),
                   'sha256': hashlib.sha256(data).hexdigest(), 'origin': 'fixture'}
            inventory = {'file_count': 1, 'payload_size': len(data), 'files': [row]}
            receipt = root / 'SOURCE_INVENTORY.json'
            receipt.write_text(json.dumps(inventory) + '\n', encoding='utf-8')
            expected = delivery.digest(receipt)
            result = delivery.pack(root, Path(folder, 'source.zip'), expected)
            self.assertEqual(result['files'], 2)
            with zipfile.ZipFile(Path(folder, 'source.zip')) as archive:
                self.assertEqual(set(archive.namelist()), {'src/example.py', 'SOURCE_INVENTORY.json'})
                self.assertEqual(archive.read('src/example.py'), data)
            with self.assertRaises(ValueError):
                delivery.pack(root, Path(folder, 'source.zip'), expected)
            path.write_bytes(data + b'# modified\n')
            with self.assertRaises(ValueError):
                delivery.verify(root, expected)

    def test_unlisted_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'source').mkdir()
            receipt = root / 'SOURCE_INVENTORY.json'
            receipt.write_text(json.dumps({'file_count': 0, 'payload_size': 0, 'files': []}), encoding='utf-8')
            (root / 'source' / 'private.txt').write_text('private', encoding='utf-8')
            with self.assertRaises(ValueError):
                delivery.verify(root, delivery.digest(receipt))

    def test_prepare_preserves_full_function_source_and_excludes_local_state(self):
        with tempfile.TemporaryDirectory() as folder:
            root, prepared = Path(folder, 'input'), Path(folder, 'prepared')
            public = ('README.md', 'requirements-runtime.lock', 'inspect-daily.bat',
                      'delivery/runtime-downloads.json', 'delivery/acceptance.json',
                      'delivery/baselines/r38-package-manifest.json',
                      'delivery/baselines/r46-package-manifest.json', 'delivery/baselines/r49-package-manifest.json', 'delivery/baselines/r50-package-manifest.json', 'delivery/baselines/r51-package-manifest.json', 'delivery/baselines/r52-package-manifest.json', 'delivery/baselines/r53-package-manifest.json', 'delivery/baselines/r55-package-manifest.json', 'delivery/baselines/r56-package-manifest.json', 'delivery/baselines/r57-package-manifest.json', 'delivery/baselines/r58-package-manifest.json', 'delivery/baselines/r59-package-manifest.json', 'delivery/baselines/r60-package-manifest.json', 'src/pcr_maa/labyrinth/example.py',
                      'vendor/autopcr_sync/example.py', 'assets/labyrinth/icons/icon.png',
                      'assets/resource/image/labyrinth/scene/home_level.png',
                      'third_party/materials/LICENSE', '.github/workflows/ci.yml',
                      'docs/licenses/runtime/python/LICENSE.txt')
            excluded = ('config.json', '.runtime/run/report.json', 'gui/App/bin/compiled.dll',
                        'gui/App/obj/generated.cs', 'src/__pycache__/cached.pyc',
                        'sessions/private.json', 'delivery/runtime-package-manifest.json',
                        'delivery/private-evidence.json', 'delivery/baselines/private-manifest.json')
            for name in public + excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture\n', encoding='utf-8')
            inventory = delivery.prepare(root, prepared, revision=39)
            self.assertEqual({row['path'] for row in inventory['files']}, set(public))
            self.assertEqual(inventory['revision'], 39)
            self.assertEqual(delivery.verify(prepared, delivery.digest(prepared / 'SOURCE_INVENTORY.json')),
                             inventory)
            with self.assertRaises(ValueError):
                delivery.prepare(root, prepared)
            with self.assertRaises(ValueError):
                delivery.prepare(root, root / 'new-output')
            nested = delivery.prepare(root, root / '.runtime' / 'prepared')
            self.assertEqual({row['path'] for row in nested['files']}, set(public))

    def test_prepare_rejects_private_content_and_mismatched_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder, 'input')
            root.mkdir()
            (root / 'README.md').write_text('fixture', encoding='utf-8')
            manifest = Path(folder, 'manifest.json')
            manifest.write_text(json.dumps({'update': {'revision': 38}}), encoding='utf-8')
            with self.assertRaises(ValueError):
                delivery.prepare(root, Path(folder, 'mismatch'), revision=39, player_manifest=manifest)
            self.assertFalse(Path(folder, 'mismatch').exists())
            (root / 'README.md').write_text('/'.join(('C:', 'Users', 'private', 'project')),
                                           encoding='utf-8')
            with self.assertRaises(ValueError):
                delivery.prepare(root, Path(folder, 'private'))
            self.assertFalse(Path(folder, 'private').exists())

    def test_manifest_tampering_and_listed_non_source_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder, 'input')
            root.mkdir()
            (root / 'README.md').write_text('fixture', encoding='utf-8')
            prepared = Path(folder, 'prepared')
            inventory = delivery.prepare(root, prepared)
            receipt = prepared / 'SOURCE_INVENTORY.json'
            expected = delivery.digest(receipt)
            inventory['file_count'] += 1
            receipt.write_text(json.dumps(inventory), encoding='utf-8')
            with self.assertRaises(ValueError):
                delivery.verify(prepared, expected)
            with self.assertRaises(ValueError):
                delivery.verify(prepared, delivery.digest(receipt))
            path = prepared / 'source' / 'private.txt'
            path.write_text('private', encoding='utf-8')
            inventory['files'].append({'path': 'private.txt', 'size': 7,
                                      'sha256': delivery.digest(path)})
            inventory['payload_size'] += 7
            receipt.write_text(json.dumps(inventory), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'outside_allowlist'):
                delivery.verify(prepared, delivery.digest(receipt))

    def test_deterministic_pack_and_archive_outside_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder, 'input')
            root.mkdir()
            (root / 'README.md').write_text('fixture', encoding='utf-8')
            prepared = Path(folder, 'prepared')
            delivery.prepare(root, prepared)
            expected = delivery.digest(prepared / 'SOURCE_INVENTORY.json')
            first = delivery.pack(prepared, Path(folder, 'one.zip'), expected)
            second = delivery.pack(prepared, Path(folder, 'two.zip'), expected)
            self.assertEqual(first, second)
            with self.assertRaises(ValueError):
                delivery.pack(prepared, prepared / 'source' / 'package.zip', expected)

    def test_windows_device_paths_and_non_integer_sizes_are_rejected(self):
        for name in ('CON', 'src/NUL.py', 'src/trailing. ', 'src/COM1.txt',
                     'src/new\nline.py', 'src/wild*.py', 'src/question?.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                delivery.relative(name)
        with self.assertRaises(ValueError):
            delivery.row_index([{'path': 'README.md', 'size': True, 'sha256': '0' * 64}])

    def test_gate_checks_all_feature_sources_without_fixed_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root, player = Path(folder, 'input'), Path(folder, 'player')
            names = ('src/pcr_maa/labyrinth/example.py', 'vendor/autopcr_sync/example.py',
                     'assets/resource/image/labyrinth/scene/home_level.png',
                     'assets/labyrinth/icons/icon.png', 'assets/interface.json',
                     'interface.json', 'docs/RELEASE_STATUS.json', 'licenses/labyrinth/LICENSE',
                     'licenses/runtime/LICENSE')
            rows = []
            for name in names:
                for base in (root, player):
                    path = base / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text('fixture', encoding='utf-8')
                rows.append({'path': name, 'size': 7, 'sha256': delivery.digest(player / name)})
            rows.append({'path': 'runtime/python/Lib/site-packages/maa/bin/MaaFramework.dll',
                         'size': 0, 'sha256': '0' * 64})
            manifest = player / 'package-manifest.json'
            manifest.write_text(json.dumps({'update': {'revision': 39}, 'files': rows}), encoding='utf-8')
            prepared = Path(folder, 'prepared')
            delivery.prepare(root, prepared, revision=39, player_manifest=manifest)
            expected = delivery.digest(prepared / 'SOURCE_INVENTORY.json')
            result = source_gate.gate(prepared, expected, player)
            self.assertEqual(result['owned_program_source_alignment_count'], len(names))
            self.assertEqual(result['revision'], 39)
            self.assertTrue(result['sealed_runtime_manifest_snapshot_present_and_matching'])
            (player / names[0]).write_text('changed', encoding='utf-8')
            rows[0]['sha256'] = delivery.digest(player / names[0])
            manifest.write_text(json.dumps({'update': {'revision': 39}, 'files': rows}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'owned_source_mismatch'):
                source_gate.gate(prepared, expected, player)

    def test_gui_inputs_are_owned_and_matching_source_is_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root, player = Path(folder, 'input'), Path(folder, 'player')
            root.mkdir()
            player.mkdir()
            gui = root / 'gui' / 'App.cs'
            gui.parent.mkdir()
            gui.write_text('fixture', encoding='utf-8')
            (root / 'README.md').write_text('fixture', encoding='utf-8')
            (player / 'README.md').write_text('fixture', encoding='utf-8')
            build_inputs = player / 'build-source-manifest.json'
            build_inputs.write_text(json.dumps({'schema_version': 1, 'files': [
                {'path': 'gui/App.cs', 'size': 7, 'sha256': delivery.digest(gui)}]}), encoding='utf-8')
            (player / 'MFAAvalonia.dll').write_bytes(b'fixture')
            names = ('README.md', 'build-source-manifest.json', 'MFAAvalonia.dll')
            manifest = player / 'package-manifest.json'
            data = {'update': {'revision': 39}, 'files': [
                {'path': name, 'size': (player / name).stat().st_size,
                 'sha256': delivery.digest(player / name)} for name in names]}
            manifest.write_text(json.dumps(data), encoding='utf-8')
            prepared = Path(folder, 'prepared')
            delivery.prepare(root, prepared, revision=39, player_manifest=manifest)
            expected = delivery.digest(prepared / 'SOURCE_INVENTORY.json')
            result = source_gate.gate(prepared, expected, player)
            self.assertEqual(result['gui_build_input_source_alignment_count'], 1)
            data['distribution_ready'] = True
            manifest.write_text(json.dumps(data), encoding='utf-8')
            delivery.refresh_player_snapshot(prepared, expected, manifest)
            expected = delivery.digest(prepared / 'SOURCE_INVENTORY.json')
            source_gate.gate(prepared, expected, player)
            changed = Path(folder, 'changed')
            gui.write_text('changed', encoding='utf-8')
            delivery.prepare(root, changed, revision=39, player_manifest=manifest)
            with self.assertRaisesRegex(ValueError, 'gui_build_input_source_mismatch'):
                source_gate.gate(changed, delivery.digest(changed / 'SOURCE_INVENTORY.json'), player)
            data['files'] = [row for row in data['files'] if row['path'] != 'build-source-manifest.json']
            manifest.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'gui_build_input_binding_missing'):
                source_gate.gate(prepared, expected, player)

    def test_generated_launcher_snapshot_preserves_exact_owned_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root, player = Path(folder, 'input'), Path(folder, 'player')
            root.mkdir()
            player.mkdir()
            names = ('导入补丁包.bat', '启动 Maa PCR.bat')
            rows = []
            for name in names:
                if name == '导入补丁包.bat':
                    (root / name).write_bytes(b'@echo off\n')
                launcher = player / name
                launcher.write_bytes(b'@echo off\r\n')
                rows.append({'path': name, 'size': launcher.stat().st_size,
                             'sha256': delivery.digest(launcher)})
            manifest = player / 'package-manifest.json'
            manifest.write_text(json.dumps({'update': {'revision': 39}, 'files': rows}),
                encoding='utf-8')
            prepared = Path(folder, 'prepared')
            delivery.prepare(root, prepared, revision=39, player_manifest=manifest)
            for name in names:
                self.assertEqual((prepared / 'source' / name).read_bytes(), (player / name).read_bytes())
            source_gate.gate(prepared, delivery.digest(prepared / 'SOURCE_INVENTORY.json'), player)


if __name__ == '__main__':
    unittest.main()
