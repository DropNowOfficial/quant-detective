"""Immutable quote provenance and fail-closed temporal gates, not trading rules."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo
import math

import exchange_calendars as xc

ET = ZoneInfo("America/New_York")
BAR_SECONDS = 300
GRACE_SECONDS = 60
RECEIPT_MAX_SECONDS = 120
CLOCK_SKEW_SECONDS = 5


def utc_time(value):
    """Parse an aware timestamp; missing/naive/malformed evidence stays missing."""
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _bar_time(stamp):
    try:
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            return None
        return datetime.fromtimestamp(stamp, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


@lru_cache(maxsize=8)
def _calendar(year):
    return xc.get_calendar("XNYS", start=f"{year-1}-12-01", end=f"{year+1}-01-31")


def session_bounds(day):
    calendar = _calendar(day.year)
    label = day.isoformat()
    if not calendar.is_session(label):
        return None
    return calendar.session_open(label).to_pydatetime(), calendar.session_close(label).to_pydatetime()


def session_name(stamp):
    instant = _bar_time(stamp)
    if instant is None:
        return "UNKNOWN"
    local = instant.astimezone(ET)
    bounds = session_bounds(local.date())
    if bounds is None:
        return "CLOSED"
    opening, closing = bounds
    if opening <= instant < closing:
        return "RTH"
    if local.hour >= 4 and instant < opening:
        return "PRE"
    if closing <= instant and local.hour < 20:
        return "POST"
    return "OVERNIGHT"


def previous_session_date(now):
    day = now.astimezone(ET).date() - timedelta(days=1)
    return _calendar(day.year).date_to_session(day.isoformat(), direction="previous").date().isoformat()


def quote_evidence(stamp, received_at, now, *, equity=True, daily_date=None, require_daily=False):
    start = _bar_time(stamp)
    receipt = utc_time(received_at)
    end = start + timedelta(seconds=BAR_SECONDS) if start else None
    expiry = end + timedelta(seconds=BAR_SECONDS + GRACE_SECONDS) if end else None
    receipt_expiry = receipt + timedelta(seconds=RECEIPT_MAX_SECONDS) if receipt else None
    reasons = []
    if receipt is None:
        reasons.append("MISSING_OR_INVALID_RECEIPT_TIME")
    elif (now - receipt).total_seconds() > RECEIPT_MAX_SECONDS:
        reasons.append("RECEIPT_EXPIRED")
    elif (receipt - now).total_seconds() > CLOCK_SKEW_SECONDS:
        reasons.append("RECEIPT_IN_FUTURE")
    if start is None:
        reasons.append("MISSING_OR_INVALID_BAR_TIME")
    else:
        if (start - now).total_seconds() > CLOCK_SKEW_SECONDS or (receipt and (start - receipt).total_seconds() > CLOCK_SKEW_SECONDS):
            reasons.append("BAR_IN_FUTURE")
        if now >= expiry:
            reasons.append("BAR_EXPIRED")
        if equity:
            if start.astimezone(ET).date() != now.astimezone(ET).date():
                reasons.append("BAR_SESSION_DATE_MISMATCH")
            if session_name(start.timestamp()) not in {"PRE", "RTH", "POST"}:
                reasons.append("BAR_OUTSIDE_EQUITY_SESSION")
    expected_daily = previous_session_date(now) if require_daily else None
    if require_daily and daily_date != expected_daily:
        reasons.append("DAILY_SESSION_MISMATCH")
    return {
        "version": 1,
        "source_bar_start_utc": start.isoformat() if start else None,
        "source_bar_end_utc": end.isoformat() if end else None,
        "received_at_utc": received_at.isoformat() if isinstance(received_at, datetime) else received_at,
        "evaluated_at_utc": now.isoformat(),
        "source_session": session_name(start.timestamp()) if start else "UNKNOWN",
        "evaluation_session": session_name(now.timestamp()),
        "completed_at_receipt": bool(end and receipt and receipt >= end),
        "bar_expires_at_utc": expiry.isoformat() if expiry else None,
        "receipt_valid_until_utc": receipt_expiry.isoformat() if receipt_expiry else None,
        "daily_session_date": daily_date,
        "expected_daily_session_date": expected_daily,
        "eligible": not reasons,
        "rejection_reasons": reasons,
    }


def confirmation_evidence(completed_rth, received_at, now, *, price_session):
    pair = completed_rth[-2:]
    evidence = [quote_evidence(r["t"], received_at, now) for r in pair]
    reasons = []
    if session_name(now.timestamp()) != "RTH" or price_session != "RTH":
        reasons.append("NOT_CURRENT_RTH")
    if len(pair) != 2:
        reasons.append("NEED_TWO_COMPLETED_RTH_BARS")
    elif pair[1]["t"] - pair[0]["t"] != BAR_SECONDS:
        reasons.append("NONCONSECUTIVE_CONFIRMATION_BARS")
    if evidence:
        # Only the newest bar has a freshness deadline; its predecessor must be
        # completed and consecutive, not independently as young as the newest.
        reasons.extend(evidence[-1]["rejection_reasons"])
        if not all(e["completed_at_receipt"] and e["source_session"] == "RTH" for e in evidence):
            reasons.append("UNCOMPLETED_OR_NON_RTH_CONFIRMATION")
    return {"eligible": not reasons, "rejection_reasons": list(dict.fromkeys(reasons)),
            "evaluated_at_utc": now.isoformat(), "bars": evidence,
            "bar_expires_at_utc": evidence[-1]["bar_expires_at_utc"] if evidence else None}


def revalidate_quote(evidence, now, *, equity=True, require_daily=False):
    evidence = evidence if isinstance(evidence, dict) else {}
    start = utc_time(evidence.get("source_bar_start_utc"))
    checked = quote_evidence(start.timestamp() if start else None, evidence.get("received_at_utc"), now,
                             equity=equity, daily_date=evidence.get("daily_session_date"), require_daily=require_daily)
    required = {"version", "source_bar_start_utc", "source_bar_end_utc", "received_at_utc", "completed_at_receipt"}
    if (not required <= evidence.keys() or evidence.get("version") != 1
            or evidence.get("source_bar_end_utc") != checked["source_bar_end_utc"]
            or evidence.get("completed_at_receipt") is not checked["completed_at_receipt"]):
        checked["rejection_reasons"].append("MISSING_OR_INCONSISTENT_PROVENANCE")
        checked["eligible"] = False
    # A rejected snapshot cannot be rehabilitated just by waiting. In
    # particular, future-dated receipts must be replaced by a new acquisition.
    if evidence.get("eligible") is False:
        prior_reasons = evidence.get("rejection_reasons")
        prior_reasons = prior_reasons if isinstance(prior_reasons, list) else []
        checked["rejection_reasons"].extend(r for r in prior_reasons if isinstance(r, str))
        if not prior_reasons:
            checked["rejection_reasons"].append("ORIGINALLY_INVALID_QUOTE")
        checked["rejection_reasons"] = list(dict.fromkeys(checked["rejection_reasons"]))
        checked["eligible"] = False
    return checked


def refresh_intraday(intra, now):
    out = dict(intra)
    out["quote_validity"] = revalidate_quote(intra.get("quote_validity"), now, require_daily=True)
    proof = intra.get("confirmation_validity") or {}
    malformed = not isinstance(proof, dict)
    proof = proof if isinstance(proof, dict) else {}
    original_bars = proof.get("bars") or []
    if not isinstance(original_bars, list):
        malformed = True
        original_bars = []
    bars = []
    for evidence in original_bars:
        if not isinstance(evidence, dict):
            malformed = True
            continue
        start = utc_time(evidence.get("source_bar_start_utc"))
        check = revalidate_quote(evidence, now)
        if not start or not check["completed_at_receipt"] or "MISSING_OR_INCONSISTENT_PROVENANCE" in check["rejection_reasons"]:
            malformed = True
        else:
            bars.append({"t": start.timestamp()})
    receipt = out["quote_validity"]["received_at_utc"]
    confirmation = confirmation_evidence(bars, receipt, now, price_session=out["quote_validity"]["source_session"])
    if malformed or any(b.get("received_at_utc") != receipt for b in original_bars if isinstance(b, dict)):
        confirmation["eligible"] = False
        confirmation["rejection_reasons"].append("MISSING_OR_INCONSISTENT_PROVENANCE")
    out["confirmation_validity"] = confirmation
    return out


def refresh_market_context(context, now):
    out = {}
    for symbol, value in context.items():
        proxy = dict(value)
        evidence = revalidate_quote(proxy.get("quote_validity"), now, equity=(symbol == "QQQ"))
        proxy["quote_validity"] = evidence
        proxy["ok"] = bool(proxy.get("ok") and evidence["eligible"])
        if not proxy["ok"]:
            proxy.setdefault("observed_price", proxy.get("price"))
            proxy.setdefault("observed_change_pct", proxy.get("change_pct"))
            proxy["price"] = proxy["change_pct"] = None
        out[symbol] = proxy
    return out


def publication_event(event, now=None):
    """Recheck immediately before each POST; never turn expired evidence live."""
    from .us_watch import classify

    now = now or datetime.now(timezone.utc)
    if session_name(now.timestamp()) not in {"PRE", "RTH", "POST"}:
        return None, "OUTSIDE_SUPPORTED_RUNTIME_SESSION"
    inputs = event.get("classification_inputs") or {}
    if not isinstance(inputs, dict):
        return None, "MISSING_CLASSIFICATION_INPUTS"
    if not isinstance(inputs.get("daily"), dict) or not isinstance(inputs.get("intraday"), dict):
        return None, "MISSING_CLASSIFICATION_INPUTS"
    intra = dict(inputs["intraday"])
    intra["quote_validity"] = event.get("quote_validity")
    intra["confirmation_validity"] = event.get("confirmation_validity")
    intra = refresh_intraday(intra, now)
    if not intra["quote_validity"]["eligible"]:
        return None, "; ".join(intra["quote_validity"]["rejection_reasons"])
    context = refresh_market_context(event.get("market_context") or {}, now)
    qqq = context.get("QQQ") or {}
    state = classify(inputs["daily"], intra, qqq_change=qqq.get("change_pct") if qqq.get("ok") else None)
    if event.get("state") == "ENTRY_CONFIRMED" and not intra["confirmation_validity"]["eligible"]:
        return None, "; ".join(intra["confirmation_validity"]["rejection_reasons"])
    if state["state"] != event.get("state"):
        return None, "STATE_NO_LONGER_SUPPORTED"
    return {**event, **state, "quote_validity": intra["quote_validity"],
            "confirmation_validity": intra["confirmation_validity"], "market_context": context,
            "classification_inputs": {"daily": inputs["daily"], "intraday": intra}}, None
