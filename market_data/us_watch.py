"""Headless U.S. equity watcher.

This module intentionally separates *market leadership detection* from *entry
eligibility*. A stock can be a leader and still be too extended to buy. The
watcher is designed to run without the browser UI, including from GitHub
Actions or a self-hosted runner.

Public Yahoo chart/search data are used only as observation inputs. No orders,
account access, or trading actions exist here.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from statistics import fmean, median
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from .transport import fetch

ET = ZoneInfo("America/New_York")
DEFAULT_SYMBOLS = (
    "NVDA","TSM","MSFT","AMD","AVGO","MU","INTC","SNDK","MRVL","GLW",
    "GOOG","META","ARM","ASML","AMAT","LRCX","KLAC","ANET","VRT","ORCL",
    "CRCL","MSTR","AMZN","DELL","AAPL","SMCI","CRDO","ALAB","COHR","LITE",
    "AAOI","QCOM","MPWR","MCHP","NXPI","ETN","VST","CEG","HPE","WDC","STX",
)
OUTPUT_ENV = "QD_WATCH_OUTPUT"
_DAILY_CACHE = {}
_NEWS_CACHE = {}
_VOLUME_PROFILE_CACHE = {}


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pct(a, b):
    return (a / b - 1.0) * 100.0 if _finite(a) and _finite(b) and b != 0 else None


def _yahoo_url(symbol, *, range_value, interval, include_prepost):
    path = "/v8/finance/chart/" + quote(symbol.replace(".", "-"), safe="")
    query = urlencode({
        "range": range_value,
        "interval": interval,
        "includePrePost": "true" if include_prepost else "false",
        "events": "div,splits",
    })
    return "https://query1.finance.yahoo.com" + path + "?" + query


def _news_url(symbol):
    return "https://query1.finance.yahoo.com/v1/finance/search?" + urlencode({
        "q": symbol,
        "quotesCount": 0,
        "newsCount": 6,
        "enableFuzzyQuery": "false",
    })


def _chart(fetcher, symbol, *, range_value, interval, include_prepost, require_us_equity=True):
    wrapped = fetcher(_yahoo_url(symbol, range_value=range_value, interval=interval,
                                 include_prepost=include_prepost), kind="json")
    data, receipt = wrapped["data"], wrapped["receipt"]
    chart = data.get("chart", {}) if isinstance(data, dict) else {}
    if chart.get("error"):
        raise ValueError("Yahoo chart application error: " + str(chart["error"]))
    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError("Yahoo chart result missing")
    item = results[0]
    meta = item.get("meta", {})
    expected = symbol.replace(".", "-").upper()
    if str(meta.get("symbol", "")).upper() != expected:
        raise ValueError("Yahoo returned a different symbol")
    if require_us_equity:
        if meta.get("instrumentType") not in {"EQUITY", "ETF"}:
            raise ValueError("Yahoo asset is not an equity/ETF")
        if meta.get("exchangeTimezoneName") != "America/New_York" or meta.get("currency") != "USD":
            raise ValueError("Yahoo market identity does not match U.S. USD equity")
    times = item.get("timestamp") or []
    quote_rows = ((item.get("indicators") or {}).get("quote") or [])
    if not quote_rows or not isinstance(quote_rows[0], dict):
        raise ValueError("Yahoo OHLCV missing")
    q = quote_rows[0]
    fields = [q.get(k) for k in ("open", "high", "low", "close", "volume")]
    if any(not isinstance(v, list) or len(v) != len(times) for v in fields):
        raise ValueError("Yahoo OHLCV length mismatch")
    rows = []
    for i, t in enumerate(times):
        values = [v[i] for v in fields]
        try:
            o, h, l, c, volume = [float(x) for x in values]
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (o, h, l, c, volume)) or volume < 0:
            continue
        if not l <= min(o, c) <= max(o, c) <= h:
            continue
        rows.append({"t": int(t), "open": o, "high": h, "low": l, "close": c, "volume": volume})
    if not rows:
        raise ValueError("Yahoo returned no valid bars")
    return rows, meta, receipt


def _market_proxy(fetcher, symbol, now):
    try:
        rows, meta, receipt = _chart(fetcher, symbol, range_value="5d", interval="5m",
                                     include_prepost=True, require_us_equity=False)
        last = rows[-1]
        prior = next((float(meta[k]) for k in ("regularMarketPreviousClose", "chartPreviousClose", "previousClose")
                      if _finite(meta.get(k))), None)
        return {
            "ok": True,
            "symbol": symbol,
            "instrument_type": meta.get("instrumentType"),
            "price": last["close"],
            "change_pct": _pct(last["close"], prior),
            "prior_reference": prior,
            "last_bar_utc": datetime.fromtimestamp(last["t"], timezone.utc).isoformat(),
            "known_at": receipt.get("received_at_utc"),
            "role": "OVERNIGHT_REGIME_PROXY",
        }
    except Exception as exc:
        return {"ok": False, "symbol": symbol, "role": "OVERNIGHT_REGIME_PROXY",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _market_context(fetcher, now):
    symbols = ("NQ=F", "ES=F", "QQQ")
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="qd-regime") as pool:
        futures = {pool.submit(_market_proxy, fetcher, symbol, now): symbol for symbol in symbols}
        return {symbol: future.result() for future, symbol in ((f, futures[f]) for f in as_completed(futures))}


def _news(fetcher, symbol, now):
    cached = _NEWS_CACHE.get(symbol)
    if cached and (now.timestamp() - cached[0]) < 900:
        return cached[1]
    try:
        wrapped = fetcher(_news_url(symbol), kind="json")
        data, receipt = wrapped["data"], wrapped["receipt"]
        items = data.get("news", []) if isinstance(data, dict) else []
        out = []
        for item in items[:6]:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            stamp = item.get("providerPublishTime")
            age_h = (now.timestamp() - float(stamp)) / 3600 if _finite(stamp) else None
            out.append({
                "title": str(item["title"])[:240],
                "publisher": item.get("publisher"),
                "published_at_utc": datetime.fromtimestamp(float(stamp), timezone.utc).isoformat() if _finite(stamp) else None,
                "age_hours": round(age_h, 2) if _finite(age_h) else None,
                "link": item.get("link"),
            })
        result = {"ok": True, "known_at": receipt.get("received_at_utc"), "items": out,
                  "fresh_36h_count": sum(_finite(x["age_hours"]) and 0 <= x["age_hours"] <= 36 for x in out)}
        _NEWS_CACHE[symbol] = (now.timestamp(), result)
        return result
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "items": [], "fresh_36h_count": 0}
        _NEWS_CACHE[symbol] = (now.timestamp(), result)
        return result


def _completed_daily(rows, now):
    today = now.astimezone(ET).date()
    completed = []
    for r in rows:
        d = datetime.fromtimestamp(r["t"], ET).date()
        if d < today:
            completed.append(r)
    return completed


def _daily_metrics(rows, now):
    rows = _completed_daily(rows, now)
    if len(rows) < 22:
        raise ValueError(f"need >=22 completed daily bars, got {len(rows)}")
    closes = [r["close"] for r in rows]
    ma5s = [fmean(closes[-5-i:len(closes)-i]) for i in (0, 1, 2)]
    ma10 = fmean(closes[-10:])
    prev_ma10 = fmean(closes[-11:-1])
    ma20 = fmean(closes[-20:])
    prev_ma20 = fmean(closes[-21:-1])
    trs = []
    for p, r in zip(rows[-6:-1], rows[-5:]):
        trs.append(max(r["high"]-r["low"], abs(r["high"]-p["close"]), abs(r["low"]-p["close"])))
    atr5 = fmean(trs)
    if atr5 <= 0:
        raise ValueError("ATR5 <= 0")
    return {
        "previous_session_date": datetime.fromtimestamp(rows[-1]["t"], ET).date().isoformat(),
        "prior_close": rows[-1]["close"],
        "ma5": ma5s[0],
        "ma5_slope_1d": ma5s[0] - ma5s[1],
        "ma5_3point_slope": (ma5s[0] - ma5s[2]) / 2.0,
        "ma10": ma10,
        "ma10_direction": "UP" if ma10 > prev_ma10 else "DOWN" if ma10 < prev_ma10 else "FLAT",
        "ma20": ma20,
        "ma20_direction": "UP" if ma20 > prev_ma20 else "DOWN" if ma20 < prev_ma20 else "FLAT",
        "atr5": atr5,
        "atr_pct": atr5 / rows[-1]["close"] * 100,
    }


def _session_name(stamp):
    d = datetime.fromtimestamp(stamp, ET)
    minute = d.hour * 60 + d.minute
    if 240 <= minute < 570:
        return "PRE"
    if 570 <= minute < 960:
        return "RTH"
    if 960 <= minute < 1200:
        return "POST"
    return "OVERNIGHT"


def _today_intraday(rows, now):
    today = now.astimezone(ET).date()
    out = []
    for r in rows:
        d = datetime.fromtimestamp(r["t"], ET)
        if d.date() != today:
            continue
        x = dict(r)
        x["session"] = _session_name(r["t"])
        x["completed"] = now.timestamp() >= r["t"] + 300
        out.append(x)
    return out


def _vwap_path(rows):
    total_v = 0.0
    total_pv = 0.0
    out = []
    for r in rows:
        if not r.get("completed") or r["volume"] <= 0:
            continue
        typical = (r["high"] + r["low"] + r["close"]) / 3.0
        total_v += r["volume"]
        total_pv += typical * r["volume"]
        out.append((r, total_pv / total_v))
    return out


def _same_time_rvol(current_rows, history_rows, now):
    completed = [r for r in current_rows if r.get("session") == "RTH" and r.get("completed")]
    if not completed:
        return None, 0
    target_local = datetime.fromtimestamp(completed[-1]["t"], ET)
    target_minute = target_local.hour * 60 + target_local.minute
    current_cumulative = sum(r["volume"] for r in completed)
    today = now.astimezone(ET).date()
    by_date = {}
    for r in history_rows:
        local = datetime.fromtimestamp(r["t"], ET)
        minute = local.hour * 60 + local.minute
        if local.date() >= today or not 570 <= minute < 960 or minute > target_minute:
            continue
        by_date[local.date()] = by_date.get(local.date(), 0.0) + r["volume"]
    samples = [by_date[d] for d in sorted(by_date)[-20:] if by_date[d] > 0]
    if not samples:
        return None, 0
    baseline = median(samples)
    return (current_cumulative / baseline if baseline > 0 else None), len(samples)


def _intraday_metrics(rows, daily, now, history_rows=None):
    today = _today_intraday(rows, now)
    if not today:
        raise ValueError("no bars for current ET date")
    last = max(today, key=lambda r: r["t"])
    pre = [r for r in today if r["session"] == "PRE"]
    rth = [r for r in today if r["session"] == "RTH"]
    post = [r for r in today if r["session"] == "POST"]
    completed_rth = [r for r in rth if r["completed"]]
    vpath = _vwap_path(rth)
    current_vwap = vpath[-1][1] if vpath else None
    two_above = False
    if len(vpath) >= 2:
        (a, av), (b, bv) = vpath[-2], vpath[-1]
        two_above = a["close"] > av and a["close"] > daily["ma5"] and b["close"] > bv and b["close"] > daily["ma5"]
    current = last["close"]
    prior = daily["prior_close"]
    pre_last = pre[-1]["close"] if pre else None
    rth_open = rth[0]["open"] if rth else None
    same_time_rvol, rvol_samples = _same_time_rvol(today, history_rows or [], now)
    result = {
        "current_price": current,
        "current_session": last["session"],
        "current_bar_time_utc": datetime.fromtimestamp(last["t"], timezone.utc).isoformat(),
        "change_pct": _pct(current, prior),
        "premarket_last": pre_last,
        "premarket_change_pct": _pct(pre_last, prior),
        "rth_open": rth_open,
        "open_gap_pct": _pct(rth_open, prior),
        "rth_vwap_approx": current_vwap,
        "price_vs_vwap_pct": _pct(current, current_vwap),
        "two_completed_5m_above_vwap_and_ma5": two_above,
        "rth_completed_bars": len(completed_rth),
        "premarket_bars": len(pre),
        "postmarket_bars": len(post),
        "d5_atr": (current - daily["ma5"]) / daily["atr5"],
        "same_time_rvol": same_time_rvol,
        "same_time_rvol_samples": rvol_samples,
    }
    if rth:
        result["move_from_rth_open_pct"] = _pct(current, rth_open)
        result["rth_high"] = max(r["high"] for r in rth)
        result["rth_low"] = min(r["low"] for r in rth)
    else:
        result["move_from_rth_open_pct"] = None
        result["rth_high"] = None
        result["rth_low"] = None
    return result


def classify(daily, intra, qqq_change=None):
    trend_ok = daily["ma5_slope_1d"] > 0 and daily["ma5_3point_slope"] >= 0
    d5 = intra["d5_atr"]
    chg = intra.get("change_pct")
    pre = intra.get("premarket_change_pct")
    gap = intra.get("open_gap_pct")
    from_open = intra.get("move_from_rth_open_pct")
    leader_reasons = []
    if _finite(chg) and chg >= 1.5:
        leader_reasons.append(f"day +{chg:.2f}%")
    if _finite(pre) and pre >= 0.8:
        leader_reasons.append(f"premarket +{pre:.2f}%")
    if _finite(gap) and gap >= 0.8:
        leader_reasons.append(f"open gap +{gap:.2f}%")
    if _finite(from_open) and from_open >= 1.0:
        leader_reasons.append(f"RTH from open +{from_open:.2f}%")
    leader = bool(leader_reasons)

    standard_geometry = -0.10 <= d5 <= 0.20
    observation_geometry = -0.35 <= d5 <= 0.40
    rvol = intra.get("same_time_rvol")
    confirmed = (trend_ok and standard_geometry
                 and intra.get("two_completed_5m_above_vwap_and_ma5", False)
                 and _finite(rvol) and rvol >= 0.8)

    if confirmed:
        state = "ENTRY_CONFIRMED"
        reason = "daily trend + MA5/ATR geometry + two completed 5m closes above session VWAP/MA5 + same-time RVOL >= 0.8"
    elif trend_ok and observation_geometry:
        state = "ENTRY_ARMED"
        reason = "daily trend and MA5/ATR geometry valid; VWAP/5m or same-time RVOL confirmation incomplete"
    elif leader and d5 > 0.35:
        state = "LEADER_HOT_NO_CHASE"
        reason = "strong move detected, but price is too extended above completed-day MA5"
    elif leader:
        state = "LEADER_WATCH"
        reason = "strong move detected; entry gate is not yet satisfied"
    elif d5 > 0.35:
        state = "EXTENDED"
        reason = "price > +0.35 ATR above completed-day MA5"
    else:
        state = "WATCH"
        reason = "no material leader or entry transition"

    return {
        "state": state,
        "reason": reason,
        "leader_detected": leader,
        "leader_reasons": leader_reasons,
        "daily_trend_gate": trend_ok,
        "standard_entry_geometry": standard_geometry,
        "observation_geometry": observation_geometry,
    }


def _scan_symbol(symbol, fetcher, now, qqq_change=None):
    cache_key = (symbol, now.astimezone(ET).date().isoformat())
    cached = _DAILY_CACHE.get(cache_key)
    if cached is None:
        daily_rows, _, daily_receipt = _chart(fetcher, symbol, range_value="3mo", interval="1d", include_prepost=False)
        daily = _daily_metrics(daily_rows, now)
        cached = (daily, daily_receipt.get("received_at_utc"))
        _DAILY_CACHE[cache_key] = cached
    daily, daily_known_at = cached
    volume_cached = _VOLUME_PROFILE_CACHE.get(cache_key)
    if volume_cached is None:
        volume_rows, _, volume_receipt = _chart(fetcher, symbol, range_value="1mo", interval="5m", include_prepost=False)
        volume_cached = (volume_rows, volume_receipt.get("received_at_utc"))
        _VOLUME_PROFILE_CACHE[cache_key] = volume_cached
    volume_rows, volume_known_at = volume_cached
    intraday_rows, _, intra_receipt = _chart(fetcher, symbol, range_value="5d", interval="5m", include_prepost=True)
    intra = _intraday_metrics(intraday_rows, daily, now, history_rows=volume_rows)
    state = classify(daily, intra, qqq_change=qqq_change)
    previous = datetime.fromisoformat(daily["previous_session_date"]).date()
    current = now.astimezone(ET).date()
    calendar_gap = (current - previous).days
    return {
        "symbol": symbol,
        "status": "OK",
        "source": "Yahoo Finance public chart",
        "known_at": intra_receipt.get("received_at_utc"),
        "daily_known_at": daily_known_at,
        "volume_profile_known_at": volume_known_at,
        "calendar_days_since_previous_session": calendar_gap,
        "monday_weekend_context": current.weekday() == 0 and calendar_gap >= 3,
        "daily": daily,
        "intraday": intra,
        **state,
    }


def scan_once(symbols=DEFAULT_SYMBOLS, fetcher=fetch, now=None, workers=8):
    now = now or datetime.now(timezone.utc)
    symbols = tuple(dict.fromkeys(s.upper() for s in symbols))
    market_context = _market_context(fetcher, now)
    rows = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12)), thread_name_prefix="qd-us-watch") as pool:
        futures = {pool.submit(_scan_symbol, s, fetcher, now, qqq_change): s for s in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append({"symbol": symbol, "status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:240]}"})
    rows.sort(key=lambda r: (
        {"ENTRY_CONFIRMED": 0, "ENTRY_ARMED": 1, "LEADER_HOT_NO_CHASE": 2, "LEADER_WATCH": 3,
         "EXTENDED": 4, "WATCH": 5}.get(r.get("state"), 9),
        -(r.get("intraday", {}).get("change_pct") or -999),
        r["symbol"],
    ))

    interesting = [r["symbol"] for r in rows if r.get("state") in {
        "ENTRY_CONFIRMED", "ENTRY_ARMED", "LEADER_HOT_NO_CHASE", "LEADER_WATCH"
    }]
    news = {}
    if interesting:
        with ThreadPoolExecutor(max_workers=min(6, len(interesting)), thread_name_prefix="qd-news") as pool:
            nf = {pool.submit(_news, fetcher, s, now): s for s in interesting}
            for f in as_completed(nf):
                news[nf[f]] = f.result()
    for row in rows:
        if row["symbol"] in news:
            row["news"] = news[row["symbol"]]

    et_date = now.astimezone(ET).date().isoformat()
    alert_states = {"ENTRY_CONFIRMED", "ENTRY_ARMED", "LEADER_HOT_NO_CHASE", "LEADER_WATCH"}
    alerts = []
    for r in rows:
        if r.get("state") not in alert_states:
            continue
        alerts.append({
            "event_key": f"{et_date}|{r['symbol']}|{r['state']}",
            "symbol": r["symbol"],
            "state": r["state"],
            "reason": r["reason"],
            "leader_reasons": r.get("leader_reasons", []),
            "current_price": r["intraday"]["current_price"],
            "change_pct": r["intraday"]["change_pct"],
            "premarket_change_pct": r["intraday"]["premarket_change_pct"],
            "open_gap_pct": r["intraday"]["open_gap_pct"],
            "d5_atr": r["intraday"]["d5_atr"],
            "rth_vwap_approx": r["intraday"]["rth_vwap_approx"],
            "same_time_rvol": r["intraday"]["same_time_rvol"],
            "same_time_rvol_samples": r["intraday"]["same_time_rvol_samples"],
            "relative_change_vs_qqq_pp": r.get("relative_change_vs_qqq_pp"),
            "market_context": market_context,
            "news": (r.get("news") or {}).get("items", [])[:3],
        })
    return {
        "generated_at_utc": now.isoformat(),
        "generated_at_et": now.astimezone(ET).isoformat(),
        "mode": "PUBLIC_HEADLESS_OBSERVATION",
        "trading_enabled": False,
        "sources": ["Yahoo Finance public chart", "Yahoo Finance public search/news"],
        "market_context": market_context,
        "symbols_requested": list(symbols),
        "rows": rows,
        "alerts": alerts,
    }


def _write(report, output=None):
    target = output or os.getenv(OUTPUT_ENV)
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(*, symbols=DEFAULT_SYMBOLS, poll_seconds=60, duration_minutes=0, github_alerts=False,
        once=False, output=None, fetcher=fetch):
    if isinstance(poll_seconds, bool) or not 15 <= int(poll_seconds) <= 3600:
        raise ValueError("poll_seconds must be 15..3600")
    if duration_minutes < 0 or duration_minutes > 360:
        raise ValueError("duration_minutes must be 0..360")
    started = time.monotonic()
    while True:
        report = scan_once(symbols=symbols, fetcher=fetcher)
        _write(report, output)
        compact = {
            "generated_at_et": report.get("generated_at_et"),
            "alerts": [
                {"symbol": a.get("symbol"), "state": a.get("state"), "change_pct": a.get("change_pct"),
                 "d5_atr": a.get("d5_atr")}
                for a in report.get("alerts", [])
            ],
            "errors": [
                {"symbol": r.get("symbol"), "error": r.get("error")}
                for r in report.get("rows", []) if r.get("status") == "ERROR"
            ],
        }
        print(json.dumps(compact, ensure_ascii=False, allow_nan=False), flush=True)
        if github_alerts:
            from .github_alerts import publish
            publish(report)
        if once:
            return report
        if duration_minutes and time.monotonic() - started >= duration_minutes * 60:
            return report
        time.sleep(int(poll_seconds))
