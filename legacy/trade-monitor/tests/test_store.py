import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from trade_monitor.contracts import parse_bar,RuleSpec,RuleState
from trade_monitor.quality import QualityState,check_bar
from trade_monitor.rules import evaluate
from trade_monitor.store import ReplayStore
from trade_monitor.notifications import LocalLogSink
from tests.test_contracts import raw_bar

class StoreTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.path=self.root/'state.db'
        self.bar=parse_bar(raw_bar()); self.spec=RuleSpec('demo','1','entry',D('106'),D('108'),None)
        self.q=check_bar(self.bar,QualityState(self.bar.instrument.key()),now_ms=120010,stale_after_ms=120000,session_open=True)
        self.d=evaluate(self.bar,self.spec,RuleState(),self.q)
    def test_restart_and_outbox(self):
        store=ReplayStore(self.path)
        self.assertTrue(store.commit(self.bar,self.d,self.q.state))
        self.assertFalse(store.commit(self.bar,self.d,self.q.state))
        reopened=ReplayStore(self.path)
        self.assertEqual(len(reopened.pending()),1)
        self.assertEqual(reopened.checkpoint(self.bar.instrument.key(),'demo','1'),(self.q.state,self.d.next_state))
        event=reopened.pending()[0]
        receipt=LocalLogSink(self.root/'out.jsonl').send(event)
        self.assertTrue(receipt.accepted); self.assertIsNone(receipt.delivered); self.assertIsNone(receipt.read)
        # Crash before marking acceptance: retry appends no duplicate logical event.
        LocalLogSink(self.root/'out.jsonl').send(reopened.pending()[0])
        self.assertEqual(len((self.root/'out.jsonl').read_text().splitlines()),1)
        reopened.mark_attempt(event['event_id'],'accepted',None)
        self.assertEqual(reopened.pending(),[])
        with self.assertRaises(ValueError): reopened.commit(replace(self.bar,volume=D('1')),self.d,self.q.state)
    def test_transaction_rollback(self):
        ReplayStore(self.path)
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TRIGGER forced_failure BEFORE INSERT ON checkpoints BEGIN SELECT RAISE(ABORT, 'test crash'); END")
        with self.assertRaises(sqlite3.IntegrityError): ReplayStore(self.path).commit(self.bar,self.d,self.q.state)
        with sqlite3.connect(self.path) as c:
            for table in ('events','outbox','checkpoints'):
                self.assertEqual(c.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
    def test_concurrent_commits_and_failed_attempt(self):
        ReplayStore(self.path)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:ReplayStore(self.path).commit(self.bar,self.d,self.q.state),range(4)))
        self.assertEqual(results.count(True),1)
        store=ReplayStore(self.path); store.mark_attempt(self.d.event_id,'failed','disk full')
        self.assertEqual(store.pending()[0]['status'],'failed')
        with self.assertRaises(ValueError): store.mark_attempt(self.d.event_id,'delivered',None)
    def test_suppressed_no_signal_health_channels(self):
        store=ReplayStore(self.path)
        for n,kind in enumerate(('suppressed','no_signal','health')):
            store.commit(self.bar,replace(self.d,event_id=str(n),kind=kind),self.q.state)
        self.assertEqual([x['kind'] for x in store.pending()],['health'])
    def test_sink_partial_line_quarantine_and_rotation(self):
        p=self.root/'out.jsonl'; p.write_text('{"event_id":')
        sink=LocalLogSink(p,max_bytes=300)
        with self.assertRaises(ValueError): sink.send({'event_id':'a','kind':'signal'})
        self.assertTrue(list(self.root.glob('*.corrupt-*')))
        for i in range(5): sink.send({'event_id':str(i),'kind':'signal','text':'x'*100})
        sink.send({'event_id':'0','kind':'signal','text':'x'*100})
        rows=[]
        for path in self.root.glob('out.jsonl*'):
            if 'corrupt-' not in path.name: rows += [json.loads(x) for x in path.read_text().splitlines()]
        self.assertEqual(len(rows),5)
