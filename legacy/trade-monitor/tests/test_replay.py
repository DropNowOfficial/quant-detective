import json
import tempfile
import unittest
from pathlib import Path
from trade_monitor.replay import run_replay
from tests.test_contracts import raw_bar

class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.rules=self.root/'rules.json'
        self.rules.write_text(json.dumps({'schema_version':1,'synthetic':True,'stale_after_ms':120000,'rules':[{'id':'demo','version':'1','purpose':'entry','lower':'106','upper':'108','protection_fraction':None}]}))
        self.input=self.root/'input.jsonl'
        self.rows=[]
        for n,price in enumerate(['106.5','109','106.5']):
            self.rows.append({'synthetic':True,'session_open':True,'bar':raw_bar(start_ms=60000+n*60000,end_ms=120000+n*60000,received_ms=120010+n*60000,open=price,high=price,low=price,close=price)})
        self.save()
    def save(self): self.input.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
    def run_to(self,name): return run_replay(self.input,self.rules,self.root/(name+'.db'),self.root/name)
    def test_replay_reproducible_and_evidence_complete(self):
        a=self.run_to('a'); b=self.run_to('b')
        self.assertEqual(a,b)
        self.assertEqual(a['new_signals'],2)
        self.assertEqual(a['mode'],'offline_replay'); self.assertTrue(a['synthetic_inputs'])
        self.assertFalse(a['production_ready']); self.assertIsNone(a['delivered']); self.assertIsNone(a['read'])
        self.assertEqual(a['latency_ms']['bar_end_to_received'],{'p50':10,'p95':10})
        self.assertIsNone(a['latency_ms']['calculation_to_channel'])
        self.assertEqual((self.root/'a/events.jsonl').read_text(),(self.root/'b/events.jsonl').read_text())
        self.assertEqual(self.run_to('a')['new_signals'],0)
    def test_invalid_line_does_not_erase_valid_checkpoint(self):
        self.rows.insert(1,{'synthetic':True,'bar':{'nonsense':1}}); self.save()
        with self.assertRaisesRegex(ValueError,'line 2'): self.run_to('a')
        from trade_monitor.store import ReplayStore
        from trade_monitor.contracts import parse_bar
        s=ReplayStore(self.root/'a.db').checkpoint(parse_bar(raw_bar()).instrument.key(),'demo','1')
        self.assertEqual(s[0].last_complete_end_ms,120000)
    def test_unknown_rule_no_signals_and_session_unknown(self):
        data=json.loads(self.rules.read_text()); data['rules'][0]['purpose']='mystery'
        self.rules.write_text(json.dumps(data))
        self.assertEqual(self.run_to('a')['new_signals'],0)
        del self.rows[0]['session_open']; self.save()
        self.assertIn('quality_session_unknown',self.run_to('b')['reason_counts'])
    def test_reject_missing_synthetic_label_and_invalid_config(self):
        del self.rows[0]['synthetic']; self.save()
        with self.assertRaises(ValueError): self.run_to('a')
        self.rules.write_text('{"rules": []}')
        with self.assertRaises(ValueError): self.run_to('b')
