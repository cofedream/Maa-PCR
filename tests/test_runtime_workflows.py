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


@pytest.mark.parametrize('name,folder,evidence', [
    ('ci.yml', 'ci', 'Publish Build Evidence'),
    ('release.yml', 'release', 'Publish Runtime Test Evidence'),
])
def test_workflow_runs_all_prepared_probes_and_retains_failure_evidence(name, folder, evidence):
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
    assert text.index('- name: Build Portable Candidate') < text.index('- name: Bundled Runtime Checks')
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
    if name == 'release.yml':
        assert text.index('- name: Require Reviewed Acceptance') < text.index('- name: Build Portable Candidate')
        assert text.index('- name: Bundled Runtime Checks') < text.index('- name: Seal Matching Assets')
        assert text.index('- name: Seal Matching Assets') < text.index('- name: Create GitHub Release')
        assert 'require_acceptance(d,d[\'revision\'],version=sys.argv[1])' in steps['Require Reviewed Acceptance']
        assert '--revision $acceptance.revision --version $env:PCR_RELEASE_TAG' in steps['Build Portable Candidate']
        assert '--acceptance delivery/acceptance.json --tag $env:PCR_RELEASE_TAG' in steps['Seal Matching Assets']
