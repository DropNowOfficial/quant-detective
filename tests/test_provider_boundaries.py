"""Malformed provider data is isolated without swallowing programming errors."""
import json

import pytest

from market_data import us_watch
from test_quote_validity import moment

NOW = moment('2026-10-09T19:58:00-04:00')


def chart_payload():
    return {'chart': {'result': [{
        'meta': {'symbol': 'NXPI', 'instrumentType': 'EQUITY',
                 'exchangeTimezoneName': 'America/New_York', 'currency': 'USD'},
        'timestamp': [int(NOW.timestamp())],
        'indicators': {'quote': [{'open': [100], 'high': [102], 'low': [99],
                                  'close': [101], 'volume': [100]}]},
    }]}}


def fetcher(data):
    return lambda *args, **kwargs: {'data': data, 'receipt': {'received_at_utc': NOW.isoformat()}}


def chart(data):
    return us_watch._chart(fetcher(data), 'NXPI', range_value='5d', interval='5m', include_prepost=True)


def malformed_chart(field, value):
    data = chart_payload()
    if field == 'chart':
        data['chart'] = value
    else:
        item = data['chart']['result'][0]
        if field == 'quote':
            item['indicators']['quote'] = value
        elif field == 'instrumentType':
            item['meta']['instrumentType'] = value
        else:
            item[field] = value
    return data


@pytest.mark.parametrize('field,value', [
    ('chart', None), ('chart', []), ('meta', None), ('meta', []),
    ('instrumentType', []), ('timestamp', 1), ('timestamp', 'bad'),
    ('indicators', []), ('indicators', 1), ('quote', {'0': {}}),
])
def test_malformed_chart_shapes_are_acquisition_failures(field, value):
    with pytest.raises(ValueError):
        chart(malformed_chart(field, value))


@pytest.mark.parametrize('wrapped', [None, [], {}, {'data': {}},
                                    {'data': {}, 'receipt': None},
                                    {'data': {}, 'receipt': []}])
def test_malformed_provider_envelope_is_acquisition_failure(wrapped):
    with pytest.raises(ValueError):
        us_watch._chart(lambda *a, **k: wrapped, 'NXPI', range_value='5d', interval='5m', include_prepost=True)


@pytest.mark.parametrize('stamp', [None, True, [], {}, 'bad', '1791590280',
                                    1.5, float('inf'), float('nan'), 1e300, 10**400])
def test_malformed_chart_timestamps_are_rejected(stamp):
    with pytest.raises(ValueError):
        chart(malformed_chart('timestamp', [stamp]))


@pytest.mark.parametrize('number', [True, None, [], {}, float('inf'), float('nan'), 10**400])
def test_malformed_ohlcv_numbers_are_rejected(number):
    data = chart_payload()
    data['chart']['result'][0]['indicators']['quote'][0]['close'] = [number]
    with pytest.raises(ValueError):
        chart(data)


def test_chart_keeps_valid_bars_when_another_timestamp_is_invalid():
    data = chart_payload()
    item = data['chart']['result'][0]
    item['timestamp'].insert(0, None)
    for values in item['indicators']['quote'][0].values():
        values.insert(0, values[0])
    rows, _, _ = chart(data)
    assert len(rows) == 1 and rows[0]['t'] == int(NOW.timestamp())


@pytest.mark.parametrize('data', [{'news': None}, {'news': {}}, {'news': 1},
                                   {'news': [{'title': 'item', 'providerPublishTime': 10**400}]},
                                   {'news': [{'title': 'item', 'providerPublishTime': 1e300}]}])
def test_malformed_optional_news_does_not_abort_quotes(monkeypatch, data):
    monkeypatch.setattr(us_watch, '_NEWS_CACHE', {})
    result = us_watch._news(fetcher(data), 'NXPI', NOW)
    assert result == {'ok': False, 'error': 'ACQUISITION_ERROR', 'items': [], 'fresh_36h_count': 0}


def test_huge_proxy_reference_is_unavailable_instead_of_overflow():
    data = chart_payload()
    data['chart']['result'][0]['meta']['regularMarketPreviousClose'] = 10**400
    result = us_watch._market_proxy(fetcher(data), 'NXPI', NOW)
    assert result['ok'] is True and result['change_pct'] is None


def test_malformed_symbol_response_preserves_other_usable_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(us_watch, '_market_context', lambda *args: {})
    def scan(symbol, *args):
        if symbol == 'NXPI':
            return chart({'chart': None})
        return {'symbol': symbol, 'status': 'OK'}
    monkeypatch.setattr(us_watch, '_scan_symbol', scan)
    result = us_watch.run(symbols=('NVDA', 'NXPI'), once=True, clock=lambda: NOW,
                          output=tmp_path / 'scan.json')
    assert result['scan_status'] == 'INCOMPLETE'
    assert result['coverage']['usable'] == 1 and result['coverage']['missing'] == ['NXPI']
    assert 'execution_error' not in result


def test_programming_typeerror_in_provider_call_is_not_hidden(monkeypatch, tmp_path):
    monkeypatch.setattr(us_watch, '_NEWS_CACHE', {})
    def broken(*args, **kwargs):
        raise TypeError('private-programming-error')
    with pytest.raises(TypeError):
        us_watch._news(broken, 'NXPI', NOW)
    monkeypatch.setattr(us_watch, '_market_context', lambda *args: {})
    monkeypatch.setattr(us_watch, '_scan_symbol', broken)
    path = tmp_path / 'scan.json'
    with pytest.raises(RuntimeError, match='SCAN_EXECUTION_FAILED'):
        us_watch.run(symbols=('NXPI',), once=True, clock=lambda: NOW, output=path)
    saved = json.loads(path.read_text())
    assert saved['scan_status'] == 'FAILED'
    assert saved['execution_error'] == 'SCAN_EXECUTION_FAILED'
    assert 'private-programming-error' not in path.read_text()


def test_timestamp_at_datetime_ceiling_is_an_acquisition_failure():
    data = malformed_chart('timestamp', [253402300799])
    result = us_watch._market_proxy(fetcher(data), 'NXPI', NOW)
    assert result['ok'] is False and result['error'] == 'ACQUISITION_ERROR'
