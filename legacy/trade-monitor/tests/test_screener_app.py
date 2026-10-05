import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.test_screener import fixture
from stock_screener.engine import Policy, screen

try:
    from stock_screener.artifacts import save_run, render_html, csv_text
    from stock_screener.server import make_server
except ImportError:
    save_run = render_html = csv_text = make_server = None


class ScreenerAppTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(save_run, 'artifact and app modules are not implemented')
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.raw = self.root/'raw'; self.raw.mkdir()
        self.universe = dict(schema_version=1,instruments=[
            dict(symbol=s,contract_id=i,exchange='NASDAQ',group='core' if s=='AAA' else 'benchmark',name=s)
            for i,s in enumerate(['AAA','QQQ','SOXX'],1)])
        for i,s in enumerate(['AAA','QQQ','SOXX'],1): fixture(self.raw,s,i,s!='AAA')
        self.result = screen(self.raw,self.universe,Policy(),'2026-10-04T03:40:00Z')

    def test_repeat_save_preserves_evidence_and_changed_policy_gets_new_id(self):
        path = save_run(self.result, self.root/'runs')
        original = (path/'result.json').read_bytes()
        self.assertEqual(save_run(self.result,self.root/'runs'),path)
        self.assertEqual((path/'result.json').read_bytes(),original)
        changed = screen(self.raw,self.universe,Policy(min_price=200),'2026-10-04T03:40:00Z')
        self.assertNotEqual(changed['run_id'],self.result['run_id'])
        self.assertNotEqual(save_run(changed,self.root/'runs'),path)
        self.assertEqual(json.loads(original)['rows'][0]['state'],'CANDIDATE')

    def test_corrupted_existing_run_is_not_silently_accepted(self):
        path = save_run(self.result,self.root/'runs')
        (path/'result.json').write_text('{}')
        with self.assertRaises(ValueError): save_run(self.result,self.root/'runs')

    def test_csv_prevents_formula_and_html_script_escape(self):
        self.result['rows'][0]['name']='=HYPERLINK("bad")'
        values = list(csv.DictReader(io.StringIO(csv_text(self.result))))
        self.assertTrue(values[0]['name'].startswith("'="))
        self.result['rows'][0]['name']='</script><script>globalThis.injected=1</script>'
        page=render_html(self.result)
        self.assertNotIn('</script><script>globalThis.injected', page)
        self.assertIn('\\u003c/script',page)

    def test_export_contains_both_horizons_against_both_benchmarks(self):
        row = list(csv.DictReader(io.StringIO(csv_text(self.result))))[0]
        self.assertAlmostEqual(float(row['rs5_soxx']), .013548387096774195)
        self.assertAlmostEqual(float(row['rs20_soxx']), .12214285714285711)

    def test_loopback_api_and_cross_origin_rejection(self):
        server=make_server(self.raw,self.universe,self.root/'runs',port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base=f'http://127.0.0.1:{server.server_address[1]}'
        payload=json.dumps(dict(as_of='2026-10-04T03:40:00Z',session='2026-10-02',policy={})).encode()
        req=Request(base+'/api/screen',data=payload,headers={'Content-Type':'application/json'},method='POST')
        with urlopen(req) as response:
            result=json.load(response)
        self.assertEqual(result['rows'][0]['state'],'CANDIDATE')
        with urlopen(base+'/') as response: self.assertIn('选股工作台',response.read().decode())
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(base+'/api/screen',data=payload,headers={'Content-Type':'application/json','Origin':'https://evil.example'},method='POST'))
        self.assertEqual(caught.exception.code,403)
        with self.assertRaises(HTTPError) as caught:
            urlopen(Request(base+'/api/screen',data=b'{"policy":{"max_atr_fraction":true}}',headers={'Content-Type':'application/json'},method='POST'))
        self.assertEqual(caught.exception.code,400)


if __name__=='__main__': unittest.main()
