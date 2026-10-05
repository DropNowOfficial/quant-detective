import json
from decimal import Decimal,localcontext
from dataclasses import replace
from unittest.mock import patch
from tests.test_replay import ReplayTest
from tests.test_store import StoreTest
from tests.test_contracts import raw_bar
from trade_monitor.contracts import parse_bar,canonical_bar
from trade_monitor.quality import fingerprint
from trade_monitor.notifications import LocalLogSink
from trade_monitor.store import ReplayStore

class ReplayRegression(ReplayTest):
    def test_retransmissions_and_evolving_incomplete_do_not_abort(self):
        first=self.rows[0]
        self.rows=[{**first,'bar':raw_bar(complete=False,received_ms=80000)}, {**first,'bar':raw_bar(complete=False,received_ms=90000,volume='1')},first,{**first,'bar':{**first['bar'],'received_ms':120020}},{**first,'bar':{**first['bar'],'received_ms':120030}}];self.save()
        report=self.run_to('a');self.assertEqual(report['new_signals'],1)
    def test_resume_at_every_boundary_matches_uninterrupted(self):
        rows=[self.rows[0],self.rows[1],self.rows[0],self.rows[2]]
        self.rows=rows;self.save();fresh=self.run_to('full')
        for cut in range(1,len(rows)):
            self.rows=rows[:cut];self.save();self.run_to('cut'+str(cut))
            self.rows=rows;self.save();resumed=self.run_to('cut'+str(cut))
            self.assertEqual(resumed['event_ids'],fresh['event_ids'])
            self.assertEqual(resumed['state'],fresh['state'])
    def test_actual_gap_produces_health_notification(self):
        self.rows=[self.rows[0],self.rows[2]];self.save();self.run_to('a')
        events=[json.loads(x) for x in (self.root/'a/notifications.jsonl').read_text().splitlines()]
        self.assertTrue(any(e['kind']=='health' and e['code']=='quality_gap' for e in events))

class StoreRegression(StoreTest):
    def test_rotated_corruption_is_quarantined_once(self):
        p=self.root/'out.jsonl';sink=LocalLogSink(p,max_bytes=20)
        sink.send({'event_id':'a'});sink.send({'event_id':'b'})
        part=next(self.root.glob('out.jsonl.part-*'))
        with part.open('ab') as f:f.write(b'{"event_id":')
        with self.assertRaises(ValueError):sink.send({'event_id':'c'})
        self.assertTrue(sink.send({'event_id':'c'}).accepted)
        self.assertEqual(len(list(self.root.glob('*corrupt-*'))),1)
    def test_retry_requires_successful_durability_barrier(self):
        sink=LocalLogSink(self.root/'out.jsonl')
        with patch('trade_monitor.notifications.os.fsync',side_effect=OSError('failure')):
            with self.assertRaises(OSError):sink.send({'event_id':'a'})
            with self.assertRaises(OSError):sink.send({'event_id':'a'})
    def test_stale_checkpoint_commit_rejected(self):
        from trade_monitor.quality import check_bar,QualityState
        from trade_monitor.rules import evaluate
        from trade_monitor.contracts import RuleState
        newer=replace(self.bar,start_ms=120000,end_ms=180000,received_ms=180010)
        q=check_bar(newer,QualityState(newer.instrument.key()),now_ms=180010,stale_after_ms=120000,session_open=True)
        d=evaluate(newer,self.spec,RuleState(),q);store=ReplayStore(self.path);store.commit(newer,d,q.state)
        with self.assertRaises(ValueError):store.commit(self.bar,self.d,self.q.state)
        self.assertEqual(store.checkpoint(self.bar.instrument.key(),'demo','1')[0].last_complete_end_ms,180000)
    def test_decimal_identity_never_uses_ambient_precision(self):
        a=parse_bar(raw_bar(volume='12345678901234567890123456789'));b=parse_bar(raw_bar(volume='12345678901234567890123456790'))
        self.assertNotEqual(fingerprint(a),fingerprint(b))
        text=canonical_bar(a)
        with localcontext() as ctx:
            ctx.prec=5;self.assertEqual(canonical_bar(a),text)
