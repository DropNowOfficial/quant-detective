"""Malformed input matrices against real engine and local HTTP boundaries."""
import copy
import json
import tempfile
import threading
import unittest
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.test_screener import fixture
from stock_screener.engine import Policy, screen
from stock_screener.server import make_server


class ScreenerStressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.raw=self.root/'raw';self.raw.mkdir()
        self.universe=dict(schema_version=1,instruments=[dict(symbol=s,contract_id=i,exchange='NASDAQ',group='core' if s=='AAA' else 'benchmark',name=s) for i,s in enumerate(['AAA','QQQ','SOXX'],1)])
        for i,s in enumerate(['AAA','QQQ','SOXX'],1):fixture(self.raw,s,i,s!='AAA')

    def test_malformed_ohlcv_matrix_always_blocks(self):
        original=json.loads((self.raw/'AAA_daily.json').read_text())
        values=[None,True,False,-1,float('nan'),float('inf'),{},[],'not a number']
        for column in ['open','high','low','close','volume']:
            for value in values:
                with self.subTest(column=column,value=value):
                    doc=copy.deepcopy(original);doc['data'][column][-1]=value
                    (self.raw/'AAA_daily.json').write_text(json.dumps(doc))
                    result=screen(self.raw,self.universe,Policy(),'2026-10-04T04:25:41Z')
                    self.assertEqual(result['rows'][0]['state'],'DATA_BLOCKED')
                    self.assertIsNone(result['rows'][0]['rank'])

    def test_invalid_http_inputs_do_not_silently_use_current_time_or_write_runs(self):
        server=make_server(self.raw,self.universe,self.root/'runs',port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_port}'
        before=list((self.root/'runs').glob('*/result.json'))
        cases=[[],False,None,'text',dict(extra=1),dict(policy=[]),dict(session='2026-10-03')]
        cases += [dict(as_of=x) for x in [False,0,[],{},'',True,'not a time','2026-10-02']]
        cases += [dict(policy={k:v}) for k,v in [
            ('min_price',-1),('min_price',None),('min_price','5'),('min_price',True),('min_price',float('nan')),
            ('min_adv20',-1),('min_adv20',float('inf')),('min_adv20',[]),
            ('max_atr_fraction',0),('max_atr_fraction',-1),('max_atr_fraction',1.01),('max_atr_fraction',False),
            ('min_rs20',float('inf')),('min_rs20',11),('min_rs20',-11),('unknown',1)]]
        for payload in cases:
            with self.subTest(payload=payload):
                req=Request(base+'/api/screen',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
                try:
                    with urlopen(req,timeout=3) as response:status=response.status;response.read()
                except HTTPError as e:status=e.code;e.read()
                self.assertEqual(status,400)
        self.assertEqual(len(list((self.root/'runs').glob('*/result.json'))),len(before))

    def test_empty_cli_time_cannot_silently_become_current_time(self):
        universe=self.root/'universe.json';universe.write_text(json.dumps(self.universe))
        command=[sys.executable,'-m','stock_screener','screen','--raw',str(self.raw),'--universe',str(universe),'--output',str(self.root/'runs'),'--as-of','']
        completed=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(completed.returncode,2)
        self.assertEqual(list((self.root/'runs').glob('*/result.json')),[])
