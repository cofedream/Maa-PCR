"""Required saved-dialog regressions; fixtures ship with the public test suite."""
import copy
import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace as NS

import cv2
import pytest

from pcr_maa import native
from pcr_maa.labyrinth import character_recognition as cr
from pcr_maa.replay import SavedImages

DATA = Path(__file__).parent / 'data'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Network/device access prohibited in saved-dialog tests')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)
    import maa.controller
    from maa.toolkit import Toolkit
    monkeypatch.setattr(maa.controller, 'AdbController', deny)
    monkeypatch.setattr(Toolkit, 'find_adb_devices', deny)


@pytest.fixture(scope='module')
def saved():
    metadata = json.loads((DATA / 'labyrinth-opening-bottom-20261025.json').read_text(encoding='utf-8'))
    frames = []
    for row in metadata['frames']:
        path = DATA / row['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        frame = cv2.imread(str(path))
        assert frame is not None and frame.shape == (720, 1280, 3)
        frames.append(frame)
    assert len(frames) == 2
    matcher = cr.IconMatcher()
    before = [frames[0].copy(), frames[0].copy()]
    # Render unselected artwork from the shipped pack; keep the real UI and badges.
    for card in metadata['unselected_cards']:
        index = next(i for i, row in enumerate(matcher.entries) if row['unit_icon_id'] == card['unit_icon_id'])
        x, y, w, h = card['box']
        icon = cv2.resize(matcher.images[index][:, :, ::-1], (w-14, w-14))
        visible = min(w-14, 583-y-6)
        for count, frame in enumerate(before):
            if count and card['character_id'] == '1075':
                continue
            frame[y+6:y+6+visible, x+6:x+w-8] = icon[:visible]
            if y+h <= 583:
                frame[y+98:y+128, x+100:x+128] = frames[0][y+98:y+128, x+100:x+128]
    for count, frame in enumerate(before):
        frame[85:125, 1170:1250] = 255
        cv2.putText(frame, f'{count}/3', (1198, 115), cv2.FONT_HERSHEY_SIMPLEX, .65, (80, 80, 80), 1, cv2.LINE_AA)
    recognizer = cr.CharacterRecognizer(matcher=matcher)
    for count, frame in enumerate(before):
        for _ in range(2):
            value = recognizer.recognize(frame, 'opening', current_filter='ALL', observed_count=count)
        assert value['stable'] and value['selected'] == metadata['selected'][:count]
        assert all(any(card['character_id'] == role and card['trusted'] for card in value['cards']) for role in metadata['selected'])
    return NS(metadata=metadata, frames=frames, before=before, prior=recognizer.previous['opening'], matcher=matcher)


@pytest.mark.parametrize('index', [0, 1])
@pytest.mark.parametrize('offset', [-30, -12, 0, 8, 20])
def test_bottom_row_and_selected_marker_follow_visible_card_edges(saved, index, offset):
    frame = saved.frames[index].copy()
    x, y, w, h = cr.VIEWPORTS['opening']
    content = frame[y:y+h, x:1210].copy()
    frame[y:y+h, x:1210] = 255
    start, end = max(0, offset), min(h, h+offset)
    frame[y+start:y+end, x:1210] = content[start-offset:end-offset]
    boxes = cr.detect_grid(frame, (x, y, w, h))
    target = min(boxes, key=lambda box: abs(box[0]-223)+abs(box[1]-(470+offset)))
    assert abs(target[0]-223) <= 2 and abs(target[1]-(470+offset)) <= 2
    assert cr.opening_marker(frame, target) >= .72


def test_selected_partial_card_keeps_observed_identity_across_animation(saved):
    recognizer = cr.CharacterRecognizer(matcher=saved.matcher)
    recognizer.previous['opening'] = copy.deepcopy(saved.prior)
    for frame in saved.frames * 2:
        value = recognizer.recognize(frame, 'opening', current_filter='ALL', observed_count=2)
        assert value['selected'] == saved.metadata['selected']
        target = next(card for card in value['cards'] if card['character_id'] == '1351')
        assert target['trusted'] and target['selected'] and abs(target['box'][1]-470) <= 2
    assert value['stable']


@pytest.mark.parametrize('transition', ['same', 'changed_filter', 'changed_scroll', 'wrong_count'])
def test_preclick_identity_survives_missing_intermediate_card_read(saved, transition):
    recognizer = cr.CharacterRecognizer(matcher=saved.matcher)
    before = copy.deepcopy(saved.prior)
    recognizer.opening_toggle = (before, '1351', 1)
    if transition == 'changed_scroll':
        before['scrollbar']['position'] -= .2
    value = recognizer.recognize(saved.frames[0], 'opening',
        current_filter='WATER' if transition == 'changed_filter' else 'ALL',
        observed_count=1 if transition == 'wrong_count' else 2)
    assert ('1351' in value['selected']) == (transition == 'same')


@pytest.mark.parametrize('changed', ['filter', 'scroll', 'cold'])
def test_selected_partial_card_does_not_gain_identity_from_other_views(saved, changed):
    recognizer = cr.CharacterRecognizer(matcher=saved.matcher)
    if changed != 'cold':
        previous = copy.deepcopy(saved.prior)
        if changed == 'filter':
            previous['filter'] = 'WATER'
        else:
            previous['scrollbar']['position'] = max(0, previous['scrollbar']['position']-.2)
        recognizer.previous['opening'] = previous
    value = recognizer.recognize(saved.frames[0], 'opening', current_filter='ALL', observed_count=2)
    assert '1351' not in value['selected']
    target = next(card for card in value['cards'] if abs(card['box'][0]-223) <= 2 and abs(card['box'][1]-470) <= 2)
    assert target['selected'] and not target['trusted']


def test_native_two_clicks_read_real_counts_and_both_identities(saved, tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'RUNTIME', tmp_path/'native')
    controller = SavedImages([[saved.before[0]], [saved.before[1]], saved.frames * 2])
    owner = native.bind(native.load_resource(), controller)
    characters = cr.MaaCharacters(owner, recognizer=cr.CharacterRecognizer(matcher=saved.matcher))
    try:
        batch = NS(checkpoint=lambda: None)
        value, _ = characters.read(batch, 'opening')
        assert value['count'] == 0 and value['selected'] == []
        for count, role in enumerate(saved.metadata['selected'], 1):
            characters.run('ToggleOnce', dict(mode='toggle', page='opening', role_id=role,
                expected_selected=saved.metadata['selected'][:count-1]), input=True)
            value, receipt = characters.wait_for(batch, 'opening',
                lambda observed: observed['count'] == count and observed['selected'] == saved.metadata['selected'][:count])
            assert receipt['native_ok'] and value['stable'] and value['count_native']['text'] == f'{count}/3'
        assert controller.clicks == [[570, 388], [288, 529]] and not controller.swipes
    finally:
        assert native.wait_job(owner.post_stop(), seconds=10).succeeded


@pytest.mark.parametrize('page', ['opening', 'battle', 'boss', 'roster'])
@pytest.mark.parametrize('selected', [False, True])
def test_shared_grid_keeps_clipped_rows_for_each_viewport(saved, page, selected):
    size = 120 if page == 'roster' else 130
    pitch = 131 if page == 'roster' else 141
    row_pitch = 141 if page == 'opening' else 131 if page == 'roster' else 145
    vx, vy, vw, vh = cr.VIEWPORTS[page]
    bottom = vy+vh-round(size*.8)
    tops = list(range(bottom, vy-size, -row_pitch))[::-1]
    frame = saved.frames[0].copy()
    frame[:] = 255
    for row, y in enumerate(tops):
        for col in range(2 if y == bottom else 8):
            x = 80+pitch*col
            icon = cv2.resize(saved.matcher.images[(row*8+col) % len(saved.matcher.images)][:, :, ::-1], (size-12, size-12))
            tile = cv2.copyMakeBorder(icon, 6, 6, 6, 6, cv2.BORDER_CONSTANT, value=(150, 150, 230))
            if selected and y == bottom:
                tile[3:size-3, 3:size-3] = (tile[3:size-3, 3:size-3]*.55).astype('uint8')
                center = (round(2+size*.83), round(2+size*.20))
                cv2.circle(tile, center, round(size*.17), (255, 255, 255), -1)
                cv2.putText(tile, '2', (center[0]-5, center[1]+6), cv2.FONT_HERSHEY_SIMPLEX, .5, (180, 100, 60), 1, cv2.LINE_AA)
            start, end = max(vy, y), min(vy+vh, y+size)
            frame[start:end, x:x+size] = tile[start-y:end-y]
    boxes = cr.detect_grid(frame, (vx, vy, vw, vh), card_size=size, pitch=pitch, row_pitch=row_pitch)
    target = min(boxes, key=lambda box: abs(box[0]-80)+abs(box[1]-bottom))
    assert abs(target[0]-80) <= 2 and abs(target[1]-bottom) <= 2
