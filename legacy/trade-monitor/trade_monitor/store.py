"""Atomic, restartable SQLite evidence and outbox. No external delivery."""
from dataclasses import asdict
import json
import sqlite3
from .contracts import canonical,canonical_bar,RuleState
from .quality import QualityState

class ReplayStore:
    def __init__(self,path):
        self.path=str(path)
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, bar TEXT NOT NULL, decision TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints(instrument TEXT, rule_id TEXT, version TEXT, quality TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(instrument,rule_id,version));
            CREATE TABLE IF NOT EXISTS outbox(event_id TEXT PRIMARY KEY REFERENCES events, kind TEXT NOT NULL, status TEXT NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY, event_id TEXT, bar TEXT, decision TEXT, UNIQUE(event_id,bar,decision));
            CREATE TABLE IF NOT EXISTS cursors(stream TEXT, rule_id TEXT, version TEXT, line INTEGER, prefix TEXT, PRIMARY KEY(stream,rule_id,version));
            CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, event_id TEXT, status TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS rule_definitions(rule_id TEXT, version TEXT, definition TEXT NOT NULL, PRIMARY KEY(rule_id,version));
            ''')
    def connect(self):
        c=sqlite3.connect(self.path,timeout=30)
        c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA synchronous=FULL')
        return c
    def bind_rules(self,specs,stale_after_ms):
        """A rule identity may not silently acquire new semantics on resume."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for spec in specs:
                definition=canonical({'rule':asdict(spec),'stale_after_ms':stale_after_ms})
                old=c.execute('SELECT definition FROM rule_definitions WHERE rule_id=? AND version=?',(spec.id,spec.version)).fetchone()
                if old and old[0]!=definition:
                    raise ValueError('rule content changed: increment rule version and use a fresh replay database')
                if not old:
                    # Older databases have no complete rule/freshness receipt.
                    for (payload,) in c.execute('SELECT decision FROM events'):
                        evidence=json.loads(payload)['evidence']
                        if evidence.get('rule_id')==spec.id and evidence.get('rule_version')==spec.version:
                            raise ValueError('legacy rule version is unsealed: use a fresh replay database')
                    c.execute('INSERT INTO rule_definitions VALUES(?,?,?)',(spec.id,spec.version,definition))
    def commit(self,bar,decision,quality,*,cursor=None):
        b=canonical_bar(bar); d=canonical(asdict(decision)); e=decision.evidence
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            found=c.execute('SELECT bar,decision FROM events WHERE event_id=?',(decision.event_id,)).fetchone()
            inserted=found is None
            if found and found!=(b,d):
                old=json.loads(found[1])
                if decision.kind in ('signal','no_signal') or old['kind'] in ('signal','no_signal') or old['code']!=decision.code:
                    raise ValueError('rejected: conflicting event commit')
            current=c.execute('SELECT quality FROM checkpoints WHERE instrument=? AND rule_id=? AND version=?',(bar.instrument.key(),e['rule_id'],e['rule_version'])).fetchone()
            if current:
                previous=json.loads(current[0])['last_complete_end_ms']
                if previous is not None and (quality.last_complete_end_ms is None or quality.last_complete_end_ms<previous):
                    raise ValueError('rejected: stale checkpoint commit')
            if inserted:c.execute('INSERT INTO events VALUES(?,?,?)',(decision.event_id,b,d))
            c.execute('INSERT OR IGNORE INTO observations(event_id,bar,decision) VALUES(?,?,?)',(decision.event_id,b,d))
            c.execute('INSERT INTO checkpoints VALUES(?,?,?,?,?) ON CONFLICT(instrument,rule_id,version) DO UPDATE SET quality=excluded.quality,state=excluded.state',
                (bar.instrument.key(),e['rule_id'],e['rule_version'],canonical(asdict(quality)),canonical(asdict(decision.next_state))))
            if inserted and decision.kind in ('signal','health'):
                c.execute('INSERT INTO outbox VALUES(?,?,?,NULL)',(decision.event_id,decision.kind,'pending'))
            if cursor:
                stream,line,prefix=cursor
                c.execute('INSERT INTO cursors VALUES(?,?,?,?,?) ON CONFLICT(stream,rule_id,version) DO UPDATE SET line=excluded.line,prefix=excluded.prefix',(stream,e['rule_id'],e['rule_version'],line,prefix))
        return inserted
    def checkpoint(self,instrument_key,rule_id,version):
        with self.connect() as c:
            row=c.execute('SELECT quality,state FROM checkpoints WHERE instrument=? AND rule_id=? AND version=?',(instrument_key,rule_id,version)).fetchone()
        return (QualityState(**json.loads(row[0])),RuleState(**json.loads(row[1]))) if row else None
    def pending(self):
        with self.connect() as c:
            rows=c.execute("SELECT e.decision,o.status,o.error FROM outbox o JOIN events e USING(event_id) WHERE status!='accepted' ORDER BY e.rowid").fetchall()
        return [{**json.loads(d),'status':s,'error':e} for d,s,e in rows]
    def mark_attempt(self,event_id,status,error=None):
        if status not in ('accepted','failed'): raise ValueError('only local acceptance or failure can be recorded')
        with self.connect() as c:
            if not c.execute('SELECT 1 FROM outbox WHERE event_id=?',(event_id,)).fetchone(): raise ValueError('unknown event')
            c.execute("UPDATE outbox SET status=?,error=? WHERE event_id=? AND status!='accepted'",(status,error,event_id))
            c.execute('INSERT INTO attempts(event_id,status,error) VALUES(?,?,?)',(event_id,status,error))
    def events(self):
        with self.connect() as c: rows=c.execute('SELECT decision FROM events ORDER BY rowid').fetchall()
        return [json.loads(r[0]) for r in rows]

    def cursor(self,stream,rule_id,version):
        with self.connect() as c:
            return c.execute('SELECT line,prefix FROM cursors WHERE stream=? AND rule_id=? AND version=?',(stream,rule_id,version)).fetchone()
