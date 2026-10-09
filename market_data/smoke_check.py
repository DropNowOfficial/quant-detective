"""CI acceptance of a live watch report; never relax the scanner's data gates.

An off-session SKIPPED report proves the runtime guard performed no acquisition.
Captured closed-session observations still require safe rejection of the last
supported equity session. Neither outcome is a successful live-data scan.
Missing current-date bars that produce ERROR rows remain failures.

Evidence is validated at report generation, with a separate 120-second report
age limit. This checks the captured scan, not present publication eligibility.
Futures must remain fresh; this helper does not model CME closure exceptions.
"""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import json
import math
import os
from pathlib import Path

from .quote_validity import (
    BAR_SECONDS, CLOCK_SKEW_SECONDS, ET, RECEIPT_MAX_SECONDS,
    previous_session_date, quote_evidence, session_bounds, session_name, utc_time,
)

SYMBOLS = ("NVDA", "TSM", "MSFT")
CLOSED_REASONS = {"BAR_EXPIRED", "BAR_SESSION_DATE_MISMATCH"}


def current_time():
    return datetime.now(timezone.utc)


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _object(value, label):
    _require(isinstance(value, dict), f"{label}: missing or malformed object")
    return value


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _no_errors(value, path="report", *, session_ended=False):
    if isinstance(value, dict):
        for key, item in value.items():
            expected_stop = session_ended and key == "error" and item == "RuntimeError: SESSION_ENDED_DURING_SCAN"
            _require(key not in {"error", "errors"} or not item or expected_stop, f"{path}.{key}: {item}")
            _no_errors(item, f"{path}.{key}", session_ended=session_ended)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _no_errors(item, f"{path}[{index}]", session_ended=session_ended)
    elif isinstance(value, float):
        _require(math.isfinite(value), f"{path}: non-finite number")


def _proof(value, generated, label, *, equity=True, require_daily=False):
    evidence = _object(value, f"{label} quote_validity")
    start = utc_time(evidence.get("source_bar_start_utc"))
    expected = quote_evidence(start.timestamp() if start else None,
                              evidence.get("received_at_utc"), generated, equity=equity,
                              daily_date=evidence.get("daily_session_date"), require_daily=require_daily)
    # Recompute every derived field; an asserted status/reason is not proof.
    for key, wanted in expected.items():
        actual = evidence.get(key)
        equal = key in evidence and actual == wanted
        if key.endswith("_utc") and wanted is not None:
            equal = utc_time(actual) is not None and utc_time(actual) == utc_time(wanted)
        if isinstance(wanted, bool):
            equal = actual is wanted
        _require(equal, f"{label}: inconsistent quote provenance {key}: {actual!r}; expected {wanted!r}")
    _require(type(evidence.get("version")) is int, f"{label}: malformed provenance version")
    bad = set(expected["rejection_reasons"]) - CLOSED_REASONS
    _require(not bad, f"{label}: unsafe quote rejection: {', '.join(sorted(bad))}")
    return expected


def _last_supported_close(generated):
    local = generated.astimezone(ET)
    day = local.date()
    if session_bounds(day) is None or local.hour < 20:
        day = datetime.fromisoformat(previous_session_date(generated)).date()
    return datetime.combine(day, time(20), tzinfo=ET)


def _closed_equity(evidence, generated, label):
    reasons = set(evidence["rejection_reasons"])
    _require(evidence["eligible"] is False and reasons and reasons <= CLOSED_REASONS,
             f"{label}: expected expired/date-mismatch rejection is missing")
    start = utc_time(evidence["source_bar_start_utc"])
    close = _last_supported_close(generated)
    _require(close - timedelta(seconds=BAR_SECONDS) <= start < close,
             f"{label}: source is not in final five minutes of latest supported session ending {close.isoformat()}")
    _require(evidence["completed_at_receipt"] is True, f"{label}: closing observation was not completed at receipt")


def _boundary_publications(report, started, generated, wanted):
    publication = report.get("publication_result")
    if publication is None:
        return 0
    publication = _object(publication, "publication_result")
    count, comments, requests = (publication.get(key) for key in ("published", "source_comments", "source_requests"))
    _require(type(count) is int and count >= 0 and isinstance(comments, list) and isinstance(requests, list),
             "malformed boundary publication evidence")
    _require(count == len(comments) == len(requests), "boundary publication counts disagree")
    seen_ids, seen_keys = set(), set()
    for comment, request in zip(comments, requests):
        comment, request = _object(comment, "source comment"), _object(request, "source request")
        stamp = utc_time(request.get("requested_at_utc"))
        _require(stamp is not None and started <= stamp <= generated
                 and session_name(stamp.timestamp()) in {"PRE", "RTH", "POST"},
                 "publication request was outside the scan's supported runtime")
        key = request.get("event_key")
        parts = key.split("|") if isinstance(key, str) else []
        _require(len(parts) == 3 and parts[0] == stamp.astimezone(ET).date().isoformat()
                 and parts[1] in wanted and parts[2] in {"ENTRY_CONFIRMED", "ENTRY_ARMED", "LEADER_HOT_NO_CHASE", "LEADER_WATCH"},
                 "publication event does not match scan scope")
        _require(type(comment.get("id")) is int and comment["id"] > 0
                 and type(request.get("comment_id")) is int and request["comment_id"] == comment["id"],
                 "publication response does not match its request")
        _require(comment["id"] not in seen_ids and key not in seen_keys, "duplicate boundary publication evidence")
        seen_ids.add(comment["id"])
        seen_keys.add(key)
        _require(isinstance(comment.get("body"), str) and comment["body"].splitlines()[:1] == [f"<!-- qd-event:{key} -->"],
                 "publication comment does not contain the expected event marker")
    return count


def validate_report(report, *, symbols=SYMBOLS, scan_exit_code, now=None):
    """Return an explicit CI result label or raise a diagnostic ValueError."""
    report = _object(report, "report")
    _no_errors(report, session_ended=report.get("scan_status") == "SESSION_ENDED")
    generated = utc_time(report.get("generated_at_utc"))
    now = utc_time(now if now is not None else current_time())
    _require(generated is not None and now is not None, "report time missing or malformed")
    _require(-CLOCK_SKEW_SECONDS <= (now - generated).total_seconds() <= RECEIPT_MAX_SECONDS,
             "report time is stale or in the future")
    _require(utc_time(report.get("generated_at_et")) == generated, "report ET/UTC times disagree")
    started = utc_time(report.get("scan_started_at_utc"))
    _require(started is not None and started <= generated, "report scan start missing or in the future")
    _require(report.get("mode") == "PUBLIC_HEADLESS_OBSERVATION" and report.get("trading_enabled") is False,
             "report is not a non-trading public observation")
    wanted = set(symbols)
    requested = report.get("symbols_requested")
    _require(isinstance(requested, list) and len(requested) == len(wanted)
             and all(isinstance(s, str) for s in requested) and set(requested) == wanted,
             "requested symbols do not match smoke scope")
    if report.get("scan_status") == "SESSION_ENDED":
        start_session = session_name(started.timestamp())
        end_session = session_name(generated.timestamp())
        _require(start_session in {"PRE", "RTH", "POST"}, "session-ended scan did not start in a supported session")
        _require(end_session in {"OVERNIGHT", "CLOSED"}, "session-ended scan finished inside the runtime window")
        _require(report.get("runtime_session") == start_session and report.get("ending_session") == end_session,
                 "session-ended labels contradict the calendar")
        _require(report.get("skip_reason") == "SESSION_ENDED_DURING_SCAN", "invalid session-ended reason")
        _require(report.get("real_acquisition_failure") is False, "session-ended report has unverified acquisition health")
        _require(report.get("alerts") == [] and not report.get("publication_rejections")
                 and not report.get("source_comments"), "session-ended scan contains unsupported publication evidence")
        rows = report.get("rows")
        _require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows), "malformed session-ended rows")
        row_symbols = [row.get("symbol") for row in rows]
        _require(all(isinstance(symbol, str) and symbol in wanted for symbol in row_symbols)
                 and len(set(row_symbols)) == len(row_symbols), "unexpected session-ended row symbols")
        _require(all(row.get("status") in {"OK", "INVALID_DATA", "ERROR"} for row in rows),
                 "unexpected session-ended row status")
        usable = {row["symbol"] for row in rows if row["status"] == "OK"}
        _require(report.get("incomplete_symbols") == sorted(wanted - usable), "session-ended coverage mismatch")
        _object(report.get("market_context"), "market_context")
        _require(isinstance(report.get("sources"), list), "missing session-ended sources")
        _require(scan_exit_code == 0, f"session-ended scan requires watch exit 0, got {scan_exit_code}")
        published = _boundary_publications(report, started, generated, wanted)
        return "EXPECTED_SESSION_END_WITH_PUBLICATION" if published else "EXPECTED_SESSION_END"
    if report.get("scan_status") == "SKIPPED":
        runtime_session = session_name(started.timestamp())
        _require(runtime_session in {"OVERNIGHT", "CLOSED"}, "skip is inside the active runtime window")
        _require(started == generated, "skip must be generated at its runtime session check")
        _require(report.get("runtime_session") == runtime_session, "skip runtime_session contradicts calendar")
        expected_reason = "NON_TRADING_DAY" if runtime_session == "CLOSED" else "OUTSIDE_SESSION_HOURS"
        _require(report.get("skip_reason") == expected_reason, "skip_reason contradicts calendar")
        for key, empty in (("rows", []), ("alerts", []), ("market_context", {}),
                           ("sources", []), ("incomplete_symbols", [])):
            _require(report.get(key) == empty, f"skip has missing or nonempty {key}")
        _require(not report.get("publication_rejections"), "skip contains publication observations")
        _require(_boundary_publications(report, started, generated, wanted) == 0, "skip contains confirmed publication")
        _require(scan_exit_code == 0, f"skip requires watch exit 0, got {scan_exit_code}")
        return "EXPECTED_SESSION_SKIP"
    rows = report.get("rows")
    _require(isinstance(rows, list) and len(rows) == len(wanted), "missing or duplicate smoke rows")
    row_symbols = [r.get("symbol") for r in rows if isinstance(r, dict)]
    _require(all(isinstance(s, str) for s in row_symbols) and set(row_symbols) == wanted,
             "row symbols do not match smoke scope")
    _require(isinstance(report.get("alerts"), list), "missing or malformed alerts")
    live_window = session_name(generated.timestamp()) in {"PRE", "RTH", "POST"}
    for row in rows:
        symbol = row["symbol"]
        _require(row.get("status") in {"OK", "INVALID_DATA"}, f"{symbol}: unexpected status {row.get('status')}")
        intra = _object(row.get("intraday"), f"{symbol} intraday")
        daily = _object(row.get("daily"), f"{symbol} daily")
        proof = _proof(intra.get("quote_validity"), generated, symbol, require_daily=True)
        _require(utc_time(row.get("known_at")) == utc_time(proof["received_at_utc"]),
                 f"{symbol}: known_at does not match original receipt")
        _require(daily.get("previous_session_date") == proof["daily_session_date"], f"{symbol}: daily provenance mismatch")
        _require(utc_time(intra.get("current_bar_time_utc")) == utc_time(proof["source_bar_start_utc"])
                 and intra.get("current_session") == proof["source_session"], f"{symbol}: source observation mismatch")
        _require(_finite(intra.get("current_price")) and intra["current_price"] > 0, f"{symbol}: invalid observed price")
        _require(row["status"] == ("OK" if proof["eligible"] else "INVALID_DATA"), f"{symbol}: status contradicts quote evidence")
        if proof["eligible"]:
            _require(row.get("state") in {"WATCH", "EXTENDED", "ENTRY_ARMED", "ENTRY_CONFIRMED", "LEADER_WATCH", "LEADER_HOT_NO_CHASE"},
                     f"{symbol}: invalid state for fresh data")
        else:
            _require(not live_window, f"{symbol}: invalid data in live window: {proof['rejection_reasons']}")
            _require(row.get("state") == "DATA_INVALID" and row.get("leader_detected") is False,
                     f"{symbol}: rejected source still has a material state")
            _closed_equity(proof, generated, symbol)

    incomplete = sorted(row["symbol"] for row in rows if row["status"] != "OK")
    _require(report.get("incomplete_symbols") == incomplete, "incomplete_symbols contradict row health")
    expected_status = "UNAVAILABLE" if len(incomplete) == len(rows) else "INCOMPLETE" if incomplete else "COMPLETE"
    _require(report.get("scan_status") == expected_status, "scan_status contradicts row health")
    if incomplete:
        _require(len(incomplete) == len(rows), "mixed fresh and expired observations after close; retry for a coherent smoke result")
        _require(scan_exit_code == 2, f"closed rejection requires watch exit 2, got {scan_exit_code}")
        _require(report["alerts"] == [], "closed-session rejection must not produce alerts")
    else:
        _require(scan_exit_code == 0, f"fresh scan requires watch exit 0, got {scan_exit_code}")

    context = _object(report.get("market_context"), "market_context")
    _require(set(context) == {"NQ=F", "ES=F", "QQQ"}, "missing or unexpected market proxies")
    for symbol, proxy in context.items():
        proxy = _object(proxy, symbol)
        _require(proxy.get("symbol") == symbol, f"{symbol}: mismatched proxy symbol")
        proof = _proof(proxy.get("quote_validity"), generated, symbol, equity=symbol == "QQQ")
        _require(utc_time(proxy.get("known_at")) == utc_time(proof["received_at_utc"])
                 and utc_time(proxy.get("last_bar_utc")) == utc_time(proof["source_bar_start_utc"]),
                 f"{symbol}: proxy source/receipt mismatch")
        _require(proxy.get("ok") is proof["eligible"], f"{symbol}: proxy health contradicts evidence")
        if proof["eligible"]:
            _require(_finite(proxy.get("price")) and proxy["price"] > 0 and _finite(proxy.get("change_pct")),
                     f"{symbol}: invalid live proxy values")
        else:
            _require(not live_window and symbol == "QQQ", f"{symbol}: invalid live proxy data: {proof['rejection_reasons']}")
            _closed_equity(proof, generated, symbol)
            _require(proxy.get("price") is None and proxy.get("change_pct") is None,
                     f"{symbol}: rejected proxy values were not cleared")
    return "EXPECTED_CLOSED_SESSION_REJECTION" if incomplete else "FRESH_DATA_PASS"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("--scan-exit-code", required=True, type=int)
    args = parser.parse_args(argv)
    report = None
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        label = validate_report(report, scan_exit_code=args.scan_exit_code)
        detail = {
            "FRESH_DATA_PASS": "Fresh data passed all smoke symbols at report generation.",
            "EXPECTED_SESSION_SKIP": "Runtime is outside the supported session; no market data was fetched.",
            "EXPECTED_SESSION_END": "The session ended during the scan; retained evidence is diagnostic only and no alerts were published.",
            "EXPECTED_SESSION_END_WITH_PUBLICATION": "The session ended during publication; confirmed alerts requested before the cutoff are retained, and later publication was stopped.",
            "EXPECTED_CLOSED_SESSION_REJECTION": "Expected closed-session rejection verified; this is not a fresh-data scan success.",
        }[label]
        exit_code = 0
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        label, detail, exit_code = "SMOKE_FAIL", f"{type(exc).__name__}: {exc}", 1
    lines = [f"## Live smoke: {label}", "", detail, f"watch exit: {args.scan_exit_code}"]
    if isinstance(report, dict):
        lines.append(f"Generated: {report.get('generated_at_et')} | scan_status: {report.get('scan_status')}")
        rows = report.get("rows")
        for row in rows if isinstance(rows, list) else []:
            # Include exact rejection/error evidence even when validation failed.
            if isinstance(row, dict):
                intra = row.get("intraday")
                proof = intra.get("quote_validity") if isinstance(intra, dict) else None
                lines.append(f"- {row.get('symbol')} {row.get('status')} / {row.get('state')}: "
                             f"error={row.get('error')!r}; quote_validity={json.dumps(proof, ensure_ascii=False)}")
        lines.append("Market context: " + json.dumps(report.get("market_context"), ensure_ascii=False))
    summary = "\n".join(lines) + "\n"
    print(summary, end="")
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as target:
            target.write(summary)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
