"""Resolve the preserved guide portraits against the entire verified icon catalog."""
import base64
import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import cv2
import numpy as np
from .labyrinth.character_recognition import load_character_pack

SOURCE_SHA256 = '2ded6c8e580975fd1f7af315bce861aaa222e995be2cf7838eb6821ed8c740b5'
SOURCE_74_SHA256 = 'c67f76df9c787027ba06c9f446b084fa519eaf774b59ffe2508f6bc90bc5bfc6'
SOURCE_75_SHA256 = '4d5b349e0c064707a36cf7a16a0ee66b4d55c1afe9d76ac2292bc80b8509059d'
SOURCE_82_SHA256 = '8e934e10e418ed9e9fb9ac7220d13b26811023eb2910dc056fcfcef8067674aa'
SOURCE_HASHES = {'红焰深域 7-3': SOURCE_SHA256, '红焰深域 7-4': SOURCE_74_SHA256,
                 '红焰深域 7-5': SOURCE_75_SHA256, '红焰深域 8-2': SOURCE_82_SHA256,
                 '红焰深域 8-3': 'ab4c8ee7030a03b056219b87284c69690136b5c356e2e954b755843fc5e7c5ac',
                 '红焰深域 8-4': 'ad2f6046d4e4d8d99706ed49e6045e7502d3209db3a263621453f36a39cd0a66',
                 '红焰深域 8-5': '338466d43f78bcdc394ce70e1a8d589bdbc0be114008151052e75eaeff500da5',
                 '红焰深域 8-6': '983a27faa7edd3ef167aca5709cc4dd555dfff66d951dfeeb4167558d0807842',
                 '红焰深域 8-8': '3a7bdf2e2ae0f1b841b428fa60d58d9c991dcfcbb43e7815f90baa0b9e70fbc3'}
SOURCE_URL = 'https://docs.qq.com/sheet/DQnFpRmNYWVVuWWFk?tab=m70z7u'
SOURCE_PATH = Path(__file__).resolve().parents[2] / 'assets/guide/fire73-source-row.png'


def resolve_portrait(query, entries, images, *, require_verified=True):
    # Rank distinct characters, not two icon variants belonging to the same person.
    ranked = {}
    for entry, rgb in zip(entries, images):
        score = max(cv2.minMaxLoc(cv2.matchTemplate(cv2.resize(rgb[:, :, ::-1], (size, size)),
            query, cv2.TM_CCOEFF_NORMED))[1] for size in (48, 49, 50, 51, 52))
        uid = int(entry['character_id']) * 100 + 1
        if uid not in ranked or score > ranked[uid]['Score']:
            ranked[uid] = dict(UnitId=uid, Name=entry['name'], Attribute=entry['attribute'],
                IconUnitId=int(entry['unit_icon_id']), Score=score)
    ordered = sorted(ranked.values(), key=lambda row: row['Score'], reverse=True)
    if len(ordered) < 2:
        raise ValueError('guide_source_catalog_incomplete')
    best = ordered[0]
    best['Margin'] = best['Score'] - ordered[1]['Score']
    if require_verified and (best['Score'] < .82 or best['Margin'] < .08):
        raise ValueError('guide_source_portrait_unknown_or_ambiguous')
    return best


def read_source(stage='红焰深域 7-3', *, allow_uncertain=False):
    if stage not in SOURCE_HASHES:
        raise ValueError('guide_source_stage_unsupported')
    number = stage.split()[-1].replace('-', '')
    path = SOURCE_PATH if stage == '红焰深域 7-3' else SOURCE_PATH.with_name(f'fire{number}-source-row.png')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_HASHES[stage]:
        raise ValueError('guide_source_image_changed')
    evidence = deepcopy(_resolve_source(raw, stage))
    if not allow_uncertain and any(m['Score'] < .82 or m['Margin'] < .08 for m in evidence['Members']):
        raise ValueError('guide_source_portrait_unknown_or_ambiguous')
    return evidence


@lru_cache(maxsize=6)
def _resolve_source(raw, stage):
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    area8 = stage.startswith('红焰深域 8-')
    if frame is None or frame.shape != (66, 344 if area8 else 371, 3):
        raise ValueError('guide_source_layout_unknown')
    entries, images, _ = load_character_pack()
    # shortcut: preserved source rows share this layout; other layouts need image calibration.
    members = [resolve_portrait(frame[9:34, x+10:x+40] if area8
                               else frame[9:38, x+7:x+44], entries, images, require_verified=False) for x in (50,100,150,200,250)]
    if len({m['UnitId'] for m in members}) != 5 or any(m['Attribute'] != 'FIRE' for m in members):
        raise ValueError('guide_source_identity_or_attribute_conflict')
    evidence = dict(SourceUrl=SOURCE_URL, Server='国服 B 站', Stage=stage, ImageSha256=SOURCE_HASHES[stage],
        ImageBase64=base64.b64encode(raw).decode('ascii'), Members=members)
    if stage == '红焰深域 8-8':
        evidence['ImportedSource'] = {'OriginalSha256': 'e0b31de83020c03ea36b310004b75ee99368849ed69cbb33c3b78dcc110f6119', 'Locator': '墨夕公开表格火6~8图，8-8第一队; crop=885,402,385,74; layout=fire8-344x66', 'RetrievedOn': '2026-10-19'}
    return evidence


def require_source_team(plan):
    evidence = read_source(plan.get('stage', '红焰深域 7-3'), allow_uncertain=plan.get('version') == 3)
    if (plan.get('source_image_sha256') != evidence['ImageSha256']
            or plan.get('unit_ids') != [m['UnitId'] for m in evidence['Members']]):
        raise ValueError('guide_source_plan_mismatch')
    confirmations = plan.get('source_confirmations', [])
    expected = []
    for member in evidence['Members']:
        if 'ImportedSource' not in evidence and member['Score'] >= .82 and member['Margin'] >= .08:
            continue
        binding = '|'.join(str(v) for v in (evidence['SourceUrl'], evidence['Server'], evidence['Stage'],
            evidence['ImageSha256'], member['UnitId'], member['IconUnitId'], plan.get('source_material_sha256','')))
        if 'ImportedSource' in evidence:
            binding += '|' + json.dumps(evidence['ImportedSource'],ensure_ascii=False,separators=(',',':'))
        expected.append(dict(unit_id=member['UnitId'], icon_unit_id=member['IconUnitId'], binding=hashlib.sha256(binding.encode()).hexdigest()))
    if confirmations != expected:
        raise ValueError('guide_source_manual_confirmation_required')
    if expected:
        evidence['ManualConfirmations'] = deepcopy(expected)
    return evidence
