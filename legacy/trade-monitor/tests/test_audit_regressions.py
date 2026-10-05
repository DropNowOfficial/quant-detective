import json
import tempfile
import unittest
from pathlib import Path
from tests.test_contracts import raw_bar
from trade_monitor.replay import run_replay, load_rules
from trade_monitor.store import ReplayStore


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rules = self.root/'rules.json'
        self.rules.write_text(json.dumps(dict(schema_version=1, synthetic=True, stale_after_ms=120000,
            rules=[dict(id='demo', version='1', purpose='entry', lower='106', upper='108', protection_fraction=None)])))
        self.input = self.root/'input.jsonl'
        self.rows = [dict(synthetic=True, session_open=True, bar=raw_bar(
            start_ms=60000+i*60000,end_ms=120000+i*60000,received_ms=120010+i*60000)) for i in range(3)]
        self.save()

    def save(self):
        self.input.write_text(''.join(json.dumps(r)+'\n' for r in self.rows))

    def run_replay(self):
        return run_replay(self.input,self.rules,self.root/'state.db',self.root/'out')

    def test_json_array_config_is_clear_value_error(self):
        self.rules.write_text('[]')
        with self.assertRaisesRegex(ValueError,'configuration'):
            load_rules(self.rules)

    def test_json_array_input_is_line_numbered_error(self):
        self.input.write_text('[]\n')
        with self.assertRaisesRegex(ValueError,'line 1'):
            self.run_replay()

    def test_incomplete_bar_never_has_negative_completion_latency(self):
        self.rows=[dict(synthetic=True,session_open=True,bar=raw_bar(complete=False,received_ms=80000))]
        self.save()
        report=self.run_replay()
        self.assertIsNone(report['latency_ms']['bar_end_to_received'])

    def test_late_failure_preserves_known_acceptance(self):
        self.run_replay()
        store=ReplayStore(self.root/'state.db')
        event=next(e for e in store.events() if e['kind']=='signal')
        store.mark_attempt(event['event_id'],'failed','late worker result')
        self.assertEqual(store.pending(),[])
        with store.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM attempts ORDER BY id DESC LIMIT 1').fetchone()[0],'failed')

    def test_resume_rejects_truncated_committed_input(self):
        self.run_replay()
        self.rows=self.rows[:1];self.save()
        with self.assertRaisesRegex(ValueError,'truncated'):
            self.run_replay()

    def test_rule_content_change_requires_new_version(self):
        self.run_replay()
        data=json.loads(self.rules.read_text());data['rules'][0]['lower']='105'
        self.rules.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'rule.*version'):
            self.run_replay()

    def test_formatting_only_rule_change_cannot_bypass_resume_prefix(self):
        self.run_replay()
        self.rules.write_text(json.dumps(json.loads(self.rules.read_text()),indent=2))
        self.rows=self.rows[:1];self.save()
        with self.assertRaisesRegex(ValueError,'truncated'):
            self.run_replay()

    def test_adding_rule_preserves_existing_rule_cursor(self):
        self.run_replay()
        cfg=json.loads(self.rules.read_text())
        cfg['rules'].append({**cfg['rules'][0],'id':'second'})
        self.rules.write_text(json.dumps(cfg))
        report=self.run_replay()
        self.assertNotIn('quality_out_of_order',report['reason_counts'])
