"""Read-only page classification and bounded CN survey/cloister navigation."""
import re
from types import SimpleNamespace


SURVEYS = {'sanctum_survey': '圣迹调查', 'temple_survey': '神殿调查'}
TOWER = 'tower_cloister'
_HEADER = (35, 0, 380, 77)
_MENU = (570, 74, 1278, 640)
_BODY = (8, 68, 1272, 655)
_HOME = (10, 663, 178, 719)
_TOWER_CARD = (942, 445, 1224, 610)
_CLOISTER = r'(?:回廊探索|回廊探险|回廊)'
_DIALOG = (r'确认|确定|OK|扫荡券确认|扫荡券使用确认|使用扫荡券|扫荡确认|购买确认|'
           r'挑战次数回复|回复确认|扫荡结果|网络错误|通信错误|'
           r'.*消耗扫荡券.*|通关关卡吗[？?]?')


def _matches(words, pattern, region=None):
    """Retain word geometry for button targeting and nearby field association."""
    found = []
    for word in words:
        box = word.get('box')
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or word.get('score', 0) < .90):
            continue
        x, y, w, h = box
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x+w > 1280 or y+h > 720:
            continue
        if region and not (region[0] <= x+w/2 <= region[2] and region[1] <= y+h/2 <= region[3]):
            continue
        if re.fullmatch(pattern, ''.join(word['text'].split())):
            found.append(word)
    return found


def _one(words, pattern, region=None):
    found = _matches(words, pattern, region)
    return found[0] if len(found) == 1 else None


def _stage_pattern(module, stage):
    if module == TOWER and stage is None:
        return _CLOISTER
    if module in SURVEYS and type(stage) is int and 1 <= stage <= 999:
        return rf'{SURVEYS[module]}关卡{stage}级'
    raise ValueError('unknown_optional_sweep_stage')


def _selected_cloister(words, image):
    """Bind the left tower floor to its gold selector, not to another floor's status."""
    entry = _one(words, _CLOISTER, (70,90,326,650))
    if not entry or image is None or image.shape != (720,1280,3):
        return False
    if not all(_one(words, f'wave{i}', (420,95,816,158)) for i in (1,2,3)):
        return False
    # Region immediately outside the right edge of the selected floor card.
    y = int(entry['box'][1] + entry['box'][3] / 2 + 22)
    region = image[y-9:y+10,349:371].astype('int16')
    b, g, r = region[:,:,0], region[:,:,1], region[:,:,2]
    return int(((r > 192) & (g > 152) & (r-b > 55) & (g-b > 28)).sum()) >= 120


def stage_identity(words, module, stage, image=None):
    """Reject a multi-row list, even if it includes the selected quest."""
    if module == TOWER:
        return bool(_one(words, '露娜之塔', _HEADER)) and _selected_cloister(words, image)
    titles = _matches(words, r'(?:圣迹|神殿)调查关卡\d{1,3}级')
    return len(titles) == 1 and bool(_one(titles, _stage_pattern(module, stage),
                                         (35, 23, 442, 109)))


def _counter(words):
    label = r'(?:今日)?剩余(?:挑战|扫荡)次数'
    fraction = r'(\d{1,3})[/／](\d{1,3})'
    tagged = _matches(words, label + r'[：:]?' + fraction, _BODY)
    regions = _matches(words, label + r'[：:]?', _BODY)
    if len(tagged) + len(regions) != 1:
        return None
    if tagged:
        value = ''.join(tagged[0]['text'].split())
    else:
        x, y, w, h = regions[0]['box']
        values = _matches(words, fraction,
                          (x+w, y-max(4,h*.25), min(x+w+190,1280), y+1.25*h))
        if len(values) != 1:
            return None
        value = ''.join(values[0]['text'].split())
    remaining, limit = map(int, re.search(fraction + '$', value).groups())
    return (remaining, limit) if 0 <= remaining <= limit and limit > 0 else None


def _survey_star_count(image):
    # CN quest detail, 960x540 source scaled to Maa's 1280x720 short side.
    # Source: Princess-connection-farm dc37848d, core/constant.py and
    # scenes/fight/fightinfo_base.py (three independent star centres).
    if image is None or image.shape != (720, 1280, 3):
        return None
    gold = (98, 228, 245)
    grey = (212, 171, 139)
    stars = 0
    for x in (1016, 1100, 1185):
        # Require most nearby pixels to agree; a border pixel alone is weak.
        patch = image[54:57, x-1:x+2]
        gold_pixels = sum(all(abs(int(p[i]) - gold[i]) < 48 for i in range(3))
                          for row in patch for p in row)
        if gold_pixels >= 6:
            stars += 1
        elif sum(all(abs(int(p[i]) - grey[i]) < 31 for i in range(3))
                 for row in patch for p in row) < 6:
            return None
    return stars


def stage_frame(words, module, stage, image=None):
    if _matches(words, _DIALOG) or not stage_identity(words, module, stage, image):
        return None
    base = dict(module=module, stage=stage)
    if _one(words, r'未通关|尚未通关|未首通|未解锁|通关后可扫荡|三星通关后可扫荡', _BODY):
        return dict(base, state='locked')
    counter = _counter(words)
    if counter is None:
        return None
    stars = _survey_star_count(image) if module in SURVEYS else None
    if (stars is not None and stars < 3 and _one(words, '取消', (770,555,1030,650))
            and _one(words, '挑战', (1010,555,1235,650))):
        return dict(base, state='locked', reason='stage_not_three_star',
                    remaining=counter[0], limit=counter[1], stars=stars)
    sweep = _one(words, r'扫荡|使用\d{1,3}张(?:扫荡券)?', _BODY)
    if not sweep and not (module == TOWER and counter[0] == 0 and _one(words, '挑战', _BODY)):
        return None
    if module == TOWER:
        entry = _one(words, _CLOISTER, (70,90,326,650))
        x, y, w, h = entry['box']
        cleared = _one(words, '已通关|通关', (x,y+h,x+w+65,y+h+42))
        # In the CN cloister layout the cleared floor can lack a clear badge.
        # Its usable sweep control is distinct from the dim control at 0/5.
        if not cleared and image is not None and sweep and counter[0]:
            from .optional_sweep_count import button, enabled_button
            use = button(words)
            cleared = bool(use and use[1] == sweep['box']
                           and enabled_button(SimpleNamespace(image=image), use[1]))
    else:
        cleared = _one(words, r'已通关|已完成首通|三星通关|★★★|★{3}通关', _BODY)
    if counter[0] and not (cleared or stars == 3):
        return None
    return dict(base, state='ready', remaining=counter[0], limit=counter[1])


def _special(words):
    return (_one(words, '特别关卡', _HEADER)
            and _one(words, '经验值关卡', (635,188,930,263))
            and _one(words, '玛那关卡', (940,188,1230,263)))


def _special_button(words):
    button = _one(words, '特别关卡', (890,120,1075,280))
    if button:
        return button
    # The national client stacks 特别 / 关卡 on separate lines of this card.
    first = _one(words, '特别', (912,183,1050,244))
    second = _one(words, '关卡', (912,227,1050,273))
    if first and second and abs(first['box'][0] - second['box'][0]) <= 18:
        return second
    return None


def _hit(ctx, argv, name):
    match = ctx.run_recognition(name, argv.image)
    return match is not None and match.hit is True


def _is_home(ctx, argv):
    return _hit(ctx, argv, 'HomeTextLayout') and _hit(ctx, argv, 'HomeNoKnownLargePanel')


def _page(port, ctx, argv, words):
    """Return a verified location and at most one non-consuming control."""
    module, stage = port.module, port.stage
    stage_pat = _stage_pattern(module, stage)
    state = stage_frame(words, module, stage, argv.image)
    if state:
        return 'stage', _one(words, stage_pat), state
    if (_matches(words, _DIALOG) or _matches(words, '确认|确定|取消|关闭', _BODY)):
        return None
    if _special(words):
        if module == TOWER:
            button = _one(words, '露娜之塔', _TOWER_CARD)
            if button and _one(words, '敬请期待|未开放|活动已结束', _TOWER_CARD):
                return 'closed', button, dict(module=module, stage=stage, state='closed')
        else:
            tile = (642,343,931,438) if module == 'sanctum_survey' else (939,343,1230,438)
            button = _one(words, SURVEYS[module], tile)
        if button:
            return 'special', button, {}
        return None
    if (module == TOWER and _one(words, '露娜之塔', _HEADER)
            and _one(words, '塔顶', (414,97,553,166))
            and _one(words, '模拟战', (929,420,1080,511))
            and _one(words, '实战', (1078,420,1238,511))
            and _one(words, '挑战', (933,532,1220,620))
            and not _matches(words, _CLOISTER)):
        return 'tower_summit', _one(words, '塔顶', (414,97,553,166)), {}
    if (_one(words, '冒险', _HEADER) and _one(words, '主线关卡', _MENU)
            and _one(words, '深域关卡|追忆的战场', (890,390,1240,475))):
        button = _special_button(words)
        if button:
            return 'adventure', button, {}
    if _is_home(ctx, argv):
        # EnterAdventure's same-frame header/obstruction checks plus the
        # established single-line OCR box. Full-frame OCR misses this footer.
        tab = ctx.run_recognition('HomeAdventureText', argv.image)
        if tab and tab.hit:
            return 'home', {'box': list(tab.box)}, {}
    category = SURVEYS[module] if module in SURVEYS else '露娜之塔|特别关卡'
    if _one(words, category, _HEADER):
        button = _one(words, stage_pat, _BODY)
        if module in SURVEYS and not button:
            rows = sorted(_matches(words, SURVEYS[module]+r'关卡\d{1,3}级', (735,123,1195,580)),
                          key=lambda row: row['box'][1])
            levels = [int(re.search(r'(\d+)级$', row['text']).group(1)) for row in rows]
            if (len(rows) >= 2 and all(a > b for a,b in zip(levels, levels[1:]))
                    and len(_matches(words, '剩余挑战次数', (795,130,1100,599))) >= 2):
                if stage < levels[-1] or stage > levels[0]:
                    down = stage < levels[-1]
                    anchor = rows[-1] if down else rows[0]
                    return 'scroll_list', anchor, dict(levels=levels, down=down)
        if button:
            # Only select a list entry. A partially read detail or a dimmed
            # dialog must never be reinterpreted as another navigation step.
            if module == TOWER:
                list_evidence = _one(words, r'塔顶|\d{1,4}层|楼层一览', _BODY)
            else:
                rows = _matches(words, SURVEYS[module] + r'关卡\d{1,3}级', _BODY)
                list_evidence = len({r['text'] for r in rows}) > 1 or _one(words, '关卡选择', _BODY)
            if list_evidence and not _counter(words):
                return 'stage_list', button, {}
    return None


def navigate(port):
    previous = None
    seen = set()
    for _ in range(18):
        def inspect(context, argv):
            page = _page(port, context, argv, port.words(context, argv))
            if page and (page[0], tuple(page[2].get('levels', ()))) != previous:
                name, word, state = page
                return word['box'], dict(page=name, **state)

        observed = port.frame(inspect)
        page = observed['page']
        if page == 'tower_summit':
            raise RuntimeError('当前塔顶战画面未能确认回廊入口，未发起挑战')
        if page in ('closed', 'stage'):
            return observed
        key = page, tuple(observed.get('levels', ()))
        if key in seen:
            raise RuntimeError('optional_sweep_navigation_loop')

        def click(context, argv):
            current = inspect(context, argv)
            return current if current and (current[1]['page'], tuple(current[1].get('levels', ()))) == key else None

        if page == 'scroll_list':
            port.scroll_list(click, down=observed['down'])
        else:
            port.frame(click, click=True)
        previous = key
        seen.add(key)
    raise RuntimeError('optional_sweep_navigation_incomplete')


def read_stage(port):
    def guard(context, argv):
        words = port.words(context, argv)
        state = stage_frame(words, port.module, port.stage, argv.image)
        if state:
            word = _one(words, _stage_pattern(port.module, port.stage))
            return word['box'], state
    return port.frame(guard)


def return_home(port):
    """Dismiss only a positively identified quest detail, then use the home tab."""
    previous = None
    for _ in range(4):
        def observe(context, argv):
            words = port.words(context, argv)
            if _matches(words, _DIALOG):
                return None
            if _is_home(context, argv):
                return [693,685,53,33], dict(page='home')
            detail = stage_frame(words, port.module, port.stage, argv.image)
            close = _one(words, '取消|关闭', _BODY)
            if detail and close:
                return close['box'], dict(page='close_detail')
            if _matches(words, r'确认|确定|取消|关闭', _BODY):
                return None
            category = SURVEYS.get(port.module, '露娜之塔|特别关卡')
            location = (_special(words) or detail or _one(words, category, _HEADER)
                        or _hit(context, argv, 'ActivityAdventureGuard'))
            tab = _one(words, '我的主页', _HOME)
            if location and tab:
                return tab['box'], dict(page='home_tab')

        def progressed(context, argv):
            observed = observe(context, argv)
            return observed if observed and observed[1]['page'] != previous else None

        observed = port.frame(progressed)
        page = observed['page']
        if page == 'home':
            return observed

        def click(context, argv):
            current = observe(context, argv)
            return current if current and current[1]['page'] == page else None

        port.frame(click, click=True)
        previous = page
    raise RuntimeError('optional_sweep_return_home_incomplete')
