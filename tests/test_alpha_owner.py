"""Private parent channel tests. No native initialization or device access."""
import io
import json
import time
import unittest
from types import SimpleNamespace
from pcr_maa import alpha_owner


class SnapshotTests(unittest.TestCase):
    def test_snapshot_uses_original_validation_and_is_immutable(self):
        config = dict(version=1, start='home', device=dict(input_display=2, screenshot_display='123'),
                      presets=[dict(kind=k, slot=i+1, label=k, target_per_stage=3)
                               for i, k in enumerate(('deep', 'very_hard', 'hard'))])
        from unittest.mock import patch
        with patch('pcr_maa.device.validate_connection', side_effect=lambda x: dict(x)) as connection:
            snapshot = alpha_owner.config_snapshot(config)
            self.assertIsInstance(snapshot, str)
            self.assertEqual(connection.call_count, 1)
            config['presets'][0]['slot'] = 7
            self.assertEqual(json.loads(snapshot)['config']['presets'][0]['slot'], 1)
            config['version'] = 999
            with self.assertRaises(ValueError): alpha_owner.config_snapshot(config)

    def test_agent_post_authorize_loader_rejects_changed_presets(self):
        from unittest.mock import patch
        config = dict(version=1, start='home', device=dict(input_display=2, screenshot_display='123'),
                      presets=[dict(kind=k, slot=i+1, label=k, target_per_stage=3)
                               for i, k in enumerate(('deep', 'very_hard', 'hard'))])
        entry = SimpleNamespace(load_config=lambda _: config)
        with patch('pcr_maa.device.validate_connection', side_effect=lambda x: dict(x)):
            snapshot = alpha_owner.config_snapshot(config)
            alpha_owner.bind_config_snapshot(entry, snapshot)
            self.assertEqual(entry.load_config('ignored'), config)
            config['presets'][0]['target_per_stage'] = 4
            with self.assertRaisesRegex(ValueError, 'config_changed'):
                entry.load_config('ignored')


class OwnerTests(unittest.TestCase):
    def test_denied_reply_never_grants(self):
        provider = alpha_owner.OwnerProvider(io.StringIO('{"ok":false}\n'), io.StringIO())
        context = SimpleNamespace(get_task_job=lambda: SimpleNamespace(job_id=12),
                                  tasker=SimpleNamespace(controller=SimpleNamespace(uuid='controller')))
        with self.assertRaisesRegex(ValueError, 'owner_denied'):
            provider(context, dict(version=1, selected_modules=['rewards'], rewards_final=False))
    def context(self):
        return SimpleNamespace(get_task_job=lambda: SimpleNamespace(job_id=12),
            tasker=SimpleNamespace(controller=SimpleNamespace(uuid='controller'), post_stop=lambda: None))

    def reply(self, **changes):
        data = dict(ok=True, invocation_id='new-invocation', root_task_id=12, controller_uuid='controller',
            serial='127.0.0.1:16384', input_display=2, screenshot_display='123',
            combination=dict(version=1, selected_modules=['rewards'], rewards_final=False), execute=True,
            game_day='2027-09-24', count_context='user-reported-unreset', remaining_seconds=30,
            schedule_authorizations=[], schedule_attest_current_plan=False)
        data.update(changes)
        return data

    def provider(self, reply):
        return alpha_owner.OwnerProvider(io.StringIO(json.dumps(reply)+'\n'), io.StringIO(),
                                        on_disconnect=lambda: None)

    def test_one_request_one_grant_and_monotonic_deadline(self):
        answer = self.reply()
        provider = self.provider(answer)
        start = time.monotonic()
        grant = provider(self.context(), answer['combination'])
        self.assertGreaterEqual(grant.deadline, start+30)
        self.assertLess(grant.deadline, time.monotonic()+30.1)
        self.assertEqual(grant.schedule_authorizations, ())
        self.assertEqual(grant.root_task_id, 12)
        request = provider.outgoing.getvalue()
        self.assertEqual(request.count(alpha_owner.PREFIX), 1)
        self.assertEqual(json.loads(request[len(alpha_owner.PREFIX):])['root_task_id'], 12)
        with self.assertRaisesRegex(ValueError, 'already_consumed'):
            provider(self.context(), answer['combination'])
        self.assertEqual(provider.outgoing.getvalue(), request)

    def test_mismatched_root_uuid_and_selection_rejected(self):
        for changes in [dict(root_task_id=13), dict(controller_uuid='other'),
                        dict(combination=dict(version=1,selected_modules=['event'],rewards_final=False))]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'binding_mismatch'):
                self.provider(self.reply(**changes))(self.context(), self.reply()['combination'])

    def test_invalid_remaining_time_rejected(self):
        for remaining in [0, -1, 1801, True, float('nan'), float('inf'), '30']:
            with self.subTest(remaining=remaining), self.assertRaisesRegex(ValueError, 'remaining_time'):
                self.provider(self.reply(remaining_seconds=remaining))(self.context(), self.reply()['combination'])

    def test_eof_and_pre_revocation_reject(self):
        provider = alpha_owner.OwnerProvider(io.StringIO(''), io.StringIO())
        with self.assertRaisesRegex(ValueError, 'channel_closed'):
            provider(self.context(), self.reply()['combination'])
        provider = self.provider(self.reply()); provider.revoke()
        with self.assertRaisesRegex(ValueError, 'already_consumed'):
            provider(self.context(), self.reply()['combination'])
        self.assertEqual(provider.outgoing.getvalue(), '')

    def test_bounded_wait_does_not_resend(self):
        import threading
        closed = threading.Event()
        class Blocked:
            def readline(self, limit):
                closed.wait(1)
                return ''
        provider = alpha_owner.OwnerProvider(Blocked(), io.StringIO(), timeout=.03)
        try:
            with self.assertRaisesRegex(ValueError, 'timeout_no_retry'):
                provider(self.context(), self.reply()['combination'])
            with self.assertRaisesRegex(ValueError, 'already_consumed'):
                provider(self.context(), self.reply()['combination'])
            self.assertEqual(provider.outgoing.getvalue().count(alpha_owner.PREFIX), 1)
        finally:
            closed.set()


if __name__ == '__main__':
    unittest.main()
