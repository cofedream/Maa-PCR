"""Independent durable bot run records. Safe inter-run checkpoint restoration; no mid-run takeover.

Each explicitly started batch gets a new file; confirmed clears are append-only
and deduplicated by server enter ID inside that batch. Caller holds the shared
bot transaction lock while appending. Credentials are never accepted here.
"""
from pathlib import Path
import json,os
from datetime import datetime,timezone
from time import sleep


def is_retired_invitation(event, intent):
    evidence = event.get('evidence')
    return (event.get('state') == 'historical_unknown' and event.get('operation') == 'opening_invite'
            and event.get('resolution') == 'user_confirmed_retired'
            and event.get('original_result_unknown') is True and event.get('replay_forbidden') is True
            and type(event.get('enter_id')) is int and event['enter_id'] > 0
            and type(evidence) is dict and evidence.get('source') == 'user_confirmation'
            and evidence.get('retired') is True
            and all(event.get(key) == intent.get(key) for key in
                    ('operation', 'operation_id', 'binding', 'enter_id', 'expected_roles')))


def is_reroll_unknown_history(event, intent):
    """A reroll ENTER/RETIRE that TOP could not settle; replay stays forbidden.

    Unlike a battle intent this must not block the next start: the next acquire
    begins with a fresh TOP and retires or reuses whatever run actually exists.
    """
    return (event.get('state') == 'historical_unknown'
            and event.get('recovery_scope') == 'labyrinth_reroll'
            and event.get('operation') in ('enter', 'retire')
            and event.get('original_result_unknown') is True
            and event.get('replay_forbidden') is True
            and type(event.get('resolution')) is str and bool(event['resolution'])
            and all(event.get(key) == intent.get(key) for key in ('operation', 'operation_id', 'binding')))


class RunRecord:
    def __init__(self,directory,run_id,binding,*,resume=False):
        if len(run_id)!=32 or any(x not in '0123456789abcdef' for x in run_id):raise ValueError('invalid_run_id')
        self.path=Path(directory)/'runs'/run_id/'events.jsonl';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.run_id=run_id;self.binding=binding.payload();self.clears=set()
        if resume:
            rows=[json.loads(line) for line in self.path.read_text(encoding='utf-8').splitlines()]
            if (not rows or rows[0].get('stage')!='started'
                    or any(r.get('run_id')!=run_id or r.get('binding')!=self.binding for r in rows)
                    or any(r.get('stage')=='finished' for r in rows)):
                raise ValueError('batch_resume_record_invalid')
            self.clears={r['enter_id'] for r in rows if r.get('stage')=='cleared'}
        else:
            with self.path.open('x',encoding='utf-8'):pass
    def append(self,stage,**fields):
        allowed={'started':{'settings'},'cleared':{'enter_id','guild_id','difficulty','frame','completed'},
                 'failed':{'enter_id','guild_id','difficulty','frame','completed','failed','outcome'},
                 'finished':{'outcome','completed','batch_complete'},'adapter_error':{'error_type','frames'}}
        optional={'timings'} if stage=='finished' else {'causes'} if stage=='adapter_error' else set()
        if stage not in allowed or set(fields)-optional!=allowed[stage]:raise ValueError('record_fields_invalid')
        if 'timings' in fields:
            from .timing import Timings
            if type(fields['timings']) is not dict or set(fields['timings'])!=Timings.categories:
                raise ValueError('record_timings_invalid')
        if stage=='cleared':
            uid=fields['enter_id']
            if type(uid) is not int or uid<=0 or uid in self.clears:raise ValueError('duplicate_or_invalid_clear')
        event=dict(version=1,run_id=self.run_id,binding=self.binding,stage=stage,
                   at=datetime.now(timezone.utc).isoformat(),**fields)
        with self.path.open('a',encoding='utf-8',newline='\n') as handle:
            handle.write(json.dumps(event,ensure_ascii=False,separators=(',',':'))+'\n')
            handle.flush();os.fsync(handle.fileno())
        if stage=='cleared':self.clears.add(uid)

    def checkpoint(self,settings,stage,completed,failed):
        # Only actual record counters persist; characters/relics and live inputs never do.
        value=dict(version=1,run_id=self.run_id,binding=self.binding,settings=settings,
                   stage=stage,completed=completed,failed=failed)
        path=self.path.parents[2]/'batch-checkpoint.json';tmp=path.with_suffix('.tmp')
        with tmp.open('w',encoding='utf-8',newline='\n') as handle:
            json.dump(value,handle,ensure_ascii=False);handle.flush();os.fsync(handle.fileno())
        # On Windows a reader can briefly keep the old checkpoint from being replaced.
        # Retry only the atomic replacement of the already synced temp file.
        for attempt in range(6):
            try:
                tmp.replace(path)
                return
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32) or attempt == 5:
                    raise
                sleep(.1)

    @staticmethod
    def recover(directory,binding,settings):
        path=Path(directory)/'batch-checkpoint.json'
        if not path.exists():return None
        value=json.loads(path.read_text(encoding='utf-8'))
        if (value.get('version')!=1 or value.get('stage')!='ready'
                or value.get('binding')!=binding.payload() or value.get('settings')!=settings):return None
        uid=value.get('run_id')
        if type(uid) is not str or len(uid)!=32 or any(x not in '0123456789abcdef' for x in uid):raise ValueError('invalid_run_id')
        rows=[json.loads(line) for line in (Path(directory)/'runs'/value['run_id']/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        if not rows or rows[0].get('settings')!=settings or rows[-1].get('stage') not in ('started','cleared','failed'):return None
        goals=settings['goals'];counts={str(g['guild_id']):0 for g in goals};failed=dict(counts)
        seen=set()
        for row in rows:
            if row.get('binding')!=binding.payload() or row.get('run_id')!=value['run_id']:raise ValueError('batch_resume_record_invalid')
            if row.get('stage') in ('cleared','failed'):
                uid=row['enter_id'];guild=str(row['guild_id'])
                if uid in seen or guild not in counts:raise ValueError('batch_resume_record_invalid')
                seen.add(uid)
                goal=next(g for g in goals if str(g['guild_id'])==guild)
                if row['stage']=='cleared' or goal.get('mode','clears')=='attempts':counts[guild]+=1
                if row['stage']=='failed':failed[guild]+=1
        if value.get('completed')!=counts or value.get('failed')!=failed:raise ValueError('batch_resume_record_invalid')
        return value
