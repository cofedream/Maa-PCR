"""Exact benign exceptions must not hide new credentials in the same files."""
from pathlib import Path
import ast
import json
import os
import re
import secrets
import string
import subprocess
import tomllib

import pytest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.github/gitleaks.toml'
BENIGN_PATHS = (
    'tests/test_player_lag_r32.py',
    'vendor/autopcr_sync/SOURCE_MANIFEST.json',
    'vendor/autopcr_sync/autopcr_sync/core/readonly.py',
)


@pytest.fixture
def benign_values():
    tree = ast.parse((ROOT / BENIGN_PATHS[0]).read_text(encoding='utf-8'))
    baseline = [ast.literal_eval(node.value)
                for node in ast.walk(tree) if isinstance(node, ast.Assign)
                and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == 'baseline' and isinstance(node.value, ast.Dict)]
    assert len(baseline) == 1
    baseline = baseline[0]
    assert baseline and all(isinstance(path, str) and path.endswith('.json')
                            and isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value)
                            for path, value in baseline.items())
    manifest = json.loads((ROOT / BENIGN_PATHS[1]).read_text(encoding='utf-8'))
    source = [row['source_sha256']['autopcr/core/apiclient.py']
              for row in manifest['files']
              if isinstance(row.get('source_sha256'), dict)
              and 'autopcr/core/apiclient.py' in row['source_sha256']]
    assert len(source) == 1
    tree = ast.parse((ROOT / BENIGN_PATHS[2]).read_text(encoding='utf-8'))
    protocol = [ast.literal_eval(value)
                for node in ast.walk(tree) if isinstance(node, ast.Dict)
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and key.value == 'RES-KEY']
    assert len(protocol) == 1
    return dict(zip(BENIGN_PATHS, (baseline['schedule_onekey.json'], source[0], protocol[0])))


def test_only_exact_rule_specific_value_and_path_exceptions(benign_values):
    config = tomllib.loads(CONFIG.read_text(encoding='utf-8'))
    assert set(config) == {'title', 'extend', 'rules'}
    assert config['extend'] == {'useDefault': True}
    assert len(config['rules']) == 1
    rule = config['rules'][0]
    assert set(rule) == {'id', 'allowlists'}
    assert rule['id'] == 'generic-api-key'
    assert len(rule['allowlists']) == len(BENIGN_PATHS)
    for exception, (path, value) in zip(rule['allowlists'], benign_values.items()):
        assert set(exception) == {'description', 'condition', 'regexTarget', 'paths', 'regexes'}
        assert exception['condition'] == 'AND'
        assert exception['regexTarget'] == 'secret'
        assert exception['paths'] == ['^' + re.escape(path).replace('/', r'[/\\]') + '$']
        assert exception['regexes'] == ['^' + re.escape(value) + '$']
    workflow = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert 'GITLEAKS_CONFIG: .github/gitleaks.toml' in workflow
    assert 'GITLEAKS_VERSION: 8.24.3' in workflow


@pytest.mark.parametrize('path', BENIGN_PATHS)
@pytest.mark.parametrize('case', ('known', 'different_value', 'different_path', 'provider_token'))
def test_actual_scanner_requires_both_exact_value_and_path(tmp_path, benign_values, path, case):
    executable = os.environ.get('PCR_GITLEAKS_EXE')
    if not executable:
        pytest.skip('Requires the explicitly prepared pinned Gitleaks binary')
    version = subprocess.run([executable, 'version'], capture_output=True, text=True,
                             encoding='utf-8', timeout=15)
    assert version.returncode == 0 and version.stdout.strip() == '8.24.3'
    value = benign_values[path]
    if case == 'different_value':
        value = ''.join(secrets.SystemRandom().sample(string.ascii_letters + string.digits, 40))
    elif case == 'provider_token':
        value = 'ghp_' + ''.join(secrets.SystemRandom().sample(string.ascii_letters + string.digits, 36))
    relative = 'unrelated/fixture.txt' if case == 'different_path' else path
    workspace = tmp_path / 'tree'
    sample = workspace / relative
    sample.parent.mkdir(parents=True)
    content = ((ROOT / path).read_text(encoding='utf-8') if case == 'known'
               else 'token = ' + json.dumps(value) + '\n')
    sample.write_text(content, encoding='utf-8')
    report = tmp_path / 'findings.json'
    result = subprocess.run([executable, 'dir', '.', '--config', str(CONFIG),
                             '--redact', '--no-banner', '--log-level', 'error',
                             '--ignore-gitleaks-allow', '--report-format', 'json',
                             '--report-path', str(report)], cwd=workspace,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    findings = json.loads(report.read_text(encoding='utf-8'))
    assert all(finding['File'] == relative for finding in findings)
    if case == 'known':
        assert result.returncode == 0 and findings == [], result.stderr[-2000:]
    elif case == 'provider_token':
        assert result.returncode == 1, result.stderr[-2000:]
        assert any(finding['RuleID'] == 'github-pat' for finding in findings)
    else:
        assert result.returncode == 1 and len(findings) == 1, result.stderr[-2000:]
        assert findings[0]['RuleID'] == 'generic-api-key'
