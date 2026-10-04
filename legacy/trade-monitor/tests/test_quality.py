import unittest
from dataclasses import replace
from decimal import Decimal
from trade_monitor.contracts import parse_bar
from trade_monitor.quality import QualityState, check_bar, repair_sequence
from tests.test_contracts import raw_bar

class QualityTest(unittest.TestCase):
    def setUp(self):
        self.bar=parse_bar(raw_bar())
        self.empty=QualityState(self.bar.instrument.key())
        self.first=self.check(self.bar,self.empty).state
    def check(self,bar,state=None,**kw):
        return check_bar(bar,state or self.first,now_ms=kw.pop('now_ms',bar.received_ms),stale_after_ms=120000,session_open=kw.pop('session_open',True),**kw)
    def next(self,offset=60000,**kw):
        return replace(self.bar,start_ms=self.bar.start_ms+offset,end_ms=self.bar.end_ms+offset,received_ms=self.bar.received_ms+offset,**kw)
    def test_complete_duplicate_and_conflicting(self):
        self.assertFalse(self.check(replace(self.bar,complete=False),self.empty).eligible)
        duplicate=self.check(replace(self.bar,received_ms=120020))
        self.assertEqual(duplicate.reason,'duplicate'); self.assertEqual(duplicate.state,self.first)
        self.assertEqual(self.check(replace(self.bar,volume=Decimal('1'))).reason,'conflicting_duplicate')
    def test_gap_order_source_and_pause(self):
        gap=self.check(self.next(120000)); self.assertEqual(gap.reason,'gap')
        self.assertFalse(self.check(self.next(),gap.state).eligible)
        self.assertEqual(self.check(self.next(-60000)).reason,'out_of_order')
        changed=replace(self.bar,instrument=replace(self.bar.instrument,source='other'))
        self.assertFalse(self.check(changed).eligible)
        self.assertEqual(self.check(replace(self.next(),received_ms=999999),now_ms=180010).reason,'future_data')
    def test_session_and_staleness(self):
        for session,reason in [(True,'stale'),(False,'market_closed'),(None,'session_unknown')]:
            with self.subTest(session=session):
                self.assertEqual(self.check(self.next(),now_ms=999999,session_open=session).reason,reason)
        self.assertFalse(self.check(self.next(),session_open=None).eligible)
    def test_repair_requires_full_bounded_consistent_sequence(self):
        paused=self.check(self.next(120000)).state
        result=repair_sequence([self.next(120000),self.next(),self.next()],paused)
        self.assertTrue(result.eligible)
        self.assertEqual(result.state.last_complete_end_ms,240000)
        self.assertFalse(repair_sequence([self.next(120000)],paused).eligible)
        self.assertFalse(repair_sequence([self.next(),replace(self.next(),volume=Decimal('1'))],paused).eligible)
        self.assertFalse(repair_sequence([],paused).eligible)
