"""Durable result observations; never authorize replay of an unresolved consumer.

The Journal lock is held by the business caller. Native adapters write this
checkpoint on the guarded result frame, before close/navigation can fail.
"""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from uuid import uuid4

DISPOSITION = 'native_result_observed_postread_pending'
ANCHORS = ('ExecResultTitle', 'ExecResultComplete', 'ExecResultButton')


def require(ok, reason):
    if not ok:
        raise ValueError('result_checkpoint:' + reason)


def action_completed(receipt, name):
    nodes = [n for n in receipt.get('nodes', []) if n.get('name') == name and n.get('completed')]
    return (receipt.get('tasker_inited') is True and len(nodes) == 1
            and (nodes[0].get('action') or {}).get('success') is True
            and (nodes[0].get('action') or {}).get('name') == name)


def check(record, original, prefix_sha, kind):
    require(record.get('version') == 1 and record.get('kind') == kind, 'kind')
    require(record.get('original') == original and record.get('root_sha256') == prefix_sha, 'intent_binding')
    require(original.get('state') == 'in_flight', 'original_state')
    require(0 <= record['at'] - original['at'] <= 180, 'observation_time')
    before, quote = record['before'], record['quote']
    require((kind == 'abyss' and original.get('scope') == 'abyss-outpost')
            or (kind == 'event' and original.get('scope') == 'event-hard')
            or (kind == 'preset' and 'scope' not in original and type(original.get('slot')) is int), 'namespace')
    if kind == 'abyss':
        from .abyss import check_quote
        require(check_quote(quote, before) == 'ready' and quote == original['quote'], 'quote')
        consumer = 'AbyssTicketCommit'
    else:
        from .preset_scan import key
        rows = [r for r in before['rows'] if r['remaining'] > 0] if kind == 'event' else before['rows']
        require(quote.get('identities') == [list(key(r)) for r in rows], 'identities')
        require(all(quote.get(k) == v for k, v in original['quote'].items()), 'quote')
        require(quote.get('verified') is True, 'quote_verified')
        require(isinstance(quote.get('counts'), list) and len(quote['counts']) == len(rows)
                and all(type(n) is int and n > 0 for n in quote['counts']), 'counts')
        consumer = 'ExecConfirm'
    require(action_completed(record['consumer'], consumer), 'consumer_not_completed')
    image = record['image']
    require(image.get('kind') == 'result' and type(image.get('task_id')) is int, 'result_frame')
    path = Path(image['path'])
    require(path.is_absolute() and path.is_file() and not path.is_symlink(), 'result_file')
    require(sha256(path.read_bytes()).hexdigest() == image['sha256'], 'result_file_changed')
    anchors = record['anchors']
    require(set(anchors) == set(ANCHORS), 'anchors')
    for name in ANCHORS:
        words = anchors[name].get('filtered', [])
        require(len(words) == 1 and words[0].get('score', 0) >= .9, 'anchor_score')
    import re
    for name, pattern in zip(ANCHORS, (r'^扫荡结果$', r'^扫荡完成[！!]$', r'^确认$')):
        require(re.fullmatch(pattern, anchors[name]['filtered'][0]['text'].strip()) is not None, 'anchor_text')
    return record


class ResultCheckpoint:
    def __init__(self, journal, *, kind, before=None, quote=None, original=None, prefix=None):
        self.journal, self.kind = journal, kind
        self.prefix = journal.path.read_bytes() if prefix is None else prefix
        self.original = deepcopy(journal.events()[-1] if original is None else original)
        self.root_sha = sha256(self.prefix).hexdigest()
        self.path = journal.directory / 'results' / (self.root_sha + '.json')
        self.before, self.quote = deepcopy(before), deepcopy(quote)

    def capture(self, consumer, image, anchors):
        record = dict(version=1, kind=self.kind, original=self.original, root_sha256=self.root_sha,
                      before=self.before, quote=self.quote, consumer=consumer, image=image,
                      anchors=anchors, at=time.time())
        check(record, self.original, self.root_sha, self.kind)
        # Flush the actual frame before publishing the atomic checkpoint.
        with Path(image['path']).open('r+b') as handle:
            handle.flush()
            os.fsync(handle.fileno())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            return self.read()  # immutable, never replace the first observation
        temporary = self.path.with_name(self.path.name + '.' + uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(record, handle, ensure_ascii=False, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        return record

    def read(self):
        if not self.path.exists():
            return None
        require(not self.path.is_symlink(), 'checkpoint_symlink')
        return check(json.loads(self.path.read_text(encoding='utf-8')), self.original, self.root_sha, self.kind)


def bind(port, journal, kind, before, quote):
    checkpoint = ResultCheckpoint(journal, kind=kind, before=before, quote=quote)
    # Fake business ports do not create native facts. Only adapters expose this.
    if callable(getattr(port, 'bind_result_checkpoint', None)):
        port.bind_result_checkpoint(checkpoint.capture)
    return checkpoint


def recover(journal, events, kind):
    """Append a phase-only terminal; post-counters/home remain explicitly unknown."""
    raw = journal.path.read_bytes() if journal.path.exists() else b''
    lines = raw.splitlines(keepends=True)
    for index, event in enumerate(events):
        if event.get('disposition') != DISPOSITION:
            continue
        checkpoint = ResultCheckpoint(journal, kind=kind, original=events[index-1], prefix=b''.join(lines[:index]))
        record = checkpoint.read()
        require(record is not None and event.get('checkpoint_sha256') == sha256(checkpoint.path.read_bytes()).hexdigest(), 'terminal_binding')
        require(event.get('settlement_verified') is False and event.get('post_counts_verified') is False
                and event.get('home_verified') is False and event.get('retry_permitted') is False
                and 'remaining' not in event, 'terminal_flags')
        require(event.get('original') == record['original'], 'terminal_original')
        for key in ('scope','slot','device','context','intent_id','operation','abyss_identity','game_day'):
            require(event.get(key) == record['original'].get(key), 'terminal_' + key)
        if kind != 'abyss':
            from .preset_scan import key
            require(event.get('identities') == [list(key(r)) for r in record['before']['rows']], 'terminal_identities')
            require(event.get('event_identity') == record['before'].get('event_identity'), 'terminal_event')
    if not events or events[-1]['state'] != 'in_flight':
        return events
    checkpoint = ResultCheckpoint(journal, kind=kind)
    record = checkpoint.read()
    if record is None:
        return events
    original = events[-1]
    terminal = dict(state='settled', disposition=DISPOSITION, game_day=original['game_day'], at=time.time(),
                    settlement_verified=False, post_counts_verified=False, home_verified=False,
                    consumption_result_verified=True, retry_permitted=False, original=original,
                    checkpoint_sha256=sha256(checkpoint.path.read_bytes()).hexdigest())
    for key in ('scope','slot','device','context','intent_id','operation','abyss_identity'):
        if key in original:
            terminal[key] = original[key]
    if kind != 'abyss':
        from .preset_scan import key
        terminal['identities'] = [list(key(r)) for r in record['before']['rows']]
        if record['before'].get('event_identity') is not None:
            terminal['event_identity'] = record['before']['event_identity']
    journal.append(terminal)
    return journal.events()


def annotate(report, checkpoint):
    try:
        record = checkpoint.read() if checkpoint is not None else None
    except (Exception, KeyboardInterrupt) as exc:
        report.update(outcome='result_checkpoint_invalid', error=str(exc), recovery_available=False, retry_permitted=False)
        return
    if record is not None and not report['settlement_verified']:
        report.update(consumption_result_verified=True, post_counts_verified=False, retry_permitted=False,
                      recovery_available=True, outcome='result_verified_followup_incomplete')
