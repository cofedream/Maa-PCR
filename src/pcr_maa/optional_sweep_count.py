"""Read the ticket-count button and the adjacent enabled +/- signs on one CN frame."""
import re

import cv2
import numpy as np


def button(words):
    matches = []
    for word in words:
        match = re.fullmatch(r'使用\s*([1-9]\d{0,2})\s*张', word.get('text', '').strip())
        if match is None or word.get('score', 0) < .9:
            continue
        x, y, width, height = word['box']
        if 550 <= x < x + width <= 1260 and 200 <= y < y + height <= 675:
            matches.append((int(match[1]), list(word['box'])))
    return matches[0] if len(matches) == 1 else None


def extreme_button(words, use_box, *, minimum):
    """Match a labeled endpoint beside the same count. Caller verifies the enabled use button."""
    observed = button(words)
    if observed is None or observed[1] != list(use_box) or (minimum and observed[0] == 1):
        return None
    x, y, width, height = use_box
    cx, cy = x + width // 2, y + height // 2
    expected = 'MIN' if minimum else 'MAX'
    matches = []
    for word in words:
        if word.get('text', '').strip() != expected or word.get('score', 0) < .9:
            continue
        bx, by, bw, bh = word['box']
        distance = (cx - bx - bw // 2) if minimum else (bx + bw // 2 - cx)
        if 135 <= distance <= 210 and abs(by + bh // 2 - cy) <= 12 and 15 <= bw <= 68 and 8 <= bh <= 32:
            matches.append(list(word['box']))
    return matches[0] if len(matches) == 1 else None


def enabled_button(argv, use_box):
    """The disabled button retains the same text and shape, but its blue is dimmed."""
    x, y, width, height = use_box
    if argv.image.shape != (720, 1280, 3) or x < 10 or x + width + 10 > 1280 or height < 4:
        return False
    middle = slice(y + height // 4, y + 3 * height // 4)
    for side in (slice(x - 10, x - 2), slice(x + width + 2, x + width + 10)):
        blue, green, red = argv.image[middle, side].astype(np.int16).transpose(2, 0, 1)
        bright = (blue >= 222) & (green >= 115) & (blue - green >= 35) & (green - red >= 30)
        if bright.mean() < .65:
            return False
    return True


def _button_face(image, cx, cy):
    """The blue mark must sit inside a light disc with a gold rim."""
    diagonal = [-10, 10]
    for dx in diagonal:
        for dy in diagonal:
            if image[cy + dy, cx + dx].min() < 218:
                return False
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        ray = image[cy + dy * np.arange(21, 37), cx + dx * np.arange(21, 37)].astype(np.int16)
        blue, green, red = ray.T
        if not np.any((red > 155) & (red - blue > 27) & (green - blue > 12)):
            return False
    return True


def adjust_button(context, argv, words, use_box, increase):
    """Restrict the search to the use button's row; accept exactly one signed round button."""
    observed = button(words)
    if observed is None or list(use_box) != observed[1] or not enabled_button(argv, use_box):
        return None
    x, y, width, height = use_box
    cx, cy = x + width // 2, y + height // 2
    left, right = (cx + 65, cx + 200) if increase else (cx - 200, cx - 65)
    left, right = max(36, left), min(1280 - 36, right)
    if left >= right or cy < 60 or cy > 660:
        return None
    top = cy - 25
    region = argv.image[top:cy + 26, left:right].astype(np.int16)
    blue, green, red = region.transpose(2, 0, 1)
    ink = ((blue >= 225) & (green >= 120) & (blue - green >= 44)
           & (green - red >= 23) & (red <= 169)).astype(np.uint8)
    _, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    found = []
    for label, (sx, sy, w, h, area) in enumerate(stats[1:], start=1):
        if not 16 <= w <= 35 or not (16 <= h <= 35 if increase else 3 <= h <= 9):
            continue
        shape = labels[sy:sy+h, sx:sx+w] == label
        if not shape[h // 2].mean() >= .86:
            continue
        if increase:
            if not (.75 <= w / h <= 1.35 and shape[:, w // 2].mean() >= .86
                    and .2 < area / (w * h) < .64):
                continue
        elif area / (w * h) < .65:
            continue
        mark_x, mark_y = int(left + sx + w // 2), int(top + sy + h // 2)
        if abs(mark_y - cy) <= 15 and _button_face(argv.image, mark_x, mark_y):
            found.append([mark_x - 3, mark_y - 3, 7, 7])
    return found[0] if len(found) == 1 else None
