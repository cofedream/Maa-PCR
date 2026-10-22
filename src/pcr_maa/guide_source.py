"""Public guide acquisition and offline portrait proposals. Never imports an account or controller."""
import base64
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request


def video_reference(url):
    parts = urlsplit(url)
    match = re.fullmatch(r'/video/(BV[0-9A-Za-z]{10})/?', parts.path)
    query = parse_qs(parts.query, keep_blank_values=True)
    if (parts.scheme != 'https' or parts.hostname not in ('www.bilibili.com', 'bilibili.com')
            or parts.username or parts.password or parts.port or not match
            or set(query) - {'p'} or len(query.get('p', ['1'])) != 1):
        raise ValueError('请使用 B 站原始视频链接，分 P 用 ?p=数字；不接受合集或短链接。')
    page = query.get('p', ['1'])[0]
    if not page.isascii() or not page.isdigit() or not 1 <= int(page) <= 500:
        raise ValueError('视频分 P 无效。')
    return match[1], int(page)


def _public_url(url, video=False):
    parts = urlsplit(url)
    host = parts.hostname or ''
    if (parts.scheme != 'https' or parts.username or parts.password or parts.port
            or not (host.endswith('.bilivideo.com') if video else host == 'api.bilibili.com')):
        raise ValueError('公开来源返回了不支持的地址。')


def _download(url, destination, limit, *, video=False):
    _public_url(url, video)
    # Check redirects before following them; acquisition never uses browser credentials.
    from urllib.request import HTTPRedirectHandler, build_opener
    class PublicRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _public_url(newurl, video)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'}
    with build_opener(PublicRedirect()).open(Request(url, headers=headers), timeout=30) as response:
        _public_url(response.url, video)
        size = 0
        with destination.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError('公开材料超过本次大小上限；已取得部分保留，不生成核对文件。')
                output.write(chunk)


def acquire_video(url, directory):
    """Exact BV and page binding; return a local video plus its publicly obtained metadata."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    bvid, page = video_reference(url)
    metadata = directory / 'metadata.json'
    _download('https://api.bilibili.com/x/web-interface/view?' + urlencode({'bvid': bvid}), metadata, 4_000_000)
    response = json.loads(metadata.read_text(encoding='utf-8'))
    data = response.get('data') or {}
    pages = [p for p in data.get('pages', []) if p.get('page') == page]
    if response.get('code') != 0 or data.get('bvid') != bvid or len(pages) != 1:
        raise ValueError('公开元数据与视频或分 P 不对应。')
    selected = pages[0]
    play = directory / 'playback.json'
    _download('https://api.bilibili.com/x/player/playurl?' + urlencode(
        {'bvid': bvid, 'cid': selected['cid'], 'qn': 64, 'fnval': 1}), play, 2_000_000)
    playback = json.loads(play.read_text(encoding='utf-8'))
    streams = (playback.get('data') or {}).get('durl', [])
    if playback.get('code') != 0 or len(streams) != 1:
        raise ValueError('无法取得单文件公开视频；不尝试登录或绕过限制。')
    video = directory / 'source.mp4'
    _download(streams[0]['url'], video, 256 * 1024 * 1024, video=True)
    evidence = {'source_url': url, 'bvid': bvid, 'page': page, 'cid': selected['cid'],
                'part': selected.get('part'), 'title': data.get('title'), 'author': data.get('owner'),
                'published_at': data.get('pubdate'), 'retrieved_at': datetime.now().astimezone().isoformat(),
                'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (metadata, play, video)}}
    (directory / 'acquisition.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return video, evidence


def prepare_image(raw, source_url, stage, crop, locator, retrieved_on):
    """One calibrated layout; scores remain proposals and all imported portraits require confirmation."""
    import cv2
    import numpy as np
    from io import BytesIO
    from PIL import Image
    from .guide_identity import resolve_portrait
    from .labyrinth.character_recognition import load_character_pack
    parts = urlsplit(source_url)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password
            or not re.fullmatch(r'红焰深域 8-([1-9]|10)', stage) or not locator or len(locator) > 350):
        raise ValueError('请提供完整 HTTPS 来源、红焰第八图关卡及原图定位。')
    if len(raw) < 24 or len(raw) > 20_000_000:
        raise ValueError('请提供不超过 20 MB 的 PNG 或 JPEG 原图。')
    try:
        with Image.open(BytesIO(raw)) as header:
            width, height = header.size
            if header.format not in ('PNG', 'JPEG') or not 1 <= width <= 8192 or not 1 <= height <= 8192 or width * height > 13_000_000:
                raise ValueError('原图格式不支持或尺寸过大。')
            header.verify()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError('原图内容损坏或尺寸过大。') from error
    datetime.strptime(retrieved_on, '%Y-%m-%d')
    frame = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if frame is None or frame.size > 40_000_000:
        raise ValueError('无法读取原图或尺寸过大。')
    if len(crop) != 4 or any(type(n) is not int for n in crop):
        raise ValueError('裁切位置必须是 x y width height 四个整数。')
    x, y, w, h = crop
    if x < 0 or y < 0 or w < 300 or h < 60 or x + w > frame.shape[1] or y + h > frame.shape[0] or abs(w / h - 344 / 66) > .1:
        raise ValueError('裁切不符合已校准的五人横排布局；请核对原图，不猜测位置。')
    row = cv2.resize(frame[y:y+h, x:x+w], (344, 66))
    entries, images, _ = load_character_pack()
    members = [resolve_portrait(row[9:34, left+10:left+40], entries, images, require_verified=False)
               for left in (50, 100, 150, 200, 250)]
    if len({m['UnitId'] for m in members}) != 5 or any(m['Attribute'] != 'FIRE' for m in members):
        raise ValueError('五人编号或属性冲突；停止生成，保留原图。')
    png = cv2.imencode('.png', row)[1].tobytes()
    return dict(SourceUrl=source_url, Server='国服 B 站', Stage=stage,
                ImageSha256=hashlib.sha256(png).hexdigest(), ImageBase64=base64.b64encode(png).decode('ascii'),
                Members=members, ImportedSource=dict(OriginalSha256=hashlib.sha256(raw).hexdigest(),
                    Locator=f'{locator}; crop={x},{y},{w},{h}; layout=fire8-344x66', RetrievedOn=retrieved_on))


def main():
    import argparse
    import cv2
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--image', type=Path, help='本机 PNG/JPEG 原图；复用时不重新获取')
    source.add_argument('--video-url')
    parser.add_argument('--source-url', help='本机原图对应的公开来源')
    parser.add_argument('--at', type=float, help='视频画面秒数')
    parser.add_argument('--stage', required=True)
    parser.add_argument('--crop', type=int, nargs=4, required=True, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'))
    parser.add_argument('--locator', required=True, help='原图中的关卡、队伍行；不自动推断')
    parser.add_argument('--retrieved-on', required=True, help='原图实际取得日期，不代填今天')
    parser.add_argument('--output', type=Path, required=True, help='新目录；保留原件、取得记录、行图及核对 JSON')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.video_url:
        if args.at is None or not math.isfinite(args.at) or args.at < 0:
            parser.error('视频须提供有效 --at 秒数。')
        video, metadata = acquire_video(args.video_url, args.output)
        cap = cv2.VideoCapture(str(video))
        try:
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0 or args.at >= cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps:
                raise ValueError('指定时刻超出已取得视频范围。')
            cap.set(cv2.CAP_PROP_POS_MSEC, args.at * 1000)
            ok, frame = cap.read()
            if not ok:
                raise ValueError('没有取得指定时刻的实际画面。')
            raw = cv2.imencode('.png', frame)[1].tobytes()
        finally:
            cap.release()
        url = args.video_url
        locator = f'BV={metadata["bvid"]}; P={metadata["page"]}; CID={metadata["cid"]}; t={args.at}s; {args.locator}'
    else:
        raw = args.image.read_bytes()
        url, locator = args.source_url or '', args.locator
    (args.output / 'original-image').write_bytes(raw)
    identity = prepare_image(raw, url, args.stage, args.crop, locator, args.retrieved_on)
    (args.output / 'row.png').write_bytes(base64.b64decode(identity['ImageBase64']))
    (args.output / 'source-identity.json').write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'stage': args.stage, 'members': identity['Members'],
                      'manual_confirmation_required': True, 'game_execution': False}, ensure_ascii=False))
