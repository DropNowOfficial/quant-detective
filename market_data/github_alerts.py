"""Publish material headless-watch events into one GitHub issue per ET date.

The issue is an observation/notification channel only. It never creates orders.
Event markers make repeated five-minute workflow runs idempotent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .quote_validity import publication_event

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


def _pages(path: str, *, token: str) -> list[dict]:
    parsed = urlsplit(path)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["per_page"] = 100
    items = []
    page = 1
    while True:
        query["page"] = page
        page_path = parsed._replace(query=urlencode(query)).geturl()
        batch = _api("GET", page_path, token=token)
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def _fmt(value, digits=2):
    return "n/a" if value is None else f"{value:.{digits}f}"


def _event_mark(key):
    return f"<!-- qd-event:{key} -->"


HEARTBEAT_MARK = "<!-- qd-heartbeat -->"


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


def _event_markdown(event: dict, *, generated_at_et: str | None = None,
                    run_id: str | None = None) -> str:
    metadata = json.dumps(
        {"schema": 1, "generated_at_et": generated_at_et, "run_id": run_id},
        separators=(",", ":"),
    )
    lines = [
        _event_mark(event["event_key"]),
        f"<!-- qd-source:{metadata} -->",
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
    ]
    evidence = event.get("quote_validity") or {}
    lines += [
        f"- Source bar: {evidence.get('source_bar_start_utc')} to {evidence.get('source_bar_end_utc')} ({evidence.get('source_session')})",
        f"- HTTP receipt: {evidence.get('received_at_utc')}",
        f"- Evaluated: {evidence.get('evaluated_at_utc')}",
        f"- Source bar expires (exclusive): {evidence.get('bar_expires_at_utc')}; receipt valid through: {evidence.get('receipt_valid_until_utc')}",
    ]
    if event.get("leader_reasons"):
        lines.append("- Leader trigger: " + "; ".join(event["leader_reasons"]))
    context = event.get("market_context") or {}
    if context:
        nq = context.get("NQ=F") or {}
        es = context.get("ES=F") or {}
        qqq = context.get("QQQ") or {}
        lines.append(f"- Regime: NQ={_fmt(nq.get('change_pct'))}% | ES={_fmt(es.get('change_pct'))}% | QQQ={_fmt(qqq.get('change_pct'))}%")
        for symbol, proxy in context.items():
            provenance = proxy.get("quote_validity") or {}
            lines.append(f"- {symbol} source bar: {provenance.get('source_bar_start_utc')} to {provenance.get('source_bar_end_utc')}; HTTP receipt: {provenance.get('received_at_utc')}")
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
    items = _pages(f"/repos/{repo}/issues?{query}", token=token)
    for item in items:
        if item.get("title") == title and "pull_request" not in item:
            return item
    return None


def _comments(repo, token, issue):
    return _pages(f"/repos/{repo}/issues/{issue['number']}/comments?per_page=100", token=token)


def _seen_markers(issue, comments):
    text = issue.get("body") or ""
    text += "\n" + "\n".join(str(c.get("body") or "") for c in comments)
    return text


def publish(report: dict, *, clock=None) -> dict:
    clock = clock or (lambda: datetime.now(timezone.utc))
    alerts = report.get("alerts") or []
    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not repo or not token:
        return {"published": 0, "reason": "GitHub runtime credentials unavailable", "source_comments": []}

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
    source_comments = []
    run_id = os.getenv("GITHUB_RUN_ID") or None
    rejections = report.setdefault("publication_rejections", [])
    for event in alerts:
        mark = _event_mark(event["event_key"])
        if mark in seen:
            continue
        evaluated = clock()
        checked, reason = publication_event(event, now=evaluated)
        if checked is None:
            rejections.append({"event_key": event.get("event_key"), "evaluated_at_utc": evaluated.isoformat(), "reason": reason})
            continue
        body = _event_markdown(checked, generated_at_et=report["generated_at_et"], run_id=run_id)
        created = _api("POST", f"/repos/{repo}/issues/{issue['number']}/comments", token=token, body={"body": body})
        source_comments.append(created)
        seen += "\n" + mark
        published += 1
    return {
        "published": published,
        "heartbeat": heartbeat_action,
        "issue_number": issue["number"],
        "issue_url": issue.get("html_url"),
        "source_comments": source_comments,
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m market_data.github_alerts REPORT.json")
    report = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(json.dumps(publish(report), ensure_ascii=False))


if __name__ == "__main__":
    main()
