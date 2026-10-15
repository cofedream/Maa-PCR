"""Curated presets must retain the observed game Activity, not the SDK launcher."""
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import pytest
from pcr_maa.daily import validate_app_target

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'gui/MFAAvalonia/MFAAvalonia/Pcr/channels.json'
ROWS = json.loads(CATALOG.read_text(encoding='utf-8'))
EVIDENCE = json.loads((ROOT / 'docs/channel-presets-evidence.json').read_text(encoding='utf-8'))


def test_only_evidenced_channels_are_exposed():
    assert [row['id'] for row in ROWS] == ['bilibili', 'xiaomi', 'tencent', 'jiuyou', 'huawei', 'vivo', 'mumu']
    assert len({row['package'] for row in ROWS}) == len(ROWS)
    assert {row['id'] for row in EVIDENCE['excluded']} == {'oppo', '4399'}
    assert not EVIDENCE['apk_installed_or_executed'] and not EVIDENCE['real_channel_tested']


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['id'])
def test_runtime_components_have_bounded_valid_syntax(row):
    validate_app_target(row['package'], row['activity'])
    assert row['source'].startswith('https://')


@pytest.mark.parametrize('row', ROWS[1:], ids=lambda row: row['id'])
def test_each_preset_matches_manifest_evidence_and_avoids_splash(row):
    actual = next(item for item in EVIDENCE['channels'] if item['id'] == row['id'])
    assert actual['source'] == row['source']
    assert actual['package'] == row['package']
    assert actual['game_activity'] == row['activity'] == 'com.bilibili.priconne.MainActivity'
    assert actual['game_activity'] != actual['launch_activity']
    assert actual['unity_game_activity_declared'] is True
    assert re.fullmatch('[a-f0-9]{64}', row['manifest_sha256'])
    assert actual['manifest_sha256'] == row['manifest_sha256']
    assert actual['version'] == row['version'] == '11.7.2'


def test_selector_replaces_manual_package_inputs():
    tree = ET.parse(ROOT / 'gui/MFAAvalonia/MFAAvalonia/Views/Pages/TaskQueueView.axaml')
    name = '{http://schemas.microsoft.com/winfx/2006/xaml}Name'
    elements = {element.get(name): element for element in tree.iter() if element.get(name)}
    assert 'PcrGamePackage' not in elements and 'PcrGameActivity' not in elements
    assert elements['PcrChannelSelector'].tag.endswith('ComboBox')
    assert 'SelectedChannel' in elements['PcrChannelSelector'].get('SelectedItem')
