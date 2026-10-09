"""Publish material headless-watch events into one GitHub issue per ET date.

The issue is an observation/notification channel only. It never creates orders.
Event markers make repeated five-minute workflow runs idempotent.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .quote_validity import publication_event, session_name

ET = ZoneInfo("America/New_York")
API = "https://api.github.com"
REPO = "DropNowOfficial/quant-detective"
REPOSITORY_ID = "1369484548"
PRODUCER_ID = 41898282


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


def _trusted_author(item):
    user = item.get("user") if isinstance(item, dict) else None
    return (isinstance(user, dict) and type(user.get("id")) is int
            and user["id"] == PRODUCER_ID and user.get("type") == "Bot")


def _trusted_issue(item, repo):
    if repo != REPO or not _trusted_author(item) or "pull_request" in item:
        return False
    number = item.get("number")
    root = f"{API}/repos/{repo}"
    return (type(number) is int and number > 0
            and item.get("url") == f"{root}/issues/{number}"
            and item.get("html_url") == f"https://github.com/{repo}/issues/{number}"
            and item.get("repository_url", root) == root)


def _trusted_comment(item, repo, issue):
    if repo != REPO or not _trusted_author(item):
        return False
    comment_id = item.get("id")
    number = issue["number"]
    return (type(comment_id) is int and comment_id > 0
            and item.get("url") == f"{API}/repos/{repo}/issues/comments/{comment_id}"
            and item.get("issue_url") == f"{API}/repos/{repo}/issues/{number}"
            and item.get("html_url") == f"https://github.com/{repo}/issues/{number}#issuecomment-{comment_id}")


def _find_daily_issue(repo, token, title):
    query = urlencode({"state": "open", "per_page": 100, "sort": "created", "direction": "desc"})
    items = _pages(f"/repos/{repo}/issues?{query}", token=token)
    matches = [item for item in items if _trusted_issue(item, repo) and item.get("title") == title]
    if len(matches) > 1:
        raise RuntimeError("Ambiguous trusted daily issues")
    return matches[0] if matches else None


def _comments(repo, token, issue):
    return _pages(f"/repos/{repo}/issues/{issue['number']}/comments?per_page=100", token=token)


def _seen_markers(issue, comments):
    # An issue body is mutable descriptive text, never event-delivery evidence.
    # Only the leading producer marker in a correctly scoped comment counts.
    seen = set()
    for comment in comments:
        if not _trusted_comment(comment, REPO, issue):
            continue
        body = comment.get("body")
        first = body.split("\n", 1)[0] if isinstance(body, str) else ""
        if re.fullmatch(r"<!-- qd-event:[^\r\n]+ -->", first):
            seen.add(first)
    return seen


def _heartbeat(comments, repo, issue):
    matches = []
    for comment in comments:
        if not _trusted_comment(comment, repo, issue):
            continue
        body = comment.get("body")
        first = body.split("\n", 1)[0] if isinstance(body, str) else ""
        if first.startswith("<!-- qd-heartbeat"):
            if first != HEARTBEAT_MARK:
                raise RuntimeError("Corrupt trusted heartbeat marker")
            matches.append(comment)
    if len(matches) > 1:
        raise RuntimeError("Ambiguous trusted heartbeat comments")
    return matches[0] if matches else None



def _market_context_semantics(event):
    # Re-evaluation timestamps may advance without changing the rendered facts.
    # Values or eligibility changing after rendering requires a fresh scan.
    return {symbol: (proxy.get("ok"), proxy.get("price"), proxy.get("change_pct"),
                     (proxy.get("quote_validity") or {}).get("eligible"))
            for symbol, proxy in (event.get("market_context") or {}).items()}


def publish(report: dict, *, clock=None) -> dict:
    clock = clock or (lambda: datetime.now(timezone.utc))
    # Keep confirmed responses and uncertain request evidence through cutoff or
    # later exceptions; the caller persists this same report in its finally.
    result = {"published": 0, "source_comments": [], "source_requests": []}
    report["publication_result"] = result
    if report.get("scan_status") in {"SKIPPED", "SESSION_ENDED"}:
        result.update(stopped=True, reason="scan status does not permit publication")
        return result
    alerts = report.get("alerts") or []
    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    if not repo or not token:
        result["reason"] = "GitHub runtime credentials unavailable"
        return result
    if repo != REPO or os.getenv("GITHUB_REPOSITORY_ID") != REPOSITORY_ID:
        raise RuntimeError("Untrusted GitHub repository")
    generated = datetime.fromisoformat(report["generated_at_et"])
    report.setdefault("runtime_session", session_name(generated.timestamp()))

    def active_now():
        now = clock()
        if session_name(now.timestamp()) in {"PRE", "RTH", "POST"}:
            return now
        from .us_watch import _end_session
        _end_session(report, now)
        result.update(stopped=True, reason="session ended before publication completed")
        return None

    if active_now() is None:
        return result
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
        if active_now() is None:
            return result
        owner = repo.split("/", 1)[0]
        issue = _api("POST", f"/repos/{repo}/issues", token=token,
                     body={"title": title, "body": body, "assignees": [owner]})
        if not _trusted_issue(issue, repo) or issue.get("title") != title:
            raise RuntimeError("Untrusted daily issue creation response")
        result.update(issue_number=issue["number"], issue_url=issue["html_url"])
        if active_now() is None:
            return result
    result.update(issue_number=issue["number"], issue_url=issue["html_url"])

    comments = _comments(repo, token, issue)
    heartbeat_body = _heartbeat_markdown(report)
    heartbeat = _heartbeat(comments, repo, issue)
    if active_now() is None:
        return result
    if heartbeat is None:
        created = _api(
            "POST", f"/repos/{repo}/issues/{issue['number']}/comments",
            token=token, body={"body": heartbeat_body},
        )
        if not _trusted_comment(created, repo, issue):
            raise RuntimeError("Untrusted heartbeat creation response")
        comments.append(created)
        result["heartbeat"] = "created"
    else:
        updated = _api(
            "PATCH", f"/repos/{repo}/issues/comments/{heartbeat['id']}",
            token=token, body={"body": heartbeat_body},
        )
        if not _trusted_comment(updated, repo, issue) or updated["id"] != heartbeat["id"]:
            raise RuntimeError("Untrusted heartbeat update response")
        result["heartbeat"] = "updated"
    if active_now() is None:
        return result

    seen = _seen_markers(issue, comments)
    run_id = os.getenv("GITHUB_RUN_ID") or None
    rejections = report.setdefault("publication_rejections", [])
    for event in alerts:
        mark = _event_mark(event["event_key"])
        if mark in seen:
            continue
        evaluated = active_now()
        if evaluated is None:
            return result
        checked, reason = publication_event(event, now=evaluated)
        if checked is None:
            rejections.append({"event_key": event.get("event_key"), "evaluated_at_utc": evaluated.isoformat(), "reason": reason})
            continue
        body = _event_markdown(checked, generated_at_et=report["generated_at_et"], run_id=run_id)
        requested = active_now()
        if requested is None:
            return result
        # Classification/formatting may themselves consume time. Recheck source
        # validity at the actual guarded request boundary, not only loop entry.
        if requested != evaluated:
            checked, reason = publication_event(event, now=requested)
            if checked is None:
                rejections.append({"event_key": event.get("event_key"), "evaluated_at_utc": requested.isoformat(), "reason": reason})
                continue
            body = _event_markdown(checked, generated_at_et=report["generated_at_et"], run_id=run_id)
        requested = active_now()
        if requested is None:
            return result
        # No rendering follows this last check: receipt/bar expiry and state
        # support must still hold at the final request timestamp.
        final_checked, reason = publication_event(event, now=requested)
        if final_checked is None:
            rejections.append({"event_key": event.get("event_key"), "evaluated_at_utc": requested.isoformat(), "reason": reason})
            continue
        if _market_context_semantics(checked) != _market_context_semantics(final_checked):
            rejections.append({"event_key": event.get("event_key"), "evaluated_at_utc": requested.isoformat(),
                               "reason": "MARKET_CONTEXT_CHANGED_DURING_RENDER"})
            continue
        request = {"event_key": event["event_key"], "comment_id": None,
                   "requested_at_utc": requested.astimezone(timezone.utc).isoformat()}
        result["source_requests"].append(request)
        created = _api("POST", f"/repos/{repo}/issues/{issue['number']}/comments", token=token, body={"body": body})
        if (not _trusted_comment(created, repo, issue)
                or not isinstance(created.get("body"), str)
                or created["body"].split("\n", 1)[0] != mark):
            raise RuntimeError("Untrusted event creation response")
        request["comment_id"] = created["id"]
        result["source_comments"].append(created)
        result["published"] += 1
        seen.add(mark)
        if active_now() is None:
            return result
    # A no-alert or all-deduplicated run can also cross the closing boundary.
    active_now()
    return result


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m market_data.github_alerts REPORT.json")
    report = json.loads(open(sys.argv[1], encoding="utf-8").read())
    print(json.dumps(publish(report), ensure_ascii=False))


if __name__ == "__main__":
    main()
