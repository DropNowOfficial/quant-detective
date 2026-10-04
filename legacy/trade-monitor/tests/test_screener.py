"""Financial boundary tests; fixtures are synthetic and never shipped as market data."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import exchange_calendars as xc
import pandas as pd

try:
    from stock_screener.calendar import last_completed_session
    from stock_screener.engine import Policy, screen
except ImportError:
    Policy = screen = last_completed_session = None


def fixture(directory, symbol='AAA', contract_id=1, constant=False):
    cal = xc.get_calendar('XNYS')
    sessions = cal.sessions_in_range('2026-06-01', '2026-10-02')[-61:]
    close = [100.0 if constant else 100.0 + i for i in range(61)]
    if not constant:
        close[-1] = 157.1
    op = [close[0]] + close[:-1]
    data = dict(time=[cal.session_open(d).isoformat() for d in sessions],
                open=op, high=[max(o,c)+2 for o,c in zip(op,close)],
                low=[min(o,c)-2 for o,c in zip(op,close)], close=close,
                volume=[1000000]*61, chart_step=86400, source='Last', corp_actions=[])
    doc = dict(symbol=symbol, contract=dict(symbol=symbol, underlying_contract_id=contract_id, exchange='NASDAQ'),
               request=dict(contract_id=contract_id, security_type='STK', step='ONE_DAY', outside_rth=False),
               retrieved_at='2026-10-03T01:00:00Z', data=data)
    path = Path(directory)/f'{symbol}_daily.json'
    path.write_text(json.dumps(doc))
    return doc


class ScreenerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(screen, 'stock_screener engine is not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name)
        self.universe = dict(schema_version=1, membership='test', instruments=[
            dict(symbol='AAA', contract_id=1, exchange='NASDAQ', group='core', name='Synthetic'),
            dict(symbol='QQQ', contract_id=2, exchange='NASDAQ', group='benchmark', name='Benchmark'),
            dict(symbol='SOXX', contract_id=3, exchange='NASDAQ', group='benchmark', name='Benchmark')])
        fixture(self.raw)
        fixture(self.raw, 'QQQ', 2, True)
        fixture(self.raw, 'SOXX', 3, True)

    def run_screen(self, **kwargs):
        return screen(self.raw, self.universe, Policy(), '2026-10-04T03:40:00Z', **kwargs)

    def change(self, symbol, fn):
        path = self.raw/f'{symbol}_daily.json'
        doc = json.loads(path.read_text()); fn(doc); path.write_text(json.dumps(doc))

    def test_hand_calculated_candidate(self):
        row = self.run_screen()['rows'][0]
        self.assertEqual(row['state'], 'CANDIDATE')
        self.assertEqual(row['rank'], 1)
        f = row['factors']
        self.assertAlmostEqual(f['ma5'], 157)
        self.assertAlmostEqual(f['atr5'], 5)
        self.assertAlmostEqual(f['d5'], .02)
        self.assertAlmostEqual(f['adv20'], 149500000)
        self.assertAlmostEqual(f['rs20_qqq'], .12214285714285711)
        self.assertEqual(row['validation'], 'UNVALIDATED_SCREEN')
        self.assertEqual(row['intraday'], 'NOT_EVALUATED')

    def test_invalid_latest_ohlc_blocks(self):
        self.change('AAA', lambda d: d['data']['high'].__setitem__(-1, 150))
        row = self.run_screen()['rows'][0]
        self.assertEqual(row['state'], 'DATA_BLOCKED')
        self.assertIn('INVALID_OHLCV', row['block_codes'])

    def test_missing_session_not_forward_filled(self):
        def remove(d):
            for key in ['time','open','high','low','close','volume']: d['data'][key].pop(-3)
        self.change('AAA', remove)
        self.assertIn('MISSING_SESSIONS', self.run_screen()['rows'][0]['block_codes'])

    def test_stale_does_not_return_previous_signal(self):
        def remove(d):
            for key in ['time','open','high','low','close','volume']: d['data'][key].pop()
        self.change('AAA', remove)
        row = self.run_screen()['rows'][0]
        self.assertEqual(row['state'], 'DATA_BLOCKED')
        self.assertIn('STALE', row['block_codes'])

    def test_contract_and_session_scope_must_match(self):
        for field, value in [('contract_id', 999), ('outside_rth', True)]:
            with self.subTest(field=field):
                fixture(self.raw)
                self.change('AAA', lambda d: d['request'].__setitem__(field, value))
                self.assertEqual(self.run_screen()['rows'][0]['state'], 'DATA_BLOCKED')

    def test_bad_benchmark_blocks_ranking(self):
        self.change('QQQ', lambda d: d['data']['close'].__setitem__(-1, None))
        row = self.run_screen()['rows'][0]
        self.assertEqual(row['state'], 'DATA_BLOCKED')
        self.assertIn('BENCHMARK_UNAVAILABLE', row['block_codes'])
        self.assertIsNone(row['rank'])

    def test_benchmark_damage_outside_all_used_windows_does_not_block(self):
        self.change('SOXX', lambda d: d['data']['close'].__setitem__(5, None))
        self.assertEqual(self.run_screen()['rows'][0]['state'], 'CANDIDATE')

    def test_future_bars_cannot_change_factors(self):
        original = self.run_screen()['rows']
        def append(d):
            d['data']['time'].append('2026-10-05T13:30:00Z')
            for key in ['open','high','low','close','volume']: d['data'][key].append(99999999)
        self.change('AAA', append)
        actual = self.run_screen()['rows']
        for a,b in zip(original,actual):
            self.assertEqual(a['factors'], b['factors'])
            self.assertEqual((a['state'],a['rank']), (b['state'],b['rank']))

    def test_partial_daily_snapshot_never_ages_into_completion(self):
        self.change('AAA', lambda d: d.__setitem__('retrieved_at','2026-10-02T15:00:00Z'))
        row = self.run_screen()['rows'][0]
        self.assertEqual(row['state'], 'DATA_BLOCKED')
        self.assertIn('SOURCE_NOT_FINAL', row['block_codes'])

    def test_duplicate_future_dates_do_not_change_historical_judgment(self):
        original = self.run_screen()['rows'][0]
        def append(d):
            for _ in range(2):
                d['data']['time'].append('2026-10-05T13:30:00Z')
                for key in ['open','high','low','close','volume']: d['data'][key].append(99999999)
        self.change('AAA', append)
        row = self.run_screen()['rows'][0]
        self.assertEqual(row['state'], original['state'])
        self.assertEqual(row['factors'], original['factors'])
        self.assertEqual(row['source']['ignored_future_rows'], 2)
        self.assertTrue(row['source']['ignored_future_time_defects'])

    def test_watch_and_gap_shock(self):
        self.change('AAA', lambda d: d['data']['close'].__setitem__(-1, 158.5))
        self.assertEqual(self.run_screen()['rows'][0]['state'], 'WATCH')
        def shock(d):
            d['data']['open'][-1]=170; d['data']['high'][-1]=172
        self.change('AAA', shock)
        self.assertEqual(self.run_screen()['rows'][0]['state'], 'NOT_MATCHED')

    def test_policy_rejects_nonfinite_bool_and_bad_bounds(self):
        for kwargs in [dict(min_price=float('nan')),dict(min_adv20=-1),dict(max_atr_fraction=True),dict(min_rs20=float('inf'))]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): Policy(**kwargs)

    def test_calendar_holiday_early_close_and_unfinished_day(self):
        self.assertEqual(str(last_completed_session('2026-11-27T17:59:00Z').date()), '2026-11-25')
        self.assertEqual(str(last_completed_session('2026-11-27T18:30:00Z').date()), '2026-11-27')
        self.assertEqual(str(last_completed_session('2026-10-02T19:00:00Z').date()), '2026-10-01')
        with self.assertRaises(ValueError): last_completed_session('2026-10-02')

    def test_historical_label_and_future_session_rejection(self):
        result = self.run_screen(session='2026-10-01')
        self.assertEqual(result['mode'], 'HISTORICAL_RECONSTRUCTION')
        with self.assertRaises(ValueError): self.run_screen(session='2026-10-05')

    def test_unknown_recent_split_blocks_unknown_adjustment(self):
        self.change('AAA', lambda d: d['data']['corp_actions'].append(dict(type='SPLIT', date='2026-09-30', value='2:1')))
        self.assertIn('SPLIT_ADJUSTMENT_UNKNOWN', self.run_screen()['rows'][0]['block_codes'])

    def test_duplicate_and_non_open_timestamps_block(self):
        for value in ['2026-10-01T13:30:00Z','2026-10-02T00:00:00Z']:
            fixture(self.raw)
            self.change('AAA', lambda d: d['data']['time'].__setitem__(-1, value))
            self.assertEqual(self.run_screen()['rows'][0]['state'], 'DATA_BLOCKED')


if __name__ == '__main__': unittest.main()
