"""Maa port for source-bound single-stage trials, borrowed from the normal Start owner."""
import json
import time
from . import native
from .guide_trial import STAGE, correct_team, team_matches, planned_sets, settings_plan
from .labyrinth.game_session import game_session_pipeline
from .labyrinth.session import title_player_id
from .labyrinth.profile import PROFILE_NODE, profile_player_id


class NativeGuideTrial:
    def __init__(self, tasker, directory):
        self.tasker, self.directory = tasker, directory
        self.account = None
        self.stage = STAGE
        self.sequence = 0
        self.progress_observation_only = False
        self.battle_start = 'not_sent'

    def checkpoint(self):
        native._check_receipt(self.tasker)

    def run(self, node, *, click=False):
        self.checkpoint()
        if self.progress_observation_only and node not in (
                'GTChoose', 'GTFireMap', 'GTFireMapBack', 'GTDeepBack', 'GTOpenDeep', 'GTOpenFire', PROFILE_NODE,
                'LabyClientHomeToAdventure', 'LabyClientOpenMainMenuOnce', 'LabyClientOpenProfileOnce',
                'LabyClientProfileBackOnce', 'LabyClientMenuToAdventureOnce'):
            raise RuntimeError('guide_progress_navigation_scope_violation')
        actions = (node,)
        starting = click and node == 'GTBattleStart'
        if starting:
            self.battle_start = 'unknown'
        try:
            result = native.run(self.tasker, node, reset_hits=actions if click else ())
        except native.NativeInputNotSent:
            if starting:
                self.battle_start = 'not_sent'
            raise
        self.sequence += 1
        stem = self.directory / f'{self.sequence:03d}-{node}'
        stem.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        frame = self.tasker.controller.cached_image
        if frame is not None:
            native.write_png(stem.with_suffix('.png'), frame)
        if click:
            try:
                native.require_native_input(result, actions)
            except native.NativeInputNotSent:
                if starting:
                    self.battle_start = 'not_sent'
                raise
            if starting:
                self.battle_start = 'sent'
        if not result['native_ok']:
            raise RuntimeError('guide_trial_page_unknown:' + node)
        return result

    def choose(self, names, *, timeout=10000):
        disconnect = 'LabyDisconnect6002Observed'
        names = [disconnect, *(name for name in names if name != disconnect)]
        # A successful read-only unknown branch keeps polling from poisoning the Context.
        nodes = {'GTChoose': dict(recognition='DirectHit', action='DoNothing', next=names + ['GTUnknown'],
                                 timeout=timeout, pre_delay=0, post_delay=0),
                 'GTUnknown': dict(recognition='DirectHit', action='DoNothing', next=[], pre_delay=0, post_delay=0)}
        self.tasker.resource.override_pipeline(nodes)
        result = self.run('GTChoose')
        hit = result['nodes'][-1]['name']
        if hit == disconnect:
            raise RuntimeError('guide_trial_disconnected_6002:battle_start=' + self.battle_start)
        return hit

    def wait(self, names, *, seconds=30):
        end = min(time.monotonic() + seconds, native.RUN_DEADLINE.get())
        while time.monotonic() < end:
            hit = self.choose(names)
            if hit != 'GTUnknown':
                return hit
            time.sleep(.3)
        raise RuntimeError('guide_trial_page_timeout')

    def configure(self, plan):
        from .guide_identity import require_source_team
        self.source_identity = require_source_team(plan)
        self.plan = plan
        self.stage = plan['stage']
        self.tasker.resource.override_pipeline(game_session_pipeline(int(plan['account_id'])))
        from .guide_trial_battle import battle_pipeline
        self.tasker.resource.override_pipeline(battle_pipeline(self.stage))
        # Five- and six-star templates stay calibrated; lower rarities use the same digit ROI.
        self.tasker.resource.override_pipeline({f'GTStar{i+1}_{star}': dict(recognition='OCR', expected=f'^{star}$',
            roi=[65+146*i,631,40,36], threshold=.9, action='DoNothing', next=[], pre_delay=0, post_delay=0)
            for i in range(5) for star in range(1,5)})
        # Read controls in the opening chain; normal starts do not enter the pause menu.
        self.tasker.resource.override_pipeline({'GTBattleStart': dict(next=['GTBattleReadSettings'],timeout=30000)})
        if self.stage != STAGE:
            area, level = self.stage.split()[-1].split('-')
            self.tasker.resource.override_pipeline({
                'GTDetailTitle': dict(expected=rf'^红焰深域\s*{area}\s*[-－—]\s*{level}$'),
                'GTOpenStage': dict(recognition='Custom', custom_recognition='GuideTrialCharacters',
                    custom_recognition_param=dict(mode='open_stage'))})
        from .guide_trial_characters import GuideTrialCharacters
        self.characters = GuideTrialCharacters(self.stage, plan=plan)
        # SDK resource views do not keep callbacks alive for the owning Resource.
        resource = getattr(self.tasker, '_resource_holder', None) or self.tasker.resource
        if not resource.register_custom_recognition('GuideTrialCharacters', self.characters):
            raise RuntimeError('guide_trial_character_callback_registration_failed')

    def prepare_resume(self, plan, resume):
        import hashlib
        from pathlib import Path
        self.battle_start = 'previously_sent'
        self.configure(plan)
        reference = Path(resume['reference_path'])
        if hashlib.sha256(reference.read_bytes()).hexdigest() != resume['frame_sha256']:
            raise RuntimeError('guide_trial_resume_reference_changed')
        self.resume_reference = native.read_image(reference)
        self.verify_paused(plan)
        # Account comes from the same durable intent and explicit same-battle confirmation.
        self.account = plan['account_id']

    def verify_paused(self, plan):
        import cv2
        import numpy as np
        self.run('GTPaused')
        frame = self.tasker.controller.cached_image
        members = self.characters.read_paused(frame)
        if not correct_team([row['unit_id'] if row else None for row in members], plan['unit_ids']):
            raise RuntimeError('guide_trial_resume_team_mismatch')
        reference = cv2.resize(self.resume_reference, (1280,720))
        # Timer and all HP/TP bars bind recovery to the observed paused progress, not just a team.
        for x,y,w,h in ((1075,15,50,35),(357,367,566,30)):
            delta = cv2.absdiff(frame[y:y+h,x:x+w], reference[y:y+h,x:x+w])
            if float(np.mean(delta)) > 3:
                raise RuntimeError('guide_trial_resume_progress_changed')

    def resume_and_read(self, plan):
        from .guide_identity import require_source_team
        from .guide_trial_battle import finish_battle
        require_source_team(plan)
        self.verify_paused(plan)
        return finish_battle(self, paused=True, set_by_unit=planned_sets(plan))

    def finish_completed_return(self, plan):
        self.battle_start = 'previously_sent'
        self.configure(plan)
        self.return_to_stage()

    def recover_victory(self, plan, recovery):
        return self.recover_result(plan, recovery)

    def recover_result(self, plan, recovery):
        import hashlib
        from pathlib import Path
        self.battle_start = 'previously_sent'
        self.configure(plan)
        reference = Path(recovery['reference_path'])
        if hashlib.sha256(reference.read_bytes()).hexdigest() != recovery['frame_sha256']:
            raise RuntimeError('guide_trial_victory_reference_changed')
        outcome = recovery.get('outcome', 'victory')
        if outcome not in ('victory','defeat'):
            raise RuntimeError('guide_trial_result_unknown')
        result = self.run('GTWinObserved' if outcome == 'victory' else 'GTDefeat')
        # Result pages have no stage identity; bind the pending intent and explicit same-battle confirmation.
        return dict(outcome=outcome, result_verified=True, settings_verified=False,
                    recovery_scope='same_observed_'+outcome, intent_id=recovery['intent_id'],
                    stage=plan['stage'], account_id=plan['account_id'],
                    identity_source='pending_intent_and_explicit_same_battle_authorization',
                    reference_sha256=recovery['frame_sha256'], result_evidence=result)

    def prepare_map(self, plan, *, observation_only=False):
        self.configure(plan)
        names = ['LabyDisconnect6002Observed', 'LabyClientTitleObserved', 'LabyClientNoticeObserved',
                 'LabyClientGameHomeObserved', 'LabyClientAdventureObserved', 'GTFormation', 'GTStageDetail', 'GTDeepHome', 'GTFireMap', 'GTDefeat']
        if observation_only:
            names = ['LabyClientGameHomeObserved', 'LabyClientAdventureObserved', 'GTDeepHome', 'GTFireMap']
        hit = self.wait(names)
        if hit == 'GTDefeat':
            self.return_to_stage()
            hit = 'GTFireMap'
        if hit == 'GTFormation':
            self.run('GTFormationCancel', click=True)
            hit = self.wait(['GTStageDetail', 'GTFireMap'])
        if hit == 'GTStageDetail':
            self.run('GTDetailCancel', click=True)
            hit = self.wait(['GTFireMap'])
        if hit == 'GTFireMap':
            self.run('GTFireMapBack', click=True)
            hit = self.wait(['GTDeepHome'])
        if hit == 'GTDeepHome':
            self.run('GTDeepBack', click=True)
            hit = self.wait(['LabyClientAdventureObserved'])
        if hit == 'LabyClientTitleObserved':
            self.account = str(title_player_id(self.run(hit)))
            if self.account != plan['account_id']:
                raise RuntimeError('guide_trial_account_mismatch')
            self.run('LabyClientTitleStartOnce', click=True)
            hit = self.wait(names[2:], seconds=80)
        if hit == 'LabyClientNoticeObserved':
            self.run('LabyClientNoticeCloseOnce', click=True)
            hit = self.wait(names[3:])
        if hit == 'LabyClientGameHomeObserved':
            self.run('LabyClientHomeToAdventure', click=True)
            hit = self.wait(['LabyClientAdventureObserved'])
        if self.account is None:
            self.run('LabyClientOpenMainMenuOnce', click=True)
            self.wait(['LabyClientMainMenuObserved'])
            self.run('LabyClientOpenProfileOnce', click=True)
            self.wait([PROFILE_NODE])
            self.account_receipt = self.run(PROFILE_NODE)
            self.account = str(profile_player_id(self.account_receipt))
            if self.account != plan['account_id']:
                raise RuntimeError('guide_trial_account_mismatch')
            self.run('LabyClientProfileBackOnce', click=True)
            self.wait(['LabyClientMainMenuObserved'])
            self.run('LabyClientMenuToAdventureOnce', click=True)
            self.wait(['LabyClientAdventureObserved'])
        self.tasker.resource.override_pipeline({'GTOpenDeep': dict(recognition='And',
            all_of=['AbyssAdventureDeepLabel','AbyssAdventureIdentity'], action='Click', target=True,
            next=[], max_hit=1, timeout=5000, post_delay=1500)})
        self.run('GTOpenDeep', click=True)
        self.wait(['GTDeepHome'])
        self.run('GTOpenFire', click=True)
        self.wait(['GTFireMap'])

    def observe_progress(self, plan):
        import hashlib
        self.progress_observation_only = True
        self.account = None
        self.prepare_map(plan, observation_only=True)
        receipt = self.run('GTFireMap')
        stem = self.directory / f'{self.sequence:03d}-GTFireMap'
        return dict(source='maa_owner_map_observation', account_id=self.account,
                    account_evidence=self.account_receipt, scene='GTFireMap', observed_at=time.time(),
                    frame_path=str(stem.with_suffix('.png')), frame_sha256=hashlib.sha256(stem.with_suffix('.png').read_bytes()).hexdigest(),
                    receipt_path=str(stem.with_suffix('.json')), receipt_sha256=hashlib.sha256(stem.with_suffix('.json').read_bytes()).hexdigest(),
                    map_evidence=receipt, battle_started=False, result_verified=False, settings_verified=False)

    def prepare(self, plan):
        self.prepare_map(plan)
        self.run('GTOpenStage', click=True)
        self.wait(['GTStageDetail'])
        self.run('GTChallenge', click=True)
        self.wait(['GTFormation'])
        self.form_team()

    def read_team(self):
        receipt = self.run('GTReadTeam')
        detail = receipt['nodes'][-1]['recognition']['best']['detail']
        if type(detail) is str:
            detail = json.loads(detail)
        return detail

    def form_team(self):
        team = self.read_team()
        if correct_team(team['unit_ids'], self.plan['unit_ids']):
            if team['member_count'] != 5:
                raise RuntimeError('guide_trial_member_count_unknown')
            return
        for _ in range(5):
            if team['member_count'] == 0:
                break
            self.tasker.resource.override_pipeline({'GTEditTeam': dict(custom_recognition_param=dict(mode='clear', count=team['member_count']))})
            self.run('GTEditTeam', click=True)
            after = self.read_team()
            if after['member_count'] != team['member_count']-1:
                raise RuntimeError('guide_trial_clear_readback_unknown')
            team = after
        for uid in self.plan['unit_ids']:
            self.run('GTFireFilter', click=True)
            # The first slice supports a bounded visible scan, not arbitrary search.
            found = False
            from .labyrinth.character_recognition import scrollbar
            for _ in range(6):
                self.read_team()
                scroll = scrollbar(self.tasker.controller.cached_image, 'battle')
                if scroll['can_scroll'] is False or scroll['position'] is not None and scroll['position'] <= .06:
                    break
                self.run('GTScrollTop', click=True)
            for page in range(16):
                self.run('GTReadTeam')
                frame = self.tasker.controller.cached_image
                candidate = self.characters.match(frame, uid, (65,155,1145,340))
                if candidate and candidate['box'][1] >= 155 and candidate['box'][1]+candidate['box'][3] <= 495:
                    self.tasker.resource.override_pipeline({'GTEditTeam': dict(custom_recognition_param=dict(mode='add', unit_id=uid))})
                    self.run('GTEditTeam', click=True)
                    team = self.read_team()
                    if uid not in team['unit_ids']:
                        raise RuntimeError('guide_trial_added_character_unknown')
                    found = True
                    break
                self.run('GTScrollDown', click=True)
            if not found:
                raise RuntimeError('guide_trial_character_not_found:' + str(uid))
        if not correct_team(self.read_team()['unit_ids'], self.plan['unit_ids']):
            raise RuntimeError('guide_trial_final_team_mismatch')

    def verify_team(self, plan):
        reading = self.read_team()
        # Star badges alternate with EX icons for over 8 seconds; observe one cycle without input.
        deadline = time.monotonic() + 30
        while correct_team(reading['unit_ids'], plan['unit_ids']) and not team_matches(reading, plan) and time.monotonic() < deadline:
            time.sleep(.3)
            reading = self.read_team()
        if not team_matches(reading, plan) or self.account != plan['account_id']:
            raise RuntimeError('guide_trial_team_or_account_mismatch')
        if set(reading['unit_ids']) != {m['UnitId'] for m in self.source_identity['Members']}:
            raise RuntimeError('guide_source_actual_team_mismatch')
        return dict(stage=self.stage, account_id=self.account, unit_ids=reading['unit_ids'], stars=reading['stars'],
                    stars_source='maa_current_formation', settings_plan=settings_plan(plan),
                    settings_verified=False, portrait_evidence=reading, source_identity=self.source_identity)

    def start_and_read(self, plan):
        from .guide_identity import require_source_team
        require_source_team(plan)
        from .guide_trial_battle import finish_battle
        receipt = self.run('GTBattleStart', click=True)
        return finish_battle(self, initial=receipt, set_by_unit=planned_sets(plan))

    def read_battle_settings(self, receipt=None):
        receipt = self.run('GTBattleReadSettings') if receipt is None else receipt
        detail = receipt['nodes'][-1]['recognition']['best']['detail']
        return json.loads(detail) if isinstance(detail,str) else detail

    def start_control_check(self, plan, check):
        from .guide_trial_control_check import check_and_exit
        receipt = self.run('GTBattleStart', click=True)
        return check_and_exit(self, plan, check, receipt)

    def observe_control_check(self, plan):
        import hashlib
        self.progress_observation_only = True
        self.configure(plan)
        hit = self.choose(['GTPaused','GTAbandonDialog','GTWinObserved','GTRewardsObserved','GTDefeat',
                           'GTFireMap','GTBattleReadSettings'], timeout=4000)
        stem = self.directory / f'{self.sequence:03d}-GTChoose'
        return dict(source='maa_owner_control_observation', scene=hit, stage=plan['stage'],
            battle_started=False, result_verified=False, account_verified=False,
            frame_path=str(stem.with_suffix('.png')), receipt_path=str(stem.with_suffix('.json')),
            frame_sha256=hashlib.sha256(stem.with_suffix('.png').read_bytes()).hexdigest(),
            receipt_sha256=hashlib.sha256(stem.with_suffix('.json').read_bytes()).hexdigest())

    def exit_control_check(self, plan):
        self.run('GTPauseBattle', click=True)
        self.wait(['GTPaused'], seconds=5)
        members = self.characters.read_paused(self.tasker.controller.cached_image)
        if not correct_team([m['unit_id'] if m else None for m in members], plan['unit_ids']):
            raise RuntimeError('guide_control_check_exit_team_unknown')
        self.run('GTAbandonBattle', click=True)
        hit = self.wait(['GTAbandonDialog','GTFireMap','GTStageDetail'], seconds=8)
        return self.finish_control_exit(plan,hit)

    def recover_control_exit(self, plan, recovery):
        import hashlib
        import cv2
        import numpy as np
        from pathlib import Path
        from .guide_trial_control_check import control_exit_result
        self.battle_start = 'previously_sent'
        self.configure(plan)
        reference = Path(recovery['reference_path'])
        if hashlib.sha256(reference.read_bytes()).hexdigest() != recovery['frame_sha256']:
            raise RuntimeError('guide_control_exit_reference_changed')
        previous = cv2.resize(native.read_image(reference),(1280,720))
        self.run('GTAbandonDialog')
        frame = cv2.resize(self.tasker.controller.cached_image,(1280,720))
        # The paused timer and visible five-person HUD must still match the stopped battle.
        for x,y,w,h in ((1065,10,65,45),(250,555,780,150)):
            if float(np.mean(cv2.absdiff(frame[y:y+h,x:x+w],previous[y:y+h,x:x+w]))) > 3:
                raise RuntimeError('guide_control_exit_progress_changed')
        self.account = plan['account_id']
        exit = self.finish_control_exit(plan,'GTAbandonDialog')
        exit['same_battle_reference_sha256'] = recovery['frame_sha256']
        return control_exit_result(recovery['check'],recovery['readings'],exit)

    def finish_control_exit(self, plan, hit):
        import hashlib
        if hit == 'GTAbandonDialog':
            self.run('GTAbandonConfirm', click=True)
            hit = self.wait(['GTFireMap','GTStageDetail'], seconds=10)
        if hit == 'GTStageDetail':
            self.run('GTDetailCancel', click=True)
        self.wait(['GTFireMap'], seconds=10)
        self.run('GTFireMap')
        stem = self.directory / f'{self.sequence:03d}-GTFireMap'
        return dict(scene='GTFireMap', verified=True, stage=plan['stage'], account_id=self.account,
            frame_sha256=hashlib.sha256(stem.with_suffix('.png').read_bytes()).hexdigest(),
            receipt_sha256=hashlib.sha256(stem.with_suffix('.json').read_bytes()).hexdigest())

    def prepare_retry(self, plan):
        from .guide_trial_battle import prepare_retry
        prepare_retry(self, plan)

    def return_to_stage(self):
        from .guide_trial_battle import return_to_stage
        return_to_stage(self)
