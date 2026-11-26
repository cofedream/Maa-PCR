"""Public calibration samples retain only reviewed source pixels."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image, ImageChops, PngImagePlugin
import pytest

from scripts.prepare_clan_public_samples import prepare


CALIBRATIONS = Path('assets/resource/clan-battle-calibrations')
INDEX = CALIBRATIONS / 'index.json'
IMAGES = Path('assets/resource/image')
VERSION = '202611131613'
SHARED = 'clan-battle/shared/menu.png'
ADDED = 'clan-battle/nov/next.png'
REGIONS = [[10, 20, 6, 5], [30, 4, 3, 4]]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def read_json(path):
    return json.loads(path.read_bytes())


def png(path, color, size, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new('RGB', size, color)
    metadata = PngImagePlugin.PngInfo()
    if private:
        metadata.add_text('Account', 'private-test-account')
        image.paste((53, 71, 89), (10, 20, 16, 25))
    image.save(path, format='PNG', pnginfo=metadata)
    return digest(path)


def descriptor(version, sha):
    return dict(calibration_id=f'cn-bilibili-{version}-clan-simulation', server='cn-bilibili',
                content_version=version, frame_size=[1280, 720], capabilities=['simulation'],
                profile_path=(CALIBRATIONS / f'cn-bilibili-{version}' / 'profile.json').as_posix(),
                profile_sha256=sha)


class Inputs:
    def __init__(self, folder):
        self.src, self.base = folder / 'source', folder / 'base'
        self.review_file, self.output = folder / 'review.json', folder / 'overlay'
        self.entry = descriptor(VERSION, '')
        self.old = descriptor('202610141448', '')
        self.private = self.src / '.runtime' / 'private-test-account-original.png'
        self.sample_sha = png(self.private, (13, 23, 37), (1280, 720), private=True)
        shared_sha = png(self.src / IMAGES / SHARED, (12, 34, 56), (12, 9))
        png(self.base / IMAGES / SHARED, (12, 34, 56), (12, 9))
        self.new_sha = png(self.src / IMAGES / ADDED, (5, 7, 9), (8, 12))
        sample = dict(path=self.private.relative_to(self.src).as_posix(), page='result',
                      sha256=self.sample_sha)
        self.profile = dict(
            version=2, server='cn-bilibili', frame_size=[1280, 720], evidence_kind='device_saved_frame',
            calibration_id=self.entry['calibration_id'], battle_hud='guide-trial-1280x720',
            support_modes=['none'], formation={}, actions={}, samples=[sample, dict(sample)],
            pages={'result': {'labels': [dict(recognition='TemplateMatch',
                         template=[SHARED, ADDED], sha256=[shared_sha, self.new_sha], roi=[0, 0, 50, 50])],
                    'fields': {'damage': REGIONS[0]},
                    'damage_line': {'sample_sha256': self.sample_sha, 'verify_roi': [11, 20, 4, 4]}}},
            simulation_targets=[dict(period='2026-11', phase='C', boss=1)])
        self.review = dict(version=1, samples={self.sample_sha: dict(name='result-line', regions=REGIONS)},
                           new_templates={ADDED: self.new_sha})
        self.save_profile()
        prior = self.make_base_profile(self.old)
        write_json(self.base / INDEX, {'version': 2, 'entries': [prior]})

    def make_base_profile(self, entry):
        relative = Path(entry['profile_path']).parent / 'samples/previous-region.png'
        old_sha = png(self.base / relative, (12, 18, 24), (1280, 720))
        old = deepcopy(self.profile)
        old['calibration_id'] = entry['calibration_id']
        old['samples'] = [dict(page='result', path=relative.as_posix(), sha256=old_sha)]
        old['pages']['result']['labels'] = []
        old['pages']['result']['damage_line']['sample_sha256'] = old_sha
        path = self.base / entry['profile_path']
        write_json(path, old)
        entry['profile_sha256'] = digest(path)
        return entry

    def save_profile(self):
        path = self.src / self.entry['profile_path']
        write_json(path, self.profile)
        self.entry['profile_sha256'] = self.review['profile_sha256'] = digest(path)
        write_json(self.src / INDEX, {'version': 2, 'entries': [self.entry]})

    def run(self):
        write_json(self.review_file, self.review)
        return prepare(self.src, self.base, VERSION, self.review_file, self.output)


@pytest.fixture
def files(tmp_path):
    return Inputs(tmp_path)


@pytest.mark.parametrize('replace', [False, True])
def test_cli_exports_only_reviewed_pixels_and_preserves_other_season(files, replace):
    if replace:
        previous = files.make_base_profile(dict(files.entry))
        write_json(files.base / INDEX, {'version': 2, 'entries': [files.old, previous]})
    base_index = (files.base / INDEX).read_bytes()
    write_json(files.review_file, files.review)
    command = [sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'scripts/prepare_clan_public_samples.py'),
               '--source-root', str(files.src), '--base-root', str(files.base), '--content-version', VERSION,
               '--review', str(files.review_file), '--output', str(files.output)]
    subprocess.run(command, check=True, capture_output=True)
    entries = read_json(files.output / INDEX)['entries']
    assert [item for item in entries if item['content_version'] != VERSION] == [files.old]
    current, = [item for item in entries if item['content_version'] == VERSION]
    profile_path = files.output / current['profile_path']
    assert digest(profile_path) == current['profile_sha256']
    profile = read_json(profile_path)
    assert profile['simulation_targets'] == files.profile['simulation_targets']
    assert current['capabilities'] == files.entry['capabilities']
    assert profile['samples'][0] == profile['samples'][1]
    public = files.output / profile['samples'][0]['path']
    assert public.is_relative_to(profile_path.parent / 'samples')
    assert digest(public) == profile['samples'][0]['sha256'] == profile['pages']['result']['damage_line']['sample_sha256']
    assert digest(public) != files.sample_sha
    with Image.open(public) as processed, Image.open(files.private) as original:
        assert processed.size == original.size and not processed.text and not processed.getexif()
        expected = Image.new('RGB', original.size)
        for x, y, width, height in REGIONS:
            box = (x, y, x + width, y + height)
            expected.paste(original.crop(box), (x, y))
        assert ImageChops.difference(expected, processed.convert('RGB')).getbbox() is None
    assert (files.output / IMAGES / ADDED).read_bytes() == (files.src / IMAGES / ADDED).read_bytes()
    assert not (files.output / IMAGES / SHARED).exists()
    assert (files.base / INDEX).read_bytes() == base_index and digest(files.private) == files.sample_sha
    assert all(b'private-test-account' not in path.read_bytes() for path in files.output.rglob('*') if path.is_file())


@pytest.mark.parametrize('bad', [
    'missing_sample', 'extra_sample', 'missing_template', 'extra_template', 'wrong_profile',
    'template_changed', 'base_template_conflict', 'sample_changed', 'source_escape', 'unsafe_name',
    'invalid_png', 'wrong_dimensions', 'negative_roi', 'out_of_bounds', 'empty_roi', 'whole_frame', 'whole_frame_split',
    'lost_result_pixel', 'template_metadata', 'template_suffix', 'duplicate_name', 'nested_private_path',
])
def test_bad_materials_do_not_publish_partial_output(files, bad):
    samples, templates = files.review['samples'], files.review['new_templates']
    if bad == 'missing_sample':
        samples.clear()
    elif bad == 'extra_sample':
        samples['f' * 64] = dict(name='extra', regions=REGIONS)
    elif bad == 'missing_template':
        templates.clear()
    elif bad == 'extra_template':
        templates['clan-battle/other.png'] = '0' * 64
    elif bad == 'wrong_profile':
        files.review['profile_sha256'] = 'a' * 64
    elif bad == 'template_changed':
        png(files.src / IMAGES / ADDED, (9, 8, 7), (8, 12))
    elif bad == 'base_template_conflict':
        png(files.base / IMAGES / ADDED, (8, 9, 7), (8, 12))
    elif bad == 'sample_changed':
        png(files.private, (8, 9, 7), (1280, 720))
    elif bad == 'source_escape':
        outside = files.src.parent / 'outside.png'
        outside.write_bytes(files.private.read_bytes())
        for sample in files.profile['samples']:
            sample['path'] = '../outside.png'
        files.save_profile()
    elif bad == 'unsafe_name':
        samples[files.sample_sha]['name'] = '../private-test-account'
    elif bad == 'nested_private_path':
        files.profile['actions']['nested'] = [{'path': '.runtime/private-test-account-original.png'}]
        files.save_profile()
    elif bad == 'duplicate_name':
        other = files.private.with_name('different.png')
        other_sha = png(other, (28, 55, 77), (1280, 720))
        files.profile['samples'][1] = dict(path=other.relative_to(files.src).as_posix(),
                                           page='result', sha256=other_sha)
        samples[other_sha] = dict(samples[files.sample_sha])
        files.save_profile()
    elif bad in ('template_metadata', 'template_suffix'):
        path = files.src / IMAGES / ADDED
        if bad == 'template_metadata':
            png(path, (5, 7, 9), (8, 12), private=True)
        else:
            path.write_bytes(path.read_bytes() + b'private-test-account')
        reviewed_sha = digest(path)
        files.profile['pages']['result']['labels'][0]['sha256'][1] = reviewed_sha
        templates[ADDED] = reviewed_sha
        files.save_profile()
    elif bad in ('invalid_png', 'wrong_dimensions'):
        if bad == 'invalid_png':
            files.private.write_bytes(b'not a png')
        else:
            png(files.private, (7, 8, 9), (640, 360))
        replacement = digest(files.private)
        samples[replacement] = samples.pop(files.sample_sha)
        for sample in files.profile['samples']:
            sample['sha256'] = replacement
        files.profile['pages']['result']['damage_line']['sample_sha256'] = replacement
        files.save_profile()
    else:
        samples[files.sample_sha]['regions'] = {
            'negative_roi': [[-1, 0, 5, 5]], 'out_of_bounds': [[1270, 0, 20, 5]], 'empty_roi': [],
            'whole_frame': [[0, 0, 1280, 720]], 'whole_frame_split': [[0, 0, 640, 720], [640, 0, 640, 720]],
            'lost_result_pixel': [[10, 20, 6, 4], [10, 24, 5, 1]],
        }[bad]
    write_json(files.review_file, files.review)
    before = set(files.src.parent.iterdir())
    with pytest.raises((OSError, ValueError)):
        files.run()
    assert set(files.src.parent.iterdir()) == before and not files.output.exists()


def test_existing_output_is_preserved(files):
    files.output.mkdir()
    sentinel = files.output / 'keep'
    sentinel.write_bytes(b'preexisting')
    with pytest.raises((OSError, ValueError)):
        files.run()
    assert list(files.output.iterdir()) == [sentinel] and sentinel.read_bytes() == b'preexisting'


@pytest.mark.parametrize('state', ['registered', 'loose', 'tampered', 'wrong_page'])
def test_review_can_reuse_a_registered_public_sample_with_the_same_page_and_digest(files, state):
    relative = Path(files.old['profile_path']).parent / 'samples/result-control.png'
    public = files.base / relative
    pixels = Image.new('RGB', (1280, 720))
    pixels.paste((53, 71, 89), (10, 20, 16, 25))
    pixels.save(public, format='PNG')
    shared_sha = digest(public)
    files.private.write_bytes(public.read_bytes())
    for sample in files.profile['samples']:
        sample['sha256'] = shared_sha
    files.profile['pages']['result']['damage_line']['sample_sha256'] = shared_sha
    files.review['samples'] = {}
    files.save_profile()

    # Only data bound by a base index entry can grant reuse. An otherwise identical loose PNG cannot.
    old = deepcopy(files.profile)
    old['calibration_id'] = files.old['calibration_id']
    old['simulation_targets'][0]['period'] = '2026-10'
    prior_path, prior_sha = relative, shared_sha
    if state == 'loose':
        prior_path = relative.with_name('previous-control.png')
        prior_sha = png(files.base / prior_path, (17, 28, 39), (1280, 720))
    old['samples'] = [dict(page='formation' if state == 'wrong_page' else 'result',
                           path=prior_path.as_posix(), sha256=prior_sha)]
    old['pages']['result']['damage_line']['sample_sha256'] = prior_sha
    old['pages']['result']['labels'] = []
    old_path = files.base / files.old['profile_path']
    write_json(old_path, old)
    files.old['profile_sha256'] = digest(old_path)
    write_json(files.base / INDEX, {'version': 2, 'entries': [files.old]})
    if state == 'tampered':
        png(public, (33, 66, 99), (1280, 720))
    if state != 'registered':
        with pytest.raises((ValueError, OSError)):
            files.run()
        assert not files.output.exists()
        return

    files.run()
    entry, = [row for row in read_json(files.output / INDEX)['entries'] if row['content_version'] == VERSION]
    profile = read_json(files.output / entry['profile_path'])
    assert all(sample == dict(page='result', path=relative.as_posix(), sha256=shared_sha) for sample in profile['samples'])
    assert profile['pages']['result']['damage_line']['sample_sha256'] == shared_sha
    assert not (files.output / relative).exists()
    assert not list(files.output.rglob('samples/*.png'))


@pytest.mark.parametrize('case', ['mixed', 'swap_lines', 'hidden_primary', 'hidden_verification', 'wrong_prior_mode'])
def test_simulation_and_formal_result_materials_are_independently_bound(files, case):
    relative = Path(files.old['profile_path']).parent / 'samples/formal-line.png'
    base_frame = files.base / relative
    frame = Image.new('RGB', (1280, 720))
    formal_roi = [340, 51, 20, 8]
    frame.paste((60, 70, 80), (340, 51, 360, 59))
    frame.save(base_frame, format='PNG')
    formal_sha = digest(base_frame)
    formal_line = dict(sample_sha256=formal_sha, verify_roi=[342, 52, 16, 5])
    formal = dict(fields=dict(damage=formal_roi), damage_line=formal_line)
    registered = dict(path=relative.as_posix(), sha256=formal_sha, page='result')
    old_file = files.base / files.old['profile_path']
    old = read_json(old_file)
    if case == 'wrong_prior_mode':
        old['samples'].append(registered)
    else:
        old['daily'] = dict(execution=dict(samples=[registered], pages=dict(result=deepcopy(formal))))
    write_json(old_file, old)
    files.old['profile_sha256'] = digest(old_file)
    write_json(files.base / INDEX, {'version': 2, 'entries': [files.old]})

    current = files.private.with_name('formal.png')
    current.write_bytes(base_frame.read_bytes())
    new_sample = dict(registered, path=current.relative_to(files.src).as_posix())
    files.profile['daily'] = dict(execution=dict(samples=[new_sample], pages=dict(result=formal)))
    sim_line = files.profile['pages']['result']['damage_line']
    if case == 'swap_lines':
        sim_line['sample_sha256'], formal_line['sample_sha256'] = formal_sha, files.sample_sha
    if case in ('hidden_primary', 'hidden_verification'):
        # First case hides only the last column outside the verification box; second covers neither right edge.
        width = 19 if case == 'hidden_primary' else 17
        files.review['samples'][formal_sha] = dict(name='formal-line', regions=[[340, 51, width, 8]])
    files.save_profile()
    if case != 'mixed':
        with pytest.raises((OSError, ValueError)):
            files.run()
        assert not files.output.exists()
        return

    files.run()
    entry, = [row for row in read_json(files.output / INDEX)['entries'] if row['content_version'] == VERSION]
    public = read_json(files.output / entry['profile_path'])
    assert public['pages']['result']['damage_line']['sample_sha256'] == public['samples'][0]['sha256'] != files.sample_sha
    execution = public['daily']['execution']
    assert execution['pages']['result']['damage_line'] == formal_line
    assert execution['samples'] == [registered] and not (files.output / relative).exists()


@pytest.mark.parametrize('second_collision', [False, True])
def test_same_month_rebuild_preserves_samples_referenced_by_another_month(files, second_collision):
    def november_sample(root):
        profile = read_json(root / files.entry['profile_path'])
        return profile['samples'][0]

    files.run()
    first = november_sample(files.output)
    short = Path(first['path'])
    assert short.name == 'result-line.png'
    new_sha = digest(files.output / short)
    assert first['sha256'] == new_sha
    alternate = short.with_name('result-line-' + new_sha + '.png')

    old_sha = png(files.base / short, (5, 9, 14), (1280, 720))
    old_bytes = (files.base / short).read_bytes()
    assert old_sha != new_sha
    # October and the currently published November both use an image in November's directory.
    november = files.make_base_profile(dict(files.entry))
    for entry in (files.old, november):
        path = files.base / entry['profile_path']
        profile = read_json(path)
        profile['samples'] = [dict(page='result', path=short.as_posix(), sha256=old_sha)]
        profile['pages']['result']['damage_line']['sample_sha256'] = old_sha
        write_json(path, profile)
        entry['profile_sha256'] = digest(path)
    write_json(files.base / INDEX, {'version': 2, 'entries': [files.old, november]})

    files.output = files.output.with_name('new-overlay')
    if second_collision:
        occupied_sha = png(files.base / alternate, (1, 12, 17), (1280, 720))
        assert occupied_sha != new_sha
        with pytest.raises((OSError, ValueError)):
            files.run()
        assert not files.output.exists()
        assert digest(files.base / alternate) == occupied_sha and (files.base / short).read_bytes() == old_bytes
        return

    files.run()
    assert not (files.output / short).exists()
    assert november_sample(files.output) == dict(page='result', path=alternate.as_posix(), sha256=new_sha)
    shutil.copytree(files.output, files.base, dirs_exist_ok=True)
    assert (files.base / short).read_bytes() == old_bytes
    assert digest(files.base / alternate) == new_sha
    for entry in read_json(files.base / INDEX)['entries']:
        path = files.base / entry['profile_path']
        assert digest(path) == entry['profile_sha256']
        for sample in read_json(path)['samples']:
            assert digest(files.base / sample['path']) == sample['sha256']

    files.review['new_templates'] = {}
    files.output = files.output.with_name('repeat-overlay')
    files.run()
    for path in (INDEX, files.entry['profile_path']):
        assert (files.output / path).read_bytes() == (files.base / path).read_bytes()
