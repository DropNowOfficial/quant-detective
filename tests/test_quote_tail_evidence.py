"""Synthetic offline diagnostic evidence; never real market observations."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import pytest
from market_data import us_watch

NOW = datetime(2026, 10, 19, 9, 15, tzinfo=timezone.utc)
STAMP = int(NOW.timestamp())


def payload(times=None):
    times = times if times is not None else [STAMP - 703, STAMP - 300]
    return {'data': {'chart': {'result': [{'meta': {'symbol': 'AAA', 'instrumentType': 'EQUITY',
        'exchangeTimezoneName': 'America/New_York', 'currency': 'USD', 'dataGranularity': '5m'},
        'timestamp': times, 'indicators': {'quote': [{
            'open': [100.0] * len(times), 'high': [101.0] * len(times),
            'low': [99.0] * len(times), 'close': [100.0] * len(times),
            'volume': [0.0] * len(times)}]}}], 'error': None}},
        'receipt': {'received_at_utc': NOW.isoformat(), 'url': 'https://fake.invalid?token=SECRET',
                    'headers': {'Authorization': 'SECRET'}}}


def run(monkeypatch, wrapped, clock=None):
    daily = {'ma5': 100., 'atr5': 10., 'prior_close': 100., 'ma5_slope_1d': 1.,
             'ma5_3point_slope': .8, 'previous_session_date': '2026-10-16'}
    key = ('AAA', '2026-10-19')
    monkeypatch.setattr(us_watch, '_DAILY_CACHE', {key: (daily, NOW.isoformat())})
    monkeypatch.setattr(us_watch, '_VOLUME_PROFILE_CACHE', {key: ([], NOW.isoformat())})
    monkeypatch.setattr(us_watch, '_market_context', lambda *a: {})
    monkeypatch.setattr(us_watch, '_news', lambda *a: {})
    calls = []
    def fetcher(*args, **kwargs):
        calls.append(1)
        return deepcopy(wrapped)
    report = us_watch.scan_once(('AAA',), fetcher=fetcher, now=NOW, workers=1, clock=clock)
    assert len(calls) == 1
    return report


def test_expired_with_null_tail_preserves_last_raw_rows(monkeypatch):
    wrapped = payload()
    wrapped['data']['chart']['result'][0]['indicators']['quote'][0]['close'][-1] = None
    report = run(monkeypatch, wrapped)
    row = report['rows'][0]
    assert row['status'] == 'INVALID_DATA'
    assert 'BAR_EXPIRED' in row['intraday']['quote_validity']['rejection_reasons']
    evidence = row['quote_failure_evidence']
    assert evidence['data_granularity'] == '5m'
    assert evidence['http_received_at_utc'] == NOW.isoformat()
    assert evidence['tail'][0]['timestamp'] == STAMP - 703  # unaligned seconds remain exact
    assert evidence['tail'][0]['volume'] == 0.0
    assert evidence['tail'][0]['discard_reason'] is None  # zero volume remains valid
    assert evidence['tail'][-1]['close'] is None
    assert evidence['tail'][-1]['discard_reason'] == 'NONNUMERIC_OHLCV'
    assert report['alerts'] == []


def test_healthy_quote_has_no_tail_evidence_anywhere(monkeypatch):
    report = run(monkeypatch, payload())
    assert report['rows'][0]['status'] == 'OK'
    assert 'quote_failure_evidence' not in json.dumps(report)
    assert '_quote_tail' not in json.dumps(report)


def test_missing_granularity_is_unknown_not_inferred(monkeypatch):
    wrapped = payload([STAMP - 703])
    del wrapped['data']['chart']['result'][0]['meta']['dataGranularity']
    assert run(monkeypatch, wrapped)['rows'][0]['quote_failure_evidence']['data_granularity'] is None


def test_tail_is_bounded_and_secrets_are_allowlisted_out(monkeypatch):
    wrapped = payload(list(range(STAMP - 2000, STAMP - 1000)))
    item = wrapped['data']['chart']['result'][0]
    item['meta'].update(userData='SECRET', dataGranularity='SECRET' * 10000)
    item['indicators']['quote'][0]['close'][-1] = 'SECRET' * 10000
    report = run(monkeypatch, wrapped)
    evidence = report['rows'][0]['quote_failure_evidence']
    assert len(evidence['tail']) == 5
    encoded = json.dumps(evidence, allow_nan=False)
    assert len(encoded.encode()) <= 4096
    assert 'SECRET' not in encoded and 'Authorization' not in encoded and 'https://' not in encoded
    assert evidence['tail'][-1]['close'] is None
    assert evidence['data_granularity'] is None


@pytest.mark.parametrize('field,value,reason', [('volume', -1, 'NONFINITE_OR_NEGATIVE_VOLUME'),
    ('close', float('nan'), 'NONFINITE_OR_NEGATIVE_VOLUME'), ('low', 102, 'INVALID_OHLC_ORDER')])
def test_discard_reasons_match_parser(monkeypatch, field, value, reason):
    wrapped = payload()
    wrapped['data']['chart']['result'][0]['indicators']['quote'][0][field][-1] = value
    row = run(monkeypatch, wrapped)['rows'][0]
    assert row['quote_failure_evidence']['tail'][-1]['discard_reason'] == reason
    json.dumps(row['quote_failure_evidence'], allow_nan=False)


def test_no_valid_bars_error_keeps_evidence(monkeypatch):
    wrapped = payload([STAMP - 703])
    wrapped['data']['chart']['result'][0]['indicators']['quote'][0]['close'][0] = None
    row = run(monkeypatch, wrapped)['rows'][0]
    assert row['status'] == 'ERROR'
    assert row['quote_failure_evidence']['tail'][0]['discard_reason'] == 'NONNUMERIC_OHLCV'


def test_late_refresh_failure_retains_tail(monkeypatch):
    ticks = iter([NOW, NOW, NOW, NOW, NOW + timedelta(minutes=7)])
    report = run(monkeypatch, payload(), clock=lambda: next(ticks))
    row = report['rows'][0]
    assert row['status'] == 'INVALID_DATA'
    assert 'BAR_EXPIRED' in row['intraday']['quote_validity']['rejection_reasons']
    assert row['quote_failure_evidence']['tail'][-1]['timestamp'] == STAMP - 300


def test_transport_failure_never_borrows_prior_chart_evidence(monkeypatch):
    def scan(symbol, fetcher, *args):
        fetcher('first', kind='json')
        fetcher('second', kind='json')
    monkeypatch.setattr(us_watch, '_scan_symbol', scan)
    responses = iter([payload(), RuntimeError('offline synthetic failure')])
    def fetcher(*args, **kwargs):
        value = next(responses)
        if isinstance(value, Exception):
            raise value
        return value
    row = us_watch._scan_symbol_with_evidence('AAA', fetcher, NOW)
    assert row['status'] == 'ERROR'
    assert '_quote_tail' not in row
