"""Prepare a portable clan calibration overlay from reviewed regions of real game frames."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
from tempfile import TemporaryDirectory

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pcr_maa.clan_battle_calibration_data import INDEX_PATH, _json, _path, _read, parse_index, read_profile

SIZE = (1280, 720)
CALIBRATIONS = PurePosixPath(INDEX_PATH).parent
IMAGES = 'assets/resource/image'


def _save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _objects(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)


def _box(rect):
    if not isinstance(rect, list) or len(rect) != 4 or any(type(v) != int for v in rect):
        raise ValueError('expected an integer [x, y, width, height] region')
    x, y, w, h = rect
    if min(x, y) < 0 or min(w, h) <= 0 or x + w > SIZE[0] or y + h > SIZE[1]:
        raise ValueError('sample region lies outside the frame')
    return x, y, x + w, y + h


def _sample_png(data, regions, required_regions):
    if not isinstance(regions, list) or not regions:
        raise ValueError('each sample requires reviewed pixel regions')
    with Image.open(io.BytesIO(data)) as original:
        if original.format != 'PNG' or original.size != SIZE or original.n_frames != 1:
            raise ValueError('expected one normalized 1280x720 PNG')
        if original.mode not in ('L', 'RGB', 'RGBA') or (
                original.mode == 'RGBA' and original.getchannel('A').getextrema() != (255, 255)):
            raise ValueError('sample must use opaque game pixels')
        pixels = original.convert('RGB')
    mask = Image.new('1', SIZE)
    for rect in regions:
        mask.paste(1, _box(rect))
    if mask.getextrema()[0] != 0:
        raise ValueError('regions cannot reveal the entire original frame')
    if any(mask.crop(_box(rect)).getextrema()[0] == 0 for rect in required_regions):
        raise ValueError('reviewed pixels must include both bound result damage regions')
    public = Image.new('RGB', SIZE)
    public.paste(pixels, mask=mask)
    if not public.getbbox():
        raise ValueError('reviewed sample contains no visible pixels')
    encoded = io.BytesIO()
    public.save(encoded, 'PNG')
    return encoded.getvalue()


def _sample_role(profile, section, page):
    if page != 'result':
        return page
    if section is profile:
        return 'simulation:result'
    if section is profile.get('daily', {}).get('execution'):
        return 'formal:result'
    return 'auxiliary:result'


def _known_samples(base, index):
    known = {}
    for entry in index['entries']:
        public_profile = read_profile(base, entry)
        for section in _objects(public_profile):
            for sample in section.get('samples', []):
                path = PurePosixPath(sample['path'])
                if (path.parent.name == 'samples' and path.parent.parent.parent == CALIBRATIONS
                        and path.suffix.lower() == '.png'):
                    role = _sample_role(public_profile, section, sample['page'])
                    known.setdefault((role, sample['sha256']), path.as_posix())
    return known


def _damage_bindings(profile):
    execution = profile.get('daily', {}).get('execution', {})
    sections = (profile, execution)
    result_frames = [{sample['sha256'] for sample in section.get('samples', []) if sample['page'] == 'result'}
                     for section in sections]
    bindings = {}
    for position, section in enumerate(sections):
        result = section.get('pages', {}).get('result', {})
        if 'damage_line' not in result:
            continue
        line = result['damage_line']
        if not isinstance(line, dict) or set(line) != {'sample_sha256', 'verify_roi'}:
            raise ValueError('invalid result damage binding')
        digest = line['sample_sha256']
        if digest not in result_frames[position] or digest in result_frames[1 - position]:
            raise ValueError('each damage line must bind to its own simulation or formal result frame')
        full = result['fields']['damage']
        verify = line['verify_roi']
        _box(full)
        _box(verify)
        bindings[digest] = (line, [full, verify])
    return bindings


def _public_sample_path(base, folder, name, data, digest):
    # Other seasons may reference an existing filename in the season being updated.
    for stem in (name, f'{name}-{digest}'):
        relative = (folder / 'samples' / (stem + '.png')).as_posix()
        existing = _path(base, relative)
        if not existing.exists() or (existing.is_file() and existing.stat().st_size == len(data)
                                     and existing.read_bytes() == data):
            return relative
    raise ValueError('public sample content-addressed path is already occupied')


def _collect_samples(source, stage, profile, reviewed, folder, base, index):
    mapping = {}
    used_names = set()
    known = None
    destinations = {}
    bindings = _damage_bindings(profile)
    for group in _objects(profile):
        if 'samples' not in group:
            continue
        if not isinstance(group['samples'], list):
            raise ValueError('invalid calibration sample list')
        for sample in group['samples']:
            if not isinstance(sample, dict) or set(sample) != {'page', 'path', 'sha256'}:
                raise ValueError('invalid calibration sample')
            digest = sample['sha256']
            _, original = _read(source, sample['path'], digest, limit=32 * 1024 * 1024)
            if digest not in reviewed:
                if known is None:
                    known = _known_samples(base, index)
                relative = known.get((_sample_role(profile, group, sample['page']), digest))
                if relative is None:
                    raise ValueError(f'missing review for a new sample: {digest}')
                _read(base, relative, digest, limit=32 * 1024 * 1024)
                mapping[digest] = relative, digest
            elif digest not in mapping:
                row = reviewed.get(digest)
                if not isinstance(row, dict) or set(row) != {'name', 'regions'}:
                    raise ValueError(f'missing or invalid sample review for {digest}')
                name = row['name']
                if (not isinstance(name, str) or not re.fullmatch('[a-z0-9][a-z0-9_-]{0,79}', name)
                        or name in used_names or name in ('con', 'nul', 'prn', 'aux')
                        or re.fullmatch('(?:com|lpt)[1-9]', name)):
                    raise ValueError('invalid or duplicate public sample name')
                used_names.add(name)
                required = bindings[digest][1] if digest in bindings else []
                public = _sample_png(original, row['regions'], required)
                public_digest = hashlib.sha256(public).hexdigest()
                relative = _public_sample_path(base, folder, name, public, public_digest)
                output = stage / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(public)
                mapping[digest] = relative, public_digest
            sample['path'], sample['sha256'] = mapping[digest]
            if destinations.setdefault(sample['path'], sample['sha256']) != sample['sha256']:
                raise ValueError('conflicting public sample destinations')
    if not mapping or set(reviewed) - set(mapping):
        raise ValueError('sample review contains unreferenced frames')
    for digest, (line, _) in bindings.items():
        line['sample_sha256'] = mapping[digest][1]


def _verify_template_png(data):
    # New templates keep their reviewed byte digest. Reject embedded metadata or trailing payloads.
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or image.n_frames != 1:
            raise ValueError('new game template must be a single PNG')
        image.verify()
    offset = 8
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], 'big')
        kind = data[offset + 4:offset + 8]
        offset += size + 12
        if kind == b'IEND':
            if size == 0 and offset == len(data):
                return
            break
        if kind not in (b'IHDR', b'PLTE', b'IDAT', b'tRNS'):
            break
    raise ValueError('new template has unexpected PNG data or metadata')


def _collect_templates(source, base, stage, profile, reviewed):
    required = {}
    for node in _objects(profile):
        if node.get('recognition') != 'TemplateMatch':
            continue
        names, digests = node['template'], node['sha256']
        if isinstance(names, str):
            names, digests = [names], [digests]
        if not isinstance(names, list) or not names or not isinstance(digests, list) or len(names) != len(digests):
            raise ValueError('invalid template list')
        for name, digest in zip(names, digests):
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.png', name):
                raise ValueError('unsafe public template name')
            relative = f'{IMAGES}/{name}'
            _, data = _read(source, relative, digest, limit=4 * 1024 * 1024)
            if _path(base, relative).exists():
                _read(base, relative, digest, limit=4 * 1024 * 1024)
                continue
            if reviewed.get(name) != digest:
                raise ValueError('missing review for new template: ' + name)
            if name not in required:
                _verify_template_png(data)
                output = stage / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
            elif required[name] != digest:
                raise ValueError('conflicting template digest: ' + name)
            required[name] = digest
    if reviewed != required:
        raise ValueError('unexpected entries in new template review')


def prepare(source_root, base_root, content_version, review, output):
    """Write a new overlay directory, leaving the source, base and existing outputs untouched."""
    source, base = Path(source_root).resolve(), Path(base_root).resolve()
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    reviewed = _json(Path(review).read_bytes())
    if (not isinstance(reviewed, dict) or set(reviewed) != {'version', 'profile_sha256', 'samples', 'new_templates'}
            or type(reviewed['version']) != int or reviewed['version'] != 1
            or not isinstance(reviewed['samples'], dict) or not isinstance(reviewed['new_templates'], dict)):
        raise ValueError('invalid public sample review')
    source_index = parse_index(source, _path(source, INDEX_PATH).read_bytes())
    index = parse_index(base, _path(base, INDEX_PATH).read_bytes())
    if index['version'] != 2 or source_index['version'] != 2:
        raise ValueError('source and public calibration indexes must be version 2')
    entry = next((item for item in source_index['entries'] if item['content_version'] == content_version), None)
    if entry is None or entry['profile_sha256'] != reviewed['profile_sha256']:
        raise ValueError('no matching reviewed calibration profile')
    profile = read_profile(source, entry)
    folder = CALIBRATIONS / f'cn-bilibili-{content_version}'
    # Any separate timing evidence must already be available at its public path in the base.
    if 'timing_measurements' in profile:
        item = profile['timing_measurements']
        if not isinstance(item, dict) or set(item) != {'path', 'sha256'} or not item['path'].startswith(str(CALIBRATIONS) + '/'):
            raise ValueError('timing evidence needs an existing public calibration path')
        for root in (source, base):
            _read(root, item['path'], item['sha256'], limit=1024 * 1024)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix='.clan-overlay-') as temp:
        stage = Path(temp) / 'public'
        stage.mkdir()
        _collect_samples(source, stage, profile, reviewed['samples'], folder, base, index)
        _collect_templates(source, base, stage, profile, reviewed['new_templates'])
        for node in _objects(profile):
            if 'path' in node:
                relative = node['path']
                if not isinstance(relative, str) or not PurePosixPath(relative).is_relative_to(CALIBRATIONS):
                    raise ValueError('profile retains a non-public resource path')
                _path(stage, relative)
        profile_path = (folder / 'profile.json').as_posix()
        entry['profile_path'] = profile_path
        entry['profile_sha256'] = _save_json(stage / profile_path, profile)
        for i, prior in enumerate(index['entries']):
            if prior['content_version'] == content_version:
                index['entries'][i] = entry
                break
        else:
            index['entries'].append(entry)
        index_digest = _save_json(stage / INDEX_PATH, index)
        parse_index(stage, (stage / INDEX_PATH).read_bytes())
        (stage / folder / 'README.md').write_text(
            '# 公会战页面识别样本\n\n'
            '样本来源为维护者在国服游戏客户端取得的画面。每张样本保留人工复核的识别区域，'
            '其余区域已遮蔽。模板来自对应的游戏界面。游戏图像的权利归原权利人，'
            '不按本项目的 MIT 许可证重新授权。\n\n'
            '这些样本用于复核所保留的控件和字段。完整阵容及实战结果以各期独立验收记录为准。\n',
            encoding='utf-8')
        if output.exists() or output.is_symlink():
            raise FileExistsError(output)
        stage.rename(output)
    return {'profile_sha256': entry['profile_sha256'], 'index_sha256': index_digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--base-root', type=Path, required=True)
    parser.add_argument('--content-version', required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(**vars(args))
    except (KeyError, OSError, TypeError, ValueError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
