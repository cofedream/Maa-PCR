"""Maa-only bounded client reconnect after the protocol handoff.

Only the independently recognized 6002 dialog is actionable. Returning to the
login title is navigation, never proof that the game session/account is restored.
The owner must observe the subsequent pages before supplying any more input.
"""
import re,math
from pcr_maa import native


def reconnect_pipeline():
    def label(text,roi):
        return dict(recognition='OCR',expected=text,roi=roi,threshold=.8,
                    action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)
    return {
        'LabyDisconnectTitle':label('^错误提示$',[560,172,180,48]),
        'LabyDisconnectCode6002':label(r'^错误代码[:：]\s*6002$',[450,302,400,42]),
        'LabyDisconnectMessage':label('^连接中断[。.]?$',[480,337,320,42]),
        'LabyDisconnectReturnMessage':label('^回到标题界面[。.]?$',[440,365,410,42]),
        'LabyDisconnectReturnLabel':label('^返回标题$',[505,467,280,62]),
        'LabyDisconnect6002Observed':dict(recognition='And',all_of=[
            'LabyDisconnectTitle','LabyDisconnectCode6002','LabyDisconnectMessage',
            'LabyDisconnectReturnMessage','LabyDisconnectReturnLabel'],
            action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0),
        'LabyDisconnectReturnTitleOnce':dict(recognition='And',all_of=[
            'LabyDisconnectReturnLabel','LabyDisconnectTitle','LabyDisconnectCode6002',
            'LabyDisconnectMessage','LabyDisconnectReturnMessage'],
            action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250,
            pre_delay=0,post_delay=0),
    }


def observe_disconnect(tasker):
    return native.run(tasker,'LabyDisconnect6002Observed')


def title_pipeline(expected_player_id):
    if type(expected_player_id) is not int or not 0<expected_player_id<=2**63-1:
        raise ValueError('account_identity_required')
    def label(text,roi):
        return dict(recognition='OCR',expected=text,roi=roi,threshold=.8,
                    action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)
    identifier=r'^ID[:：]\s*'+r'\s*'.join(str(expected_player_id))+'$'
    nodes=reconnect_pipeline()
    nodes.update({
        'LabyClientTitleLogo':label('公主.*连结',[400,442,470,105]),
        'LabyClientTitleStartLabel':label(r'^(点击屏幕\s*)?开始游戏$',[435,633,430,59]),
        'LabyClientTitleVersion':label(r'^Ver[:：]\s*[0-9]+\.[0-9]+\.[0-9]+$',[1110,632,170,37]),
        'LabyClientTitlePlayerId':label(identifier,[1065,683,215,36]),
        'LabyClientTitleObserved':dict(recognition='And',all_of=[
            'LabyClientTitleLogo','LabyClientTitleStartLabel','LabyClientTitleVersion','LabyClientTitlePlayerId'],
            action='DoNothing',next=[],timeout=5000,pre_delay=0,post_delay=0),
        'LabyClientTitleStartOnce':dict(recognition='And',all_of=[
            'LabyClientTitleStartLabel','LabyClientTitleLogo','LabyClientTitleVersion','LabyClientTitlePlayerId'],
            action='Click',target=True,max_hit=1,next=[],timeout=5000,rate_limit=250,pre_delay=0,post_delay=0),
    })
    return nodes


def title_player_id(receipt):
    if type(receipt) is not dict or receipt.get('native_ok') is not True:raise ValueError('game_account_unknown')
    nodes=receipt.get('nodes')
    if type(nodes) is not list or any(type(n) is not dict for n in nodes):raise ValueError('game_account_unknown')
    selected=[n for n in nodes if n.get('name')=='LabyClientTitleObserved']
    if len(selected)!=1 or selected[0].get('completed') is not True:raise ValueError('game_account_unknown')
    raw=selected[0].get('recognition')
    if type(raw) is not list or any(type(r) is not dict for r in raw):raise ValueError('game_account_unknown')
    rows=[r for r in raw if r.get('name')=='LabyClientTitlePlayerId']
    if len(rows)!=1 or type(rows[0].get('detail')) is not dict:raise ValueError('game_account_unknown')
    words=rows[0]['detail'].get('filtered')
    if type(words) is not list or len(words)!=1 or type(words[0]) is not dict:raise ValueError('game_account_unknown')
    word=words[0];score=word.get('score');box=word.get('box');text=word.get('text')
    if type(score) not in (int,float) or not math.isfinite(score) or not .8<=score<=1:raise ValueError('game_account_unknown')
    if (type(box) is not list or len(box)!=4 or any(type(n) is not int for n in box)
        or box[0]<1065 or box[1]<683 or box[2]<=0 or box[3]<=0
        or box[0]+box[2]>1280 or box[1]+box[3]>719 or type(text) is not str):raise ValueError('game_account_unknown')
    match=re.fullmatch(r'ID[:：]([0-9]+)',re.sub(r'\s+','',text))
    if match is None or not 0<int(match[1])<=2**63-1:raise ValueError('game_account_unknown')
    return int(match[1])


def notice_pipeline():
    def label(text,roi):
        return dict(recognition='OCR',expected=text,roi=roi,threshold=.8,
                    action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)
    return {
        'LabyClientNoticeTitle':label('^通知$',[570,35,170,48]),
        'LabyClientNoticeUpdates':label('^更新信息$',[270,89,185,42]),
        'LabyClientNoticeCalendar':label('^活动日历$',[865,89,205,42]),
        'LabyClientNoticeCloseLabel':label('^关闭$',[505,610,285,63]),
        'LabyClientNoticeCloseOnce':dict(recognition='And',all_of=[
            'LabyClientNoticeCloseLabel','LabyClientNoticeTitle',
            'LabyClientNoticeUpdates','LabyClientNoticeCalendar'],
            action='Click',target=True,max_hit=1,next=[],timeout=3000,rate_limit=250,
            pre_delay=0,post_delay=0),
    }


def resume_home_pipeline():
    return {
        'LabyClientActiveRunBadge':dict(recognition='OCR',expected='^挑战中$',roi=[720,341,155,48],
            threshold=.8,action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0),
        'LabyClientResumeHomeObserved':dict(recognition='And',all_of=[
            'LabyHomeTitle','LabyHomeDepart','LabyHomeTasks','LabyClientActiveRunBadge'],
            action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0),
        'LabyClientResumeHomeDepartOnce':dict(recognition='And',all_of=[
            'LabyHomeDepart','LabyHomeTitle','LabyHomeTasks','LabyClientActiveRunBadge'],
            action='Click',target=True,max_hit=1,next=['LabyMapObserved'],timeout=5000,rate_limit=250,
            pre_delay=0,post_delay=0),
    }
