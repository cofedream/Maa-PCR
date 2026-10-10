"""Add one exact verified release-component report before sealing the player."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil


parser = argparse.ArgumentParser()
parser.add_argument('--player', required=True)
parser.add_argument('--source', required=True)
parser.add_argument('--sha256', required=True)
parser.add_argument('--manifest-sha256', required=True)
parser.add_argument('--receipt', required=True)
args = parser.parse_args()
player, source = Path(args.player), Path(args.source)
manifest_path = player / 'package-manifest.json'
spec = importlib.util.spec_from_file_location('metadata_maintenance', player / 'src/pcr_maa/player_maintenance.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
tool._root(player)
assert tool._sha(manifest_path) == args.manifest_sha256
assert tool._sha(source) == args.sha256
manifest = tool._read_json(manifest_path)
assert manifest['status'] == 'release_staging' and manifest['update']['revision'] == 24
rows = tool._rows(manifest)
tool._verify(player, rows)
name = 'licenses/verified-r24/RELEASE_COMPONENTS-r24.json'
tool._name(name)
assert name not in rows and not (player / name).exists()
shutil.copyfile(source, player / name)
assert tool._sha(player / name) == args.sha256
rows[name] = {'path': name, 'size': source.stat().st_size, 'sha256': args.sha256}
manifest['files'] = [rows[key] for key in sorted(rows)]
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
tool._verify(player, tool._rows(manifest))
receipt = {'passed': True, 'added_release_provenance': rows[name],
    'manifest_sha256': tool._sha(manifest_path), 'owned_payload_count': len(rows),
    'previous_owned_program_bytes_preserved': True, 'personal_files_accessed': 0}
output = Path(args.receipt)
assert not output.exists()
output.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
print(json.dumps(receipt))
