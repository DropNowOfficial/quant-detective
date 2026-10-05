"""Synthetic provider contract tests; these numbers are fixtures, never market data."""
from urllib.parse import parse_qs, urlparse
from datetime import datetime

import pytest


def api():
    from market_data.providers import get_catalog, get_candles
    return get_catalog, get_candles


class FetchFailure(Exception):
    def __init__(self, status=451, error="HTTP 451"):
        self.receipt = {"url": "https://blocked.test", "requested_at_utc": "2026-10-04T00:00:00Z", "received_at_utc": "2026-10-04T00:00:01Z", "http_status": status, "ok": False, "error": error}


class Fetch:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, kind="json"):
        self.calls.append((url, kind))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            response.receipt["url"] = url
            raise response
        return {"data": response, "receipt": {"url": url, "requested_at_utc": "2026-10-04T00:00:00Z", "received_at_utc": "2026-10-04T00:00:01Z", "http_status": 200, "ok": True, "error": None}}


def candle(t, **changes):
    values = {"o": "100", "h": "105", "l": "99", "c": "101", "v": "20", "buy": "7"}
    values.update(changes)
    return [t, values["o"], values["h"], values["l"], values["c"], values["v"], t+59999, "2000", 3, values["buy"], "700", "0"]


def test_spot_uses_actual_taker_column_and_sorts_and_marks_open():
    _, get = api()
    fetch = Fetch([candle(120000), candle(60000)])
    result = get("binance_spot", "SOLUSDT", "1m", 2, fetch, now_ms=150000)
    assert result["ok"] and result["source"]
    assert [r["open_time_ms"] for r in result["rows"]] == [60000, 120000]
    assert [r["closed"] for r in result["rows"]] == [True, False]
    assert result["rows"][0]["taker_buy_volume"] == 7
    assert result["rows"][0]["volume"] == 20
    assert result["request_count"] == 1


@pytest.mark.parametrize("changes", [{"o": "nan"}, {"h": "inf"}, {"c": "-inf"}, {"v": "-1"}, {"h": "98"}, {"o": "98"}, {"o": "106"}, {"c": "50"}, {"c": "106"}])
def test_invalid_numeric_candles_are_discarded(changes):
    _, get = api()
    result = get("binance_spot", "BTCUSDT", "1m", 2, Fetch([candle(0, **changes), candle(60000)]), now_ms=200000)
    assert len(result["rows"]) == 1 and result["dropped_rows"] == 1
    assert result["partial"]


def test_missing_taker_is_not_volume_half_or_zero():
    _, get = api()
    row = candle(0)
    row[9] = None
    result = get("binance_spot", "BTCUSDT", "1m", 1, Fetch([row]), now_ms=100000)
    assert result["rows"][0]["taker_buy_volume"] is None
    assert result["warnings"]


def test_binance_backward_pagination_uses_oldest_minus_one_and_caps_pages():
    _, get = api()
    newest = [candle(t*60000) for t in range(2, 1002)]
    fetch = Fetch(newest, [candle(60000)])
    result = get("binance_spot", "BTCUSDT", "1m", 1001, fetch, now_ms=100000000)
    queries = [parse_qs(urlparse(url).query) for url, _ in fetch.calls]
    assert queries[0]["limit"] == ["1000"]
    assert queries[1]["limit"] == ["1"]
    assert queries[1]["endTime"] == ["119999"]
    assert len(result["rows"]) == 1001 and result["request_count"] == 2


def test_gate_contract_seconds_and_no_taker_volume():
    _, get = api()
    fetch = Fetch([{"t": 60, "o": "2", "h": "3", "l": "1", "c": "2.5", "v": 100}])
    result = get("gate_usdt", "SOLUSDT", "1m", 1, fetch, now_ms=100000)
    assert "contract=SOL_USDT" in fetch.calls[0][0] and "limit=1000" in fetch.calls[0][0]
    row = result["rows"][0]
    assert row["open_time_ms"] == 60000 and row["taker_buy_volume"] is None and row["closed"] is False
    assert "合约" in result["volume_unit"] and any("没有主动买量" in w for w in result["warnings"])


def test_http_error_is_visible_and_never_falls_back():
    _, get = api()
    fetch = Fetch(FetchFailure())
    result = get("binance_usdm", "BTCUSDT", "1m", 5, fetch)
    assert not result["ok"] and result["http_status"] == 451 and not result["rows"]
    assert len(fetch.calls) == 1 and "fapi.binance.com" in fetch.calls[0][0]


def test_second_page_failure_preserves_first_page_as_partial_with_failure_status():
    _, get = api()
    fetch = Fetch([candle(t*60000) for t in range(1, 1001)], FetchFailure(None, "TIMEOUT after 12 seconds"))
    result = get("binance_spot", "BTCUSDT", "1m", 1001, fetch)
    assert not result["ok"] and result["partial"] and len(result["rows"]) == 1000
    assert result["request_count"] == 2 and "TIMEOUT" in result["error"]


def test_okx_provider_confirm_flag_is_required_and_page_size_is_300():
    _, get = api()
    rows = [[str(t*60000), "2", "3", "1", "2", "10", ".1", ".2", "1"] for t in range(2, 302)]
    rows[-1][-1] = "0"
    fetch = Fetch({"code": "0", "data": rows}, {"code": "0", "data": [["60000", "2", "3", "1", "2", "10", ".1", ".2", "1"]]})
    result = get("okx_swap", "BTC-USDT-SWAP", "1m", 301, fetch, now_ms=100000000)
    assert len(result["rows"]) == 301
    assert parse_qs(urlparse(fetch.calls[0][0]).query)["limit"] == ["300"]
    assert parse_qs(urlparse(fetch.calls[1][0]).query)["after"] == ["120000"]
    assert result["rows"][-1]["closed"] is False
    assert all(r["taker_buy_volume"] is None for r in result["rows"])


def test_application_error_is_not_an_empty_success():
    _, get = api()
    result = get("okx_swap", "BAD-USDT-SWAP", "1m", 5, Fetch({"code": "51000", "msg": "Invalid instrument", "data": []}))
    assert not result["ok"] and "51000" in result["error"]


def test_us_catalog_joins_official_directories_excluding_test_issues():
    catalog, _ = api()
    fetch = Fetch("Symbol|Security Name|Test Issue|ETF\nAAPL|Apple|N|N\nTEST|Test|Y|N\nFile Creation Time: 1004|||\n", "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\nBRK.B|Berkshire|N|BRK.B|N|100|N|BRK/B\n")
    result = catalog("us_equity", fetch)
    instruments = {r["symbol"]: r for r in result["instruments"]}
    assert set(instruments) == {"AAPL", "BRK.B"}
    assert instruments["BRK.B"]["provider_symbol"] == "BRK-B"
    assert all(kind == "text" for _, kind in fetch.calls)
    assert result["request_count"] == 2


def test_binance_spot_catalog_omits_large_permission_sets_without_filtering_symbols():
    catalog, _ = api()
    fetch = Fetch({"symbols": [{"symbol": "BTCUSDT", "status": "TRADING"}, {"symbol": "ETHUSDT", "status": "TRADING"}, {"symbol": "SOLUSDT", "status": "TRADING"}]})
    result = catalog("binance_spot", fetch)
    query = parse_qs(urlparse(fetch.calls[0][0]).query)
    assert query == {"showPermissionSets": ["false"]}
    assert {row["symbol"] for row in result["instruments"]} == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


def test_yahoo_candles_preserve_missing_values_and_original_symbol():
    _, get = api()
    payload = {"chart": {"error": None, "result": [{"meta": {"symbol": "BRK-B", "currency": "USD", "instrumentType": "EQUITY", "exchangeName": "NYQ", "exchangeTimezoneName": "America/New_York"}, "timestamp": [60, 120], "indicators": {"quote": [{"open": [100, None], "high": [102, 103], "low": [99, 99], "close": [101, 102], "volume": [10, 20]}]}}]}}
    # 1970-01-01 14:30 UTC is a regular-session interval start in New York.
    payload["chart"]["result"][0]["timestamp"] = [52200, 52260]
    fetch = Fetch(payload)
    result = get("us_equity", "BRK.B", "1m", 2, fetch, now_ms=52300000)
    assert result["symbol"] == "BRK.B" and len(result["rows"]) == 1 and result["dropped_rows"] == 1
    assert "/BRK-B?" in fetch.calls[0][0] and result["rows"][0]["taker_buy_volume"] is None


def yahoo(times, symbol="AAPL", market="us_equity"):
    meta = {"symbol": symbol, "instrumentType": "EQUITY", "exchangeName": "NMS" if market == "us_equity" else "SHH", "exchangeTimezoneName": "America/New_York" if market == "us_equity" else "Asia/Shanghai", "currency": "USD" if market == "us_equity" else "CNY"}
    return {"chart": {"error": None, "result": [{"meta": meta, "timestamp": times, "indicators": {"quote": [{k: [v]*len(times) for k, v in {"open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 10}.items()}]}}]}}


def seconds(value):
    return int(datetime.fromisoformat(value).timestamp())


def test_equity_terminal_quote_and_lunch_nonbars_are_excluded():
    _, get = api()
    times = [seconds(t) for t in ["2026-09-30T03:29:00+00:00", "2026-09-30T03:30:00+00:00", "2026-09-30T04:00:00+00:00", "2026-09-30T05:00:00+00:00", "2026-09-30T07:00:00+00:00"]]
    result = get("cn_equity", "600519", "1m", 5, Fetch(yahoo(times, "600519.SS", "cn_equity")), now_ms=seconds("2026-09-30T08:00:00+00:00")*1000)
    assert [r["open_time_ms"] for r in result["rows"]] == [times[0]*1000, times[3]*1000]
    assert result["excluded_non_session_rows"] == 3 and result["partial"]


def test_first_yahoo_request_asks_source_for_latest_trading_day_not_calendar_day():
    _, get = api()
    fetch = Fetch(yahoo([seconds("2026-10-02T19:59:00+00:00")]))
    result = get("us_equity", "AAPL", "1m", 1, fetch, now_ms=seconds("2026-10-04T06:00:00+00:00")*1000)
    query = parse_qs(urlparse(fetch.calls[0][0]).query)
    assert query["range"] == ["1d"] and "period1" not in query
    assert result["rows"][0]["closed"]


def test_yahoo_pagination_skips_weekend_and_never_exceeds_1000_time_slots():
    _, get = api()
    fetch = Fetch(yahoo([seconds("2026-10-05T13:30:00+00:00")]), yahoo([seconds("2026-10-02T19:59:00+00:00")]))
    result = get("us_equity", "AAPL", "1m", 2, fetch, now_ms=seconds("2026-10-05T14:00:00+00:00")*1000)
    query = parse_qs(urlparse(fetch.calls[1][0]).query)
    assert int(query["period2"][0]) == seconds("2026-10-02T20:00:00+00:00")
    assert int(query["period2"][0]) - int(query["period1"][0]) <= 60000
    assert len(result["rows"]) == 2 and result["request_count"] == 2


def test_cn_catalog_is_a_disclosure_search_directory_not_certified_active_universe():
    catalog, _ = api()
    fetch = Fetch({"stockList": [{"code": "000001", "zwjc": "平安银行", "category": "A股"}, {"code": "600000", "zwjc": "浦发银行", "category": "A股"}, {"code": "920799", "zwjc": "艾融软件", "category": "A股"}, {"code": "200001", "zwjc": "B 股合成条目", "category": "B股"}]})
    result = catalog("cn_equity", fetch)
    assert result["ok"] and result["partial"]
    assert {r["symbol"] for r in result["instruments"]} == {"000001", "600000", "920799"}
    assert all(r["listing_status"] == "UNVERIFIED" for r in result["instruments"])
    assert result["catalog_scope"] == "current_and_historical_disclosure_symbols"
    assert any("北交所" in w for w in result["warnings"])
    assert len(fetch.calls) == 1 and "cninfo.com.cn" in fetch.calls[0][0]


@pytest.mark.parametrize("market,payload", [("gate_usdt", [None]), ("binance_spot", {"symbols": ["malformed"]}), ("cn_equity", {"stockList": [17]})])
def test_malformed_catalog_rows_return_a_visible_error_not_a_server_crash(market, payload):
    catalog, _ = api()
    result = catalog(market, Fetch(payload))
    assert not result["ok"] and result["error"] and result["http_status"] == 200


def test_yahoo_wrong_security_is_rejected_not_relabelled_as_requested_symbol():
    _, get = api()
    payload = yahoo([seconds("2026-10-02T19:59:00+00:00")])
    payload["chart"]["result"][0]["meta"]["symbol"] = "WRONG"
    result = get("us_equity", "AAPL", "1m", 1, Fetch(payload), now_ms=seconds("2026-10-04T06:00:00+00:00")*1000)
    assert not result["ok"] and result["rows"] == [] and "品种" in result["error"]


@pytest.mark.parametrize("symbol,meta", [
    ("BTC-USD", {"instrumentType": "CRYPTOCURRENCY", "exchangeName": "CCC", "exchangeTimezoneName": "UTC"}),
    ("EURUSD=X", {"instrumentType": "CURRENCY", "exchangeName": "CCY", "exchangeTimezoneName": "Europe/London"}),
    ("7203.T", {"instrumentType": "EQUITY", "exchangeName": "JPX", "exchangeTimezoneName": "Asia/Tokyo", "currency": "JPY"}),
    ("GC=F", {"instrumentType": "FUTURE", "exchangeName": "CMX"}),
    ("AAPL", {"exchangeName": "FOREIGN_EXCHANGE"}),
    ("AAPL", {"exchangeTimezoneName": "Asia/Tokyo"}),
    ("AAPL", {"currency": "EUR"}),
])
def test_us_quotes_reject_wrong_asset_class_or_market_identity(symbol, meta):
    _, get = api()
    payload = yahoo([seconds("2026-10-02T14:30:00+00:00")], symbol.replace(".", "-"))
    payload["chart"]["result"][0]["meta"].update(meta)
    fetch = Fetch(payload)
    result = get("us_equity", symbol, "1m", 1, fetch, now_ms=seconds("2026-10-04T06:00:00+00:00")*1000)
    assert not result["ok"] and not result["rows"] and result["error"]
    assert len(fetch.calls) == 1


@pytest.mark.parametrize("missing", ["meta", "symbol", "instrumentType", "exchangeName", "exchangeTimezoneName", "currency"])
def test_yahoo_missing_identity_evidence_never_passes_as_verified_equity(missing):
    _, get = api()
    payload = yahoo([seconds("2026-10-02T14:30:00+00:00")])
    item = payload["chart"]["result"][0]
    if missing == "meta":
        del item["meta"]
    else:
        del item["meta"][missing]
    result = get("us_equity", "AAPL", "1m", 1, Fetch(payload))
    assert not result["ok"] and not result["rows"] and "身份" in result["error"]


@pytest.mark.parametrize("symbol", ["AAPL", "BTC-USD", "900901.SS", "200002.SZ", "600519.SZ", "000001.SS"])
def test_cn_rejects_non_a_share_codes_and_mismatched_exchange_suffix_without_fetch(symbol):
    _, get = api()
    fetch = Fetch()
    result = get("cn_equity", symbol, "1m", 1, fetch)
    assert not result["ok"] and result["rows"] == [] and result["error"]
    assert not fetch.calls


@pytest.mark.parametrize("change", [{"instrumentType": "ETF"}, {"exchangeName": "SHZ"}, {"exchangeTimezoneName": "America/New_York"}, {"currency": "USD"}])
def test_cn_requires_a_share_quote_metadata_matching_the_code_family(change):
    _, get = api()
    payload = yahoo([seconds("2026-09-30T05:00:00+00:00")], "600519.SS", "cn_equity")
    payload["chart"]["result"][0]["meta"].update(change)
    result = get("cn_equity", "600519", "1m", 1, Fetch(payload))
    assert not result["ok"] and not result["rows"]


def test_verified_us_etf_and_sz_a_share_still_work_with_one_get():
    _, get = api()
    etf = yahoo([seconds("2026-10-02T14:30:00+00:00")], "QQQ")
    etf["chart"]["result"][0]["meta"].update(instrumentType="ETF", exchangeName="NGM")
    sz = yahoo([seconds("2026-09-30T05:00:00+00:00")], "000001.SZ", "cn_equity")
    sz["chart"]["result"][0]["meta"]["exchangeName"] = "SHZ"
    for market, symbol, payload in [("us_equity", "QQQ", etf), ("cn_equity", "000001", sz)]:
        fetch = Fetch(payload)
        result = get(market, symbol, "1m", 1, fetch)
        assert result["ok"] and len(result["rows"]) == 1 and len(fetch.calls) == 1


@pytest.mark.parametrize("market,symbol", [("okx_futures", "BTC-USDT-SWAP"), ("okx_swap", "BTC-USDT-261225")])
def test_okx_contract_subtype_cannot_be_mislabeled_even_if_price_endpoint_would_accept(market, symbol):
    _, get = api()
    fetch = Fetch({"code": "0", "data": [["60000", "100", "105", "99", "101", "20", ".1", ".2", "1"]]})
    result = get(market, symbol, "1m", 1, fetch, now_ms=120000)
    assert not result["ok"] and not result["rows"] and not fetch.calls


def test_okx_dated_contract_has_correct_label_and_remains_available():
    _, get = api()
    fetch = Fetch({"code": "0", "data": [["60000", "100", "105", "99", "101", "20", ".1", ".2", "1"]]})
    result = get("okx_futures", "BTC-USD-261225", "1m", 1, fetch, now_ms=120000)
    assert result["ok"] and len(result["rows"]) == 1 and "交割" in result["source"] and len(fetch.calls) == 1


@pytest.mark.parametrize("market,interval,count", [("invented", "1m", 5), ("gate_usdt", "1h", 5), ("gate_usdt", "1m", 0), ("gate_usdt", "1m", True), ("gate_usdt", "1m", 100001)])
def test_invalid_request_rejected_before_network(market, interval, count):
    _, get = api()
    fetch = Fetch()
    with pytest.raises(ValueError):
        get(market, "SOLUSDT", interval, count, fetch)
    assert not fetch.calls


def test_cn_benchmark_has_dedicated_etf_identity_and_cash_stock_api_stays_strict():
    from market_data.providers import get_benchmark, _yahoo_symbol, ProviderError
    stamp=1790994600  # 2026-10-03 is a weekend; identity tested even if session filtering removes rows.
    def response(kind='ETF',symbol='510300.SS'):
        return {'chart':{'error':None,'result':[{'meta':{'symbol':symbol,'instrumentType':kind,'exchangeName':'SHH','exchangeTimezoneName':'Asia/Shanghai','currency':'CNY'},'timestamp':[stamp], 'indicators':{'quote':[{'open':[2],'high':[3],'low':[1],'close':[2],'volume':[10]}]}}]}}
    fetch=Fetch(response())
    out=get_benchmark('cn_equity','1m',1,fetch,now_ms=stamp*1000+60000)
    assert out['ok'] and out['instrument_role']=='ETF_BENCHMARK'
    assert '510300.SS' in fetch.calls[0][0]
    assert not get_benchmark('cn_equity','1m',1,Fetch(response('EQUITY')),now_ms=stamp*1000+60000)['ok']
    with pytest.raises(ProviderError):_yahoo_symbol('510300','cn_equity')
