"""Battle HUD settings, explicit paused recovery and bounded result reading."""
def battle_pipeline(stage='红焰深域 7-3'):
    from .guide_identity import SOURCE_HASHES
    if stage not in SOURCE_HASHES:
        raise ValueError('guide_source_stage_unsupported')
    area, level = stage.split()[-1].split('-')
    def ocr(text, roi):
        return dict(recognition='OCR', expected=text, roi=roi, threshold=.8,
                    action='DoNothing', next=[], pre_delay=0, post_delay=0)

    def group(names, *, click=False):
        node = dict(recognition='And', all_of=names, action='Click' if click else 'DoNothing',
                    target=True, next=[], timeout=5000, pre_delay=0, post_delay=200 if click else 0)
        if click:
            node['max_hit'] = 1
        return node

    nodes = {
        'GTPauseTitle': ocr('^主菜单$', [540,92,190,55]),
        'GTPausedStage': ocr(rf'^红焰深域\s*{area}\s*[-－—]\s*{level}$', [775,157,170,42]),
        'GTPaused': group(['GTPauseTitle','GTPausedStage']),
        'GTPauseReturn': ocr('^返回$', [365,550,165,55]),
        'GTSetOn': ocr('(?i)^SET\\s*开启$', [355,455,178,45]),
        'GTSetOff': ocr('(?i)^SET\\s*关闭$', [355,455,178,45]),
        'GTSetDisabled': ocr('(?i)^SET\\s*无效$', [355,455,178,45]),
        'GTAutoOn': ocr('(?i)^AUTO\\s*开启$', [550,455,180,45]),
        'GTAutoOff': ocr('(?i)^AUTO\\s*关闭$', [550,455,180,45]),
        'GTBattleMenu': ocr('^菜单$', [1130,10,145,55]),
        # shortcut: these stages start at 1:30; wait for the clock to move before clicking the menu.
        'GTBattleTimer': ocr(r'^(?:1\s*[:：]\s*[0-2][0-9]|0\s*[:：]\s*[0-5][0-9])$', [1060,12,70,45]),
        'GTPauseBattle': group(['GTBattleMenu','GTBattleTimer'],click=True),
        'GTCnDefeatTitle': ocr('^战斗失败$', [470,5,340,115]),
        'GTCnDamageReport': ocr('^伤害报告$', [860,20,235,60]),
        'GTReturnDeepLabel': ocr('^前往深域关卡$', [945,620,285,80]),
        'GTRewardsWin': ocr('(?i)^WIN!?$', [470,5,350,145]),
    }
    for i, x in enumerate((432,548,664,780,896), 1):
        nodes[f'GTSet{i}On'] = dict(recognition='TemplateMatch', template='guide-trial/set-on.png',
            roi=[x,245,55,55], threshold=.88, action='DoNothing', next=[], pre_delay=0, post_delay=0)
    nodes['GTAllSet'] = group(['GTPaused','GTSetOn'] + [f'GTSet{i}On' for i in range(1,6)])
    nodes['GTFullSettings'] = group(['GTAllSet','GTAutoOn'])
    for name, state in (('GTEnableSet','GTSetOff'),('GTDisableSet','GTSetOn'),
                        ('GTEnableSetFromDisabled','GTSetDisabled'),('GTEnableAuto','GTAutoOff')):
        nodes[name] = group([state,'GTPaused'],click=True)
    nodes['GTResumeBattle'] = group(['GTPauseReturn','GTFullSettings'],click=True)
    nodes['GTAbandonLabel'] = ocr('^放弃$', [745,545,180,65])
    nodes['GTAbandonBattle'] = group(['GTAbandonLabel','GTPaused'], click=True)
    nodes['GTAbandonTitle'] = ocr('^放弃关卡$', [510,170,260,70])
    nodes['GTAbandonText'] = ocr('^确定要退出吗[？?]?$', [440,385,400,55])
    nodes['GTAbandonCancelLabel'] = ocr('^取消$', [350,460,285,80])
    nodes['GTAbandonConfirmLabel'] = ocr('^放弃$', [650,460,285,80])
    nodes['GTAbandonDialog'] = group(['GTAbandonTitle','GTAbandonText','GTAbandonCancelLabel','GTAbandonConfirmLabel'])
    nodes['GTAbandonConfirm'] = group(['GTAbandonConfirmLabel','GTAbandonDialog'], click=True)
    nodes['GTDefeat'] = group(['GTCnDefeatTitle','GTCnDamageReport','GTReturnDeepLabel'])
    nodes['GTDefeatEnd'] = group(['GTReturnDeepLabel','GTDefeat'],click=True)
    nodes['GTWinObserved'] = group(['GTRewardsWin','LabyNormalBattleDamageReport','LabyNormalBattleNext'])
    nodes['GTWinNext'] = group(['LabyNormalBattleNext','GTRewardsWin','LabyNormalBattleDamageReport'],click=True)
    nodes['GTRewardsObserved'] = group(['GTRewardsWin','LabyNormalBattleObtained','LabyNormalBattleNext'])
    nodes['GTRewardsNext'] = group(['LabyNormalBattleNext','GTRewardsWin','LabyNormalBattleObtained'],click=True)
    for page, action, labels in (
            ('GTLimitedShopObserved','GTLimitedShopCancel',
             ['ExecLimitedShopButton','ExecLimitedShopTitle','ExecLimitedShopNotice','ExecLimitedShopInstruction']),
            ('GTLevelUpObserved','GTLevelUpDismiss', ['ExecLevelUpButton','ExecLevelUpTitle','ExecLevelUpBody'])):
        # Reuse daily recognition, while keeping the successor in this stage's return flow.
        nodes[page] = group(labels)
        nodes[action] = dict(group(labels, click=True), post_delay=600)
    for name,mode in (('GTBattleReadSettings','battle_read'),('GTBattleSettingsConfirmed','battle_read'),
                      ('GTBattleEnableAuto','battle_auto'),('GTBattleEnableAllSet','battle_all_set'),('GTBattleToggleSet','battle_set')):
        click = mode != 'battle_read'
        nodes[name] = dict(recognition='Custom',custom_recognition='GuideTrialCharacters',
            custom_recognition_param=dict(mode=mode),action='Click' if click else 'DoNothing',target=True,
            next=['GTBattleSettingsConfirmed'] if click else [],timeout=4000,pre_delay=0,post_delay=200 if click else 0)
        if click:nodes[name]['max_hit']=1
    return nodes


def ensure_running_settings(port, desired=(True,True,True,True,True), *, initial=None, set_by_unit=None):
    if len(desired) != 5 or any(type(value) is not bool for value in desired):
        raise ValueError('guide_trial_set_selection_invalid')
    desired = list(desired)
    actions = []
    state = port.read_battle_settings(initial)
    ids = state.get('unit_ids')
    if set_by_unit is not None:
        if (type(set_by_unit) is not dict or len(set_by_unit) != 5 or any(type(v) is not bool for v in set_by_unit.values())
                or type(ids) is not list or len(ids) != 5 or any(type(uid) is not int for uid in ids)
                or len(set(ids)) != 5 or {str(uid) for uid in ids} != set(set_by_unit)):
            raise RuntimeError('guide_trial_battle_identity_unknown')
        desired = [set_by_unit[str(uid)] for uid in ids]
    if type(state['auto']) is not bool or len(state['sets']) != 5 or any(type(value) is not bool for value in state['sets']):
        raise RuntimeError('guide_trial_settings_unknown')
    def change(node, expected, slot=None):
        nodes = {'GTBattleSettingsConfirmed':dict(custom_recognition_param=dict(mode='battle_read',auto=True,sets=expected))}
        if slot is not None:
            nodes['GTBattleToggleSet'] = dict(custom_recognition_param=dict(mode='battle_set',slot=slot,previous=state['sets'][slot]))
        if set_by_unit is not None:
            nodes['GTBattleSettingsConfirmed']['custom_recognition_param']['unit_ids'] = ids
            params = nodes.setdefault(node, dict(custom_recognition_param=dict(mode={
                'GTBattleEnableAuto':'battle_auto', 'GTBattleEnableAllSet':'battle_all_set', 'GTBattleToggleSet':'battle_set'}[node])))['custom_recognition_param']
            params.update(unit_ids=ids, sets=state['sets'], auto=state['auto'])
        if port.tasker.resource.override_pipeline(nodes) is False:
            raise RuntimeError('guide_trial_settings_pipeline_rejected')
        reading = port.read_battle_settings(port.run(node,click=True))
        if reading['auto'] is not True or reading['sets'] != expected or set_by_unit is not None and reading.get('unit_ids') != ids:
            raise RuntimeError('guide_trial_settings_readback_unknown')
        actions.append(dict(node=node, unit_id=ids[slot] if slot is not None and ids else None,
            previous=state['sets'][slot] if slot is not None else None,
            target=expected[slot] if slot is not None else None))
        return reading
    if state['auto'] is False:
        state = change('GTBattleEnableAuto',state['sets'])
    if state['auto'] is not True or any(type(value) is not bool for value in state['sets']):
        raise RuntimeError('guide_trial_settings_unknown')
    if all(desired) and not any(state['sets']) and state['set_button'] is False:
        state = change('GTBattleEnableAllSet',desired)
    else:
        for i,target in enumerate(desired):
            if state['sets'][i] is not target:
                expected = state['sets'].copy();expected[i] = target
                state = change('GTBattleToggleSet',expected,i)
    result = dict(source='battle_hud',auto=True,set_slots=state['sets'],observation=state,actions=actions)
    if set_by_unit is not None:
        result.update(unit_ids=ids, set_by_unit={str(uid): value for uid,value in zip(ids,state['sets'])})
    return result


def confirm_paused_settings(port):
    port.wait(['GTPaused'], seconds=5)
    actions = {'GTSetOff':'GTEnableSet', 'GTSetDisabled':'GTEnableSetFromDisabled', 'GTSetOn':'GTDisableSet'}
    state = port.choose(['GTAllSet', *actions])
    # The real CN menu has three modes; every click must produce a different observed mode.
    for _ in range(3):
        if state == 'GTAllSet':
            break
        if state not in actions:
            raise RuntimeError('guide_trial_set_state_unknown')
        port.run(actions[state], click=True)
        state = port.wait(['GTAllSet', *(name for name in actions if name != state)], seconds=5)
    if state != 'GTAllSet':
        raise RuntimeError('guide_trial_all_set_not_verified')
    state = port.choose(['GTAutoOn','GTAutoOff'])
    if state == 'GTAutoOff':
        port.run('GTEnableAuto', click=True)
    port.wait(['GTFullSettings'], seconds=5)
    settings = port.run('GTFullSettings')
    port.run('GTResumeBattle', click=True)
    return settings


def finish_battle(port, *, paused=False, initial=None, set_by_unit=None):
    if paused and set_by_unit is not None and not all(set_by_unit.values()):
        raise RuntimeError('guide_trial_mixed_set_paused_recovery_unsupported')
    port.tasker.resource.override_pipeline(battle_pipeline(getattr(port, 'stage', '红焰深域 7-3')))
    settings = confirm_paused_settings(port) if paused else ensure_running_settings(port,initial=initial,set_by_unit=set_by_unit)
    hit = port.wait(['GTWinObserved','GTDefeat',
                     'LabyDisconnect6002Observed','LabyClientTitleObserved'], seconds=180)
    if hit not in ('GTWinObserved','GTDefeat'):
        raise RuntimeError('guide_trial_battle_interrupted_result_unknown')
    result = port.run(hit)
    report = dict(outcome='victory' if hit == 'GTWinObserved' else 'defeat',
                result_verified=True, settings_verified=True, all_set=True if paused else all(settings['set_slots']), auto=True,
                settings_evidence=settings, result_evidence=result)
    if set_by_unit is not None:
        report['set_by_unit'] = dict(set_by_unit) if paused else settings['set_by_unit']
    return report


def return_to_stage(port):
    if port.tasker.resource.override_pipeline(battle_pipeline(getattr(port, 'stage', '红焰深域 7-3'))) is False:
        raise RuntimeError('guide_trial_return_pipeline_rejected')
    destinations = ['GTFireMap','GTStageDetail']
    actions = dict(GTWinObserved='GTWinNext', GTDefeat='GTDefeatEnd',
        GTRewardsObserved='GTRewardsNext', GTLimitedShopObserved='GTLimitedShopCancel',
        GTLevelUpObserved='GTLevelUpDismiss')
    hit = port.choose([*destinations, *actions])
    while hit not in destinations:
        if hit not in actions:
            raise RuntimeError('guide_trial_return_page_unknown')
        port.run(actions.pop(hit), click=True)
        # An unchanged page is no evidence of navigation; never resend its action.
        actions.pop('GTWinObserved', None)
        actions.pop('GTDefeat', None)
        hit = port.wait([*destinations, *actions], seconds=20)
    if hit == 'GTStageDetail':
        port.run('GTDetailCancel', click=True)
        port.wait(['GTFireMap'], seconds=10)


def prepare_retry(port, plan):
    return_to_stage(port)
    port.run('GTOpenStage', click=True)
    port.wait(['GTStageDetail'])
    port.run('GTChallenge', click=True)
    port.wait(['GTFormation'])
    port.form_team()
