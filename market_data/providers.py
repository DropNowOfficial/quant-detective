"""Public, read-only market adapters. Prices from different sources are never spliced.

The injected fetch function owns the 12-second HTTP deadline and receipts. These
adapters contain no credentials, account clients, order routes, or signal logic.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import io
import math
import re
import time
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo


INTERVALS = {"1m": 60, "5m": 300, "15m": 900}
MARKETS = {
    "binance_spot": {"source": "来源 A · Binance 现货公开镜像", "volume_unit": "基础币数量", "host": "https://data-api.binance.vision", "catalog": "/api/v3/exchangeInfo", "candles": "/api/v3/klines"},
    "gate_usdt": {"source": "来源 B · Gate USDT 永续合约", "volume_unit": "合约张数（不是基础币数量）", "host": "https://api.gateio.ws", "catalog": "/api/v4/futures/usdt/contracts", "candles": "/api/v4/futures/usdt/candlesticks"},
    "okx_swap": {"source": "OKX 永续合约公开行情", "volume_unit": "合约张数（vol 字段）", "host": "https://www.okx.com", "catalog": "/api/v5/public/instruments", "candles": "/api/v5/market/candles", "instType": "SWAP"},
    "okx_futures": {"source": "OKX 交割合约公开行情", "volume_unit": "合约张数（vol 字段）", "host": "https://www.okx.com", "catalog": "/api/v5/public/instruments", "candles": "/api/v5/market/candles", "instType": "FUTURES"},
    "binance_usdm": {"source": "Binance USDⓈ-M 合约公开行情", "volume_unit": "API 原始 volume（合约族未经目录核实时不换算）", "host": "https://fapi.binance.com", "catalog": "/fapi/v1/exchangeInfo", "candles": "/fapi/v1/klines"},
    "binance_coinm": {"source": "Binance COIN-M 合约公开行情", "volume_unit": "API 原始 volume（合约族未经目录核实时不换算）", "host": "https://dapi.binance.com", "catalog": "/dapi/v1/exchangeInfo", "candles": "/dapi/v1/klines"},
    "us_equity": {"source": "Yahoo Finance 美国证券公开分钟行情；Nasdaq Trader 证券目录", "volume_unit": "股 / 基金份额（来源原始 volume）"},
    "cn_equity": {"source": "Yahoo Finance A 股公开分钟行情；巨潮资讯披露搜索目录", "volume_unit": "来源原始 volume（未核实股 / 手单位，不跨源比较）"},
}


class ProviderError(ValueError):
    """An HTTP-200 response whose application payload cannot supply the request."""


def _utc(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _url(host, path, **params):
    return host + path + ("?" + urlencode({k: v for k, v in params.items() if v is not None}) if params else "")


def _base(market):
    if market not in MARKETS:
        raise ValueError("不支持的市场；请选择公开市场目录中的市场")
    info = MARKETS[market]
    return {"ok": True, "market": market, "source": info["source"], "requested_at_utc": None, "received_at_utc": None, "http_status": None, "source_url": None, "request_count": 0, "partial": False, "warnings": [], "error": None, "request_receipts": []}


def _request(fetch, out, url, kind="json"):
    try:
        response = fetch(url, kind=kind)
    except Exception as exc:
        receipt = getattr(exc, "receipt", None)
        if receipt is None:
            raise
        _receipt(out, receipt)
        raise ProviderError(receipt.get("error") or f"HTTP {receipt.get('http_status')}") from exc
    _receipt(out, response["receipt"])
    return response["data"]


def _receipt(out, receipt):
    out["request_receipts"].append(receipt)
    out["request_count"] = len(out["request_receipts"])
    if out["requested_at_utc"] is None:
        out["requested_at_utc"] = receipt.get("requested_at_utc")
        out["source_url"] = receipt.get("url")
    out["received_at_utc"] = receipt.get("received_at_utc")
    out["http_status"] = receipt.get("http_status")


def _fail(out, error):
    out["ok"] = False
    out["error"] = str(error)
    out["partial"] = bool(out.get("rows") or out.get("instruments"))
    return out


def _okx_data(data):
    if not isinstance(data, dict) or str(data.get("code")) != "0":
        raise ProviderError(f"OKX 应用错误 {data.get('code')}: {data.get('msg')}" if isinstance(data, dict) else "OKX 响应不是 JSON 对象")
    if not isinstance(data.get("data"), list):
        raise ProviderError("OKX data 不是数组")
    return data["data"]


def _array(data, provider):
    if not isinstance(data, list):
        detail = f" {data.get('code', data.get('label', ''))}: {data.get('msg', data.get('message', ''))}" if isinstance(data, dict) else ""
        raise ProviderError(provider + " K 线响应不是数组" + detail)
    return data


def _instrument(symbol, name, venue, provider_symbol=None, **extra):
    return {"symbol": symbol, "name": name or symbol, "venue": venue, "provider_symbol": provider_symbol or symbol, **extra}


def _objects(rows):
    if any(not isinstance(row, dict) for row in rows):
        raise ProviderError("目录条目不是 JSON 对象；没有用示例品种补齐。")
    return rows


def get_catalog(market, fetch):
    """Fetch the current public catalogue, rather than a hard-coded symbol list."""
    out = _base(market)
    out["instruments"] = []
    info = MARKETS[market]
    try:
        if market == "us_equity":
            for filename, symbol_key, default_venue in [("nasdaqlisted.txt", "Symbol", "NASDAQ"), ("otherlisted.txt", "ACT Symbol", "US listed")]:
                raw = _request(fetch, out, f"https://www.nasdaqtrader.com/dynamic/SymDir/{filename}", kind="text")
                if not isinstance(raw, str):
                    raise ProviderError("Nasdaq Trader 目录不是文本")
                reader = csv.DictReader(io.StringIO(raw), delimiter="|")
                if not reader.fieldnames or symbol_key not in reader.fieldnames or "Test Issue" not in reader.fieldnames:
                    raise ProviderError("Nasdaq Trader 目录表头不匹配")
                for row in reader:
                    symbol = (row.get(symbol_key) or "").strip()
                    if not symbol or symbol.startswith("File Creation") or row.get("Test Issue") != "N":
                        continue
                    out["instruments"].append(_instrument(symbol, row.get("Security Name"), row.get("Exchange") or default_venue, symbol.replace(".", "-"), etf=row.get("ETF") == "Y"))
            out["warnings"].append("目录含美国交易所上市股票及 ETF 等证券，不含全部 OTC；列出不代表 Yahoo 分钟数据必定可用。特殊证券符号映射失败会保留错误。")
        elif market == "cn_equity":
            data = _request(fetch, out, "https://www.cninfo.com.cn/new/data/szse_stock.json")
            if not isinstance(data, dict) or not isinstance(data.get("stockList"), list):
                raise ProviderError("巨潮资讯目录缺少 stockList 数组")
            out["catalog_scope"] = "current_and_historical_disclosure_symbols"
            out["partial"] = True
            for row in _objects(data["stockList"]):
                symbol = str(row.get("code", "")).strip()
                if row.get("category") != "A股" or not re.fullmatch(r"\d{6}", symbol):
                    continue
                provider_symbol = _yahoo_symbol(symbol, market)
                venue = "SSE" if provider_symbol.endswith(".SS") else "SZSE" if provider_symbol.endswith(".SZ") else "BSE（分钟覆盖未认证）"
                out["instruments"].append(_instrument(symbol, row.get("zwjc"), venue, provider_symbol, listing_status="UNVERIFIED", quote_support="UNVERIFIED", disclosure_org_id=row.get("orgId")))
            out["warnings"].append("巨潮资讯 A 股披露搜索目录包含历史、退市、改码或别名，上市状态未经核实；这不是当前可交易股票池，不能据此认证全市场活跃证券覆盖。")
            out["warnings"].append("北交所目录可搜索，但 Yahoo 北交所分钟行情未认证；目录存在不代表该来源有 K 线，404 / 超时会原样报告。")
        else:
            params = {"instType": info["instType"]} if "instType" in info else {}
            if market == "binance_spot":
                params["showPermissionSets"] = "false"
            data = _request(fetch, out, _url(info["host"], info["catalog"], **params))
            if market.startswith("okx_"):
                for row in _objects(_okx_data(data)):
                    if row.get("state") != "live":
                        continue
                    symbol = row.get("instId")
                    if symbol:
                        out["instruments"].append(_instrument(symbol, symbol, "OKX", contract_type=info["instType"], base_asset=row.get("ctValCcy"), quote_asset=row.get("quoteCcy"), contract_size=row.get("ctVal")))
            elif market == "gate_usdt":
                for row in _objects(_array(data, "Gate")):
                    symbol = row.get("name")
                    if symbol and not row.get("in_delisting", False):
                        out["instruments"].append(_instrument(symbol, row.get("underlying") or symbol, "Gate USDT perpetual", contract_type=row.get("contract_type"), quanto_multiplier=row.get("quanto_multiplier")))
                out["warnings"].append("全部条目均为 Gate USDT 永续衍生合约；股票、指数等标的合约不是现金股票或原市场行情。")
            else:
                if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
                    raise ProviderError("Binance exchangeInfo 没有 symbols 数组")
                for row in _objects(data["symbols"]):
                    if row.get("status", row.get("contractStatus")) != "TRADING":
                        continue
                    if market == "binance_spot" and row.get("isSpotTradingAllowed") is False:
                        continue
                    symbol = row.get("symbol")
                    if symbol:
                        out["instruments"].append(_instrument(symbol, symbol, info["source"], base_asset=row.get("baseAsset"), quote_asset=row.get("quoteAsset"), margin_asset=row.get("marginAsset"), contract_size=row.get("contractSize"), contract_type=row.get("contractType")))
        out["instruments"] = sorted({r["symbol"]: r for r in out["instruments"]}.values(), key=lambda r: r["symbol"])
        if not out["instruments"]:
            out["warnings"].append("来源返回空目录；没有用内置示例品种替代。")
    except (ProviderError, KeyError, TypeError, ValueError) as exc:
        return _fail(out, exc)
    return out


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("缺少数值")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("非有限数")
    return number


def _row(raw, market, interval_ms, now_ms, out):
    try:
        if market == "gate_usdt":
            stamp = _number(raw["t"]) * 1000
            values = [_number(raw[key]) for key in ("o", "h", "l", "c", "v")]
        else:
            stamp = _number(raw[0])
            values = [_number(v) for v in raw[1:6]]
            if len(values) != 5:
                raise ValueError("OHLCV 字段不足")
        opening, high, low, close, volume = values
        if (stamp != int(stamp) or stamp < 0 or volume < 0
                or not low <= min(opening, close) <= max(opening, close) <= high):
            raise ValueError("时间或 OHLCV 无效")
        stamp = int(stamp)
        if market.endswith("equity") and not _regular_session(stamp, market):
            out["excluded_non_session_rows"] += 1
            return None
        row = dict(zip(("open", "high", "low", "close", "volume"), values))
        row.update(open_time_ms=stamp, open_time_utc=_utc(stamp), taker_buy_volume=None, closed=now_ms >= stamp + interval_ms)
        if market.startswith("okx_"):
            row["closed"] = row["closed"] and len(raw) > 8 and str(raw[8]) == "1"
            row["close_status_basis"] = "来源 confirm + 周期结束时间"
        else:
            row["close_status_basis"] = "根据请求时刻与周期结束时间推断"
        if market == "binance_spot":
            try:
                buy = _number(raw[9])
                if buy < 0:
                    raise ValueError("负主动买量")
                row["taker_buy_volume"] = buy
            except (ValueError, TypeError, IndexError):
                if "来源 A 部分主动买量缺失或无效，保留为空。" not in out["warnings"]:
                    out["warnings"].append("来源 A 部分主动买量缺失或无效，保留为空。")
        return row
    except (ValueError, TypeError, IndexError, KeyError, OverflowError, OSError):
        out["dropped_rows"] += 1
        return None


def _regular_session(stamp, market):
    local = datetime.fromtimestamp(stamp / 1000, ZoneInfo("America/New_York" if market == "us_equity" else "Asia/Shanghai"))
    minute = local.hour * 60 + local.minute
    if local.weekday() >= 5:
        return False
    return 570 <= minute < 960 if market == "us_equity" else 570 <= minute < 690 or 780 <= minute < 900


def _equity_end(now_ms, market):
    """Skip predictable weekends/overnight gaps without inventing trade dates."""
    local = datetime.fromtimestamp(now_ms / 1000, ZoneInfo("America/New_York" if market == "us_equity" else "Asia/Shanghai"))
    close_hour = 16 if market == "us_equity" else 15
    opening = local.replace(hour=9, minute=30, second=0, microsecond=0)
    closing = local.replace(hour=close_hour, minute=0, second=0, microsecond=0)
    if local.weekday() >= 5 or local < opening:
        while local.weekday() >= 5 or local < opening:
            local -= timedelta(days=1)
            local = local.replace(hour=close_hour, minute=0, second=0, microsecond=0)
            opening = local.replace(hour=9, minute=30, second=0, microsecond=0)
        return int(local.timestamp())
    return int(min(local, closing).timestamp()) + (1 if local < closing else 0)


def _yahoo_symbol(symbol, market):
    if market == "us_equity":
        return symbol.replace(".", "-")
    match = re.fullmatch(r"(\d{6})(?:\.(SS|SZ|BJ))?", symbol)
    if not match:
        raise ProviderError("A 股品种必须是六位 A 股代码，可附正确的 .SS / .SZ / .BJ 后缀；不接受其他市场代码。")
    code, supplied_suffix = match.groups()
    if code.startswith("6"):
        suffix = "SS"
    elif code.startswith(("0", "3")):
        suffix = "SZ"
    elif code.startswith(("4", "8", "92")):
        suffix = "BJ"
    else:
        raise ProviderError("该代码不属于本工具已定义的 A 股代码族；B 股及其他证券不能标为 A 股。")
    if supplied_suffix and supplied_suffix != suffix:
        raise ProviderError("A 股代码与交易场所后缀不一致；拒绝跨市场标注。")
    return code + "." + suffix


def _validate_yahoo_meta(meta, expected_symbol, market):
    required = ("symbol", "instrumentType", "exchangeName", "exchangeTimezoneName", "currency")
    if not isinstance(meta, dict) or any(not isinstance(meta.get(key), str) or not meta[key].strip() for key in required):
        raise ProviderError("Yahoo 品种身份元数据缺失或无效，无法核实资产类别 / 交易场所 / 时区 / 币种；拒绝标为股票行情。")
    if meta["symbol"].upper() != expected_symbol.upper():
        raise ProviderError("Yahoo 返回的品种与请求不一致；拒绝错误标签行情。")
    if market == "us_equity":
        # Deliberately limited to exchange identities observed in the captured
        # source metadata. The broader official directory is still searchable.
        if (meta["instrumentType"] not in {"EQUITY", "ETF"}
                or meta["exchangeName"] not in {"NMS", "NGM", "NYQ"}
                or meta["exchangeTimezoneName"] != "America/New_York"
                or meta["currency"] != "USD"):
            raise ProviderError("UNSUPPORTED_MARKET_IDENTITY：美国报价仅认证 NMS / NGM / NYQ 的 EQUITY / ETF、纽约时区及 USD；其他资产或上市交易场所尚未认证，不能跨市场标为美股。")
    else:
        expected_exchange = {"SS": "SHH", "SZ": "SHZ"}.get(expected_symbol.rsplit(".", 1)[-1])
        if expected_exchange is None:
            raise ProviderError("UNSUPPORTED_MARKET_IDENTITY：北交所分钟行情的交易场所身份尚未认证；目录可搜索不等于该来源报价可用。")
        if (meta["instrumentType"] != "EQUITY" or meta["exchangeName"] != expected_exchange
                or meta["exchangeTimezoneName"] != "Asia/Shanghai" or meta["currency"] != "CNY"):
            raise ProviderError("UNSUPPORTED_MARKET_IDENTITY：Yahoo 资产类别、交易场所、时区或币种与请求的 A 股代码不一致；拒绝跨市场标注。")


def _yahoo_rows(data, expected_symbol, market):
    if not isinstance(data, dict) or not isinstance(data.get("chart"), dict):
        raise ProviderError("Yahoo 响应缺少 chart")
    chart = data["chart"]
    if chart.get("error"):
        raise ProviderError("Yahoo 应用错误: " + str(chart["error"]))
    result = chart.get("result")
    if not isinstance(result, list) or not result:
        raise ProviderError("Yahoo 未返回该品种的分钟 K 线")
    item = result[0]
    if not isinstance(item, dict):
        raise ProviderError("Yahoo result 条目不是对象")
    _validate_yahoo_meta(item.get("meta"), expected_symbol, market)
    times = item.get("timestamp", [])
    indicators = item.get("indicators", {})
    if not isinstance(times, list) or not isinstance(indicators, dict):
        raise ProviderError("Yahoo 时间或指标结构无效")
    quotes = indicators.get("quote", [])
    if not times:
        return []
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        raise ProviderError("Yahoo quote 数据缺失")
    fields = [quotes[0].get(k) for k in ("open", "high", "low", "close", "volume")]
    if any(not isinstance(values, list) or len(values) != len(times) for values in fields):
        raise ProviderError("Yahoo 时间 / OHLCV 数组长度不一致")
    return [[t * 1000, *(values[i] for values in fields)] for i, t in enumerate(times)]


def get_candles(market="binance_spot", symbol="SOLUSDT", interval="1m", limit=5, fetch=None, now_ms=None):
    """Fetch a bounded batch, explicitly retaining partial/error receipt semantics."""
    out = _base(market)
    if interval not in INTERVALS:
        raise ValueError("周期只支持 1m、5m、15m")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100000:
        raise ValueError("根数必须是 1 到 100000 的整数；每页最多 1000 根")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._^=/-]{0,79}", symbol):
        raise ValueError("品种代码无效")
    if fetch is None:
        raise ValueError("必须提供有 12 秒请求上限的公开 GET 传输")
    symbol = symbol.upper()
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    info = MARKETS[market]
    out.update(symbol=symbol, interval=interval, requested_count=limit, volume_unit=info["volume_unit"], rows=[], dropped_rows=0, excluded_non_session_rows=0)
    out["warnings"].append("公开来源一次批量读取；不代表实时行情。未收盘状态按来源标志或请求时间推断。")
    if market != "binance_spot":
        out["warnings"].append("来源 B 没有主动买量。" if market == "gate_usdt" else "本来源不提供本工具定义的主动买量，保留为空。")
    if market.startswith("binance_") and market != "binance_spot":
        out["warnings"].append("volume 为接口原始字段；未用合约乘数转换基础币或名义金额。")
    if market.endswith("equity"):
        out["warnings"].append("Yahoo 为第三方来源，分钟历史有保留期限制；不保证目录中每种证券可用，不把停市或旧成交写成当前行情。")
        out["warnings"].append("仅保留正常交易时段的周期起点，排除午休及每日收盘时间的终值点；未认证逐日节假日 / 半日市日历。")
    if market == "us_equity":
        out["warnings"].append("美国证券目录保留全部可搜索条目；报价身份目前仅认证 NMS / NGM / NYQ 的 EQUITY / ETF，其他上市交易场所尚未认证，会明确返回 unsupported。")
    seen = {}
    cursor = None
    consumed = 0
    try:
        if market == "okx_swap" and not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9_]+-SWAP", symbol):
            raise ProviderError("OKX 永续品种必须使用以 -SWAP 结尾的规范合约代码；拒绝将交割合约标为永续。")
        if market == "okx_futures" and not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9_]+-\d{6}", symbol):
            raise ProviderError("OKX 交割品种必须使用以六位交割日期结尾的规范合约代码；拒绝将 -SWAP 永续合约标为交割。")
        while consumed < limit:
            page_size = min(300 if market.startswith("okx_") else 1000, limit - consumed)
            if market == "gate_usdt":
                contract = symbol if symbol.endswith("_USDT") else symbol[:-4] + "_USDT" if symbol.endswith("USDT") else symbol
                url = _url(info["host"], info["candles"], contract=contract, interval=interval, limit=1000, to=(now_ms // 1000 if cursor is None else cursor // 1000 - 1))
                raw = _array(_request(fetch, out, url), "Gate")
            elif market.startswith("okx_"):
                path = info["candles"] if cursor is None else "/api/v5/market/history-candles"
                url = _url(info["host"], path, instId=symbol, bar=interval, limit=page_size, after=cursor)
                raw = _okx_data(_request(fetch, out, url))
            elif market.startswith("binance_"):
                url = _url(info["host"], info["candles"], symbol=symbol, interval=interval, limit=page_size, endTime=None if cursor is None else cursor - 1)
                raw = _array(_request(fetch, out, url), "Binance")
            else:
                provider_symbol = _yahoo_symbol(symbol, market)
                path = "/v8/finance/chart/" + quote(provider_symbol, safe="")
                if cursor is None:
                    # Source-defined latest trading day includes fewer than
                    # 1000 minute slots and handles holidays without guessing
                    # the last exchange session from the wall-clock weekday.
                    url = _url("https://query1.finance.yahoo.com", path, range="1d", interval=interval, includePrePost="false", events="div,splits")
                else:
                    end = _equity_end(cursor - 1, market)
                    start = max(0, end - 1000 * INTERVALS[interval])
                    url = _url("https://query1.finance.yahoo.com", path, period1=start, period2=end, interval=interval, includePrePost="false", events="div,splits")
                raw = _yahoo_rows(_request(fetch, out, url), provider_symbol, market)
            if not raw:
                out["warnings"].append("该页无可用 K 线；停止分页，没有补造历史。")
                break
            timestamps = []
            for item in raw:
                try:
                    timestamps.append(int(_number(item["t"]) * 1000) if market == "gate_usdt" else int(_number(item[0])))
                except (KeyError, IndexError, TypeError, ValueError):
                    pass
                row = _row(item, market, INTERVALS[interval] * 1000, now_ms, out)
                if row is not None:
                    seen.setdefault(row["open_time_ms"], row)
            out["rows"] = sorted(seen.values(), key=lambda r: r["open_time_ms"])[-limit:]
            consumed += len(raw)
            if not timestamps:
                break
            earliest = min(timestamps)
            if cursor is not None and earliest >= cursor:
                out["warnings"].append("分页时间未向前推进；已停止重复读取。")
                break
            cursor = earliest
            if not market.endswith("equity") and len(raw) < (1000 if market == "gate_usdt" else page_size):
                break
        out["partial"] = len(out["rows"]) < limit
        if out["dropped_rows"]:
            out["warnings"].append(f"已丢弃 {out['dropped_rows']} 根无效 OHLCV K 线；未以 0 或估计值补齐。")
        if out["excluded_non_session_rows"]:
            out["warnings"].append(f"已排除 {out['excluded_non_session_rows']} 个午休、收盘终值或正常时段外数据点。")
        if out["partial"]:
            out["warnings"].append(f"请求 {limit} 根，仅得到 {len(out['rows'])} 根有效且时间唯一的 K 线。")
    except (ProviderError, KeyError, TypeError, ValueError) as exc:
        return _fail(out, exc)
    return out
