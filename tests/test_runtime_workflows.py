"""Keep portable-runtime probes ahead of release sealing without running a build."""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
PROBES = {
    'test_packaged_runtime_ignores_external_python_environment',
    'test_packaged_native_crt_loads_from_python_directory',
    'test_native_entry_uses_bundled_runtime',
}
EXIT_CHECK = 'if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }'


def test_token_check_is_manual_read_only_and_never_publishes():
    text = (ROOT / '.github/workflows/release-token-check.yml').read_text(encoding='utf-8')
    assert '\non: workflow_dispatch\n' in text
    assert '\n  contents: read\n  actions: read\n' in text
    assert 'GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}' in text
    assert 'p.fetch_metadata(' in text and "str(exc) != 'accepted_source_changed'" in text
    assert 'tests/test_release_promotion.py tests/test_runtime_workflows.py' in text
    assert 'write' not in text and 'gh release' not in text and 'build_release.py' not in text


@pytest.mark.parametrize('name,folder,evidence,candidate_step', [
    ('ci.yml', 'ci', 'Publish Build Evidence', 'Build Portable Candidate'),
    ('release.yml', 'release', 'Publish Runtime Test Evidence', 'Promote Accepted CI Artifact'),
])
def test_workflow_runs_all_prepared_probes_and_retains_failure_evidence(name, folder, evidence, candidate_step):
    text = (ROOT / '.github/workflows' / name).read_text(encoding='utf-8')
    steps = {part.splitlines()[0].removeprefix('name: '): part
             for part in text.split('\n      - ') if part.startswith('name: ')}
    probes = steps['Bundled Runtime Checks']
    selected = re.findall(r'tests/test_player_candidate.py::(test_\w+)', text)
    assert len(selected) == 3 and set(selected) == PROBES
    assert probes.count('python -X utf8 -B -m pytest ') == 1
    assert f"$env:PCR_PLAYER_PACKAGE_ROOT = [IO.Path]::GetFullPath((Join-Path $env:GITHUB_WORKSPACE '.runtime/{folder}/build/player'))" in probes
    assert '$env:PCR_PLAYER_PACKAGE_ALIAS = $env:PCR_PLAYER_PACKAGE_ROOT' in probes
    assert f'--basetemp .runtime/{folder}/bundled-runtime-temp' in probes
    assert f'--junitxml .runtime/{folder}/bundled-runtime.xml' in probes
    assert "python-version: '3.13.16'" in text
    assert text.index('- name: ' + candidate_step) < text.index('- name: Bundled Runtime Checks')
    for step in steps.values():
        lines = [line.strip() for line in step.splitlines()]
        for index, line in enumerate(lines):
            if line.startswith(('python ', 'dotnet ', 'gh ')):
                assert lines[index + 1] == EXIT_CHECK, line
    upload = steps[evidence]
    assert 'if: always()' in upload and 'uses: actions/upload-artifact@v4' in upload
    assert 'include-hidden-files: true' in upload
    assert f'.runtime/{folder}/bundled-runtime.xml' in upload
    assert f'.runtime/{folder}/bundled-runtime-temp/' in upload
    assert not re.search(r'^\s+\.runtime/(?:ci|release)/\*', upload, re.MULTILINE)
    if name == 'ci.yml':
        assert 'retention-days: 30' in upload
        archives = [line.strip() for line in upload.splitlines() if line.strip().endswith('.zip')]
        assert len(archives) == 3 and set(archives) == {
            '.runtime/ci/build/Maa-PCR-*-win-x64.zip',
            '.runtime/ci/build/Maa-PCR-*-source.zip',
            '.runtime/ci/build/Maa-PCR-R*-to-R*.patch.zip',
        }
    assert '--base-manifest delivery/baselines/r51-package-manifest.json' in text


def test_release_promotes_only_the_exact_reviewed_ci_artifact_without_rebuilding():
    text = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
    steps = {part.splitlines()[0].removeprefix('name: '): part
             for part in text.split('\n      - ') if part.startswith('name: ')}
    assert 'scripts/build_release.py' not in text
    assert '- name: Build Portable Candidate' not in text
    assert '  actions: read\n' in text
    assert 'uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n' in text
    order = ('Require Reviewed Acceptance', 'Public Regression', 'GUI Update Contract Checks',
             'Fetch Accepted CI Metadata', 'Download Accepted CI Artifact',
             'Promote Accepted CI Artifact', 'Bundled Runtime Checks',
             'Seal Matching Assets', 'Create GitHub Release')
    positions = [text.index('- name: ' + name) for name in order]
    assert positions == sorted(positions)
    gate = steps['Require Reviewed Acceptance']
    assert 'id: promotion' in gate
    assert 'require_acceptance(d,d[\'revision\'],version=sys.argv[1])' in gate
    assert gate.index('require_acceptance(') < gate.index('scripts/promote_release_candidate.py request ')
    assert 'request --acceptance delivery/acceptance.json --tag $env:PCR_RELEASE_TAG --repository $env:GITHUB_REPOSITORY' in gate
    assert '--output .runtime/release/PROMOTION_REQUEST.json --github-output $env:GITHUB_OUTPUT' in gate
    metadata = steps['Fetch Accepted CI Metadata']
    assert 'GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}' in metadata
    assert 'scripts/promote_release_candidate.py fetch-metadata --acceptance delivery/acceptance.json --tag $env:PCR_RELEASE_TAG --repository $env:GITHUB_REPOSITORY --output-dir .runtime/release/metadata' in metadata
    assert 'gh api ' not in text
    assert 'PCR_ACCEPTED_RUN_ID' not in metadata and 'PCR_ACCEPTED_ARTIFACT_ID' not in metadata
    download = steps['Download Accepted CI Artifact']
    assert 'uses: actions/download-artifact@v4' in download
    assert 'artifact-ids: ${{ steps.promotion.outputs.artifact_id }}' in download
    assert 'run-id: ${{ steps.promotion.outputs.run_id }}' in download
    assert 'repository: ${{ github.repository }}' in download
    assert 'github-token: ${{ secrets.GITHUB_TOKEN }}' in download
    assert 'path: .runtime/release/accepted-ci/' in download
    assert 'merge-multiple: true' in download
    assert '\n          name:' not in download and '\n          pattern:' not in download
    promote = steps['Promote Accepted CI Artifact']
    assert 'scripts/promote_release_candidate.py promote --acceptance delivery/acceptance.json --tag $env:PCR_RELEASE_TAG --repository $env:GITHUB_REPOSITORY' in promote
    assert '--run-metadata .runtime/release/metadata/run.json --artifact-metadata .runtime/release/metadata/artifact.json' in promote
    assert '--artifact-root .runtime/release/accepted-ci/build --player .runtime/release/build/player' in promote
    assert '--acceptance delivery/acceptance.json --tag $env:PCR_RELEASE_TAG' in steps['Seal Matching Assets']
    assert '.runtime/release/assets/SOURCE_ALIGNMENT.json' in steps['Create GitHub Release']
    upload = steps['Publish Runtime Test Evidence']
    assert '.runtime/release/accepted-ci/' not in upload
    assert '.runtime/release/build/' not in upload
    for evidence in ('gui-checks.log', 'PROMOTION_REQUEST.json', 'metadata/run.json',
                     'metadata/artifact.json', 'promotion.log'):
        assert '.runtime/release/' + evidence in upload


def ci_crt_python():
    import textwrap
    text = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    step = text.split('      - name: Build Portable Candidate\n', 1)[1].split('\n      - name:', 1)[0]
    assert "'@ | python -X utf8 -B -\n          " + EXIT_CHECK in step
    assert '--vc-redist .runtime/ci/vc-redist-r51' in step
    return textwrap.dedent(step.split("          @'\n", 1)[1].split("          '@", 1)[0])


@pytest.mark.parametrize('tampered', [None, 'baseline', 'archive_manifest', 'dll'])
def test_ci_extracts_only_manifest_bound_crt_and_rejects_tampering(tmp_path, monkeypatch, tampered):
    import hashlib
    import json
    import sys
    import zipfile
    from scripts import build_release as build
    from test_player_patch_builder import manifest as player_manifest

    program = compile(ci_crt_python(), '.github/workflows/ci.yml', 'exec')
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.setitem(sys.modules, 'build_release', build)
    monkeypatch.chdir(tmp_path)
    payload = {'runtime/python/' + name: ('synthetic ' + name).encode() for name in build.CRT_FILES}
    manifest = json.dumps(player_manifest(payload, 51)).encode()
    monkeypatch.setattr(build, 'R51_MANIFEST_SHA256', hashlib.sha256(manifest).hexdigest())
    baseline = tmp_path / 'delivery/baselines/r51-package-manifest.json'
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(manifest + (b' ' if tampered == 'baseline' else b''))
    archive = tmp_path / 'public-r51-synthetic.zip'
    with zipfile.ZipFile(archive, 'w') as source:
        source.writestr('package-manifest.json', manifest + (b' ' if tampered == 'archive_manifest' else b''))
        for name, data in payload.items():
            source.writestr(name, data + (b'changed' if tampered == 'dll' else b''))
        source.writestr('unselected/extra.dll', b'must not extract')
    downloads = []
    monkeypatch.setattr(build, 'download', lambda record, cache: downloads.append((record, cache)) or archive)
    if tampered:
        with pytest.raises(ValueError, match='R51_crt_.*_mismatch'):
            exec(program, {})
        assert len(downloads) == (0 if tampered == 'baseline' else 1)
        return
    exec(program, {})
    target = tmp_path / '.runtime/ci/vc-redist-r51'
    assert {path.name: path.read_bytes() for path in target.iterdir()} == {
        name.removeprefix('runtime/python/'): data for name, data in payload.items()}
    assert len(downloads) == 1 and downloads[0][1] == Path('.runtime/downloads')
