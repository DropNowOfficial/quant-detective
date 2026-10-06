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
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from statistics import fmean, median
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from .quality import BarFact, BarQualityInput, QualityResult, evaluate_bars
from .quality_profiles import US_PUBLIC_5M_POLICY
from .session_clock import schedule_at
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
_SESSION_SCHEDULE_CACHE = {}


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
    receipt = dict(receipt)
    captured_at_ms = _receipt_capture_ms(receipt)
    rows, invalid, duplicates, conflicts = [], [], [], []
    selected = {}
    for i, t in enumerate(times):
        stamp = t if type(t) is int and t >= 0 else None
        values = [v[i] for v in fields]
        reason = None
        try:
            if stamp is None or any(isinstance(x, bool) for x in values):
                raise ValueError("invalid timestamp or boolean value")
            o, h, l, c, volume = [float(x) for x in values]
            if not all(math.isfinite(x) for x in (o, h, l, c, volume)) or volume < 0:
                raise ValueError("nonfinite OHLCV or negative volume")
            if min(o, h, l, c) <= 0 or not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError("invalid OHLC bounds")
        except (TypeError, ValueError, OverflowError) as exc:
            reason = str(exc)
        if reason is not None:
            invalid.append({"index": i, "t": stamp, "reason": reason})
            continue
        end_ms = stamp*1000 + 300_000
        if interval == "1d":
            day_schedule = _historical_schedule(datetime.fromtimestamp(stamp, ET))
            end_ms = day_schedule.close_ms
        row = {"t": stamp, "open": o, "high": h, "low": l, "close": c, "volume": volume,
               "closed_at_capture": end_ms is not None and end_ms <= captured_at_ms}
        if stamp in selected:
            duplicates.append({"index": i, "t": stamp})
            if any(selected[stamp][k] != row[k] for k in ("open", "high", "low", "close", "volume")):
                conflicts.append({"index": i, "t": stamp, "reason": "conflicting duplicate OHLCV"})
            continue
        selected[stamp] = row
        rows.append(row)
    receipt.update(captured_at_ms=captured_at_ms, invalid_rows=len(invalid),
                   invalid_row_evidence=invalid, duplicate_rows=len(duplicates),
                   duplicate_row_evidence=duplicates, duplicate_conflicts=len(conflicts),
                   duplicate_conflict_evidence=conflicts)
    return sorted(rows, key=lambda row: row["t"]), meta, receipt


def _receipt_capture_ms(receipt):
    """Source receipt only; absent or malformed timestamps fail the quality core."""
    try:
        stamp = receipt.get("received_at_utc")
        if not isinstance(stamp, str):
            return -1
        captured = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if captured.tzinfo is None:
            return -1
        return int(captured.timestamp()*1000)
    except (KeyError, TypeError, ValueError, OverflowError):
        return -1


def _schedule(now, schedule_provider=schedule_at):
    return schedule_provider(int(now.timestamp()*1000), calendar_name="XNYS",
                             interval_ms=US_PUBLIC_5M_POLICY.interval_ms,
                             grace_ms=US_PUBLIC_5M_POLICY.publication_grace_ms)


def _bar_facts(rows, captured_at_ms):
    return tuple(BarFact(r["t"]*1000, r["t"]*1000+300_000,
                         r.get("closed_at_capture", r["t"]*1000+300_000 <= captured_at_ms),
                         r.get("is_fill_forward", False)) for r in rows)


def _window_invalid(receipt, predicate):
    # Unlocatable corrupt rows cannot be proved outside a dependency window.
    return sum(e.get("t") is None or predicate(e["t"])
               for key in ("invalid_row_evidence", "duplicate_row_evidence")
               for e in receipt.get(key, []))


def _consumed_intraday_invalid(receipt, rows, now, *, include_premarket):
    """Scope defects to the rows consumed by metrics, not publication grace.

    Metrics consume every current RTH row (including live/grace-window rows),
    the latest current price, and, for stocks, the last premarket price.
    """
    today = now.astimezone(ET).date()
    timestamps = [row["t"] for row in rows]
    timestamps.extend(error["t"] for key in ("invalid_row_evidence", "duplicate_row_evidence")
                      for error in receipt.get(key, []) if error.get("t") is not None)
    current = [stamp for stamp in timestamps if datetime.fromtimestamp(stamp, ET).date() == today]
    latest = max(current) if current else None
    pre = [stamp for stamp in current if _session_name(stamp) == "PRE"]
    latest_pre = max(pre) if include_premarket and pre else None
    return _window_invalid(receipt, lambda stamp:
        datetime.fromtimestamp(stamp, ET).date() == today
        and (_session_name(stamp) == "RTH" or stamp == latest or stamp == latest_pre))


def _historical_schedule(day, schedule_provider=schedule_at):
    key = (day.date().isoformat(), schedule_provider)
    if key not in _SESSION_SCHEDULE_CACHE:
        result = _schedule(day.replace(hour=12, minute=0, second=0, microsecond=0), schedule_provider)
        if result.phase == "CALENDAR_ERROR":
            return result
        _SESSION_SCHEDULE_CACHE[key] = result
    return _SESSION_SCHEDULE_CACHE[key]


def _required_daily_dates(now):
    """The last 22 official completed sessions, not the last 22 retained rows."""
    dates = set()
    day = now.astimezone(ET).replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
    for _ in range(60):
        if _historical_schedule(day).open_ms is not None:
            dates.add(day.date().isoformat())
        if len(dates) == 22:
            break
        day -= timedelta(days=1)
    return dates


def _market_proxy(fetcher, symbol, now, *, clock=None):
    try:
        rows, meta, receipt = _chart(fetcher, symbol, range_value="5d", interval="5m",
                                     include_prepost=True, require_us_equity=symbol == "QQQ")
        if clock is not None:
            now = clock()  # Live process boundary, after source acquisition.
        last = rows[-1]
        prior = next((float(meta[k]) for k in ("regularMarketPreviousClose", "chartPreviousClose", "previousClose")
                      if _finite(meta.get(k))), None)
        result = {
            "ok": True, "symbol": symbol, "instrument_type": meta.get("instrumentType"),
            "price": last["close"], "change_pct": _pct(last["close"], prior),
            "prior_reference": prior,
            "last_bar_utc": datetime.fromtimestamp(last["t"], timezone.utc).isoformat(),
            "current_bar_time_utc": datetime.fromtimestamp(last["t"], timezone.utc).isoformat(),
            "known_at": receipt.get("received_at_utc"), "role": "OVERNIGHT_REGIME_PROXY",
        }
        if symbol == "QQQ":
            captured_at_ms = receipt["captured_at_ms"]
            today = _today_intraday(rows, now, captured_at_ms=captured_at_ms)
            rth = [r for r in today if r["session"] == "RTH"]
            current_schedule = _schedule(now)
            invalid = _consumed_intraday_invalid(receipt, rows, now, include_premarket=False)
            benchmark_policy = replace(US_PUBLIC_5M_POLICY, policy_id="us_public_5m_benchmark_v1",
                                       min_rvol_sessions=0, min_completed_bars=1)
            quality = evaluate_bars(BarQualityInput(int(now.timestamp()*1000), captured_at_ms,
                _bar_facts(rth, captured_at_ms), invalid, 0, None), benchmark_policy, current_schedule)
            result.update(quality=asdict(quality), quality_policy_id=benchmark_policy.policy_id)
        return result
    except Exception as exc:
        return {"ok": False, "symbol": symbol, "role": "OVERNIGHT_REGIME_PROXY",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _market_context(fetcher, now, *, clock=None):
    symbols = ("NQ=F", "ES=F", "QQQ")
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="qd-regime") as pool:
        futures = {pool.submit(_market_proxy, fetcher, symbol, now, clock=clock): symbol for symbol in symbols}
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
        if d < today and r.get("closed_at_capture", True):
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


def _today_intraday(rows, now, *, captured_at_ms):
    today = now.astimezone(ET).date()
    out = []
    for r in rows:
        d = datetime.fromtimestamp(r["t"], ET)
        if d.date() != today:
            continue
        x = dict(r)
        x["session"] = _session_name(r["t"])
        x["completed"] = r.get("closed_at_capture", captured_at_ms >= (r["t"] + 300)*1000)
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


def _history_sessions(history_rows, now, schedule_provider):
    today = now.astimezone(ET).date()
    by_date = {}
    for row in history_rows:
        local = datetime.fromtimestamp(row["t"], ET)
        if local.date() < today:
            by_date.setdefault(local.date(), []).append(row)
    candidates = []
    for day in sorted(by_date):
        historical = _historical_schedule(datetime.combine(day, datetime.min.time(), ET), schedule_provider)
        if historical.phase != "CLOSED":
            candidates.append((day, historical))
    return by_date, candidates[-20:]


def _same_time_rvol(current_rows, history_rows, now, *, schedule_provider):
    """Median cumulative volume of 20 complete, calendar-comparable sessions.

    A zero-volume bar is present evidence. Missing/duplicate/open/fill-forward
    bars are not filled with zero; their historical session is excluded.
    """
    current_schedule = _schedule(now, schedule_provider)
    completed = sorted((r for r in current_rows if r.get("session") == "RTH"
                        and r.get("completed") and not r.get("is_fill_forward", False)),
                       key=lambda r: r["t"])
    if not completed or current_schedule.open_ms is None:
        return None, 0
    target_end = (completed[-1]["t"]+300)*1000
    expected = tuple(range(current_schedule.open_ms//1000, target_end//1000, 300))
    if tuple(r["t"] for r in completed) != expected:
        return None, 0
    target_elapsed = target_end-current_schedule.open_ms
    current_cumulative = sum(r["volume"] for r in completed)
    by_date, candidates = _history_sessions(history_rows, now, schedule_provider)
    samples = []
    for day, historical in candidates:
        if historical.open_ms is None or historical.close_ms is None:
            continue
        historical_target = historical.open_ms+target_elapsed
        if historical_target > historical.close_ms:
            continue
        required = tuple(range(historical.open_ms//1000, historical_target//1000, 300))
        window = sorted((r for r in by_date[day]
                         if historical.open_ms//1000 <= r["t"] < historical_target//1000),
                        key=lambda r: r["t"])
        if (tuple(r["t"] for r in window) != required
                or any(not r.get("closed_at_capture", True) or r.get("is_fill_forward", False)
                       or not _finite(r.get("volume")) or r["volume"] < 0 for r in window)):
            continue
        samples.append(sum(r["volume"] for r in window))
    if len(samples) < US_PUBLIC_5M_POLICY.min_rvol_sessions:
        return None, len(samples)
    baseline = median(samples)
    return (current_cumulative/baseline if baseline > 0 else None), len(samples)


def _intraday_metrics(rows, daily, now, history_rows=None, *, captured_at_ms: int):
    today = _today_intraday(rows, now, captured_at_ms=captured_at_ms)
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
    same_time_rvol, rvol_samples = _same_time_rvol(today, history_rows or [], now, schedule_provider=schedule_at)
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


def classify(daily, intra, qqq_change=None, *, quality: QualityResult):
    if not isinstance(quality, QualityResult):
        raise TypeError("quality must be a QualityResult")
    if not quality.observation_ok:
        closed = "SESSION_NOT_OPEN" in quality.reason_codes
        return {"state": "MARKET_CLOSED" if closed else "DATA_UNAVAILABLE",
                "reason": ", ".join(quality.reason_codes), "leader_detected": False,
                "leader_reasons": [], "daily_trend_gate": False,
                "standard_entry_geometry": False, "observation_geometry": False,
                "relative_change_vs_qqq_pp": None}
    trend_ok = daily["ma5_slope_1d"] > 0 and daily["ma5_3point_slope"] >= 0
    d5 = intra["d5_atr"]
    chg = intra.get("change_pct")
    pre = intra.get("premarket_change_pct")
    gap = intra.get("open_gap_pct")
    from_open = intra.get("move_from_rth_open_pct")
    leader_reasons = []
    rs_qqq = chg - qqq_change if _finite(chg) and _finite(qqq_change) else None
    if _finite(chg) and chg >= 1.0:
        leader_reasons.append(f"day +{chg:.2f}%")
    if _finite(rs_qqq) and rs_qqq >= 0.5:
        leader_reasons.append(f"vs QQQ +{rs_qqq:.2f}pp")
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
    confirmed = (quality.confirmation_ok and trend_ok and standard_geometry
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
        "relative_change_vs_qqq_pp": rs_qqq,
    }


def _scan_symbol(symbol, fetcher, now, *, qqq_context=None, clock=None):
    cache_key = (symbol, now.astimezone(ET).date().isoformat())
    cached = _DAILY_CACHE.get(cache_key)
    if cached is None:
        daily_rows, _, daily_receipt = _chart(fetcher, symbol, range_value="3mo", interval="1d", include_prepost=False)
        daily_error = None
        try:
            daily = _daily_metrics(daily_rows, now)
        except ValueError as exc:
            daily, daily_error = {}, str(exc)
        completed_daily = _completed_daily(daily_rows, now)
        daily_sessions = {datetime.fromtimestamp(r["t"], ET).date().isoformat() for r in completed_daily}
        cached = (daily, daily_receipt, daily_sessions, daily_error)
        _DAILY_CACHE[cache_key] = cached
    daily, daily_receipt, daily_sessions, daily_error = cached
    volume_cached = _VOLUME_PROFILE_CACHE.get(cache_key)
    if volume_cached is None:
        volume_rows, _, volume_receipt = _chart(fetcher, symbol, range_value="1mo", interval="5m", include_prepost=False)
        volume_cached = (volume_rows, volume_receipt)
        _VOLUME_PROFILE_CACHE[cache_key] = volume_cached
    volume_rows, volume_receipt = volume_cached
    intraday_rows, _, intra_receipt = _chart(fetcher, symbol, range_value="5d", interval="5m", include_prepost=True)
    if clock is not None:
        now = clock()  # Queued/acquisition time is elapsed time, not future data.
    captured_at_ms = intra_receipt["captured_at_ms"]
    intraday = _today_intraday(intraday_rows, now, captured_at_ms=captured_at_ms)
    current_schedule = _schedule(now)
    intra_invalid = _consumed_intraday_invalid(intra_receipt, intraday_rows, now, include_premarket=True)
    daily_dates = _required_daily_dates(now)
    missing_daily = daily_dates-daily_sessions
    daily_invalid = _window_invalid(daily_receipt,
        lambda stamp: datetime.fromtimestamp(stamp, ET).date().isoformat() in daily_dates)
    daily_capture_valid = (0 <= daily_receipt["captured_at_ms"]
                           <= int(now.timestamp()*1000)+US_PUBLIC_5M_POLICY.future_clock_tolerance_ms)
    daily_invalid += len(missing_daily) + (22-len(daily_dates)) + int(not daily_capture_valid)
    if daily_error and not daily_invalid:
        daily_invalid += 1
    # A malformed/duplicate historical bar excludes its day from RVOL, not the
    # independent daily/current structure. Preserve the complete receipt evidence.
    history_unknown = any(e.get("t") is None for e in volume_receipt.get("invalid_row_evidence", []))
    history_capture_valid = (0 <= volume_receipt["captured_at_ms"]
                             <= int(now.timestamp()*1000)+US_PUBLIC_5M_POLICY.future_clock_tolerance_ms)
    history_for_rvol = [] if history_unknown or not history_capture_valid else list(volume_rows)
    for key in ("invalid_row_evidence", "duplicate_row_evidence"):
        for error in volume_receipt.get(key, []):
            if error.get("t") is not None and history_capture_valid and not history_unknown:
                # A located invalid/duplicate remains in the historical window
                # as unusable evidence, so only a dependent window rejects it.
                history_for_rvol.append({"t": error["t"], "volume": None})
    if daily and intraday:
        intra = _intraday_metrics(intraday_rows, daily, now, history_rows=history_for_rvol,
                                  captured_at_ms=captured_at_ms)
    else:
        _, rvol_samples = _same_time_rvol(intraday, history_for_rvol, now, schedule_provider=schedule_at)
        intra = {"same_time_rvol": None, "same_time_rvol_samples": rvol_samples}
    facts = _bar_facts([r for r in intraday if r["session"] == "RTH"], captured_at_ms)
    completed_ends = [f.end_ms for f in facts if f.closed_at_capture and not f.is_fill_forward]
    elapsed = max(completed_ends)-current_schedule.open_ms if completed_ends and current_schedule.open_ms is not None else 0
    _, history_candidates = _history_sessions(history_for_rvol, now, schedule_at)
    history_schedules = dict(history_candidates)
    def in_history_window(stamp):
        historical = history_schedules.get(datetime.fromtimestamp(stamp, ET).date())
        return (historical is not None and historical.open_ms is not None
                and historical.close_ms is not None
                and historical.open_ms+elapsed <= historical.close_ms
                and historical.open_ms <= stamp*1000 < historical.open_ms+elapsed)
    history_invalid = _window_invalid(volume_receipt, in_history_window)
    # Reject this result now. Retry only via the next ordinary scan, keeping the
    # configured source/range; insufficient RVOL count alone never retries.
    if daily_invalid:
        _DAILY_CACHE.pop(cache_key, None)
    if history_invalid or not history_capture_valid:
        _VOLUME_PROFILE_CACHE.pop(cache_key, None)
    quality_input = BarQualityInput(int(now.timestamp()*1000), captured_at_ms, facts,
        intra_invalid+daily_invalid, intra["same_time_rvol_samples"],
        daily.get("previous_session_date", max(daily_sessions) if daily_sessions else None))
    quality = evaluate_bars(quality_input, US_PUBLIC_5M_POLICY, current_schedule)
    benchmark = _benchmark_dependency(qqq_context, intra, quality)
    state = classify(daily, intra, qqq_change=benchmark["change_pct"], quality=quality)
    previous = datetime.fromisoformat(quality_input.last_daily_session).date() if quality_input.last_daily_session else None
    current = now.astimezone(ET).date()
    calendar_gap = (current-previous).days if previous else None
    # JSON-safe contract for cached-structure consumers: recreate BarQualityInput
    # with fresh now_ms and these immutable facts, then call evaluate_bars with a
    # fresh XNYS schedule. Dependency snapshots retain original source captures;
    # their TTL is not a live-bar TTL. daily.invalid_rows is last-22-session scoped;
    # history invalidity is already reflected in complete_rvol_sessions.
    evidence = {
        "schema_version": 1, "policy_id": US_PUBLIC_5M_POLICY.policy_id,
        "calendar_name": "XNYS", "captured_at_ms": captured_at_ms,
        "bars": [asdict(f) for f in facts], "invalid_rows": quality_input.invalid_rows,
        "complete_rvol_sessions": quality_input.complete_rvol_sessions,
        "last_daily_session": quality_input.last_daily_session,
        "intraday": {"captured_at_ms": captured_at_ms, "bars": [asdict(f) for f in facts],
                     "invalid_rows": intra_invalid, "receipt": intra_receipt},
        "daily": {"captured_at_ms": daily_receipt["captured_at_ms"], "capture_valid": daily_capture_valid,
                  "invalid_rows": daily_invalid,
                  "required_sessions": sorted(daily_dates), "completed_sessions": sorted(daily_sessions),
                  "missing_sessions": sorted(missing_daily), "metrics_error": daily_error, "receipt": daily_receipt},
        "history": {"captured_at_ms": volume_receipt["captured_at_ms"], "capture_valid": history_capture_valid,
                    "invalid_rows": history_invalid, "complete_rvol_sessions": intra["same_time_rvol_samples"], "receipt": volume_receipt},
    }
    return {
        "symbol": symbol, "status": "OK", "source": "Yahoo Finance public chart",
        "known_at": intra_receipt.get("received_at_utc"),
        "daily_known_at": daily_receipt.get("received_at_utc"),
        "volume_profile_known_at": volume_receipt.get("received_at_utc"),
        "calendar_days_since_previous_session": calendar_gap,
        "monday_weekend_context": current.weekday() == 0 and calendar_gap is not None and calendar_gap >= 3,
        "daily": daily, "intraday": intra, "quality": asdict(quality),
        "quality_policy_id": US_PUBLIC_5M_POLICY.policy_id, "quality_evidence": evidence,
        "benchmark_dependency": benchmark, **state,
    }


def _benchmark_dependency(context, intra, quality):
    """Relative strength alone depends on an independently fresh aligned QQQ."""
    result = {"change_pct": None, "reason_codes": ["BENCHMARK_UNAVAILABLE"]}
    if not isinstance(context, dict):
        return result
    benchmark_quality = context.get("quality") or {}
    expiry = benchmark_quality.get("valid_until_ms")
    if (not benchmark_quality.get("observation_ok") or type(expiry) is not int
            or quality.evaluated_at_ms >= expiry):
        result["reason_codes"] = ["STALE_BENCHMARK"]
    elif (context.get("current_bar_time_utc") != intra.get("current_bar_time_utc")
          or benchmark_quality.get("last_bar_end_ms") != quality.last_bar_end_ms):
        result["reason_codes"] = ["MISALIGNED_BENCHMARK"]
    elif _finite(context.get("change_pct")):
        result = {"change_pct": context["change_pct"], "reason_codes": []}
    result["known_at"] = context.get("known_at")
    result["quality"] = benchmark_quality
    return result


def _utc_now():
    """Wall time belongs only at the live scan's injectable process boundary."""
    return datetime.now(timezone.utc)


def scan_once(symbols=DEFAULT_SYMBOLS, fetcher=fetch, now=None, workers=8, *, clock=None):
    """Scan live with a fresh post-acquisition clock, or replay at explicit now.

    An explicit ``now`` with no ``clock`` fixes every evaluation to that time.
    Live scans default to ``_utc_now``; callers can inject a datetime-returning
    clock (also with a scan-start ``now``) without changing captured source facts.
    Pure metric/quality calculations never sample wall time themselves.
    """
    if now is None:
        clock = clock or _utc_now
        now = clock()
    symbols = tuple(dict.fromkeys(s.upper() for s in symbols))
    market_context = _market_context(fetcher, now, clock=clock)
    qqq_context = market_context.get("QQQ")
    rows = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 12)), thread_name_prefix="qd-us-watch") as pool:
        futures = {pool.submit(_scan_symbol, s, fetcher, now, qqq_context=qqq_context, clock=clock): s for s in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:
                rows.append({"symbol": symbol, "status": "ERROR", "error": f"{type(exc).__name__}: {str(exc)[:240]}"})
    if clock is not None:
        now = clock()
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
        # Publication owns a fresh final clock check. Preserve the actual stock
        # decision and original QQQ evidence; bound all displayed dependencies.
        deadline = r["quality"]["valid_until_ms"]
        benchmark = r["benchmark_dependency"]
        if r.get("relative_change_vs_qqq_pp") is not None:
            benchmark_deadline = (benchmark.get("quality") or {}).get("valid_until_ms")
            deadline = (min(deadline, benchmark_deadline)
                        if type(deadline) is int and type(benchmark_deadline) is int else None)
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
            "quality": r["quality"],
            "quality_policy_id": r["quality_policy_id"],
            "valid_until_ms": deadline,
            "known_at": r["known_at"],
            "captured_at_ms": r["quality_evidence"]["captured_at_ms"],
            "current_bar_time_utc": r["intraday"].get("current_bar_time_utc"),
            "benchmark_dependency": benchmark,
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
    symbols = tuple(dict.fromkeys(s.upper() for s in symbols))
    started = time.monotonic()
    while True:
        report = scan_once(symbols=symbols, fetcher=fetcher)
        if once:
            ok_symbols = {r.get("symbol") for r in report.get("rows", []) if r.get("status") == "OK"}
            report["incomplete_symbols"] = sorted(set(symbols) - ok_symbols)
            report["scan_status"] = "INCOMPLETE" if not symbols or report["incomplete_symbols"] else "COMPLETE"
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
            # Preserve partial observations and publication before failing the
            # hosted workflow. Process completion alone is not scan health.
            if report["scan_status"] != "COMPLETE":
                detail = ", ".join(report["incomplete_symbols"]) or "no requested symbols"
                raise RuntimeError(f"one-shot scan incomplete: {detail}")
            return report
        if duration_minutes and time.monotonic() - started >= duration_minutes * 60:
            return report
        time.sleep(int(poll_seconds))
