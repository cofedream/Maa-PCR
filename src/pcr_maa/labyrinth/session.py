"""Maa-only bounded client reconnect after the protocol handoff.

Only the independently recognized 6002 dialog is actionable. Returning to the
login title is navigation, never proof that the game session/account is restored.
The owner must observe the subsequent pages before supplying any more input.
"""
import re,math
from dataclasses import dataclass
import time
import xml.etree.ElementTree as ET
from pcr_maa import native
from .batch import BatchStopped
from .servers import get_server


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


def home_announcement_pipeline():
    """One sampled HOME popup: the activity-ending notice.

    ROIs come from an actual 1280x720 client frame (title word 556,43,166,28;
    前往活动 732,624,108,28; 关闭 465,623,57,30). Only this explicit title is
    closed, never an arbitrary dialog: the close needs all three anchors. The
    关闭 label keeps detector boxes so its actual word box is the click target,
    which stays inside that button and never reaches 前往活动.
    """
    def label(text,roi,*,only_rec=True):
        return dict(recognition='OCR',expected=text,roi=roi,threshold=.8,only_rec=only_rec,
                    action='DoNothing',next=[],timeout=1000,pre_delay=0,post_delay=0)
    return {
        'LabyClientAnnouncementTitle':label('^活动结束预告$',[531,33,216,48]),
        'LabyClientAnnouncementActivityLabel':label('^前往活动$',[700,600,175,70]),
        'LabyClientAnnouncementCloseLabel':label('^关闭$',[440,608,107,51],only_rec=False),
        'LabyClientAnnouncementCloseOnce':dict(recognition='And',all_of=[
            'LabyClientAnnouncementCloseLabel','LabyClientAnnouncementTitle',
            'LabyClientAnnouncementActivityLabel'],
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


def _prompt_bounds(value):
    match=re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',value or '')
    if match is None:raise ValueError('channel_window_invalid')
    bounds=tuple(map(int,match.groups()))
    if bounds[2]<=bounds[0] or bounds[3]<=bounds[1] or max(bounds)>16384:
        raise ValueError('channel_window_invalid')
    return bounds


@dataclass(frozen=True)
class ChannelPrompt:
    package: str
    target: tuple
    click: tuple
    screen: tuple
    ancestor_hops: int


def channel_window(xml,foreground,server='cn-bilibili'):
    """Read one Android window; no OCR or other channel supplies a login click."""
    selected=get_server(server)
    if foreground!=selected.package:raise ValueError('game_client_foreground_unknown')
    if type(xml) is not str or len(xml)>2*1024*1024 or '<!DOCTYPE' in xml.upper():
        raise ValueError('channel_window_invalid')
    start=xml.find('<hierarchy');end=xml.find('</hierarchy>',start)
    if start<0 or end<0:raise ValueError('channel_window_invalid')
    root=ET.fromstring(xml[start:end+12])
    children=list(root)
    if len(children)!=1:raise ValueError('channel_window_invalid')
    bounds=_prompt_bounds(children[0].get('bounds'))
    if bounds[:2]!=(0,0):raise ValueError('channel_window_invalid')
    width,height=bounds[2:]
    portrait=height>width
    if portrait:
        if not selected.transient_rotation:raise ValueError('game_capture_rotation_unsupported')
        return None,True
    if width*9!=height*16:raise ValueError('game_capture_size_changed')
    if selected.server_id!='cn-aligames':return None,False
    parents={child:parent for parent in root.iter() for child in parent}
    nodes=list(root.iter('node'))
    labels=lambda node:[re.sub(r'\s+','',node.get(key,'')) for key in ('text','content-desc')]
    if not any('登录历史' in label for node in nodes for label in labels(node)):return None,False
    targets=[node for node in nodes if '最近登录' in labels(node)]
    if len(targets)!=1:return None,True
    target=targets[0]
    if target.get('package')!=selected.package:return None,True
    target_box=_prompt_bounds(target.get('bounds'));owner=target;hops=0
    while owner.get('clickable')!='true' and hops<3:
        owner=parents.get(owner)
        hops+=1
        if owner is None or owner is children[0] or owner is root:break
    if (owner is None or owner is children[0] or owner is root
            or owner.get('clickable')!='true'
            or owner.get('package')!=selected.package
            or any(node is not owner and node.get('clickable')=='true' for node in owner.iter())):
        # The unique text itself is still safe for a gesture when no clickable owner exists.
        owner=target;hops=0
    click_box=_prompt_bounds(owner.get('bounds'))
    if not (click_box[0]<=target_box[0]<target_box[2]<=click_box[2]
            and click_box[1]<=target_box[1]<target_box[3]<=click_box[3]
            and click_box[2]<=width and click_box[3]<=height):return None,True
    return ChannelPrompt(selected.package,target_box,click_box,(width,height),hops),False


class ChannelPrompts:
    """Bounded SDK window handling owned by one game session."""
    def __init__(self,tasker,stamp,server):
        self.tasker,self.stamp,self.server=tasker,stamp,get_server(server)
        self.previous=None;self.last_poll=float('-inf');self.last_tap=float('-inf')
        self.taps=0;self.visible_until=0;self.rotation_started=None

    def _read(self,batch):
        from .app_version import controller_shell
        batch.checkpoint()
        controller=self.tasker.controller
        focus=controller_shell(controller,'dumpsys window windows',stopped=batch.stopped)
        matches=re.findall(r'mCurrentFocus=Window\{[^\r\n]*?\s([A-Za-z0-9_.]+)/[^\r\n]*?\}',focus)
        if len(matches)!=1:raise BatchStopped('game_client_foreground_unknown')
        if matches[0]!=self.server.package:raise BatchStopped('game_client_foreground_unknown')
        xml=controller_shell(controller,'uiautomator dump /dev/tty',stopped=batch.stopped)
        batch.checkpoint()
        try:return channel_window(xml,matches[0],self.server.server_id)
        except (ValueError,ET.ParseError):raise BatchStopped('channel_window_unavailable') from None

    def poll(self,batch,fields):
        if not self.server.channel:return False
        # Other channels have no SDK click rule or observed temporary rotation.
        if self.server.server_id not in ('cn-aligames','cn-vivo','cn-4399'):return False
        batch.checkpoint();now=native.active_now()
        if now-self.last_poll<.5:return now<self.visible_until
        self.last_poll=now
        try:match,blocked=self._read(batch)
        except BatchStopped:raise
        except Exception:raise BatchStopped('channel_window_unavailable') from None
        batch.checkpoint();now=native.active_now()
        if blocked:
            self.previous=None;self.visible_until=now+1.5
            if self.rotation_started is None:self.rotation_started=now
            if now-self.rotation_started>=120:raise BatchStopped('channel_login_timeout')
            return True
        self.rotation_started=None
        if match is None:
            self.previous=None
            return now<self.visible_until
        self.visible_until=now+1.5
        if match!=self.previous:
            self.previous=match;return True
        if self.taps>=3:raise BatchStopped('channel_login_prompt_exhausted')
        if now-self.last_tap<4:return True
        fresh,blocked=self._read(batch)
        batch.checkpoint()
        if blocked or fresh!=match:
            self.previous=None;return True
        width,height=match.screen;x1,y1,x2,y2=match.click
        target=[round(x1*1280/width),round(y1*720/height),
            max(1,round((x2-x1)*1280/width)),max(1,round((y2-y1)*720/height))]
        node='LabyChannelLoginPromptOnce'
        if not self.tasker.resource.override_pipeline({node:dict(recognition='DirectHit',
                action='Click',target=target,max_hit=1,next=[],timeout=1000,pre_delay=0,post_delay=0)}):
            raise BatchStopped('channel_login_pipeline_unavailable')
        intent=dict(fields,server=self.server.server_id,package=self.server.package,
            rule='aligames-login-history',prompt_target=target)
        def send(oid):
            batch.checkpoint()
            checked,blocked=self._read(batch)
            if blocked or checked!=match:raise BatchStopped('channel_login_prompt_changed')
            receipt=native.run(self.tasker,node,reset_hits=(node,))
            batch.checkpoint()
            rows=receipt.get('nodes',[]) if type(receipt) is dict else []
            if (receipt.get('native_ok') is not True or len(rows)!=1
                    or rows[0].get('name')!=node or rows[0].get('completed') is not True
                    or rows[0].get('action',{}).get('success') is not True):
                raise BatchStopped('channel_login_input_unknown')
            return self.stamp(operation_id=oid,**intent,input_action_verified=True,native=receipt,input_native=receipt)
        batch.transaction('client_channel_prompt',intent,send)
        self.taps+=1;self.last_tap=native.active_now();self.previous=None
        return True
