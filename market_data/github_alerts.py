"""Publish material headless-watch events into one GitHub issue per ET date.

The issue is an observation/notification channel only. It never creates orders.
Event markers make repeated five-minute workflow runs idempotent.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

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


def _heartbeat_markdown(report):
    context = report.get("market_context") or {}
    stale_context = sorted(
        symbol for symbol, row in context.items()
        if isinstance(row, dict) and row.get("stale")
    )
    rows = report.get("rows") or []
    ok_rows = sum(r.get("status") == "OK" for r in rows)
    error_rows = sum(r.get("status") == "ERROR" for r in rows)
    return "\n".join([
        HEARTBEAT_MARK,
        "### Scanner heartbeat",
        "",
        f"- Last hosted fallback scan: **{report.get('generated_at_et')}**",
        f"- Coverage: **{report.get('coverage_scope')}**",
        f"- Market-wide: **{report.get('market_wide')}**",
        f"- Decision mode: **{report.get('decision_mode')}**",
        f"- Symbols OK / error: **{ok_rows} / {error_rows}**",
        f"- Material alerts in this scan: **{len(report.get('alerts') or [])}**",
        f"- Stale market-context feeds: **{', '.join(stale_context) if stale_context else 'none'}**",
        "",
        "_Heartbeat proves this GitHub fallback scan ran. It does not prove the VPS daemon is healthy._",
    ])


def _event_markdown(event):
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
        f"- Relative change vs SPY: **{_fmt(event.get('relative_change_vs_spy_pp'))} pp**",
        f"- Relative change vs QQQ: **{_fmt(event.get('relative_change_vs_qqq_pp'))} pp**",
        f"- State reason: {event.get('reason')}",
    ]
    if event.get("leader_reasons"):
        lines.append("- Current leader trigger: " + "; ".join(event["leader_reasons"]))
    if event.get("event_context"):
        lines.append("- Earlier-session context: " + "; ".join(event["event_context"]))
    context = event.get("market_context") or {}
    if context:
        spy = context.get("SPY") or {}
        qqq = context.get("QQQ") or {}
        iwm = context.get("IWM") or {}
        soxx = context.get("SOXX") or {}
        nq = context.get("NQ=F") or {}
        es = context.get("ES=F") or {}
        rty = context.get("RTY=F") or {}
        lines.append(
            f"- Regime: SPY={_fmt(spy.get('change_pct'))}% | QQQ={_fmt(qqq.get('change_pct'))}% | "
            f"IWM={_fmt(iwm.get('change_pct'))}% | SOXX={_fmt(soxx.get('change_pct'))}% | "
            f"NQ={_fmt(nq.get('change_pct'))}% | ES={_fmt(es.get('change_pct'))}% | RTY={_fmt(rty.get('change_pct'))}%"
        )
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


def publish(report):
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
            "The heartbeat comment is operational status only: no new alert comment does not mean "
            "there was no scan, and a GitHub heartbeat does not prove the VPS daemon is healthy.\n"
        )
        owner = repo.split("/", 1)[0]
        issue = _api("POST", f"/repos/{repo}/issues", token=token,
                     body={"title": title, "body": body, "assignees": [owner]})

    comments = _comments(repo, token, issue)
    heartbeat_body = _heartbeat_markdown(report)
    heartbeat = next(
        (c for c in comments if HEARTBEAT_MARK in str(c.get("body") or "")),
        None,
    )
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
        mark = _event_mark(event["event_key"])
        if mark in seen:
            continue
        body = _event_markdown(event)
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
