"""Reroll unknown handling: preserve the session, reconcile with TOP only.

ENTER/RETIRE are the only operations that may survive an unknown result, and
they do it without replaying the original request: one read-only TOP decides
whether the run exists. Everything else keeps the older "unknown stops" rule.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from pcr_maa.labyrinth.acquisition import ProtocolAcquisitionPort
from pcr_maa.labyrinth.batch import Binding
from pcr_maa.labyrinth.protocol import ProtocolUnknown
from pcr_maa.labyrinth.unknown import RerollUnknown


class FakeAuth:
    def __init__(self, viewer_id):
        self.viewer_id = viewer_id


class FakeClient:
    phase = 'protocol'
    logged_in = True
    session_generation = None

    def __init__(self, *, enter_unknown=False, retire_unknown=False, top_enter=0, top_on_retire=None):
        self.auth = FakeAuth(123)
        self.enter_unknown = enter_unknown
        self.retire_unknown = retire_unknown
        self.top_enter = top_enter
        self.top_on_retire = top_on_retire
        self.retire_calls = 0
        self.closed = False

    async def enter(self, guild, difficulty, **kwargs):
        if self.enter_unknown:
            self.enter_unknown = False
            raise ProtocolUnknown('network_failed')
        self.top_enter = 7
        return dict(enter_id=7, map_list=[])

    async def top(self):
        return dict(enter_id=self.top_enter, guild_id=1, difficulty=1)

    async def resume(self, enter_id, **kwargs):
        self.top_enter = enter_id
        return dict(enter_id=enter_id, guild_id=1, difficulty=1, block_id=0, map_list=opening_map())

    async def retire(self, enter_id, **kwargs):
        self.retire_calls += 1
        if self.retire_unknown:
            self.retire_unknown = False
            raise ProtocolUnknown('network_failed')
        self.top_enter = 0
        return True

    def close(self):
        self.closed = True


def opening_map():
    nodes = []
    for area in (1, 2, 3):
        kinds = (1, 6, 8) if area == 3 else (1, 6, 6)
        for column, kind in enumerate(kinds, 1):
            uid = area * 100 + column
            quest = 7703 * 100000 + 30000 + 4 * 100 + 1 if kind == 8 else None
            nodes.append(dict(block_id=uid, area=area, column=column, row=1, block_type=kind,
                              next_block_id_list=[uid + 1] if column < len(kinds) else [],
                              quest_id=quest))
    return nodes


def make_port(client):
    port = ProtocolAcquisitionPort(client, None, Binding(123, 'custom-controller'))
    return port


class RerollReconcileTests(unittest.TestCase):
    def test_unknown_enter_adopts_the_run_top_reports(self):
        client = FakeClient(enter_unknown=True, top_enter=7)
        port = make_port(client)
        try:
            created = port.enter(1, 1, 'oid-1')
        finally:
            port.close()
        self.assertEqual(created['enter_id'], 7)
        self.assertTrue(created['reconciled'])
        self.assertEqual(created['source'], 'protocol_reconciled')

    def test_reconciled_enter_reads_the_map_without_a_creation_ack(self):
        client = FakeClient(enter_unknown=True, top_enter=7)
        port = make_port(client)
        try:
            port.enter(1, 1, 'oid-6')
            receipt = port.read_opening(7)
        finally:
            port.close()
        self.assertEqual(receipt['enter_id'], 7)
        self.assertIn('map_sha256', receipt)
        self.assertEqual(len(receipt['map_list']), 9)

    def test_unknown_enter_without_a_run_is_reported_as_not_created(self):
        client = FakeClient(enter_unknown=True, top_enter=0)
        port = make_port(client)
        try:
            with self.assertRaises(RerollUnknown) as raised:
                port.enter(1, 1, 'oid-2')
        finally:
            port.close()
        self.assertEqual(str(raised.exception), 'enter_not_created')

    def test_unknown_retire_accepts_a_clean_top(self):
        client = FakeClient(retire_unknown=True, top_enter=0)
        port = make_port(client)
        try:
            receipt = port.retire(5, 'oid-3')
        finally:
            port.close()
        self.assertTrue(receipt['reconciled'])
        self.assertEqual(client.retire_calls, 1)

    def test_unknown_retire_retries_once_when_top_still_shows_the_run(self):
        client = FakeClient(retire_unknown=True, top_enter=5)
        port = make_port(client)
        try:
            receipt = port.retire(5, 'oid-4')
        finally:
            port.close()
        self.assertTrue(receipt['reconciled'])
        self.assertTrue(receipt['retried'])
        self.assertEqual(client.retire_calls, 2)

    def test_unknown_retire_twice_stops(self):
        client = FakeClient(retire_unknown=True, top_enter=5)

        async def retire_still_unknown(enter_id, **kwargs):
            raise ProtocolUnknown('network_failed')

        client.retire = retire_still_unknown
        port = make_port(client)
        try:
            with self.assertRaises(RerollUnknown) as raised:
                port.retire(5, 'oid-5')
        finally:
            port.close()
        self.assertEqual(str(raised.exception), 'retire_still_unknown')


if __name__ == '__main__':
    unittest.main()
