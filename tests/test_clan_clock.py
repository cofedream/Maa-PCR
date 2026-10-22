"""The fixed upstream templates are local research inputs, never game fixtures."""
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from pcr_maa.clan_clock import (LOCAL_TEMPLATE_SHA256, MIN_IOU, TIMER_ROI, _digit_scores, _load_templates,
                                _normalize, _read_crop, read_clock_candidate, read_local_clock_candidate)


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / '.runtime/clan-agent-20261020/preopen-daily/kokkoro-source/source/assets/templates'
LOCAL = ROOT / 'assets/resource/image/guide-trial/clock'


@pytest.fixture
def templates():
    if not (TEMPLATES / '9.png').is_file():
        pytest.skip('fixed upstream templates are not distributed with the source')
    return TEMPLATES


def clock(text, templates):
    image = Image.new('RGB', (100, 32), 'white')
    x = 3
    for digit in text:
        if digit == ':':
            ImageDraw.Draw(image).rectangle((x, 10, x+1, 12), fill='black')
            ImageDraw.Draw(image).rectangle((x, 19, x+1, 21), fill='black')
            x += 8
        else:
            with Image.open(templates / f'{digit}.png') as glyph:
                image.paste(glyph.convert('RGB'), (x, 4))
                x += glyph.width + 4
    return image.crop((0, 0, x, 32))


def read_crop(image, templates):
    return read_clock_candidate(image, templates, roi=[0, 0, image.width, image.height])


@pytest.mark.parametrize('text,expected', [('1:30', 90), ('1:28', 88), ('1:17', 77), ('0:59', 59), ('0:01', 1)])
def test_exact_template_clock(templates, text, expected):
    result = read_crop(clock(text, templates), templates)
    assert result['remaining_seconds'] == expected, result
    assert result['unknown'] is False and result['ambiguous'] is False
    assert result['confidence'] >= MIN_IOU
    assert len(result['groups']) == 4
    assert result['source']['production_enabled'] is False


@pytest.mark.parametrize('text', ['0:00', '1:31', '1:59', '2:01', '0:60', '9:99'])
def test_out_of_range_is_unknown(templates, text):
    result = read_crop(clock(text, templates), templates)
    assert result['remaining_seconds'] is None and result['unknown'] is True
    assert result['reason'] == 'clock_out_of_range', result


@pytest.mark.parametrize('text', ['128', '11:28', '1::28'])
def test_wrong_column_count_is_unknown(templates, text):
    result = read_crop(clock(text, templates), templates)
    assert result['remaining_seconds'] is None
    assert result['reason'] == 'clock_split_failed'


def test_colon_must_have_two_separate_marks(templates):
    image = clock('1:28', templates)
    pixels = np.asarray(image).copy()
    columns = np.flatnonzero((pixels[:, :, 0] < 80).sum(axis=0) >= 2)
    groups = np.split(columns, np.flatnonzero(np.diff(columns) != 1)+1)
    ImageDraw.Draw(image).rectangle((int(groups[1][0]), 10, int(groups[1][-1]), 21), fill='black')
    result = read_crop(image, templates)
    assert result['reason'] == 'clock_colon_unknown' and result['unknown'] is True


def test_blank_and_blurred_clock_are_not_actionable(templates):
    blank = read_crop(Image.new('RGB', (70, 45), 'white'), templates)
    blurred = read_crop(clock('1:28', templates).filter(ImageFilter.GaussianBlur(3)), templates)
    assert blank['remaining_seconds'] is None
    assert blurred['remaining_seconds'] is None


def test_multi_candidate_is_rejected(templates):
    from pcr_maa.clan_clock import _load_templates
    image = clock('1:28', templates)
    normalized = _load_templates(templates)
    normalized[3] = normalized[2].copy()
    seconds, _, ambiguous, reason, _ = _read_crop(np.asarray(image), normalized)
    assert seconds is None and ambiguous is True and reason == 'clock_ambiguous'


def test_template_change_is_rejected_before_readback(templates, tmp_path):
    local = tmp_path / 'templates'
    shutil.copytree(templates, local)
    changed = local / '0.png'
    changed.write_bytes(changed.read_bytes() + b'changed')
    result = read_crop(clock('1:28', templates), local)
    assert result['remaining_seconds'] is None
    assert result['reason'] == 'template_hash_mismatch:0'


def test_missing_templates_are_unknown(tmp_path):
    result = read_clock_candidate(Image.new('RGB', (1280, 720)), tmp_path)
    assert result['remaining_seconds'] is None and result['reason'] == 'clock_templates_unavailable'


def test_default_requires_original_normalized_hud_size(templates):
    result = read_clock_candidate(clock('1:28', templates), templates)
    assert result['reason'] == 'clock_hud_size_unknown'


@pytest.mark.parametrize('roi', [[-1, 0, 5, 5], [0, 0, 0, 5], [0, 0, 500, 5], [0, 0, True, 5]])
def test_invalid_roi_is_unknown(templates, roi):
    result = read_clock_candidate(Image.new('RGB', (70, 45)), templates, roi=roi)
    assert result['reason'] == 'clock_roi_invalid'


def test_native_bgr_and_pil_rgb_are_equivalent(templates):
    crop = clock('1:28', templates)
    pil = read_crop(crop, templates)
    array = read_clock_candidate(np.asarray(crop)[:, :, ::-1], templates,
                                 roi=[0, 0, crop.width, crop.height])
    assert array['remaining_seconds'] == pil['remaining_seconds'] == 88
    assert array['source']['crop_rgb_sha256'] == pil['source']['crop_rgb_sha256']


def test_default_roi_retains_existing_gtbattle_region(templates):
    image = Image.new('RGB', (1280, 720), 'white')
    crop = clock('1:28', templates)
    assert crop.width <= TIMER_ROI[2] and crop.height <= TIMER_ROI[3]
    image.paste(crop, TIMER_ROI[:2])
    result = read_clock_candidate(image, templates)
    assert result['remaining_seconds'] == 88
    assert result['source']['roi_source'] == 'existing_GTBattleTimer'


def test_normalizer_preserves_aspect_and_centers_foreground():
    image = Image.new('RGB', (20, 30), 'white')
    ImageDraw.Draw(image).rectangle((3, 4, 7, 23), fill='black')
    normalized = _normalize(np.asarray(image))
    yy, xx = np.where(normalized)
    assert normalized.shape == (24, 24)
    assert (int(xx.min()), int(xx.max()), int(yy.min()), int(yy.max())) == (9, 13, 2, 21)


def local_clock(text):
    image = Image.new('RGB', (110, 34), 'white')
    x = 3
    for digit in text:
        if digit == ':':
            ImageDraw.Draw(image).rectangle((x, 10, x+1, 12), fill='black')
            ImageDraw.Draw(image).rectangle((x, 19, x+1, 21), fill='black')
            x += 8
        else:
            with Image.open(LOCAL / f'{digit}.png') as template:
                pixels = np.asarray(template)
                yy, xx = np.where(pixels == 0)
                glyph = template.crop((int(xx.min()), int(yy.min()), int(xx.max()+1), int(yy.max()+1)))
                image.paste(glyph.convert('RGB'), (x, 4))
                x += glyph.width + 4
    return image.crop((0, 0, x, 34))


def local_read_crop(image, templates=None):
    if templates is None:
        templates = _load_templates(LOCAL, LOCAL_TEMPLATE_SHA256, normalized=True)
    return _read_crop(np.asarray(image.convert('RGB')), templates, align=True)


@pytest.mark.parametrize('second', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 19, 26, 37, 48, 59, 60, 77, 88, 90])
def test_local_complete_digits_and_seconds(second):
    image = local_clock(f'{second//60}:{second%60:02d}')
    seconds, confidence, ambiguous, reason, detail = local_read_crop(image)
    assert detail['raw_text'] == f'{second//60}:{second%60:02d}' and confidence >= MIN_IOU
    assert seconds == second and not ambiguous and reason is None


def test_thin_binary_seven_tip_is_preserved_without_lowering_iou_guards():
    seconds, confidence, ambiguous, reason, detail = local_read_crop(local_clock('1:17'))
    assert seconds == 77 and confidence >= MIN_IOU and not ambiguous and reason is None
    assert detail['raw_text'] == '1:17'


@pytest.mark.parametrize('text', ['0:00', '1:31', '1:59', '2:01', '0:60', '9:99'])
def test_local_out_of_range_never_infers_seconds(text):
    seconds, _, _, reason, _ = local_read_crop(local_clock(text))
    assert seconds is None and reason == 'clock_out_of_range'


def test_local_api_uses_fixed_hud_profile_and_bgr_order():
    full = Image.new('RGB', (1280, 720), (224, 240, 255))
    full.paste(local_clock('1:28'), TIMER_ROI[:2])
    pil = read_local_clock_candidate(full, LOCAL)
    bgr = read_local_clock_candidate(np.asarray(full)[:, :, ::-1], LOCAL)
    assert pil['remaining_seconds'] == bgr['remaining_seconds'] == 88
    assert pil['source']['crop_rgb_sha256'] == bgr['source']['crop_rgb_sha256']
    assert pil['source']['template_profile'] == 'local_saved_hud_v1'
    assert pil['source']['production_enabled'] is pil['source']['device_calibrated'] is False
    assert read_local_clock_candidate(local_clock('1:28'), LOCAL)['reason'] == 'clock_hud_size_unknown'


def test_local_template_tampering_is_unknown(tmp_path):
    copied = tmp_path / 'templates'
    shutil.copytree(LOCAL, copied)
    changed = copied / '9.png'
    changed.write_bytes(changed.read_bytes() + b'changed')
    result = read_local_clock_candidate(Image.new('RGB', (1280, 720)), copied)
    assert result['remaining_seconds'] is None and result['reason'] == 'template_hash_mismatch:9'


def test_local_colon_and_extra_digits_are_rejected():
    for text in ('128', '11:28', '1::28', '1:288'):
        assert local_read_crop(local_clock(text))[0] is None
    image = local_clock('1:28')
    pixels = np.asarray(image)
    columns = np.flatnonzero((pixels[:, :, 0] == 0).sum(0) >= 2)
    groups = np.split(columns, np.flatnonzero(np.diff(columns) != 1)+1)
    ImageDraw.Draw(image).rectangle((int(groups[1][0]), 10, int(groups[1][-1]), 21), fill='black')
    assert local_read_crop(image)[3] == 'clock_colon_unknown'


def test_local_low_confidence_and_ambiguity_are_unknown():
    blurred = local_clock('1:28').filter(ImageFilter.GaussianBlur(3))
    assert local_read_crop(blurred)[0] is None
    templates = _load_templates(LOCAL, LOCAL_TEMPLATE_SHA256, normalized=True)
    templates[3] = templates[2].copy()
    seconds, _, ambiguous, reason, _ = local_read_crop(local_clock('1:28'), templates)
    assert seconds is None and ambiguous is True and reason == 'clock_ambiguous'


@pytest.mark.parametrize('text,expected,digit', [('1:10', 70, 0), ('1:16', 76, 6)])
def test_perfect_local_zero_and_six_are_distinguished_by_margin(text, expected, digit):
    seconds, _, ambiguous, reason, detail = local_read_crop(local_clock(text))
    last = detail['digits'][-1]
    assert seconds == expected and not ambiguous and reason is None
    assert last['digit'] == digit and last['score'] == 1.0
    assert .8 <= last['ranking'][1]['score'] <= .81 and last['margin'] >= .12
    assert len(last['candidates']) == 2


def test_nearly_identical_local_digit_is_unknown():
    templates = _load_templates(LOCAL, LOCAL_TEMPLATE_SHA256, normalized=True)
    templates[8] = templates[0].copy()
    yy, xx = np.where(templates[8])
    templates[8][yy[0], xx[0]] = False
    seconds, confidence, ambiguous, reason, _ = local_read_crop(local_clock('1:10'), templates)
    assert seconds is None and confidence >= MIN_IOU and ambiguous and reason == 'clock_ambiguous'


def test_local_one_pixel_alignment_is_bounded_and_keeps_duplicate_ambiguity():
    templates = _load_templates(LOCAL, LOCAL_TEMPLATE_SHA256, normalized=True)
    shifted = np.zeros((24, 24), dtype=bool)
    shifted[1:, 1:] = templates[1][:-1, :-1]
    plain = _digit_scores(shifted, templates, False)
    aligned = _digit_scores(shifted, templates, True)
    assert aligned[0][0] == 1.0 and aligned[0][1] == 1
    assert plain[0][0] < aligned[0][0]
    templates[3] = templates[1].copy()
    ambiguous = _digit_scores(shifted, templates, True)
    assert ambiguous[0][0] == ambiguous[1][0] == 1.0


def test_local_alignment_zero_fills_instead_of_wrapping_edge_pixels():
    corner = np.zeros((24, 24), dtype=bool)
    corner[0, 0] = True
    opposite = np.zeros_like(corner)
    opposite[-1, -1] = True
    assert _digit_scores(corner, [opposite], True)[0][0] == 0.0


@pytest.mark.parametrize('factor', [.5, .65])
def test_dark_pill_background_is_not_artificially_repainted_white(factor):
    canvas = Image.new('RGB', (70, 45), 'black')
    ImageDraw.Draw(canvas).rectangle((0, 5, 64, 35), fill='white')
    canvas.paste(local_clock('1:28').crop((0, 0, local_clock('1:28').width, 27)), (2, 7))
    dark = Image.fromarray((np.asarray(canvas).astype(np.float64)*factor).astype(np.uint8))
    seconds, confidence, ambiguous, reason, _ = local_read_crop(dark)
    assert seconds == 88 and confidence >= MIN_IOU and not ambiguous and reason is None


def test_separate_watermark_band_does_not_pollute_clock_otsu():
    canvas = Image.new('RGB', (110, 70), 'white')
    canvas.paste(local_clock('0:52'), (4, 3))
    draw = ImageDraw.Draw(canvas)
    for left in range(3,100,10):
        draw.rectangle((left,51,left+5,63),fill='black')
    seconds, confidence, ambiguous, reason, _ = local_read_crop(canvas)
    assert seconds == 52 and confidence >= MIN_IOU and not ambiguous and reason is None


def test_two_clock_ink_bands_are_ambiguous_before_digit_scoring():
    canvas = Image.new('RGB', (110, 82), 'white')
    canvas.paste(local_clock('0:52'), (4, 3))
    canvas.paste(local_clock('1:28'), (4, 45))
    seconds, _, _, reason, _ = local_read_crop(canvas)
    assert seconds is None and reason == 'clock_band_ambiguous'


def test_same_band_extra_glyph_cannot_be_ignored_as_a_watermark():
    canvas = Image.new('RGB', (110,70), 'white')
    canvas.paste(local_clock('0:52'), (4,3))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((90,10,95,24),fill='black')
    draw.rectangle((5,52,80,61),fill='black')
    assert local_read_crop(canvas)[0] is None


@pytest.mark.private_replay
@pytest.mark.parametrize('position,expected', [(60,52),(90,26),(110,18)])
def test_existing_video_sparse_smoke_after_ink_band_separation(position, expected):
    path=ROOT/f'.runtime/guide-source-flow-20261019/july88-referer/frame-{position:06.2f}.png'
    if not path.is_file():
        pytest.skip('private video stills are not distributed')
    with Image.open(path) as image:
        result=read_local_clock_candidate(image,LOCAL)
    assert result['remaining_seconds'] in (None,expected),result
    assert result['reason'] != 'clock_border_glyph_unknown',result


@pytest.mark.parametrize('fill', ['black', 'white'])
def test_local_blank_never_produces_a_time(fill):
    assert local_read_crop(Image.new('RGB', (70, 45), fill))[0] is None


def test_pill_border_is_removed_but_connected_extra_glyph_is_not_hidden():
    canvas = Image.new('RGB', (110, 45), 'white')
    canvas.paste(local_clock('1:28'), (4, 5))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 109, 44), outline='black')
    assert local_read_crop(canvas)[0] == 88
    with Image.open(LOCAL / '1.png') as glyph:
        canvas.paste(glyph.convert('RGB'), (80, 9))
    ImageDraw.Draw(canvas).line((94, 0, 94, 15), fill='black', width=2)
    assert local_read_crop(canvas)[0] is None


def test_extra_glyph_joined_to_right_edge_cannot_be_removed_as_pill_curve():
    canvas = Image.new('RGB', (110, 45), 'white')
    canvas.paste(local_clock('1:28'), (4, 5))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, 109, 44), outline='black')
    with Image.open(LOCAL / '1.png') as glyph:
        extra = np.asarray(glyph)
        for y in range(24):
            xx = np.flatnonzero(extra[y] == 0)
            if len(xx):
                draw.line((80+int(xx.min()), y+9, 109, y+9), fill='black')
    assert local_read_crop(canvas)[0] is None


@pytest.mark.parametrize('side', ['left', 'right', 'top', 'bottom'])
def test_cropped_glyph_is_not_discarded_as_background(side):
    image = local_clock('11:28' if side == 'left' else '1:288')
    pixels = np.asarray(image)
    yy, xx = np.where(pixels[:, :, 0] == 0)
    boxes = {'left': (int(xx.min()+2), 0, image.width, image.height),
             'right': (0, 0, int(xx.max()-2), image.height),
             'top': (0, int(yy.min()+2), image.width, image.height),
             'bottom': (0, 0, image.width, int(yy.max()-2))}
    assert local_read_crop(image.crop(boxes[side]))[0] is None


@pytest.mark.private_replay
def test_original_three_private_frames_are_independent_holdouts():
    import json
    inventory = ROOT / '.runtime/clan-agent-20261020/clock-adapt/inventory/INVENTORY.json'
    if not inventory.is_file():
        pytest.skip('private native frames are not distributed')
    samples = json.loads(inventory.read_text(encoding='utf-8'))['original_three_holdout']
    for sample in samples:
        if not Path(sample['path']).is_file():
            pytest.skip('private native frames are not distributed')
        with Image.open(sample['path']) as image:
            result = read_local_clock_candidate(image, LOCAL)
        assert result['remaining_seconds'] == sample['expected_remaining_seconds'], result
        assert result['confidence'] >= MIN_IOU and not result['ambiguous']


@pytest.mark.private_replay
def test_uniform_brightness_changes_of_training_frames_do_not_infer_wrong_times():
    import json
    provenance = ROOT / '.runtime/clan-agent-20261020/clock-adapt/implementation/TEMPLATE_PROVENANCE.json'
    if not provenance.is_file():
        pytest.skip('private native frames are not distributed')
    rows = json.loads(provenance.read_text(encoding='utf-8'))['templates']
    sources = {row['source_image_sha256']: row for row in rows}
    for row in sources.values():
        if not Path(row['source_path']).is_file():
            pytest.skip('private native frames are not distributed')
        with Image.open(row['source_path']) as image:
            pixels = np.asarray(image.convert('RGB'))
        minute, second = row['text'].split(':')
        expected = int(minute)*60+int(second)
        for factor in (.5, .65):
            changed = Image.fromarray((pixels.astype(np.float64)*factor).astype(np.uint8))
            result = read_local_clock_candidate(changed, LOCAL)
            assert result['remaining_seconds'] in (None, expected), result
            if row['text'] == '1:04':
                assert result['remaining_seconds'] == expected, result
