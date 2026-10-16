"""Exact candidate deltas keep the ordinary updater's revision guard."""
import hashlib
import json

import pytest
from pcr_maa import player_maintenance as maintenance
from pcr_maa import player_update as updater
from test_player_patch_builder import builder, case, save_manifest


def candidate(case, **changes):
    base, target = case[:2]
    for root in (base, target):
        metadata = json.loads((root / maintenance.MANIFEST).read_bytes())
        metadata.update(status='release_staging', distribution_ready=False)
        metadata['update'].update(revision=53, version='v0.1.8-r53-candidate')
        if root == target:
            metadata.update(changes)
        save_manifest(root, metadata)
    return tuple(hashlib.sha256((root / maintenance.MANIFEST).read_bytes()).hexdigest()
                 for root in (base, target))


def test_exact_candidate_delta_requires_explicit_pair_and_preserves_private_state(builder, case):
    base, target, output, _, new = case
    pair = candidate(case)
    private = base / 'config/instances/player.json'
    private.parent.mkdir(parents=True)
    private.write_bytes(b'private settings keep exactly')
    result = builder.build_patch_from_manifest(base / maintenance.MANIFEST, target, output,
        pair[0], candidate_replacement=pair)
    assert result['base_manifest_sha256'] == pair[0]
    assert result['target_manifest_sha256'] == pair[1]
    with pytest.raises(ValueError, match='update_not_newer'):
        updater.validate_archive(base, output)
    with pytest.raises(ValueError, match='update_not_newer'):
        maintenance.apply_update(base, output, process_probe=lambda _: [])
    maintenance.apply_update(base, output, process_probe=lambda _: [], candidate_replacement=pair)
    assert all((base / name).read_bytes() == data for name, data in new.items())
    assert private.read_bytes() == b'private settings keep exactly'


@pytest.mark.parametrize('fault', ['omitted', 'wrong_old', 'wrong_new', 'list', 'target_status',
    'target_distribution', 'target_version', 'target_revision', 'conflicting_transition'])
def test_candidate_delta_rejects_unbound_or_ineligible_transition(builder, case, fault):
    pair = candidate(case)
    base, target, output = case[:3]
    kwargs = {'candidate_replacement': pair}
    if fault == 'omitted':
        kwargs.clear()
    elif fault == 'wrong_old':
        kwargs['candidate_replacement'] = ('0' * 64, pair[1])
    elif fault == 'wrong_new':
        kwargs['candidate_replacement'] = (pair[0], '0' * 64)
    elif fault == 'list':
        kwargs['candidate_replacement'] = list(pair)
    elif fault == 'conflicting_transition':
        kwargs['r54_release_transition'] = pair
    else:
        metadata = json.loads((target / maintenance.MANIFEST).read_bytes())
        if fault == 'target_status': metadata['status'] = 'player_test_release'
        if fault == 'target_distribution': metadata['distribution_ready'] = True
        if fault == 'target_version': metadata['update']['version'] = 'v0.1.8-r53-other-candidate'
        if fault == 'target_revision': metadata['update']['revision'] = 54
        save_manifest(target, metadata)
        kwargs['candidate_replacement'] = (pair[0], hashlib.sha256(
            (target / maintenance.MANIFEST).read_bytes()).hexdigest())
    with pytest.raises(ValueError):
        builder.build_patch_from_manifest(base / maintenance.MANIFEST, target, output, pair[0], **kwargs)
    assert not output.exists()
