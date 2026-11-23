"""Turn the in-run Labyrinth character-join animation off through the game UI.

`animation_setting.py` first tries the client's local PlayerPrefs.  That path
needs ADB root and a client build whose encrypted values still match the pinned
sample.  When it cannot confirm "不播放" the run still has to work, so this module
drives the same option through the game UI instead: 黎明界迷宫地图右上角「菜单」
→「设定」→「角色加入动画」→「不播放」.

The client has no public API for those pages, so every page/anchor below is an
OCR or template recognition calibrated from a real 1280x720 frame.  No
coordinate is guessed: while `CALIBRATION` is incomplete `available()` is False
and the caller keeps the scene runtime's 30s character-acquisition budget.

Safety contract
- a page is identified by one complete action-free anchor set on one frame; a
  click node is an And of that page guard and its control, so a frame change can
  only turn the tap into a proven-unsent input, never a blind tap;
- after the toggle the option state is re-read on a fresh frame and only
  "不播放" leaves the settings page;
- the flow starts and ends on a fresh `LabyMapObserved` frame; anything else
  readable is waited on, and an unreadable page stops the batch.
"""
from .. import native
from .batch import BatchStopped
from .timing import Timings

DEFAULT_TIMEOUT_SECONDS=60
# The pref/root attempt did not confirm "不播放"; only these statuses need the
# in-game fallback.  "already_off"/"fixed" are confirmed, "stopped" is already
# the owner stopping, and "adb_missing" cannot automate Maa either.
FALLBACK_STATUSES=frozenset({'no_root','unknown','write_failed','error',
    'stop_unverified','stop_failed'})

# Calibrated from real client frames before this fallback is allowed to run.
# Schema (all coordinates for the 1280x720 Maa frame):
#   'menu_anchors':     {name: raw node} extra anchors that prove the opened panel
#   'menu_guard':       names of additional shared pipeline nodes for that panel
#   'settings_guard':   names of additional shared pipeline nodes for that page
#   'settings_anchors': {name: raw node} extra anchors for the settings page
#   'menu_entry':       raw OCR/template node for the 「设定」entry
#   'option':           raw node for the 「角色加入动画」row
#   'state_on'/'state_off': raw nodes for the two printed option states
#   'settings_close':   raw node for closing the settings page, or {'target':[...]}
#   'menu_close':       raw node for leaving the panel, or {'target':[...]}
#   'targets':          optional explicit click points per role; absent means
#                       the recognised control box itself
#   'threshold':        OCR/template threshold used for the new nodes
# Sampled 2026-11-21, CN Bilibili client (MuMu 12, 1280x720): 地图右上角「菜单」
# 打开右侧「详情」面板（详情/指南/玩法/帮助 + 设定），设定弹窗两个单选项。
# The shared LabyMapObserved guard still matches the open panel (the map is not
# dimmed), so page detection below checks settings/menu before map.
CALIBRATION={
    'menu_anchors':{
        'LabyJoinAnimationMenuDetail':{'expected':r'^.{0,2}详情$','roi':[920,20,180,60]},
        'LabyJoinAnimationMenuGuide':{'expected':r'^指南$','roi':[955,175,120,45]},
        'LabyJoinAnimationMenuHelp':{'expected':r'^帮助$','roi':[1045,270,120,40]},
    },
    'menu_guard':(),
    'menu_entry':{'expected':r'^设定$','roi':[1045,122,110,42]},
    'settings_guard':(),
    'settings_anchors':{
        'LabyJoinAnimationSettingsTitle':{'expected':r'^设定$','roi':[560,170,160,55]},
        'LabyJoinAnimationSettingsPlay':{'expected':r'^播放$','roi':[455,345,120,60]},
        'LabyJoinAnimationSettingsStop':{'expected':r'^不播放$','roi':[700,345,130,60]},
    },
    'option':{'expected':r'^设定是否播放角色加入动画[。.]?$','roi':[470,272,340,50]},
    # The two radio circles: the same selected-dot template, one ROI per side.
    'state_on':{'template':'labyrinth/join_animation/radio_selected.png',
        'roi':[415,345,60,60],'threshold':.9},
    'state_off':{'template':'labyrinth/join_animation/radio_selected.png',
        'roi':[658,345,60,60],'threshold':.9},
    'settings_close':{'expected':r'^关闭$','roi':[545,460,200,75]},
    'menu_close':{'target':[1217,71,1,1]},
    # Click points verified on the same client frames (panel opens/closes with
    # the same 菜单 button; the 设定 tile and both radio circles are big targets).
    'targets':{'menu':[1217,71,1,1],'menu_entry':[1093,111,1,1],
        'toggle':[690,380,1,1],'settings_close':[637,494,1,1]},
    'threshold':.8,
    'timeout_seconds':60,
}


def needs_ui_fallback(status):
    """True when the pref/root attempt left "不播放" unconfirmed."""
    return status in FALLBACK_STATUSES


def available(calibration=None):
    value=CALIBRATION if calibration is None else calibration
    if type(value) is not dict:return False
    return all(value.get(key) is not None for key in (
        'menu_entry','option','state_on','state_off','settings_close','menu_close'))


def _defaults(node):
    value=dict(node)
    value.setdefault('pre_delay',0);value.setdefault('post_delay',0)
    value.setdefault('timeout',1000);value.setdefault('next',[])
    value['action']='DoNothing';value['next']=[]
    return value


def _and(names,**extra):
    node=dict(recognition='And',all_of=list(names),action='DoNothing',next=[],
        timeout=3000,pre_delay=0,post_delay=0)
    node.update(extra)
    return node


def _click(guard,target):
    return _and(guard,action='Click',target=True if target is None else target,
        max_hit=1,rate_limit=250)


def _control_nodes(calibration):
    """New recognition/click nodes for one calibration; None when incomplete."""
    if not available(calibration):return None
    c=calibration;threshold=c.get('threshold',.8)
    def control(spec):
        node=_defaults(spec)
        if 'recognition' not in node:
            if 'expected' in spec:
                node['recognition']='OCR';node['expected']=spec['expected']
                node['roi']=list(spec['roi']);node.setdefault('threshold',threshold)
            elif 'template' in spec:
                node.update(recognition='TemplateMatch',template=spec['template'],
                    roi=list(spec['roi']),method=spec.get('method',5),
                    threshold=spec.get('threshold',threshold),count=1)
            else:
                raise BatchStopped('join_animation_calibration_incomplete')
        return node
    nodes={}
    menu_anchors=dict(c.get('menu_anchors') or {})
    for name,spec in menu_anchors.items():menu_anchors[name]=control(spec)
    nodes.update(menu_anchors)
    menu_entry=control(c['menu_entry']);nodes['LabyJoinAnimationMenuEntry']=menu_entry
    option=control(c['option']);nodes['LabyJoinAnimationOption']=option
    nodes['LabyJoinAnimationStateOn']=control(c['state_on'])
    nodes['LabyJoinAnimationStateOff']=control(c['state_off'])
    extra=dict(c.get('settings_anchors') or {})
    for name,spec in extra.items():extra[name]=control(spec)
    nodes.update(extra)
    # Self-contained interruption anchors: session date/disconnect nodes are only
    # installed during a real client session, so do not depend on them here.
    from .scene import _OCR
    for name,key,label in (('LabyJoinAnimationDate','DATE','date_changed'),
            ('LabyJoinAnimationDisconnect','DISCONNECT','disconnected')):
        pattern,roi=_OCR[key]
        nodes[name]=control({'expected':pattern,'roi':list(roi),'threshold':.8})
    menu_guard=[*menu_anchors,*(c.get('menu_guard') or ()),'LabyJoinAnimationMenuEntry']
    settings_guard=[*(c.get('settings_guard') or ()),*extra,
        'LabyJoinAnimationOption']
    targets=dict(c.get('targets') or {})
    settings_close=c['settings_close']
    if type(settings_close) is dict and 'target' in settings_close and 'recognition' not in settings_close:
        settings_close_node=None;close_target=settings_close['target']
    else:
        settings_close_node='LabyJoinAnimationSettingsClose'
        nodes[settings_close_node]=control(settings_close);close_target=targets.get('settings_close')
    nodes['LabyJoinAnimationMenuObserved']=_and(menu_guard)
    nodes['LabyJoinAnimationSettingsObserved']=_and(settings_guard)
    nodes['LabyJoinAnimationStateObserved']=dict(recognition='Or',
        any_of=['LabyJoinAnimationStateOn','LabyJoinAnimationStateOff'],
        action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    # Order matters: the map stays visible behind the opened panel, so an open
    # panel must be classified before MAP, never as MAP.
    nodes['LabyJoinAnimationPageObserved']=dict(recognition='Or',any_of=[
        'LabyJoinAnimationSettingsObserved','LabyJoinAnimationMenuObserved',
        'LabyMapObserved','LabyJoinAnimationDate','LabyJoinAnimationDisconnect'],
        action='DoNothing',next=[],timeout=3000,pre_delay=0,post_delay=0)
    nodes['LabyJoinAnimationMenuOpenOnce']=_click(['LabyMapObserved','LabyMapMenu'],
        targets.get('menu'))
    nodes['LabyJoinAnimationSettingsOpenOnce']=_click(menu_guard,
        targets.get('menu_entry'))
    nodes['LabyJoinAnimationToggleOnce']=_click(settings_guard+['LabyJoinAnimationStateOn'],
        targets.get('toggle'))
    nodes['LabyJoinAnimationSettingsCloseOnce']=_click(
        settings_guard+([] if settings_close_node is None else [settings_close_node]),
        close_target)
    menu_close=c['menu_close']
    if type(menu_close) is dict and 'target' in menu_close and 'recognition' not in menu_close:
        menu_close_target=menu_close['target']
    else:
        nodes['LabyJoinAnimationMenuClose']=control(menu_close)
        menu_close_target=targets.get('menu_close')
    nodes['LabyJoinAnimationMenuCloseOnce']=_click(menu_guard,menu_close_target)
    return nodes


def _root(receipt,node,*,require_hit=True):
    """The single node of this task; a miss is only allowed when asked for.

    `require_hit=False` lets a page/state reader treat "task ran, nothing
    matched" as an unknown frame it can wait through, instead of a broken
    readback.  A different node or broken native evidence always raises.
    """
    if (type(receipt) is not dict or receipt.get('native_ok') is not True
            or receipt.get('tasker_inited') is not True
            or type(receipt.get('task_id')) is not int or receipt['task_id']<=0):
        raise BatchStopped('join_animation_readback_unknown')
    nodes=receipt.get('nodes')
    if (type(nodes) is not list or len(nodes)!=1 or type(nodes[0]) is not dict
            or nodes[0].get('name')!=node):
        raise BatchStopped('join_animation_readback_unknown')
    if require_hit and nodes[0].get('completed') is not True:
        raise BatchStopped('join_animation_readback_unknown')
    return nodes[0]


def _matched(root):
    """Names of the Or/And alternatives that matched this frame.

    Maa's Or/And detail keeps every alternative and only the matched row has a
    box.  A different shape is an unknown frame, never a partial accept.
    """
    raw=root.get('recognition')
    if type(raw) is not list:raise BatchStopped('join_animation_readback_unknown')
    names=[]
    for row in raw:
        if type(row) is not dict or type(row.get('name')) is not str:
            raise BatchStopped('join_animation_readback_unknown')
        if row.get('box') is not None:names.append(row['name'])
    return names


class MaaJoinAnimationSetting:
    """Calibrated 菜单→设定→角色加入动画 flow; zero input when uncalibrated."""

    def __init__(self,tasker,stamp,*,timings=None,calibration=None):
        self.tasker,self.stamp=tasker,stamp
        self.timings=timings or Timings()
        self.calibration=dict(calibration) if type(calibration) is dict else calibration
        if self.calibration is None:self.calibration=CALIBRATION

    def available(self):
        return available(self.calibration)

    def ensure_off(self,batch,*,enter_id):
        """Return 'toggled' or 'already_off'; never guess a coordinate."""
        if not available(self.calibration):return 'skipped_uncalibrated'
        if type(enter_id) is not int or enter_id<=0:
            raise BatchStopped('join_animation_identity_required')
        timeout=self.calibration.get('timeout_seconds',DEFAULT_TIMEOUT_SECONDS)
        started=native.active_now()
        token=native.RUN_DEADLINE.set(min(native.RUN_DEADLINE.get(),started+timeout))
        try:
            nodes=_control_nodes(self.calibration)
            if not self.tasker.resource.override_pipeline(nodes):
                raise BatchStopped('join_animation_pipeline_unavailable')
            return self._run(batch,enter_id)
        finally:
            native.RUN_DEADLINE.reset(token)

    def _run(self,batch,enter_id):
        fields=dict(enter_id=enter_id,phase='join_animation_setting')
        page=self._page(batch)
        if page!='map':raise BatchStopped('join_animation_start_page_unknown')
        self._input(batch,'join_animation_menu_open',fields,
            'LabyJoinAnimationMenuOpenOnce')
        if self._wait(batch,{'menu'},'join_animation_settings_page_unknown')!='menu':
            raise BatchStopped('join_animation_settings_page_unknown')
        self._input(batch,'join_animation_settings_open',fields,
            'LabyJoinAnimationSettingsOpenOnce')
        if self._wait(batch,{'settings'},'join_animation_settings_page_unknown')!='settings':
            raise BatchStopped('join_animation_settings_page_unknown')
        state=self._state(batch);result='already_off'
        if state=='on':
            self._input(batch,'join_animation_toggle',fields,
                'LabyJoinAnimationToggleOnce')
            if self._wait(batch,{'settings'},'join_animation_setting_readback_unknown')!='settings':
                raise BatchStopped('join_animation_setting_readback_unknown')
            if self._state(batch)!='off':
                raise BatchStopped('join_animation_setting_readback_unknown')
            result='toggled'
        self._input(batch,'join_animation_settings_close',fields,
            'LabyJoinAnimationSettingsCloseOnce')
        page=self._wait(batch,{'menu','map'},'join_animation_return_unknown')
        if page=='menu':
            self._input(batch,'join_animation_menu_close',fields,
                'LabyJoinAnimationMenuCloseOnce')
            page=self._wait(batch,{'map'},'join_animation_return_unknown')
        if page!='map':raise BatchStopped('join_animation_return_unknown')
        # The panel may still be sliding away on the frame that matched MAP;
        # require one more fresh MAP frame through the same bounded wait instead
        # of failing on a single transient frame.
        if self._wait(batch,{'map'},'join_animation_return_unknown')!='map':
            raise BatchStopped('join_animation_return_unknown')
        return result

    def _read(self,node,*,require_hit=True):
        with self.timings.measure('page_wait'):
            receipt=native.run(self.tasker,node)
        return _root(receipt,node,require_hit=require_hit)

    def _matched(self,root):
        return _matched(root)

    def _page(self,batch):
        batch.checkpoint()
        root=self._read('LabyJoinAnimationPageObserved',require_hit=False)
        if root.get('completed') is not True:return 'unknown'
        names=self._matched(root)
        if 'LabyJoinAnimationDate' in names:raise BatchStopped('date_changed')
        if 'LabyJoinAnimationDisconnect' in names:raise BatchStopped('disconnected')
        # An opened panel keeps the map visible, so it wins over MAP here.
        for page,node in (('settings','LabyJoinAnimationSettingsObserved'),
                ('menu','LabyJoinAnimationMenuObserved'),('map','LabyMapObserved')):
            if node in names:return page
        return 'unknown'

    def _wait(self,batch,expected,reason):
        while True:
            batch.checkpoint()
            try:page=self._page(batch)
            except RuntimeError as error:
                if error.args not in (('Native task exceeded 90 seconds or inspection deadline',),
                        ('Inspection deadline exceeded before task input',),
                        ('Context deadline exceeded after synchronous return; outcome unknown',)):
                    raise
                batch.checkpoint();raise BatchStopped(reason) from error
            if page in expected:return page
            if native.active_now()>=native.RUN_DEADLINE.get():
                raise BatchStopped(reason)
            self._pause(batch,reason)

    def recover_to_map(self,batch,*,enter_id):
        """Leave a calibrated menu/settings page after a safe-abort; no guessing.

        Only pages this module can fully recognise are touched again, so this
        never continues from an unknown frame.  Returns True only on a fresh
        map read.
        """
        fields=dict(enter_id=enter_id,phase='join_animation_recovery')
        for _ in range(4):
            batch.checkpoint()
            try:page=self._page(batch)
            except BatchStopped:return False
            if page=='map':return True
            if page=='settings':
                self._input(batch,'join_animation_settings_close',fields,
                    'LabyJoinAnimationSettingsCloseOnce')
            elif page=='menu':
                self._input(batch,'join_animation_menu_close',fields,
                    'LabyJoinAnimationMenuCloseOnce')
            else:return False
            if native.active_now()>=native.RUN_DEADLINE.get():return False
            self._pause(batch,'join_animation_return_unknown')
        batch.checkpoint()
        try:return self._page(batch)=='map'
        except BatchStopped:return False

    def _state(self,batch):
        batch.checkpoint()
        root=self._read('LabyJoinAnimationStateObserved',require_hit=False)
        if root.get('completed') is not True:
            raise BatchStopped('join_animation_setting_state_unknown')
        names=self._matched(root)
        on='LabyJoinAnimationStateOn' in names
        off='LabyJoinAnimationStateOff' in names
        if on==off:raise BatchStopped('join_animation_setting_state_unknown')
        return 'on' if on else 'off'

    def _input(self,batch,operation,fields,node):
        def send(oid):
            batch.checkpoint()
            with self.timings.measure('page_wait'):
                receipt=native.run(self.tasker,node,reset_hits=(node,))
            batch.checkpoint()
            root=_root(receipt,node);action=root.get('action')
            if (type(action) is not dict or action.get('name')!=node
                    or action.get('success') is not True):
                raise BatchStopped('join_animation_input_unknown')
            return self.stamp(operation_id=oid,**fields,native=receipt,
                input_native=receipt,input_action_verified=True)
        try:
            return batch.transaction(operation,fields,send)
        except BatchStopped as error:
            cause=error.__cause__
            receipt=cause.receipt if isinstance(cause,native.NativeInputNotSent) else {}
            # Only an affirmative "no input was sent" lets the caller re-read
            # the page; an unknown input still stops.
            if (error.args==('native_input_not_sent',)
                    and isinstance(cause,native.NativeInputNotSent)
                    and receipt.get('input_execution')=='not_sent'
                    and receipt.get('pre_input_validation_failed') is not True):
                return None
            raise

    def _pause(self,batch,reason):
        from .autoplay import _poll_pause
        _poll_pause(self.tasker,batch,'LabyJoinAnimationPollSpacing',reason)
