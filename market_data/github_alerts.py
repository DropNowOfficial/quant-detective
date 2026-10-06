"""Publish material headless-watch events into one GitHub issue per ET date.

The issue is an observation/notification channel only. It never creates orders.
Event markers make repeated five-minute workflow runs idempotent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .quality_profiles import US_PUBLIC_5M_POLICY

ET = ZoneInfo("America/New_York")
API = "https://api.github.com"


def _api(method, path, *, token, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = Request(API + path, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "quant-detective-market-watch")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc


def _fmt(value, digits=2):
    return "n/a" if value is None else f"{value:.{digits}f}"


def _event_mark(key):
    return f"<!-- qd-event:{key} -->"


HEARTBEAT_MARK = "<!-- qd-heartbeat -->"
ALERT_STATES = {"ENTRY_CONFIRMED", "ENTRY_ARMED", "LEADER_HOT_NO_CHASE", "LEADER_WATCH"}
QUALITY_FIELDS = {"state", "reason_codes", "observation_ok", "confirmation_ok", "evaluated_at_ms",
                  "valid_until_ms", "expected_bar_end_ms", "last_bar_end_ms", "sample_count"}


def _quality_complete(quality):
    if not isinstance(quality, dict) or not QUALITY_FIELDS <= quality.keys():
        return False
    return (isinstance(quality["state"], str)
            and quality["state"] in {"VALID", "OBSERVATION_ONLY", "UNAVAILABLE"}
            and type(quality["observation_ok"]) is bool
            and type(quality["confirmation_ok"]) is bool
            and isinstance(quality["reason_codes"], (list, tuple))
            and all(isinstance(reason, str) for reason in quality["reason_codes"])
            and type(quality["evaluated_at_ms"]) is int and quality["evaluated_at_ms"] >= 0
            and type(quality["valid_until_ms"]) is int
            and quality["valid_until_ms"] > quality["evaluated_at_ms"]
            and type(quality["sample_count"]) is int and quality["sample_count"] >= 0
            and all(value is None or (type(value) is int and value >= 0)
                    for value in (quality["expected_bar_end_ms"], quality["last_bar_end_ms"])))


def _quality_usable(quality, now_ms, *, confirmation=False):
    return (_quality_complete(quality) and quality["observation_ok"] is True
            and quality["state"] in {"VALID", "OBSERVATION_ONLY"}
            and quality["sample_count"] > 0 and quality["last_bar_end_ms"] is not None
            # This publisher consumes completed-bar decisions, not IBKR quote
            # clocks. A1 selects completed bars only at/before evaluation.
            and quality["last_bar_end_ms"] <= quality["evaluated_at_ms"]
            and (quality["expected_bar_end_ms"] is None
                 or quality["expected_bar_end_ms"] <= quality["last_bar_end_ms"])
            and quality["evaluated_at_ms"] <= now_ms < quality["valid_until_ms"]
            and (not confirmation or (quality["state"] == "VALID"
                 and quality["confirmation_ok"] is True and not quality["reason_codes"])))


def _source_capture_ms(observed_at):
    """Read the original receipt, never substitute evaluation/publication time."""
    if not isinstance(observed_at, str):
        return None
    try:
        observed = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
        if observed.utcoffset() is None:
            return None
        return int(observed.timestamp() * 1000)
    except (ValueError, OverflowError, OSError):
        return None


def _capture_consistent(quality, captured_at_ms):
    return (type(captured_at_ms) is int and captured_at_ms >= 0
            and captured_at_ms <= quality["evaluated_at_ms"] + US_PUBLIC_5M_POLICY.future_clock_tolerance_ms
            and quality["last_bar_end_ms"] <= captured_at_ms)


def _confirmation_usable(event, now_ms):
    quality = event.get("quality")
    expiry = event.get("valid_until_ms")
    policy_id = event.get("quality_policy_id")
    if (not isinstance(policy_id, str) or not policy_id.strip()
            or type(expiry) is not int or now_ms >= expiry
            or not _quality_usable(quality, now_ms, confirmation=True)
            or not _capture_consistent(quality, event.get("captured_at_ms"))):
        return False
    if expiry > quality["valid_until_ms"]:
        return False
    # Any populated relative result carries the original QQQ dependency. Its
    # independent observation deadline cannot be extended by the stock receipt.
    if event.get("relative_change_vs_qqq_pp") is not None:
        benchmark = event.get("benchmark_dependency")
        if (not isinstance(benchmark, dict) or benchmark.get("reason_codes") != []
                or not _quality_usable(benchmark.get("quality"), now_ms)
                or not _capture_consistent(benchmark["quality"], _source_capture_ms(benchmark.get("known_at")))):
            return False
        if expiry > benchmark["quality"]["valid_until_ms"]:
            return False
        change = benchmark.get("change_pct")
        if (not isinstance(change, (int, float)) or isinstance(change, bool)
                or not math.isfinite(change)):
            return False
    return True


def _quality_label(event, now_ms):
    quality = event.get("quality")
    if (not event.get("quality_policy_id") or not _quality_complete(quality)
            or type(event.get("valid_until_ms")) is not int):
        return "LEGACY_UNVALIDATED"
    if not _quality_usable(quality, now_ms) or now_ms >= event["valid_until_ms"]:
        return "UNAVAILABLE_OR_EXPIRED_OBSERVATION"
    return quality["state"]


def _time_ms(value):
    if type(value) is not int or value < 0:
        return "n/a"
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return "n/a"


def _heartbeat_markdown(report):
    rows = report.get("rows") or []
    ok_rows = sum(r.get("status") == "OK" for r in rows)
    error_rows = sum(r.get("status") == "ERROR" for r in rows)
    repo = os.getenv("GITHUB_REPOSITORY") or ""
    run_id = os.getenv("GITHUB_RUN_ID") or ""
    event_name = os.getenv("GITHUB_EVENT_NAME") or "unknown"
    sha = os.getenv("GITHUB_SHA") or ""
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}" if repo and run_id else None
    lines = [
        HEARTBEAT_MARK,
        "### Scanner heartbeat",
        "",
        f"- Last hosted fallback scan: **{report.get('generated_at_et')}**",
        f"- Trigger: **{event_name}**",
        f"- GitHub run id: **{run_id or 'n/a'}**",
        f"- Commit: **{sha[:12] if sha else 'n/a'}**",
        "- Scope: **configured core watchlist / public observation fallback**",
        f"- Symbols OK / error: **{ok_rows} / {error_rows}**",
        f"- Material alerts in this scan: **{len(report.get('alerts') or [])}**",
    ]
    if run_url:
        lines.append(f"- Actions run: {run_url}")
    lines += [
        "",
        "_This proves that specific GitHub hosted fallback run executed. It does not prove the VPS daemon is healthy._",
        "_No new material-alert comment does not mean there was no scan or no market opportunity._",
    ]
    return "\n".join(lines)


def _event_markdown(event, *, now_ms):
    quality = event.get("quality") if isinstance(event.get("quality"), dict) else {}
    lines = [
        _event_mark(event["event_key"]),
        f"### {event['symbol']} — {event['state']}",
        "",
        f"- Price: **{_fmt(event.get('current_price'))}**",
        f"- Day change: **{_fmt(event.get('change_pct'))}%**",
        f"- Premarket vs prior close: **{_fmt(event.get('premarket_change_pct'))}%**",
        f"- RTH open gap: **{_fmt(event.get('open_gap_pct'))}%**",
        f"- Distance from completed-day MA5: **{_fmt(event.get('d5_atr'))} ATR**",
        f"- RTH VWAP (volume-weighted HLC3 approximation): **{_fmt(event.get('rth_vwap_approx'))}**",
        f"- Same-time RVOL: **{_fmt(event.get('same_time_rvol'))}** ({event.get('same_time_rvol_samples', 0)} historical sessions)",
        f"- Relative change vs QQQ: **{_fmt(event.get('relative_change_vs_qqq_pp'))} pp**",
        f"- State reason: {event.get('reason')}",
        f"- Input quality: **{_quality_label(event, now_ms)}** ({event.get('quality_policy_id') or 'n/a'})",
        f"- Market bar time: {event.get('current_bar_time_utc') or 'n/a'}; completed watermark: {_time_ms(quality.get('last_bar_end_ms'))}",
        f"- Source observed at: {event.get('known_at') or 'n/a'}; captured at: {_time_ms(event.get('captured_at_ms'))}",
        f"- Quality evaluated at: {_time_ms(quality.get('evaluated_at_ms'))}; publication checked at: {_time_ms(now_ms)}",
        f"- Valid until (exclusive): {_time_ms(event.get('valid_until_ms'))}",
    ]
    if event.get("leader_reasons"):
        lines.append("- Leader trigger: " + "; ".join(event["leader_reasons"]))
    context = event.get("market_context") or {}
    if context:
        nq = context.get("NQ=F") or {}
        es = context.get("ES=F") or {}
        qqq = context.get("QQQ") or {}
        lines.append(f"- Regime: NQ={_fmt(nq.get('change_pct'))}% | ES={_fmt(es.get('change_pct'))}% | QQQ={_fmt(qqq.get('change_pct'))}%")
    news = event.get("news") or []
    if news:
        lines.append("- Recent news context:")
        for item in news[:3]:
            title = str(item.get("title") or "")[:180]
            publisher = item.get("publisher") or "unknown publisher"
            age = _fmt(item.get("age_hours"), 1)
            lines.append(f"  - {title} — {publisher}, {age}h ago")
    lines += ["", "_Observation only. No order was created or drafted._"]
    return "\n".join(lines)


def _find_daily_issue(repo, token, title):
    query = urlencode({"state": "open", "per_page": 100, "sort": "created", "direction": "desc"})
    items = _api("GET", f"/repos/{repo}/issues?{query}", token=token)
    for item in items:
        if item.get("title") == title and "pull_request" not in item:
            return item
    return None


def _comments(repo, token, issue):
    return _api("GET", f"/repos/{repo}/issues/{issue['number']}/comments?per_page=100", token=token)


def _seen_markers(issue, comments):
    text = issue.get("body") or ""
    text += "\n" + "\n".join(str(c.get("body") or "") for c in comments)
    return text


def publish(report, *, now_ms: int | None = None) -> dict:
    # Wall time is sampled only at this process boundary, never from generated_at
    # or a source receipt. Explicit now_ms makes replay deterministic.
    live_clock = now_ms is None
    now_ms = int(time.time() * 1000) if live_clock else now_ms
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a nonnegative integer")
    alerts = report.get("alerts") or []
    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not repo or not token:
        return {"published": 0, "reason": "GitHub runtime credentials unavailable"}

    generated = datetime.fromisoformat(report["generated_at_et"])
    title = "Market Watch | " + generated.astimezone(ET).date().isoformat() + " ET"
    issue = _find_daily_issue(repo, token, title)
    if issue is None:
        body = (
            "Automated Quant Detective headless watch.\n\n"
            "This issue records material **leader** and **entry-state** observations. "
            "Leader detection is intentionally separate from entry eligibility, so an extended stock "
            "can trigger a warning without becoming a buy setup. No trading action is enabled.\n\n"
            "A mutable heartbeat comment records scan execution separately from material alerts. "
            "GitHub fallback health and VPS health are independent.\n"
        )
        owner = repo.split("/", 1)[0]
        issue = _api("POST", f"/repos/{repo}/issues", token=token,
                     body={"title": title, "body": body, "assignees": [owner]})

    comments = _comments(repo, token, issue)
    heartbeat_body = _heartbeat_markdown(report)
    heartbeat = next((c for c in comments if HEARTBEAT_MARK in str(c.get("body") or "")), None)
    if heartbeat is None:
        created = _api(
            "POST",
            f"/repos/{repo}/issues/{issue['number']}/comments",
            token=token,
            body={"body": heartbeat_body},
        )
        comments.append(created)
        heartbeat_action = "created"
    else:
        _api(
            "PATCH",
            f"/repos/{repo}/issues/comments/{heartbeat['id']}",
            token=token,
            body={"body": heartbeat_body},
        )
        heartbeat["body"] = heartbeat_body
        heartbeat_action = "updated"

    seen = _seen_markers(issue, comments)
    published = 0
    for event in alerts:
        if event.get("state") not in ALERT_STATES:
            continue
        # GitHub acquisition/heartbeat work can outlive a generated decision.
        # Live mode samples again immediately before each event; replay remains
        # fixed to the caller's explicit clock and never reads implicit time.
        event_now_ms = int(time.time() * 1000) if live_clock else now_ms
        if event["state"] == "ENTRY_CONFIRMED" and not _confirmation_usable(event, event_now_ms):
            continue
        mark = _event_mark(event["event_key"])
        if mark in seen:
            continue
        body = _event_markdown(event, now_ms=event_now_ms)
        _api("POST", f"/repos/{repo}/issues/{issue['number']}/comments", token=token, body={"body": body})
        seen += "\n" + mark
        published += 1
    return {
        "published": published,
        "heartbeat": heartbeat_action,
        "issue_number": issue["number"],
        "issue_url": issue.get("html_url"),
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m market_data.github_alerts REPORT.json")
    report = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(json.dumps(publish(report), ensure_ascii=False))


if __name__ == "__main__":
    main()
