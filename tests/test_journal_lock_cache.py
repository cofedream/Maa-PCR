"""A locked ledger reads old history once without weakening durable intents."""
import json
import os
from pathlib import Path

import pytest

from pcr_maa.preset_execution import Journal


def seed(journal, pairs=100):
    journal.directory.mkdir(parents=True, exist_ok=True)
    events = [dict(state=state, operation='synthetic', operation_id=str(n), nested={'value': n})
              for n in range(pairs) for state in ('in_flight', 'settled')]
    journal.path.write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
    return events


def test_locked_transactions_read_history_once_and_keep_fsync(tmp_path, monkeypatch):
    journal = Journal(tmp_path)
    old = seed(journal, 1000)
    prefix = journal.path.read_bytes()
    reads, syncs = [], []
    read_text, fsync = Path.read_text, os.fsync

    def read(path, *args, **kwargs):
        if path == journal.path:
            reads.append(1)
        return read_text(path, *args, **kwargs)

    def sync(fd):
        syncs.append(1)
        return fsync(fd)

    monkeypatch.setattr(Path, 'read_text', read)
    monkeypatch.setattr(os, 'fsync', sync)
    with journal.locked():
        assert journal.events() == old
        for n in range(20):
            assert journal.last_event()['state'] == 'settled'
            intent = dict(state='in_flight', operation='synthetic', operation_id=f'new-{n}')
            journal.append(intent)
            assert journal.last_event() == intent
            journal.append(dict(intent, state='settled'))
        assert len(journal.events()) == len(old) + 40
    assert len(reads) == 1
    assert len(syncs) == 40
    assert journal.path.read_bytes().startswith(prefix)
    assert len(journal.events()) == len(old) + 40
    assert len(reads) == 2


def test_cached_returns_and_append_inputs_are_independent(tmp_path):
    journal = Journal(tmp_path)
    seed(journal, 1)
    with journal.locked():
        journal.events()[0]['nested']['value'] = 'changed'
        journal.last_event()['nested']['value'] = 'changed'
        assert journal.events()[0]['nested']['value'] == 0
        event = dict(state='in_flight', nested={'value': [1]})
        journal.append(event)
        event['nested']['value'].append(2)
        assert journal.last_event()['nested']['value'] == [1]
    assert Journal(tmp_path).events()[-1]['nested']['value'] == [1]


@pytest.mark.parametrize('change', ['append', 'replace', 'truncate', 'same_size', 'delete'])
def test_external_change_revokes_cached_reads_and_append(tmp_path, change):
    journal = Journal(tmp_path)
    seed(journal, 2)
    with journal.locked():
        journal.events()
        if change == 'append':
            with journal.path.open('a', encoding='utf-8') as stream:
                stream.write('{"state":"in_flight"}\n')
        elif change == 'replace':
            replacement = tmp_path / 'replacement'
            replacement.write_bytes(journal.path.read_bytes())
            replacement.replace(journal.path)
        elif change == 'truncate':
            journal.path.write_text('', encoding='utf-8')
        elif change == 'same_size':
            before = journal.path.stat()
            journal.path.write_bytes(journal.path.read_bytes().replace(b'synthetic', b'different'))
            os.utime(journal.path, ns=(before.st_atime_ns, before.st_mtime_ns + 10_000_000))
        else:
            journal.path.unlink()
        modified = journal.path.read_bytes() if journal.path.exists() else None
        for action in (journal.events, journal.last_event,
                       lambda: journal.append(dict(state='in_flight'))):
            with pytest.raises(ValueError, match='changed'):
                action()
        assert (journal.path.read_bytes() if journal.path.exists() else None) == modified


def test_new_lock_reloads_and_never_hides_unknown(tmp_path):
    journal = Journal(tmp_path)
    with journal.locked():
        assert journal.last_event() is None
        journal.append(dict(state='in_flight', operation='synthetic'))
    with journal.locked():
        assert journal.last_event()['state'] == 'in_flight'
    journal.path.write_text('{broken', encoding='utf-8')
    with journal.locked(), pytest.raises(ValueError):
        journal.last_event()


def test_failed_flush_cannot_leave_a_trusted_cache(tmp_path, monkeypatch):
    journal = Journal(tmp_path)
    seed(journal, 1)
    with journal.locked():
        journal.events()
        monkeypatch.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('synthetic disk failure')))
        with pytest.raises(OSError):
            journal.append(dict(state='in_flight'))
        with pytest.raises(ValueError, match='changed'):
            journal.last_event()
    assert Journal(tmp_path).events()[-1]['state'] == 'in_flight'


def test_locked_any_event_scans_the_cache_without_copying(tmp_path, monkeypatch):
    journal = Journal(tmp_path)
    seed(journal, 1000)
    from pcr_maa import preset_execution
    copies = []
    real = preset_execution.deepcopy
    monkeypatch.setattr(preset_execution, 'deepcopy',
                        lambda value: copies.append(1) or real(value))
    with journal.locked():
        assert journal.any_event(lambda event: event.get('operation') == 'synthetic')
        assert not journal.any_event(lambda event: event.get('operation') == 'missing')
        assert copies == []
        journal.append(dict(state='in_flight', operation='appended'))
        assert journal.any_event(lambda event: event.get('operation') == 'appended')
        assert copies == []
        assert journal.events()  # the compatibility copy is still available
        assert copies == [1]


def test_any_event_reads_the_file_outside_a_lock(tmp_path):
    journal = Journal(tmp_path)
    seed(journal, 2)
    assert journal.any_event(lambda event: event.get('state') == 'settled')
    assert not journal.any_event(lambda event: event.get('state') == 'historical_unknown')
    assert Journal(tmp_path).any_event(lambda event: event.get('operation') == 'synthetic')


def test_paired_tail_checks_all_pairs_and_only_copies_the_returned_event(tmp_path, monkeypatch):
    from pcr_maa import preset_execution
    journal = Journal(tmp_path)
    rows = seed(journal, 3)
    original = preset_execution.deepcopy
    copied = []
    def copy_event(event):
        assert isinstance(event, dict)
        copied.append(event['state'])
        return original(event)
    monkeypatch.setattr(preset_execution, 'deepcopy', copy_event)
    with journal.locked():
        paired, tail = journal.paired_tail('operation_id', 'binding')
        assert paired and tail == rows[-1]
        tail['nested']['value'] = 'external change'
        journal.append(dict(state='in_flight', operation='pending'))
        assert journal.paired_tail('operation_id', 'binding') == (True, dict(state='in_flight', operation='pending'))
        assert copied == ['settled','in_flight']
        rows[1]['operation'] = 'another'
        journal.path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
        with pytest.raises(ValueError, match='changed'):
            journal.paired_tail('operation')
    with journal.locked():
        assert journal.paired_tail('operation','operation_id','binding') == (False, None)
        assert journal.paired_tail('operation_id','binding') == (True, rows[-1])
        assert copied[-1] == 'settled'


def test_any_event_refuses_an_external_change_like_the_other_reads(tmp_path):
    journal = Journal(tmp_path)
    seed(journal, 2)
    with journal.locked():
        assert journal.any_event(lambda event: True)
        journal.path.write_text('', encoding='utf-8')
        with pytest.raises(ValueError, match='changed'):
            journal.any_event(lambda event: True)

