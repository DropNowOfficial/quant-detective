import copy
import unittest
from dataclasses import replace
from decimal import Decimal
from trade_monitor.contracts import parse_bar, canonical_bar, event_id, RuleSpec

def raw_bar(**changes):
    raw = dict(schema_version=1, instrument=dict(venue='NASDAQ',symbol='AAOI',asset_class='stock',currency='USD',source='synthetic',timezone='America/New_York',session_id='synthetic-session'),start_ms=60000,end_ms=120000,received_ms=120010,interval_ms=60000,open='106.5',high='107',low='106.32',close='106.32',volume='0',complete=True)
    raw.update(changes)
    return raw

class ContractsTest(unittest.TestCase):
    def test_contract_identity_and_prices(self):
        b=parse_bar(raw_bar())
        for field,value in [('venue','OKX'),('symbol','AMD'),('asset_class','perpetual'),('currency','USDT'),('source','other'),('timezone','UTC'),('session_id','other')]:
            self.assertNotEqual(b.instrument.key(),replace(b.instrument,**{field:value}).key())
        self.assertEqual(b.close,Decimal('106.32'))
        self.assertEqual(canonical_bar(b),canonical_bar(parse_bar(raw_bar())))
        rule=RuleSpec('demo','1','entry',Decimal('100'),Decimal('103'),None)
        self.assertEqual(event_id(b,rule,'entered'),event_id(replace(b,received_ms=120020),rule,'entered'))
        self.assertNotEqual(event_id(b,rule,'entered'),event_id(b,replace(rule,version='2'),'entered'))

    def test_reject_invalid_bar(self):
        bads=[raw_bar(close=x) for x in ['NaN','Infinity','-1',106.32,True]]
        bads += [raw_bar(**x) for x in [dict(volume='-1'),dict(end_ms=1),dict(high='105'),dict(interval_ms=0),dict(complete=1),dict(start_ms=True),dict(schema_version=2),dict(extra='x'),dict(received_ms=1)]]
        for field,value in [('timezone','NoSuch/Zone'),('source',''),('asset_class','crypto')]:
            raw=raw_bar(); raw['instrument'][field]=value; bads.append(raw)
        raw=raw_bar(); del raw['instrument']['source']; bads.append(raw)
        for raw in bads:
            with self.subTest(raw=raw),self.assertRaises(ValueError): parse_bar(raw)

if __name__=='__main__': unittest.main()
