import unittest
from dataclasses import replace
from decimal import Decimal as D
from trade_monitor.contracts import parse_bar, RuleSpec, RuleState
from trade_monitor.quality import QualityResult,QualityState
from trade_monitor.rules import evaluate
from tests.test_contracts import raw_bar

class RulesTest(unittest.TestCase):
    def setUp(self):
        self.bar=parse_bar(raw_bar())
        self.spec=RuleSpec('synthetic','1','entry',D('100.31'),D('102.66'),None)
        self.quality=QualityResult(True,'valid',QualityState(self.bar.instrument.key()))
    def test_purpose_and_counterexample(self):
        d=evaluate(self.bar,self.spec,RuleState(),self.quality)
        self.assertEqual((d.kind,d.code),('no_signal','range_not_touched'))
        missing=evaluate(self.bar,replace(self.spec,purpose='protection'),RuleState(),self.quality)
        self.assertEqual((missing.kind,missing.code),('suppressed','rule_incomplete'))
        for purpose in ['exit','protection','invented']:
            d=evaluate(self.bar,replace(self.spec,purpose=purpose,protection_fraction=D('.5')),RuleState(),self.quality)
            self.assertEqual(d.code,'unsupported_rule')
    def test_entry_repetition_reentry_stable_ids(self):
        spec=replace(self.spec,lower=D('106'),upper=D('108'))
        first=evaluate(self.bar,spec,RuleState(),self.quality)
        self.assertEqual(first.kind,'signal')
        repeated=evaluate(self.bar,spec,first.next_state,self.quality)
        self.assertEqual(repeated.kind,'no_signal')
        outside=replace(self.bar,open=D('109'),low=D('109'),high=D('110'),close=D('110'),start_ms=120000,end_ms=180000,received_ms=180010)
        leave=evaluate(outside,spec,first.next_state,self.quality)
        newer=replace(self.bar,start_ms=180000,end_ms=240000,received_ms=240010)
        reentry=evaluate(newer,spec,leave.next_state,self.quality)
        self.assertEqual(reentry.kind,'signal'); self.assertNotEqual(first.event_id,reentry.event_id)
        self.assertEqual(first,evaluate(self.bar,spec,RuleState(),self.quality))
        for field in ['rule_id','rule_version','purpose','ohlc','bounds','start_ms','end_ms','quality_reason']:
            self.assertIn(field,first.evidence)
    def test_quality_suppression_preserves_zone(self):
        s=RuleState(60000,True,'valid')
        bad=replace(self.quality,eligible=False,reason='gap')
        d=evaluate(self.bar,self.spec,s,bad)
        self.assertEqual(d.kind,'health'); self.assertTrue(d.next_state.in_zone)
        self.assertEqual(d.next_state.last_end_ms,60000)
    def test_missing_invalid_range_suppresses(self):
        for change in [dict(lower=None),dict(lower=D('NaN')),dict(lower=D('200')),dict(upper=D('-1'))]:
            d=evaluate(self.bar,replace(self.spec,**change),RuleState(),self.quality)
            self.assertEqual(d.kind,'suppressed')
