import base64
import hashlib
import json

import pytest

from scripts.prepare_clan_season import attach_replay


def encode(document):
    return json.dumps(document, ensure_ascii=False).encode('utf-8')


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def replay(tmp_path):
    image = tmp_path / 'frames' / '首领.png'
    image.parent.mkdir()
    image.write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNoAAAAggCBd81ytgAAAABJRU5ErkJggg=='))
    identity = dict(period='2026-11', content_version='202611131613')
    cases = [dict(page='boss_detail', expect=True, file='frames/首领.png', phase='C', boss=1),
             dict(page='result', expect=False, file='frames/首领.png')]
    frames = tmp_path / 'frames.json'
    frames.write_bytes(encode(dict(identity, cases=cases)))
    turnover = dict(season={'period': identity['period']}, database={'content_version': identity['content_version']})
    files = {'profile.json': encode({'pages': {'boss_detail': {}, 'result': {}, 'formation': {}}}),
             'turnover.json': encode(turnover), 'index-entry.json': encode({'capabilities': []})}
    rows = [dict(case, file=image.name, sha256=digest(image.read_bytes()), matched=matched)
            for case, matched in zip(cases, (True, None), strict=True)]
    observed = dict(identity, kind='clan_season_layout_replay', device_inputs=0, capabilities=[],
                    input_profile_sha256=digest(files['profile.json']), manifest_sha256=digest(frames.read_bytes()),
                    cases=rows, actions={},
                    pages={'boss_detail': 'positive_only', 'result': 'unverified', 'formation': 'missing'})
    return files, frames, tmp_path / 'observed.json', observed


def test_attach_preserves_candidate_and_records_only_evaluated_page_scope(replay):
    files, frames, path, observed = replay
    profile, index = files['profile.json'], files['index-entry.json']
    path.write_bytes(encode(observed))
    attach_replay(files, frames, path)
    assert files['profile.json'] == profile and files['index-entry.json'] == index
    assert json.loads(files['turnover.json'])['layout_replay'] == {
        'sha256': digest(path.read_bytes()), 'manifest_sha256': digest(frames.read_bytes()),
        'cases': 2, 'unique_frames': 1, 'pages': observed['pages'], 'actions': {}}
    previous = dict(files)
    attach_replay(files, frames, path)
    assert files == previous


@pytest.mark.parametrize(('key', 'value'), [
    ('input_profile_sha256', '0' * 64), ('manifest_sha256', '1' * 64), ('period', '2026-10'),
    ('content_version', '202610141448'), ('device_inputs', 1), ('device_inputs', False),
    ('capabilities', ['simulation']), ('kind', 'different'),
    ('pages', {'boss_detail': 'matched', 'result': 'matched', 'formation': 'matched'}),
    ('actions', {'formation/start': 'matched'}), ('cases', []),
])
def test_attach_rejects_wrong_run_identity_and_unbacked_summary(replay, key, value):
    files, frames, path, observed = replay
    before = dict(files)
    observed[key] = value
    path.write_bytes(encode(observed))
    with pytest.raises(ValueError):
        attach_replay(files, frames, path)
    assert files == before


@pytest.mark.parametrize(('key', 'value'), [
    ('file', 'other.png'), ('sha256', 'a' * 64), ('matched', 1),
    ('phase', 'D'), ('boss', 2), ('boss', True), ('expect', 1), ('page', 'battle'),
])
def test_attach_requires_the_same_labeled_case(replay, key, value):
    files, frames, path, observed = replay
    observed['cases'][0][key] = value
    path.write_bytes(encode(observed))
    with pytest.raises(ValueError):
        attach_replay(files, frames, path)


def test_an_action_not_labeled_in_the_frame_cannot_be_added_to_the_summary(replay):
    files, frames, path, observed = replay
    observed['cases'][0]['action'] = 'start'
    observed['actions']['boss_detail/start'] = 'positive_only'
    path.write_bytes(encode(observed))
    with pytest.raises(ValueError):
        attach_replay(files, frames, path)


def test_attach_rehashes_current_frame_bytes(replay):
    files, frames, path, observed = replay
    path.write_bytes(encode(observed))
    (frames.parent / 'frames/首领.png').write_bytes(b'changed since replay')
    with pytest.raises(ValueError):
        attach_replay(files, frames, path)


def test_matching_old_report_and_frame_manifest_cannot_attach_to_new_season(replay):
    files, frames, path, observed = replay
    manifest = json.loads(frames.read_bytes())
    manifest['period'] = observed['period'] = '2026-10'
    frames.write_bytes(encode(manifest))
    observed['manifest_sha256'] = digest(frames.read_bytes())
    path.write_bytes(encode(observed))
    with pytest.raises(ValueError):
        attach_replay(files, frames, path)


@pytest.mark.parametrize(('key', 'value'), [('expect', 1), ('boss', False), ('boss', 6)])
def test_digest_agreement_does_not_make_malformed_frame_labels_valid(replay, key, value):
    files, frames, path, observed = replay
    manifest = json.loads(frames.read_bytes())
    manifest['cases'][0][key] = observed['cases'][0][key] = value
    frames.write_bytes(encode(manifest))
    observed['manifest_sha256'] = digest(frames.read_bytes())
    path.write_bytes(encode(observed))
    with pytest.raises(ValueError):
        attach_replay(files, frames, path)
