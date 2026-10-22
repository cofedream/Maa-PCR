"""Offline clock candidates; this module does not authorize battle inputs.

Method reference: wbero/kokkoro-clan-battle-assistant, commit
0abfb47c79610f010bfc49a260b8a4a6989ec322, recognition/{ClockRecognizer,
DigitNormalizer,StructuralMatcher}.kt. Independently implemented from the
documented pixel operations. The author's templates remain an explicit local
research input; neither those images nor the author's ROI are packaged here.
"""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image


SOURCE_COMMIT = '0abfb47c79610f010bfc49a260b8a4a6989ec322'
TIMER_ROI = (1060, 12, 70, 45)  # Existing GTBattleTimer, not a clan calibration.
MIN_IOU = .8
MIN_MARGIN = .12
TEMPLATE_SHA256 = (
    '6e685da2509b8d83999a44699c1c1dd99002bc8d32b2f2938ae6e9198d83a186',
    '528d11b9ce4705502f90ce8aadf0cf44bd40db5943ed6f7db5231f30e7b000eb',
    'be2809729d161d686212b0c3891f78fdac910c4776efa4b689af6919c8a92828',
    '281adf433d9c8078d489d1ba17a27b1e20d184c9bc1b1ef524b25cb356b9d449',
    '345e6da74894d06e6a764ad053f52bc8952c58b6defa93e479ead4a4375f026a',
    'f2608f97b4aa8ad7ddb629ede7e80dc36236e7d40316e9561b383296b2930234',
    '918d95e00ebd24e10a0d560ad456ba3a97dc2a765d2a49e8903a129a9d52e67b',
    'c82d0b6f117eaf012ef3961bfa0e6e8f555314a54c29c8fe431384375eef346d',
    'd6d6194a1dbf4736b3a212fdfdf468f30dabbbce3578eb2902b031b62a834a6e',
    '962cb21db2292f0ae8dd619d383994a8ad52d54d31e066a02abfee5b7a2c784a',
)
LOCAL_TEMPLATE_SHA256 = (
    'c88ca91ba18d40213ab12183507bfcf9098893e66a13e9d87f0e9f54b217f6e3',
    '7d58cdd1c0421bc0efee1cf03de23f004b6b1596c479c9322501370fa5fa1b09',
    'a753d71ce8b40bfd1f507a27c097a42e87d2a0d038f306a4328a88dc146fffc1',
    'b07df18b94ee16a42380ac71aea23e52dbaa1f39fe2f67facd974a789687619f',
    'c920ba06a68c87d9728e9b215061da41fa52920fc6a4961562038dc64c9696c3',
    '168fbbc95e8e2d8fe7238a55a861d4c3b0d7a67b7b346b47b8644f91d8291a01',
    '88a61c55a09567c017ce9d84b168807962a33b0efbe745145193ece8ae5b0952',
    '982b7fef4ccf84eba8ed27cd6f11511c99466a27e21404fceda8db616dac0fa7',
    '80b7b62f07ee39d07fcd78685451defccda1b955aec04a9c5342b6b5d90ac549',
    'a18ac9049bedeb801bfba513b09752b8e575b028499072dc66d9ac86b0b80b43',
)


def _runs(values):
    edges = np.flatnonzero(np.diff(np.pad(np.asarray(values, dtype=np.int8), (1, 1))))
    return [(int(left), int(right)) for left, right in zip(edges[::2], edges[1::2])]


def _binarize(rgb):
    gray = ((rgb[..., 0].astype(np.int32) * 299 + rgb[..., 1].astype(np.int32) * 587
             + rgb[..., 2].astype(np.int32) * 114 + 500) // 1000).ravel()
    histogram = np.bincount(gray, minlength=256)
    count = histogram.cumsum()
    total = int(count[-1])
    sums = (histogram * np.arange(256)).cumsum()
    valid = (count > 0) & (count < total)
    if not np.any(valid):
        return np.zeros(rgb.shape[:2], dtype=bool)
    variance = np.full(256, -1.0)
    variance[valid] = ((sums[-1] * count[valid] - sums[valid] * total).astype(float) ** 2
                       / (count[valid] * (total - count[valid])))
    return gray.reshape(rgb.shape[:2]) <= int(variance.argmax())


def _normalize(rgb):
    return _normalize_mask(_binarize(rgb))


def _normalize_mask(mask):
    yy, xx = np.where(mask)
    if not len(xx):
        return np.zeros((24, 24), dtype=bool)
    glyph = mask[yy.min():yy.max()+1, xx.min():xx.max()+1]
    scale = min(20 / glyph.shape[1], 20 / glyph.shape[0])
    width, height = [max(1, min(20, int(value * scale + .5))) for value in glyph.shape[::-1]]
    normalized = glyph[np.arange(height) * glyph.shape[0] // height][:, np.arange(width) * glyph.shape[1] // width]
    result = np.zeros((24, 24), dtype=bool)
    x, y = (24 - width) // 2, (24 - height) // 2
    result[y:y+height, x:x+width] = normalized
    return result


def _clock_foreground(crop):
    luminance = crop.astype(np.int32).sum(axis=2)
    dark = luminance < float(luminance.mean()) - 80
    removed = np.zeros_like(dark)
    height, width = dark.shape
    boundary = [(int(y), int(x)) for y, x in zip(*np.where(dark))
                if y in (0, height-1) or x in (0, width-1)]
    components = []
    for start in boundary:
        if removed[start]:
            continue
        pending, edges, rows = [start], set(), []
        while pending:
            y, x = pending.pop()
            if removed[y, x]:
                continue
            removed[y, x] = True
            rows.append(y)
            if y == 0:
                edges.add('top')
            if y == height-1:
                edges.add('bottom')
            if x == 0:
                edges.add('left')
            if x == width-1:
                edges.add('right')
            for ny, nx in ((y-1, x), (y+1, x), (y, x-1), (y, x+1)):
                if 0 <= ny < height and 0 <= nx < width and dark[ny, nx] and not removed[ny, nx]:
                    pending.append((ny, nx))
        components.append((edges, min(rows), max(rows)))
    foreground = dark & ~removed
    padded = np.pad(foreground, 1)
    neighbors = np.zeros_like(foreground)
    for dy in range(3):
        for dx in range(3):
            if dy != 1 or dx != 1:
                neighbors |= padded[dy:dy+height, dx:dx+width]
    foreground &= neighbors
    bands = _runs(foreground.any(axis=1))
    window_top, window_bottom = 0, height
    if len(bands) > 1:
        candidates = [index for index, (top, bottom) in enumerate(bands)
                      if _known_colon(foreground[top:bottom], _clock_groups(foreground[top:bottom]), height)]
        if len(candidates) != 1:
            raise ValueError('clock_band_ambiguous' if candidates else 'clock_band_unknown')
        index = candidates[0]
        top, bottom = bands[index]
        window_top = (bands[index-1][1] + top)//2 if index else 0
        window_bottom = (bottom + bands[index+1][0])//2 if index+1 < len(bands) else height
        glyph_rows = np.arange(top, bottom)
    else:
        glyph_rows = np.flatnonzero(foreground.any(axis=1))
    if len(glyph_rows):
        left_edges, right_edges = [], []
        for row in removed[glyph_rows[0]:glyph_rows[-1]+1]:
            runs = _runs(row)
            if any(left != 0 and right != width for left, right in runs):
                raise ValueError('clock_border_glyph_unknown')
            left_edges.append(next((right for left, right in runs if left == 0), 0))
            right_edges.append(next((left for left, right in runs if right == width), width))
        # A convex white pill cannot have a glyph-shaped dent from either edge.
        left, right = np.asarray(left_edges), np.asarray(right_edges)
        if (np.any(left > np.maximum(np.minimum.accumulate(left), np.minimum.accumulate(left[::-1])[::-1]))
                or np.any(right < np.minimum(np.maximum.accumulate(right), np.maximum.accumulate(right[::-1])[::-1]))):
            raise ValueError('clock_border_glyph_unknown')
    for edges, top, bottom in components:
        # A cropped glyph cannot be discarded as a pill or corner decoration.
        if (len(edges) == 1 and len(glyph_rows)
                and top <= glyph_rows[-1] and bottom >= glyph_rows[0]):
            raise ValueError('clock_border_glyph_unknown')
    # The wide timer ROI includes a dark pill border, not just the glyphs.
    cleaned = crop.copy()
    if np.any(removed) or window_top or window_bottom != height:
        window = crop[window_top:window_bottom]
        background = np.percentile(window[~removed[window_top:window_bottom]], 90, axis=0).astype(np.uint8)
        cleaned[removed] = background
        # Blank-row separators retain glyph edge padding without feeding a watermark to Otsu.
        cleaned[:window_top] = cleaned[window_bottom:] = background
    foreground[:window_top] = foreground[window_bottom:] = False
    return foreground, cleaned, int(removed.sum())


def _glyph(cleaned, dark, left, right, padding):
    start, end = max(0, left-padding), min(cleaned.shape[1], right+padding)
    # Thin horizontal tips may have only one dark pixel in a column.
    while start > 0 and dark[:, start-1].any():
        start -= 1
    while end < cleaned.shape[1] and dark[:, end].any():
        end += 1
    binary = _binarize(cleaned[:, start:end])
    selected = np.zeros_like(binary)
    seeds = dark[:, start:end].copy()
    seeds[:, :left-start] = False
    seeds[:, right-start:] = False
    pending = [(int(y), int(x)) for y, x in zip(*np.where(seeds & binary))]
    while pending:
        y, x = pending.pop()
        if selected[y, x]:
            continue
        selected[y, x] = True
        for ny in range(max(0, y-1), min(binary.shape[0], y+2)):
            for nx in range(max(0, x-1), min(binary.shape[1], x+2)):
                if binary[ny, nx] and not selected[ny, nx]:
                    pending.append((ny, nx))
    return _normalize_mask(selected)


def _clock_groups(dark):
    counts = dark.sum(axis=0)
    minimum = max(2, int(dark.shape[0] * .06))
    # Keep thin connected tips and gaps, but never promote single-pixel noise.
    return [(left, right) for left, right in _runs(counts > 0)
            if np.any(counts[left:right] >= minimum)]


def _known_colon(dark, groups, height):
    if len(groups) != 4:
        return False
    rows = _runs(dark[:, groups[1][0]:groups[1][1]].any(axis=1))
    return (len(rows) == 2 and all(end-start <= height*.3 for start, end in rows)
            and groups[1][1]-groups[1][0] <= max(end-start for start, end in groups)*.6)


def _load_templates(directory, expected_hashes=TEMPLATE_SHA256, *, normalized=False):
    result = []
    for digit, expected in enumerate(expected_hashes):
        content = (Path(directory) / f'{digit}.png').read_bytes()
        if sha256(content).hexdigest() != expected:
            raise ValueError(f'template_hash_mismatch:{digit}')
        with Image.open(BytesIO(content)) as image:
            if image.height != 24 or not 1 <= image.width <= 24:
                raise ValueError(f'template_dimensions_invalid:{digit}')
            if normalized:
                if image.mode != 'L' or image.size != (24, 24):
                    raise ValueError(f'template_dimensions_invalid:{digit}')
                result.append(np.asarray(image) == 0)
            else:
                result.append(_normalize(np.asarray(image.convert('RGB'))))
    return result


def _digit_scores(glyph, templates, align):
    variants = [(glyph, [0, 0])]
    if align:
        padded = np.pad(glyph, 1)
        variants += [(padded[1+dy:25+dy, 1+dx:25+dx], [dy, dx])
                     for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
    scores = []
    for digit, template in enumerate(templates):
        best, offset = -1.0, [0, 0]
        for candidate, translation in variants:
            union = int(np.count_nonzero(candidate | template))
            score = int(np.count_nonzero(candidate & template)) / union if union else 0.0
            if score > best:
                best, offset = score, translation
        scores.append((best, digit, offset))
    return sorted(scores, key=lambda item: (item[0], item[1]), reverse=True)


def _read_crop(crop, templates, *, align=False):
    try:
        dark, cleaned, removed = _clock_foreground(crop)
    except ValueError as exc:
        return None, 0.0, False, str(exc), dict(groups=[], digits=[], candidates=[])
    groups = _clock_groups(dark)
    detail = dict(groups=[list(group) for group in groups], digits=[], candidates=[], removed_border_pixels=removed)
    if len(groups) != 4:
        return None, 0.0, False, 'clock_split_failed', detail
    if not _known_colon(dark, groups, crop.shape[0]):
        return None, 0.0, False, 'clock_colon_unknown', detail
    chosen, confidence, ambiguous = [], 1.0, False
    for index, padding in ((0, 2), (2, 1), (3, 1)):
        left, right = groups[index]
        glyph = _glyph(cleaned, dark, left, right, padding)
        scores = _digit_scores(glyph, templates, align)
        score, digit, offset = scores[0]
        margin = score - scores[1][0]
        options = [number for value, number, _ in scores if value >= MIN_IOU]
        ambiguous |= margin < MIN_MARGIN
        confidence = min(confidence, score)
        chosen.append(digit)
        detail['digits'].append(dict(digit=digit, score=score, margin=margin, candidates=options, offset=offset,
                                     ranking=[dict(digit=number, score=value, offset=shift)
                                              for value, number, shift in scores]))
    minutes, tens, ones = chosen
    remaining = minutes * 60 + tens * 10 + ones
    detail['raw_text'] = f'{minutes}:{tens}{ones}'
    detail['candidates'] = [remaining] if minutes <= 1 and tens <= 5 and 1 <= remaining <= 90 else []
    if confidence < MIN_IOU:
        return None, confidence, ambiguous, 'clock_low_confidence', detail
    if ambiguous:
        return None, confidence, True, 'clock_ambiguous', detail
    if minutes > 1 or tens > 5 or not 1 <= remaining <= 90:
        return None, confidence, False, 'clock_out_of_range', detail
    return remaining, confidence, False, None, detail


def read_clock_candidate(image, template_dir, *, roi=None):
    """Read an offline candidate from a PIL RGB image or native BGR uint8 array.

    The default ROI requires the existing 1280x720 normalized HUD. Explicit ROI
    is for isolated crop checks, not a production calibration override. No score
    threshold or ambiguous-digit fallback can be configured by callers.
    """
    return _read_clock(image, template_dir, roi=roi, local=False)


def read_local_clock_candidate(image, template_dir):
    """Read the existing 1280x720 HUD with pinned local saved-frame glyphs.

    This offline profile has no production caller or clan/device calibration.
    """
    return _read_clock(image, template_dir, roi=None, local=True)


def _read_clock(image, template_dir, *, roi, local):
    hashes = LOCAL_TEMPLATE_SHA256 if local else TEMPLATE_SHA256
    source = dict(evidence='offline_pixel_candidate', method='border_components_glyph_otsu_24x24_padding2_iou',
                  reference_commit=SOURCE_COMMIT, template_sha256=list(hashes),
                  template_profile='local_saved_hud_v1' if local else 'upstream_reference',
                  alignment='translation_1px_zero_fill' if local else 'none',
                  local_alignment_pixels=1 if local else 0,
                  background_fill='remaining_rgb_percentile90',
                  ink_band_policy='unique_four_columns_and_colon_separated_by_blank_rows',
                  production_enabled=False, device_calibrated=False, min_iou=MIN_IOU, min_margin=MIN_MARGIN)
    result = dict(remaining_seconds=None, confidence=0.0, ambiguous=False, unknown=True,
                  reason=None, source=source, groups=[], digits=[], candidates=[])
    try:
        templates = _load_templates(template_dir, hashes, normalized=local)
    except (OSError, ValueError) as exc:
        result['reason'] = str(exc) if isinstance(exc, ValueError) else 'clock_templates_unavailable'
        return result
    if isinstance(image, Image.Image):
        pixels = np.asarray(image.convert('RGB'))
        source['input_order'] = 'PIL_RGB'
    elif isinstance(image, np.ndarray) and image.dtype == np.uint8 and image.ndim == 3 and image.shape[2] == 3:
        pixels = image[..., ::-1]
        source['input_order'] = 'native_BGR'
    else:
        result['reason'] = 'clock_image_invalid'
        return result
    source['image_size'] = list(pixels.shape[1::-1])
    if roi is None and pixels.shape[:2] != (720, 1280):
        result['reason'] = 'clock_hud_size_unknown'
        return result
    roi = TIMER_ROI if roi is None else roi
    if (not isinstance(roi, (tuple, list)) or len(roi) != 4 or any(type(v) is not int for v in roi)
            or any(v < 0 for v in roi[:2]) or any(v <= 0 for v in roi[2:])
            or roi[0] + roi[2] > pixels.shape[1] or roi[1] + roi[3] > pixels.shape[0]):
        result['reason'] = 'clock_roi_invalid'
        return result
    x, y, width, height = roi
    crop = pixels[y:y+height, x:x+width]
    source.update(roi=list(roi), roi_source='existing_GTBattleTimer' if tuple(roi) == TIMER_ROI else 'explicit_offline_crop',
                  crop_rgb_sha256=sha256(crop.tobytes()).hexdigest())
    seconds, confidence, ambiguous, reason, detail = _read_crop(crop, templates, align=local)
    result.update(detail, remaining_seconds=seconds, confidence=confidence, ambiguous=ambiguous,
                  unknown=seconds is None, reason=reason)
    return result
