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
        f"- State reason: {event.get('reason')}",
    ]
    if event.get("leader_reasons"):
        lines.append("- Leader trigger: " + "; ".join(event["leader_reasons"]))
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


def _seen_markers(repo, token, issue):
    text = issue.get("body") or ""
    comments = _api("GET", f"/repos/{repo}/issues/{issue['number']}/comments?per_page=100", token=token)
    text += "\n" + "\n".join(str(c.get("body") or "") for c in comments)
    return text


def publish(report):
    alerts = report.get("alerts") or []
    if not alerts:
        return {"published": 0, "reason": "no material alerts"}
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
            "can trigger a warning without becoming a buy setup. No trading action is enabled.\n"
        )
        issue = _api("POST", f"/repos/{repo}/issues", token=token, body={"title": title, "body": body})

    seen = _seen_markers(repo, token, issue)
    published = 0
    for event in alerts:
        mark = _event_mark(event["event_key"])
        if mark in seen:
            continue
        body = _event_markdown(event)
        _api("POST", f"/repos/{repo}/issues/{issue['number']}/comments", token=token, body={"body": body})
        seen += "\n" + mark
        published += 1
    return {"published": published, "issue_number": issue["number"], "issue_url": issue.get("html_url")}


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m market_data.github_alerts REPORT.json")
    report = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(json.dumps(publish(report), ensure_ascii=False))


if __name__ == "__main__":
    main()
