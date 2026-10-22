"""Public clan guide import. Proposals retain source pixels; importing never confirms a plan."""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from .guide_source import acquire_video, video_reference, _download


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def period_from_title(title):
    match = re.search(r'(20\d{2})[.年/-](1[0-2]|0?[1-9])(?:月|\D|$)', title)
    return f'{match[1]}-{int(match[2]):02}' if match else None


def parse_header(words, label):
    text = ' '.join(w['text'] for w in words)
    phase_match = re.search(r'(?<![A-Z])(BC|[BCDE])\s*面', text, re.I)
    phase = phase_match[1].upper() if phase_match else (label.upper() if label.upper() in ('BC', 'B', 'C', 'D', 'E') else None)
    rounds = re.search(r'(\d+)\s*[~～\-至]\s*(\d+)\s*周目', text)
    lower = re.search(r'(\d+)\s*\+\s*周目', text)
    return dict(phase=phase, round_min=int(rounds[1]) if rounds else int(lower[1]) if lower else None,
                round_max=int(rounds[2]) if rounds else None)


def parse_support(text):
    if re.search(r'qsjs|缺谁借谁', text, re.I):
        return 'qsjs'
    return 'specified' if re.search(r'借\s*[^\s,，。]+', text) else None


def parse_damage(text):
    values = re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*[wW万](?![\w])', text)
    return int(float(values[0]) * 10000) if len(values) == 1 else None


def parse_switches(text, unit_ids):
    """Only explicit remaining-time commands; unsupported text remains an unresolved warning."""
    if re.search(r'UB|卡帧|(?:角色|技能)触发', text, re.I):
        return None
    switches = []
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(?:剩余\s*)?(?:([01]):)?([0-5]?\d|[6-8]\d)\s*(?:s|秒)?\s*[:：,， ]+\s*(AUTO|SET\s*[1-5])\s*(开|关|ON|OFF)\s*', line, re.I)
        if not match:
            if re.search(r'(?:\d+[:：]\d{2}|\d+\s*(?:秒|s)?)\s*.*(?:SET|AUTO|UB)', line, re.I):
                return None
            continue
        second = int(match[1] or 0)*60 + int(match[2])
        if not 0 <= second <= 89:
            return None
        state = match[4].upper() in ('开', 'ON')
        target = match[3].upper().replace(' ', '')
        values = {} if target == 'AUTO' else {str(unit_ids[int(target[-1])-1]): state}
        if 'None' in values:
            return None
        if switches and switches[-1]['remaining_seconds'] < second:
            return None
        if not switches or switches[-1]['remaining_seconds'] != second:
            switches.append(dict(remaining_seconds=second, auto=None, set_by_unit={}))
        step = switches[-1]
        if target == 'AUTO':
            if step['auto'] is not None and step['auto'] != state:
                return None
            step['auto'] = state
        else:
            uid = next(iter(values))
            if uid in step['set_by_unit'] and step['set_by_unit'][uid] != state:
                return None
            step['set_by_unit'].update(values)
    return switches


def verify_source(job):
    source = job.get('source') or {}
    bvid, page = video_reference(source.get('url', ''))
    if bvid != source.get('bvid') or page != source.get('page'):
        raise ValueError('clan_source_page_mismatch')
    path = Path(source.get('image_path', ''))
    if not path.is_absolute() or not path.is_file() or digest(path) != source.get('image_sha256'):
        raise ValueError('clan_source_image_changed')
    acquisition_path = path.parent.parent/'acquisition.json'
    recognition_path = path.parent.parent.parent/'recognition.json'
    if not acquisition_path.is_file() or not recognition_path.is_file():
        raise ValueError('clan_source_manifest_missing')
    acquisition = json.loads(acquisition_path.read_text(encoding='utf-8'))
    if any(acquisition.get(k) != source.get(k) for k in ('bvid', 'page', 'cid')):
        raise ValueError('clan_source_acquisition_mismatch')
    records = json.loads(recognition_path.read_text(encoding='utf-8'))
    matched = [row for record in records if record.get('page') == page for row in record.get('observations', [])
               if row.get('at_seconds') == source.get('at_seconds') and row.get('image_path') == str(path)]
    if len(matched) != 1 or source.get('job_id') not in [t.get('job_id') for t in matched[0].get('tables', [])]:
        raise ValueError('clan_source_observation_mismatch')
    return True


def static_frames(video, directory):
    """Group matching actual frames and retain a middle frame; reject large fades and drift."""
    import cv2
    import numpy as np
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    cap = cv2.VideoCapture(str(video))
    groups, current = [], []
    try:
        fps, count = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps <= 0 or count <= 0 or count / fps > 7200:
            raise ValueError('clan_video_duration_unknown_or_over_two_hours')
        previous = reference = None
        for second in range(int(count / fps)):
            cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
            ok, frame = cap.read()
            if not ok:
                raise ValueError('clan_video_frame_unreadable')
            thumbnail = cv2.resize(frame, (320, 180)).astype(np.int16)
            if previous is not None and (float(np.abs(thumbnail-previous).mean()) > 2
                                         or float(np.abs(thumbnail-reference).mean()) > 2):
                if len(current) >= 3:
                    groups.append(current[len(current)//2])
                current = []
            if not current:
                reference = thumbnail
            current.append(second)
            previous = thumbnail
        if len(current) >= 3:
            groups.append(current[len(current)//2])
        if len(groups) > 120:
            raise ValueError('clan_video_has_over_120_stable_slides')
        results = []
        for second in groups:
            cap.set(cv2.CAP_PROP_POS_MSEC, second * 1000)
            ok, frame = cap.read()
            if not ok:
                raise ValueError('clan_video_frame_unreadable')
            raw = cv2.imencode('.png', frame)[1].tobytes()
            path = directory / f'{second:05}.png'
            path.write_bytes(raw)
            results.append((second, path, frame))
    finally:
        cap.release()
    return results


@contextmanager
def offline_ocr(directory):
    import cv2
    import numpy as np
    from maa.pipeline import JOCR
    from . import native
    from .replay import SavedImages
    native.RUNTIME = Path(directory) / 'native'
    resource = native.load_resource()
    controller = SavedImages([[np.zeros((720, 1280, 3), np.uint8)]])
    tasker = native.bind(resource, controller)
    def recognize(image, scale=1):
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale != 1 else image
        h, w = image.shape[:2]
        job = native.wait_job(tasker.post_recognition('OCR', JOCR(expected=['.+'], roi=(0, 0, w, h), threshold=.5), image), seconds=45)
        if not job.succeeded:
            raise ValueError('clan_source_ocr_failed')
        detail = job.get()
        return [dict(text=word['text'], box=[round(n/scale, 2) for n in word['box']], score=word['score'])
                for node in detail.nodes if node.recognition
                for word in node.recognition.raw_detail.get('all', [])]
    try:
        yield recognize
    finally:
        if not native.stop_tasker(tasker).get('idle'):
            raise RuntimeError('clan_offline_ocr_not_idle')


def table_candidates(frame):
    """Recognize the calibrated one-chart grid by its colored portrait band and table lines."""
    import cv2
    import numpy as np
    if frame.shape[1] * 9 != frame.shape[0] * 16:
        return []
    f = cv2.resize(frame, (1280, 720))
    hsv = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
    rows = []
    # shortcut: this author's two chart grids are calibrated; other layouts remain unresolved until calibrated.
    grids = [(x, y, 64, 60, None, '通用') for y in (226, 361) for x in (120, 506)]
    grids += [(x, y, 37, 35, i+1, f'刀{j+1}') for j, y in enumerate((160, 306, 452))
              for i, x in enumerate((170, 380, 600, 814, 1028))]
    for x, y, cell, height, boss, label in grids:
        cells = [hsv[y:y+height, x+i*cell:x+(i+1)*cell] for i in range(5)]
        # Blank slide/table regions cannot become a five-person team.
        if all(float(np.mean((c[:,:,1] > 45) & (c[:,:,2] > 55))) > .30 for c in cells):
            rows.append(dict(x=x, y=y, cell=cell, height=height, boss=boss, team_label=label))
    return rows


def identify_members(frame, table, entries, images):
    import cv2
    from .guide_identity import resolve_portrait
    members = []
    for i in range(5):
        x, y = table['x']+i*table['cell'], table['y']
        portrait = frame[y:y+table['height'], x:x+table['cell']]
        portrait = cv2.resize(portrait, (50, 50))
        # Exclude the source's power number at the top and star/element badges below.
        result = resolve_portrait(portrait[15:32, 15:38], entries, images, require_verified=False)
        trusted = result['Score'] >= .82 and result['Margin'] >= .08
        members.append(dict(unit_id=result['UnitId'] if trusted else None, name=result['Name'] if trusted else None,
                            star=None, set=None, score=round(result['Score'], 5), margin=round(result['Margin'], 5),
                            proposal_unit_id=result['UnitId'], proposal_name=result['Name']))
    ids = [m['unit_id'] for m in members]
    if len({uid for uid in ids if uid is not None}) != len([uid for uid in ids if uid is not None]):
        for m in members:
            m['unit_id'] = m['name'] = None
    return members


def operation_mark(frame, table, index):
    """A closed circle and a diagonal cross are distinct; uncertain pixels stay unknown."""
    import cv2
    import numpy as np
    cell = table['cell']
    cx = table['x'] + (index+.5)*cell
    cy = table['y'] + (126.5 if cell == 64 else 94.5)
    half = 7 if cell == 64 else 5
    patch = cv2.cvtColor(frame[round(cy)-half:round(cy)+half+1, round(cx)-half:round(cx)+half+1], cv2.COLOR_BGR2GRAY)
    votes = []
    for threshold in (115, 145, 175):
        ink = np.uint8(patch < threshold)*255
        contours, hierarchy = cv2.findContours(ink, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        outer = [(c, h) for c, h in zip(contours, hierarchy[0]) if h[3] == -1]
        if len(outer) != 1:
            continue
        contour, relation = outer[0]
        x,y,w,h = cv2.boundingRect(contour)
        if not 3 <= w <= 2*half-1 or not 3 <= h <= 2*half-1 or not .6 <= w/h <= 1.5:
            continue
        if relation[2] != -1:
            votes.append(True)
        else:
            ys,xs = np.where(ink[y:y+h,x:x+w] > 0)
            if len(xs) < 5:
                continue
            nx,ny = xs/max(1,w-1),ys/max(1,h-1)
            diagonals = np.minimum(abs(nx-ny),abs(nx+ny-1)) < .22
            if float(diagonals.mean()) > .8:
                votes.append(False)
    return votes[0] if len(votes) >= 2 and all(v == votes[0] for v in votes) else None


def table_details(words, table, members, frame=None):
    lines = []
    for word in sorted(words, key=lambda w:w['box'][1]+w['box'][3]/2):
        cy = word['box'][1]+word['box'][3]/2
        if not lines or abs(lines[-1][0]-cy) > 4:
            lines.append((cy, []))
        lines[-1][1].append(word)
    text = '\n'.join(' '.join(w['text'] for w in sorted(row,key=lambda w:w['box'][0])) for _,row in lines)
    # The numeric star row is parsed only when its label is actually recognized.
    star_labels = [w for w in words if re.fullmatch(r'星级', w['text']) and w['score'] >= .8]
    cy = (star_labels[0]['box'][1] + star_labels[0]['box'][3]/2 if len(star_labels) == 1 else
          103.5 if table['cell'] == 64 else 88)
    for index, member in enumerate(members):
        cx = (index+1.5)*table['cell']
        values = [w for w in words if re.fullmatch('[1-6]', w['text']) and w['score'] >= .8
                  and abs(w['box'][1]+w['box'][3]/2-cy) < 4
                  and abs(w['box'][0]+w['box'][2]/2-cx) < table['cell']*.3]
        if len(values) == 1:
            member['star'] = int(values[0]['text'])
        if frame is not None:
            member['set'] = operation_mark(frame, table, index)
    return text


def build_page_jobs(frames, evidence, period, recognize):
    import cv2
    from .labyrinth.character_recognition import load_character_pack
    entries, images, _ = load_character_pack()
    jobs, seen, records = [], set(), []
    for at, path, original in frames:
        frame = cv2.resize(original, (1280, 720))
        tables = table_candidates(frame)
        if not tables:
            continue
        header_words = recognize(frame[:150, :])
        header = parse_header(header_words, evidence['part'] or '')
        if not header['phase'] or header['round_min'] is None:
            continue
        page_record = dict(at_seconds=at, image_path=str(path.resolve()), layout='moxi-one-chart-1280x720', header_ocr=header_words, tables=[])
        backup = bool(re.search('备用', ' '.join(w['text'] for w in recognize(frame[580:, 1100:]))))
        for number, table in enumerate(tables, 1):
            members = identify_members(frame, table, entries, images)
            if max(m['score'] for m in members) < .6:
                continue
            x, y, cell, height = (table[k] for k in ('x', 'y', 'cell', 'height'))
            left = max(0, x-cell)
            crop = frame[max(0,y-16):min(580,y+(135 if cell == 64 else 124)), left:min(1280,x+cell*5+5)]
            words = recognize(crop, 3)
            text = table_details(words, table, members, frame)
            ids = [m['unit_id'] for m in members]
            support = parse_support(text)
            damage = parse_damage(text)
            switches = parse_switches(text, ids)
            signature = (header['phase'], header['round_min'], header['round_max'], table['boss'], table['team_label'], backup,
                         tuple(m['proposal_unit_id'] for m in members), tuple(m['star'] for m in members),
                         tuple(m['set'] for m in members), json.dumps(switches, sort_keys=True), support, damage)
            if signature in seen:
                continue
            seen.add(signature)
            job_id = f'{evidence["bvid"]}-p{evidence["page"]}-t{at}-r{number}'
            source = dict(url=evidence['source_url'], bvid=evidence['bvid'], page=evidence['page'], cid=evidence['cid'],
                          at_seconds=at, image_path=str(path.resolve()), image_sha256=digest(path), job_id=job_id)
            warnings = ['导入为识别候选，须核对原图后保存确认。', '练度、EX与强化差异只作提醒。']
            if any(uid is None for uid in ids): warnings.append('存在未知或相似角色，不能执行。')
            if any(m['star'] is None for m in members): warnings.append('星级未完整识别，需核对或使用账号实际值。')
            warnings.append('操作行○/×按既有约定解析为SET开/关候选，须核对；不明确字形保留未知。')
            if table['boss'] is None: warnings.append('原图声明通用队伍，BOSS须明确选择。')
            if support == 'specified': warnings.append('原图指定借人，需核对具体角色。')
            if support is None: warnings.append('原图支援方式未明确识别。')
            if switches is None: warnings.append('存在未支持或无法完整解析的操作轴，不能当纯auto执行；须核对完整原图。')
            pure_auto = bool(re.search(r'纯\s*auto', evidence.get('title') or '', re.I))
            job = dict(period=period, **header, boss=table['boss'], boss_name='', package='备用' if backup else '主表',
                       team_label=table['team_label'] if table['boss'] else f'通用队{number}', unit_ids=ids,
                       stars=[m['star'] for m in members], auto=True if pure_auto and switches is not None else None,
                       set_by_unit={str(m['unit_id']): m['set'] for m in members if m['unit_id'] is not None}, switches=switches,
                       support_mode=support, support_unit_id=None, reference_damage=damage, source=source,
                       warnings=warnings, members=members,
                       image_base64=base64.b64encode(cv2.imencode('.png', crop)[1].tobytes()).decode('ascii'))
            jobs.append(job)
            page_record['tables'].append(dict(job_id=job_id, geometry=table, ocr=words, members=members))
        records.append(page_record)
    return jobs, records


def prepare_bundle(url, output, *, pages=None):
    """Acquire every public part, detect static charts, propose teams, preserve all material."""
    bvid, selected_page = video_reference(url)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    index = output / 'video-metadata.json'
    _download('https://api.bilibili.com/x/web-interface/view?' + urlencode({'bvid': bvid}), index, 4_000_000)
    raw = json.loads(index.read_text(encoding='utf-8'))
    metadata = raw.get('data') or {}
    if raw.get('code') != 0 or metadata.get('bvid') != bvid:
        raise ValueError('clan_video_metadata_mismatch')
    candidates = metadata.get('pages') or []
    requested = pages if pages else [selected_page] if 'p' in parse_qs(urlsplit(url).query) else [p['page'] for p in candidates]
    selected = [p for p in candidates if p.get('page') in requested]
    if not selected or len(selected) != len(set(requested)) or len(selected) > 30:
        raise ValueError('clan_video_pages_missing_or_over_30')
    period = period_from_title(metadata.get('title', ''))
    bundle = dict(version=1, source=dict(url=url, bvid=bvid, title=metadata.get('title'), author=metadata.get('owner'), period=period),
                  pages=[dict(page=p['page'], cid=p['cid'], label=p.get('part', '')) for p in selected], jobs=[])
    records = []
    with offline_ocr(output) as recognize:
        for page in selected:
            page_url = f'https://www.bilibili.com/video/{bvid}/?p={page["page"]}'
            directory = output / f'p{page["page"]}'
            video, evidence = acquire_video(page_url, directory)
            if evidence['cid'] != page['cid'] or evidence['bvid'] != bvid or evidence['page'] != page['page']:
                raise ValueError('clan_video_cross_page_mismatch')
            frames = static_frames(video, directory/'frames')
            jobs, observations = build_page_jobs(frames, evidence, period, recognize)
            bundle['jobs'].extend(jobs)
            records.append(dict(page=page['page'], acquisition=evidence, observations=observations))
    if not bundle['jobs']:
        bundle['warnings'] = ['未识别出已校准布局的五人作业；原件已保存，不能执行。']
    (output/'recognition.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'bundle.json').write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding='utf-8')
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--video-url', '--url', required=True)
    parser.add_argument('--output', '--output-dir', type=Path, required=True)
    parser.add_argument('--page', type=int, action='append')
    args = parser.parse_args()
    bundle = prepare_bundle(args.video_url, args.output, pages=args.page)
    print(json.dumps(dict(bundle_path=str((args.output/'bundle.json').resolve()), jobs=len(bundle['jobs']),
                          manual_confirmation_required=True, game_execution=False), ensure_ascii=False))
