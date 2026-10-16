"""Maa CN single-preset adapter; final settlement requires fresh complete counts."""
import json
import re
import traceback

from maa.custom_recognition import CustomRecognition

from . import native
from .preset_scan import scan, key


def completed(receipt, name):
    return receipt.get('native_ok') is True and any(
        n['name'] == name and n['completed'] for n in receipt.get('nodes', []))


def number(receipt, name):
    node = next((n for n in receipt.get('nodes', []) if n['name'] == name and n['completed']), None)
    words = (node.get('recognition') or {}).get('filtered', []) if node else []
    values = [w['text'].strip() for w in words if w.get('score', 0) >= .9]
    return int(values[0]) if len(values) == 1 and re.fullmatch(r'\d{1,9}', values[0]) else None


def _updated_count(receipt, action, expected, limit):
    """Read only this adjustment's completed action and nested OCR guard."""
    if receipt.get('native_ok') is not True or receipt.get('tasker_inited') is not True:
        return None
    nodes = receipt.get('nodes', [])
    actions = [(i, n) for i, n in enumerate(nodes) if n.get('name') == action]
    reads = [(i, n) for i, n in enumerate(nodes) if n.get('name') == 'FitUpdatedRead']
    if len(actions) != 1 or len(reads) != 1:
        return None
    (action_index, clicked), (read_index, read) = actions[0], reads[0]
    sent = clicked.get('action') or {}
    if (clicked.get('completed') is not True or sent.get('name') != action
            or sent.get('success') is not True or read.get('completed') is not True
            or action_index >= read_index):
        return None
    parts = read.get('recognition')
    if not isinstance(parts, list):
        return None
    guards = [p for p in parts if p.get('name') == 'FitUpdatedGuard']
    if len(guards) != 1 or guards[0].get('algorithm') != 'Custom':
        return None
    matches = (guards[0].get('detail') or {}).get('filtered', [])
    if len(matches) != 1 or not isinstance(matches[0].get('detail'), dict):
        return None
    words = matches[0]['detail'].get('filtered', [])
    if len(words) != 1 or not words[0].get('score', 0) >= .9:
        return None
    text = words[0].get('text', '').strip()
    if not re.fullmatch(r'\d{1,3}', text):
        return None
    value = int(text)
    return value if 1 <= value <= limit and value == expected else None


class PresetNumber(CustomRecognition):
    """Read one configured numeric field from the action frame using Maa OCR."""
    def __init__(self, names):
        super().__init__()
        self.names = frozenset(names)

    def analyze(self, context, argv):
        name = json.loads(argv.custom_recognition_param)
        if name not in self.names:
            return None
        # A separate native recognition keeps the local detector independent of
        # merged danger/page OCR. Parameters and exact expected values stay in JSON.
        found = context.run_recognition(name, argv.image)
        words = found.raw_detail.get('filtered', []) if found and found.hit else []
        if len(words) != 1 or words[0].get('score', 0) < .9:
            return None
        word = words[0]
        pattern = r'\d{1,3}' if name in ('FitExpected', 'FitUpdated') else r'\d{1,9}'
        if not re.fullmatch(pattern, word['text'].strip()):
            return None
        return self.AnalyzeResult(word['box'], found.raw_detail)


class NativePreset:
    def __init__(self, tasker):
        self.tasker = tasker
        self.count_limit = 10

    def bind_result_checkpoint(self, callback):
        self._result_checkpoint = callback

    def read_count(self, slot):
        """Guarded page-local read only; never navigate to recover a value."""
        if not self.tasker.resource.override_pipeline({'FitPreset': {
                'all_of': ['PresetsReached', f'SelectedSlot{slot}', 'ScanCheckedLabel', 'FitLabel']}}):
            raise RuntimeError('Cannot bind count reader to original preset')
        receipt = native.run(self.tasker, 'FitRead')
        self._count_receipt = receipt
        value = number(receipt, 'FitCount')
        return value if completed(receipt, 'FitCount') and type(value) is int and 1 <= value <= self.count_limit else None

    def fit_count(self, slot, target):
        """At most nine individual native clicks, each with actual OCR readback."""
        result = dict(verified=False, target=target, observed=None, steps=[])
        if type(target) is not int or not 1 <= target <= self.count_limit:
            return result
        current = self.read_count(slot)
        result.update(observed=current, initial_evidence=self._count_receipt)
        result['initial_receipt'] = str(native.save_receipt(result, 'count-fit-before'))
        if current is None:
            return result
        resource = self.tasker.resource
        if not resource.register_custom_recognition('CountFitNumber', PresetNumber(('FitExpected', 'FitUpdated'))):
            raise RuntimeError('Cannot register count OCR guard')
        try:
            import time
            deadline = time.monotonic() + 120
            for _ in range(self.count_limit - 1):
                if current == target:
                    result['verified'] = True
                    return result
                if time.monotonic() >= deadline:
                    return result
                direction = 1 if current < target else -1
                entry = 'FitIncreaseStart' if direction == 1 else 'FitDecreaseStart'
                # Guard the same action frame against intervening count changes.
                if not self.tasker.resource.override_pipeline({
                        'FitExpected': {'expected': f'^{current}$'},
                        'FitUpdated': {'expected': f'^{current + direction}$'}}):
                    raise RuntimeError('Cannot bind count action to observed value')
                receipt = native.run(self.tasker, entry)
                action = 'FitIncrease' if direction == 1 else 'FitDecrease'
                observed = _updated_count(receipt, action, current + direction, self.count_limit)
                result['steps'].append(dict(before=current, observed=observed, evidence=receipt,
                                            readback=receipt))
                result['observed'] = observed
                if observed is None:
                    return result  # stuck, unknown, or unexpected jump: no further clicks
                current = observed
            result['verified'] = current == target
            return result

        finally:
            resource.unregister_custom_recognition('CountFitNumber')

    def verify_preset(self, slot):
        """Read only the current page and actual slot, without navigation or input."""
        if type(slot) is not int or not 1 <= slot <= 7:
            raise ValueError('slot must be 1..7')
        observed = native.run(self.tasker, 'PresetReadbackStart')
        page_verified = completed(observed, 'ObservePresets')
        observed['observation'] = dict(
            page='presets' if page_verified else 'unknown',
            selected_slot=next((i for i in range(1, 8)
                                if page_verified and completed(observed, f'SelectedSlot{i}')), None),
            list_complete=False, daily_complete=False)
        return dict(native_ok=observed['native_ok'], requested_slot=slot,
                    selected_slot=observed['observation']['selected_slot'],
                    ocr_receipt=observed, observation=observed['observation'])

    def read(self, slot):
        result = scan(self.tasker, slot, source='fresh native preset execution reading')
        # The scan supplies row OCR; this fresh frame verifies only page and slot.
        observed = self.verify_preset(slot)['ocr_receipt']
        result.update(ocr_receipt=observed, observation=observed['observation'])
        from .preset_execution import identity_ok
        normal = identity_ok(result.get('rows', []), 'normal')
        self.count_limit = 999 if normal else 10
        overrides = {}
        for name in ('FitCount', 'ExecCount', 'FitExpected', 'FitUpdated'):
            params = self.tasker.resource.get_node_data(name)['attach']
            overrides[name] = dict(roi=params['normal_roi' if normal else 'default_roi'],
                                   only_rec=True if normal else params['default_only_rec'])
        if not self.tasker.resource.override_pipeline(overrides):
            raise RuntimeError('Cannot bind count readers to observed family')
        result['reading_receipt'] = str(native.save_receipt(result, 'preset-reading'))
        return result

    def quote(self, reading):
        slot = reading['requested_slot']
        if not self.tasker.resource.override_pipeline({'ExecPresetGuard': {
                'all_of': ['PresetsReached', f'SelectedSlot{slot}', 'ScanCheckedLabel']}}):
            raise RuntimeError('Cannot bind quote to selected slot')
        return self.read_quote(reading, {'slot': slot})

    def read_quote(self, reading, identity):
        receipt = native.run(self.tasker, 'ExecReadQuote')
        quote = dict(verified=False, **identity, identities=[list(key(r)) for r in reading['rows']],
                     identity_verified=completed(receipt, 'ExecPresetGuardRead'),
                     evidence=receipt, source='native OCR; preset footer')
        count = number(receipt, 'ExecCount')
        fields = dict(stamina_before='ExecStaminaBefore', stamina_after='ExecStaminaAfter')
        quote.update({field: number(receipt, name) for field, name in fields.items()})
        quote['counts'] = [count] * len(reading['rows'])
        quote['verified'] = (completed(receipt, 'ExecQuoteDone') and count is not None
                             and all(quote[f] is not None for f in fields))
        # Explicit shortage text is observation only, never permission to recover.
        shortage = native.run(self.tasker, 'ExecReadShortage')
        quote['shortage_evidence'] = shortage
        for name, resource in [('ExecStaminaShortage', 'stamina'), ('ExecTicketsShortage', 'tickets')]:
            if completed(shortage, name):
                quote['insufficient'] = resource
        return quote

    def confirmation_page(self, *, stable_short=False):
        from .preset_confirmation import ConfirmationPage
        return ConfirmationPage(stable_short=stable_short)

    def return_guard(self, quote):
        return ['PresetsReached', f"SelectedSlot{quote['slot']}", 'ScanCheckedLabel']

    def read_confirmation(self):
        import time
        from .preset_confirmation import ConfirmationPage, ConfirmationCollector
        collector = ConfirmationCollector()
        resource = self.tasker.resource
        page = self.confirmation_page(stable_short=True)
        # One total short-list acceptance window, including initial OCR and both
        # fresh frames. It is never renewed by an animation or changed candidate.
        page.short_deadline = time.monotonic() + resource.get_node_data('ExecScanStart')['timeout'] / 1000
        if not resource.register_custom_recognition('ConfirmationPage', page):
            raise RuntimeError('Cannot register confirmation reader')
        try:
            if not resource.register_custom_action('CollectConfirmation', collector):
                raise RuntimeError('Cannot register confirmation collector')
            receipt = native.run(self.tasker, 'ExecScanStart')
        finally:
            resource.unregister_custom_recognition('ConfirmationPage')
            resource.unregister_custom_action('CollectConfirmation')
        return dict(native_ok=receipt['native_ok'], evidence=receipt, pages=collector.pages,
                    rows=collector.rows, list_complete=collector.complete and receipt['native_ok'],
                    incomplete_reason=collector.reason,
                    summary=collector.pages[-1]['summary'] if collector.pages else {})

    def confirm(self, reading, quote):
        import time
        from .preset_confirmation import check_confirmation
        self._confirmed = None
        fields = dict(ExecCount=quote['counts'][0])
        # Permit one naturally regenerated point, with matching after balance.
        # The subsequent full confirmation checks the exact cost and affordability.
        for name, value in [('ExecStaminaBefore', quote['stamina_before']),
                            ('ExecStaminaAfter', quote['stamina_after'])]:
            fields[name] = value
        overrides = {name: {'expected': f'^{value}$'} for name, value in fields.items()}
        for name in ('ExecStaminaBefore', 'ExecStaminaAfter'):
            overrides[name] = {'expected': f"^({fields[name]}|{fields[name]+1})$"}
        if not self.tasker.resource.override_pipeline(overrides):
            raise RuntimeError('Cannot bind opening to fresh quote')
        resource = self.tasker.resource
        registered = False
        try:
            if not resource.register_custom_recognition('CountFitNumber', PresetNumber(fields)):
                raise RuntimeError('Cannot register opening footer OCR guard')
            registered = True
            opened = native.run(self.tasker, 'ExecSubmit')
        finally:
            if registered:
                resource.unregister_custom_recognition('CountFitNumber')
            if not resource.override_pipeline({name: {'expected': r'^\d{1,9}$'} for name in fields}):
                raise RuntimeError('Cannot restore footer readers')
        if not completed(opened, 'ExecConfirmHeader'):
            return dict(verified=False, opening=opened)
        result = self.read_confirmation()
        result['opening'] = opened
        result['verified'] = check_confirmation(result, reading, quote)
        if result['verified']:
            self._confirmed = (reading, quote, result, time.monotonic())
        return result

    def cancel_confirmation(self):
        self._confirmed = None
        return completed(native.run(self.tasker, 'ExecCancelStart'), 'ExecCancelled')

    def submit(self, quote):
        import time
        import json
        from maa.custom_recognition import CustomRecognition
        from .preset_confirmation import ConfirmationPage, check_confirmation
        snapshot = getattr(self, '_confirmed', None)
        self._confirmed = None  # One dispatch only; preview never invokes this method.
        if snapshot is None or quote != snapshot[1] or time.monotonic() - snapshot[3] > 180:
            raise native.NativeInputNotSent(dict(input_execution='not_sent',
                reason='confirmation_snapshot_missing_changed_or_expired', dispatch_attempted=False))
        reading, _, result, _ = snapshot
        if not self.tasker.resource.override_pipeline({'ExecReturnedPreset': {
                'all_of': self.return_guard(quote)}}):
            raise RuntimeError('Cannot bind settlement to original slot')
        page_factory = self.confirmation_page

        class Guard(CustomRecognition):
            def analyze(self, context, argv):
                try:
                    return self.check(context, argv)
                except Exception:
                    traceback.print_exc()
                    return None

            def check(self, context, argv):
                if time.monotonic() - snapshot[3] > 180:
                    return None
                # Re-read the current final page and all amounts on the action frame.
                argv.custom_recognition_param = json.dumps(result['pages'][-1]['kind'])
                page = page_factory().analyze(context, argv)
                if page is None:
                    return None
                detail = page.detail
                if isinstance(detail, str):
                    detail = json.loads(detail)
                old = result['pages'][-1]['rows']
                fields = ('title', 'category', 'difficulty', 'remaining', 'limit', 'count', 'fields_verified')
                if detail['gaps'] or [[r[f] for f in fields] for r in detail['rows']] != [[r[f] for f in fields] for r in old]:
                    return None
                fresh = dict(result, summary=detail['summary'])
                return self.AnalyzeResult([0, 0, 1, 1], {}) if check_confirmation(fresh, reading, quote) else None

        resource = self.tasker.resource
        if not resource.register_custom_recognition('ConfirmationCommitGuard', Guard()):
            raise RuntimeError('Cannot register challenge guard')
        try:
            # execute_one has already fsynced this transaction's in_flight
            # under its Journal lock; the confirmation snapshot was consumed
            # above. Do not rearm consumers at generic native task entry.
            receipt = native.run(self.tasker, 'ExecChallenge', reset_hits=('ExecConfirm',))
            native.require_native_input(receipt, ('ExecConfirm',))
            from .result_checkpoint import action_completed
            submission = dict(native_receipt=receipt, settlement_verified=False,
                              consumption_dispatched=action_completed(receipt, 'ExecConfirm'))
        finally:
            resource.unregister_custom_recognition('ConfirmationCommitGuard')
        if not submission['consumption_dispatched']:
            return submission
        return self._finish_result(reading, quote, submission)

    def _finish_result(self, reading, quote, submission, entry='ExecResultStart'):
        import time
        from .result_checkpoint import action_completed
        resource = self.tasker.resource
        # This is the only caller of the post-consumption chain. Registration
        # lasts for this single dispatch, never for preview/inspection/restart.
        evidence_images = []

        class ClanPromptGuard(CustomRecognition):
            def analyze(self, context, argv):
                try:
                    # Bind the notice to this dispatch's known preset family.
                    from .preset_execution import identity_ok
                    if not any(identity_ok(reading.get('rows', []), kind)
                               for kind in ('deep', 'very_hard', 'hard', 'normal')):
                        return None
                    # Nested Context tasks retain the outer task ID, whose
                    # detail does not contain this result task's nodes. The
                    # official latest-node query includes nested tasks; bind
                    # its successful action to this dispatch's saved evidence
                    # so an older result on the same Tasker cannot authorize it.
                    result_node = context.tasker.get_latest_node('ExecResultConfirm')
                    if (not result_node or not result_node.completed or not result_node.action
                            or result_node.action.name != 'ExecResultConfirm'
                            or not result_node.action.success or not result_node.recognition
                            or context.get_hit_count('ExecClanDismiss') != 0):
                        return None
                    details = result_node.recognition.raw_detail
                    if not isinstance(details, list):
                        return None
                    saved = [item for item in evidence_images if item['kind'] == 'result']
                    proof = [part for part in details if part.get('name') == 'ExecResultEvidence'
                             and part.get('algorithm') == 'Custom']
                    if (len(saved) != 1 or len(proof) != 1
                            or saved[0].get('task_id') != context.get_task_job().job_id):
                        return None
                    filtered = proof[0].get('detail', {}).get('filtered', [])
                    if len(filtered) != 1 or filtered[0].get('detail') != saved[0]:
                        return None
                    evidence = {}
                    # All checks use the click's current image; never a saved frame.
                    for name in ('ExecClanDanger', 'ExecConfirmHeader'):
                        found = context.run_recognition(name, argv.image)
                        if found is None or found.hit:
                            return None
                    for name in ('ExecClanTitle', 'ExecClanCP', 'ExecClanIncrease',
                                 'ExecClanCancelLabel', 'ExecClanGoLabel', 'ExecClanBackground'):
                        found = context.run_recognition(name, argv.image)
                        words = found.raw_detail.get('filtered', []) if found and found.hit else []
                        if len(words) != 1 or words[0].get('score', 0) < .9:
                            return None
                        evidence[name] = found.raw_detail
                    # This verified CP modal covers the swatches of slots 3..6.
                    # Check visible slots now; every slot remains bound before
                    # dispatch and by ExecReturnedPreset after the dismissal.
                    if quote['slot'] not in (3, 4, 5, 6):
                        slot_name = f"SelectedSlot{quote['slot']}"
                        selected = context.run_recognition(slot_name, argv.image)
                        if not selected or not selected.hit:
                            return None
                        evidence[slot_name] = selected.raw_detail
                    return self.AnalyzeResult([248, 84, 1, 1], evidence)
                except Exception:
                    traceback.print_exc()
                    return None

        self_adapter = self

        class ResultEvidence(CustomRecognition):
            def analyze(self, context, argv):
                import cv2
                import hashlib
                try:
                    param = json.loads(argv.custom_recognition_param or 'null')
                    kind = param if param in ('limited_shop', 'clan_prompt') else 'result'
                    anchors = {}
                    if kind == 'result':
                        # Validate each exact anchor on this image independently of And order.
                        for name in ('ExecResultTitle', 'ExecResultComplete', 'ExecResultButton'):
                            found = context.run_recognition(name, argv.image)
                            if not found or not found.hit:
                                return None
                            anchors[name] = found.raw_detail
                        if any(i['kind'] == 'result' for i in evidence_images):
                            return None
                    path = native.RUNTIME / f'preset-{kind}-{time.time_ns()}.png'
                    ok, encoded = cv2.imencode('.png', argv.image)
                    if not ok:
                        return None
                    data = encoded.tobytes()
                    path.write_bytes(data)
                    item = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), kind=kind,
                                source='current transaction action frame; native 720 short side',
                                task_id=context.get_task_job().job_id)
                    checkpoint = getattr(self_adapter, '_result_checkpoint', None)
                    if kind == 'result' and checkpoint is not None:
                        checkpoint(submission['native_receipt'], item, anchors)
                    evidence_images.append(item)
                    return self.AnalyzeResult([0, 0, 1, 1], item)
                except Exception:
                    traceback.print_exc()
                    return None

        if not resource.register_custom_recognition('CurrentResultEvidence', ResultEvidence()):
            raise RuntimeError('Cannot register current transaction result evidence')
        try:
            if not resource.register_custom_recognition('CurrentClanPrompt', ClanPromptGuard()):
                raise RuntimeError('Cannot register current transaction clan notice guard')
            # Exactly one result dispatch for this evidenced consumption.
            receipt = native.run(self.tasker, entry, reset_hits=(
                'ExecResultConfirm', 'ExecLimitedShopCancel', 'ExecClanDismiss', 'ExecLevelUpDismiss') + (
                ('ExecSweepSkipFinish',) if entry == 'ExecResultStart' else ()))
            submission.update(result_evidence=receipt, result_images=evidence_images,
                              result_verified=action_completed(receipt, 'ExecResultConfirm'),
                              preset_return_verified=completed(receipt, 'ExecReturnedPreset'),
                              clan_prompt_dismissed=any(n['name'] == 'ExecClanDismiss' and n['completed']
                                                        for n in receipt['nodes']),
                              shop_cancelled=any(n['name'] == 'ExecLimitedShopCancel' and n['completed']
                                                 for n in receipt['nodes']))
            submission['result_receipt'] = str(native.save_receipt(submission, 'preset-result'))
            return submission
        finally:
            resource.unregister_custom_recognition('CurrentClanPrompt')
            resource.unregister_custom_recognition('CurrentResultEvidence')

    def recover_result(self, record):
        quote = record['quote']
        if not self.tasker.resource.override_pipeline({'ExecReturnedPreset': {'all_of': self.return_guard(quote)}}):
            raise RuntimeError('Cannot bind result recovery to original preset')
        result = self._finish_result(record['before'], quote, dict(native_receipt=record['consumer']),
                                     entry='ExecRecoverResultStart')
        receipt = result['result_evidence']
        result['recovery_verified'] = completed(receipt, 'ObserveHome') or completed(receipt, 'ExecReturnedPreset')
        return result

    def home(self):
        return completed(native.run(self.tasker, 'ReturnHome'), 'ObserveHome')
