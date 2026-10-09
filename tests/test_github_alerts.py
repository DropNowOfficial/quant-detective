from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from market_data import github_alerts
from test_quote_validity import bar, event_at, moment



def _publish(report, **kwargs):
    kwargs.setdefault("clock", lambda: moment("2026-10-05T16:05:00-04:00"))
    return github_alerts.publish(report, **kwargs)

def _report(alerts=None):
    return {
        "generated_at_et":"2026-10-05T16:05:00-04:00",
        "rows":[
            {"symbol":"NVDA","status":"OK"},
            {"symbol":"TSM","status":"OK"},
        ],
        "alerts":list(alerts or []),
    }


def test_heartbeat_updates_even_without_material_alerts(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY","DropNowOfficial/quant-detective")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1369484548")
    monkeypatch.setenv("GITHUB_TOKEN","token")
    monkeypatch.setenv("GITHUB_EVENT_NAME","schedule")
    monkeypatch.setenv("GITHUB_RUN_ID","12345")
    monkeypatch.setenv("GITHUB_SHA","abcdef1234567890")
    calls=[]
    issue = _issue()
    heartbeat = _created_comment(99, github_alerts.HEARTBEAT_MARK + "\nold")

    def fake_api(method,path,*,token,body=None):
        calls.append((method,path,body))
        if path.startswith("/repos/DropNowOfficial/quant-detective/issues?"):
            return [issue]
        if path.startswith("/repos/DropNowOfficial/quant-detective/issues/7/comments?"):
            return [heartbeat]
        if method=="PATCH" and path=="/repos/DropNowOfficial/quant-detective/issues/comments/99":
            return _created_comment(99, body["body"])
        raise AssertionError((method,path,body))

    monkeypatch.setattr(github_alerts,"_api",fake_api)
    out=_publish(_report())
    assert out["published"]==0
    assert out["heartbeat"]=="updated"
    patches=[c for c in calls if c[0]=="PATCH"]
    assert len(patches)==1
    assert "Last hosted fallback scan" in patches[0][2]["body"]
    assert "Trigger: **schedule**" in patches[0][2]["body"]
    assert "12345" in patches[0][2]["body"]
    assert "abcdef123456" in patches[0][2]["body"]
    assert "https://github.com/DropNowOfficial/quant-detective/actions/runs/12345" in patches[0][2]["body"]


def test_heartbeat_created_when_missing(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY","DropNowOfficial/quant-detective")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1369484548")
    monkeypatch.setenv("GITHUB_TOKEN","token")
    calls=[]
    issue = _issue()

    def fake_api(method,path,*,token,body=None):
        calls.append((method,path,body))
        if path.startswith("/repos/DropNowOfficial/quant-detective/issues?"):
            return [issue]
        if path.startswith("/repos/DropNowOfficial/quant-detective/issues/7/comments?"):
            return []
        if method=="POST" and path=="/repos/DropNowOfficial/quant-detective/issues/7/comments":
            return _created_comment(100, body["body"])
        raise AssertionError((method,path,body))

    monkeypatch.setattr(github_alerts,"_api",fake_api)
    out=_publish(_report())
    assert out["heartbeat"]=="created"
    posts=[c for c in calls if c[0]=="POST"]
    assert len(posts)==1
    assert github_alerts.HEARTBEAT_MARK in posts[0][2]["body"]


def _runtime(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "DropNowOfficial/quant-detective")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1369484548")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")


def _issue():
    return {
        "number": 7,
        "url": "https://api.github.com/repos/DropNowOfficial/quant-detective/issues/7",
        "repository_url": "https://api.github.com/repos/DropNowOfficial/quant-detective",
        "user": {"id": 41898282, "type": "Bot", "login": "github-actions[bot]"},
        "title": "Market Watch | 2026-10-05 ET",
        "body": "",
        "html_url": "https://github.com/DropNowOfficial/quant-detective/issues/7",
    }


def _event(key="2026-10-05|NVDA|ENTRY_ARMED"):
    # A fresh POST price supports an observation, never an RTH confirmation.
    event = event_at("2026-10-05T16:05:00-04:00", rows=[
        bar("2026-10-05T16:05:00-04:00", close=101.25)])
    return {**event, "event_key": key, "symbol": "NVDA"}


def _created_comment(comment_id, body):
    return {
        "id": comment_id,
        "user": {"id": 41898282, "type": "Bot", "login": "github-actions[bot]"},
        "url": f"https://api.github.com/repos/DropNowOfficial/quant-detective/issues/comments/{comment_id}",
        "issue_url": "https://api.github.com/repos/DropNowOfficial/quant-detective/issues/7",
        "body": body,
        "html_url": f"https://github.com/DropNowOfficial/quant-detective/issues/7#issuecomment-{comment_id}",
        "created_at": "2026-10-05T20:05:01Z",
        "updated_at": "2026-10-05T20:05:01Z",
    }


def _page(path):
    query = parse_qs(urlsplit(path).query)
    return int(query.get("page", ["1"])[0])


def test_event_return_contains_only_confirmed_source_comments(monkeypatch):
    _runtime(monkeypatch)
    event = _event()
    existing = _event("2026-10-05|TSM|LEADER_WATCH")
    report = _report([existing, event, event])
    api_created_comments = []
    expected_api_comments = []
    submitted_bodies = []

    def fake_api(method, path, *, token, body=None):
        if method == "GET" and path.startswith("/repos/DropNowOfficial/quant-detective/issues?"):
            return [_issue()]
        if method == "GET" and path.startswith("/repos/DropNowOfficial/quant-detective/issues/7/comments?"):
            return [_created_comment(90, github_alerts._event_mark(existing["event_key"]))]
        if method == "POST" and path == "/repos/DropNowOfficial/quant-detective/issues/7/comments":
            submitted_bodies.append(body["body"])
            created = _created_comment(100 + len(api_created_comments),
                                       body["body"] + "\nServer-confirmed response fixture.")
            created["user"] = {"id": 41898282, "type": "Bot"}
            expected_api_comments.append(deepcopy(created))
            api_created_comments.append(created)
            return created
        raise AssertionError((method, path, body))

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    result = _publish(report, clock=lambda: moment("2026-10-05T16:05:00-04:00"))
    api_created_comment = api_created_comments[1]
    assert result["source_comments"] == [expected_api_comments[1]]
    assert result["source_comments"][0] is api_created_comment
    assert api_created_comment == expected_api_comments[1]
    assert api_created_comment["body"] != submitted_bodies[1]
    assert result["published"] == 1
    assert result["heartbeat"] == "created"
    assert result["issue_number"] == 7
    assert result["issue_url"] == "https://github.com/DropNowOfficial/quant-detective/issues/7"
    assert len(api_created_comments) == 2
    # Submitted source metadata is separate from retained server provenance.
    submitted_event_body = submitted_bodies[1]
    lines = submitted_event_body.splitlines()
    assert lines[0] == github_alerts._event_mark(event["event_key"])
    assert lines[1].startswith("<!-- qd-source:")
    assert lines[1].endswith(" -->")
    metadata = json.loads(lines[1][len("<!-- qd-source:"):-len(" -->")])
    assert metadata == {"schema": 1, "generated_at_et": report["generated_at_et"], "run_id": "12345"}
    assert metadata["generated_at_et"] == report["generated_at_et"]
    assert metadata["run_id"] == "12345"
    assert lines[1] == '<!-- qd-source:{"schema":1,"generated_at_et":"2026-10-05T16:05:00-04:00","run_id":"12345"} -->'
    assert "- Price: **101.25**" in submitted_event_body
    assert f"- State reason: {event['reason']}" in submitted_event_body
    assert submitted_event_body.endswith("_Observation only. No order was created or drafted._")


def test_event_source_metadata_defaults_to_null():
    markdown = github_alerts._event_markdown(_event())
    assert markdown.splitlines()[1] == '<!-- qd-source:{"schema":1,"generated_at_et":null,"run_id":null} -->'


def test_all_pages_prevent_duplicate_events_and_heartbeat(monkeypatch):
    _runtime(monkeypatch)
    event = _event()
    comments = [_created_comment(index + 1, "unrelated") for index in range(201)]
    comments[100]["body"] = github_alerts.HEARTBEAT_MARK + "\nold"
    comments[200]["body"] = github_alerts._event_mark(event["event_key"])
    event_posts = []
    heartbeat_posts = []

    def fake_api(method, path, *, token, body=None):
        if method == "GET" and path.startswith("/repos/DropNowOfficial/quant-detective/issues?"):
            return [_issue()]
        if method == "GET" and path.startswith("/repos/DropNowOfficial/quant-detective/issues/7/comments?"):
            start = (_page(path) - 1) * 100
            return comments[start:start + 100]
        if method == "PATCH" and path == "/repos/DropNowOfficial/quant-detective/issues/comments/101":
            return _created_comment(101, body["body"])
        if method == "POST" and path == "/repos/DropNowOfficial/quant-detective/issues/7/comments":
            posts = heartbeat_posts if github_alerts.HEARTBEAT_MARK in body["body"] else event_posts
            posts.append(body["body"])
            return _created_comment(500 + len(event_posts) + len(heartbeat_posts), body["body"])
        raise AssertionError((method, path, body))

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    loaded_comments = github_alerts._comments("DropNowOfficial/quant-detective", "token", _issue())
    result = _publish(_report([event]))
    assert len(loaded_comments) == 201
    assert event_posts == []
    assert heartbeat_posts == []
    assert result["heartbeat"] == "updated"
    assert result["published"] == 0
    assert result["source_comments"] == []


def test_daily_issue_is_found_at_item_101_without_creating_another(monkeypatch):
    _runtime(monkeypatch)
    issues = [{"number": 1000 + index, "title": "Unrelated"} for index in range(100)]
    issues[15] = {**_issue(), "pull_request": {"url": "https://example/pr"}}
    issues.append(_issue())
    issue_posts = []

    def fake_api(method, path, *, token, body=None):
        if method == "GET" and path.startswith("/repos/DropNowOfficial/quant-detective/issues?"):
            assert parse_qs(urlsplit(path).query)["state"] == ["open"]
            start = (_page(path) - 1) * 100
            return issues[start:start + 100]
        if method == "POST" and path == "/repos/DropNowOfficial/quant-detective/issues":
            issue_posts.append(body)
            return {**_issue(), "number": 999}
        if method == "GET" and "/comments?" in path:
            return [_created_comment(99, github_alerts.HEARTBEAT_MARK)]
        if method == "PATCH" and path == "/repos/DropNowOfficial/quant-detective/issues/comments/99":
            return _created_comment(99, body["body"])
        raise AssertionError((method, path, body))

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    result = _publish(_report())
    assert result["issue_number"] == 7
    assert issue_posts == []


def test_exactly_100_comments_reads_explicit_empty_tail_page(monkeypatch):
    comments = [{"id": index, "body": "unrelated"} for index in range(100)]
    requested_pages = []

    def fake_api(method, path, *, token, body=None):
        assert method == "GET"
        assert token == "token"
        query = parse_qs(urlsplit(path).query)
        assert query["per_page"] == ["100"]
        requested_pages.append(query.get("page"))
        return comments if _page(path) == 1 else []

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    assert github_alerts._comments("DropNowOfficial/quant-detective", "token", _issue()) == comments
    assert requested_pages == [["1"], ["2"]]


@pytest.mark.parametrize("failed_collection", ["issues", "comments"])
def test_second_page_failure_prevents_new_event_posts(monkeypatch, failed_collection):
    _runtime(monkeypatch)
    writes = []

    def fake_api(method, path, *, token, body=None):
        if method != "GET":
            writes.append((method, path, body))
            if method == "POST" and path == "/repos/DropNowOfficial/quant-detective/issues":
                return _issue()
            return _created_comment(100, body["body"])
        collection = "comments" if "/comments?" in path else "issues"
        if collection != failed_collection:
            return [_issue()]
        if _page(path) == 2:
            raise RuntimeError("GitHub API 503: second page unavailable")
        return [{"number": index, "title": "Unrelated", "id": index, "body": "unrelated"}
                for index in range(100)]

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    with pytest.raises(RuntimeError, match="second page unavailable"):
        _publish(_report([_event()]))
    assert writes == []


def test_second_event_post_failure_keeps_first_remote_source(monkeypatch):
    _runtime(monkeypatch)
    remote_event_ids = []
    remote_event_bodies = []
    failure = RuntimeError("GitHub API 503: event unavailable")
    raised = False

    def fake_api(method, path, *, token, body=None):
        if method == "GET" and path.startswith("/repos/DropNowOfficial/quant-detective/issues?"):
            return [_issue()]
        if method == "GET" and path.startswith("/repos/DropNowOfficial/quant-detective/issues/7/comments?"):
            return [_created_comment(99, github_alerts.HEARTBEAT_MARK)]
        if method == "PATCH" and path == "/repos/DropNowOfficial/quant-detective/issues/comments/99":
            return _created_comment(99, body["body"])
        if method == "POST" and path == "/repos/DropNowOfficial/quant-detective/issues/7/comments":
            if remote_event_ids:
                raise failure
            created = _created_comment(101, body["body"])
            remote_event_ids.append(created["id"])
            remote_event_bodies.append(created["body"])
            return created
        raise AssertionError((method, path, body))

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    with pytest.raises(RuntimeError) as caught:
        try:
            _publish(_report([_event(), _event("2026-10-05|TSM|ENTRY_ARMED")]),
                                  clock=lambda: moment("2026-10-05T16:05:00-04:00"))
        except RuntimeError:
            raised = True
            raise
    assert caught.value is failure
    assert raised is True
    assert remote_event_ids == [101]
    assert remote_event_bodies[0].startswith("<!-- qd-event:")


def test_missing_credentials_returns_empty_source_comments(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def forbidden_api(*args, **kwargs):
        raise AssertionError("No API request is allowed without credentials")

    monkeypatch.setattr(github_alerts, "_api", forbidden_api)
    result = _publish(_report([_event()]))
    assert result["published"] == 0
    assert result["reason"] == "GitHub runtime credentials unavailable"
    assert result["source_comments"] == []


def _server(monkeypatch, *, issues=None, comments=None, on_request=None, after_write=None):
    """Persist full REST-shaped server objects across publication retries."""
    state = {"issues": [_issue()] if issues is None else issues,
             "comments": list(comments or []), "calls": []}

    def api(method, path, *, token, body=None):
        state["calls"].append((method, path, body))
        if on_request:
            on_request(method, path, body)
        if method == "GET" and "/comments?" in path:
            return deepcopy(state["comments"])
        if method == "GET" and "issues?" in path:
            return deepcopy(state["issues"])
        if method == "POST" and path.endswith("/issues"):
            created = _issue() | {"body": body["body"]}
            state["issues"].append(created)
        elif method == "POST":
            created = _created_comment(1000 + len(state["comments"]), body["body"])
            state["comments"].append(created)
        elif method == "PATCH":
            comment_id = int(path.rsplit("/", 1)[1])
            created = next(c for c in state["comments"] if c["id"] == comment_id)
            created["body"] = body["body"]
        else:
            raise AssertionError((method, path))
        if after_write:
            after_write(method, path, body)
        return deepcopy(created)

    monkeypatch.setattr(github_alerts, "_api", api)
    _runtime(monkeypatch)
    return state


@pytest.mark.parametrize("mutation", [
    {"user": {"id": 999, "type": "Bot", "login": "github-actions[bot]"}},
    {"user": {"id": 41898282, "type": "User", "login": "github-actions[bot]"}},
    {"user": {"id": "41898282", "type": "Bot"}},
    {"user": {"id": True, "type": "Bot"}},
    {"url": "https://api.github.com/repos/other/repo/issues/7"},
    {"repository_url": "https://api.github.com/repos/other/repo"},
    {"html_url": "https://github.com/other/repo/issues/7"},
    {"pull_request": {}},
])
def test_spoofed_daily_issue_is_ignored_before_title_selection(monkeypatch, mutation):
    state = _server(monkeypatch, issues=[_issue() | mutation, _issue()])
    assert github_alerts._find_daily_issue("DropNowOfficial/quant-detective", "token", _issue()["title"]) == _issue()
    assert all(method == "GET" for method, _, _ in state["calls"])


def test_duplicate_trusted_daily_issues_fail_closed_before_any_write(monkeypatch):
    second = _issue() | {"number": 8,
                        "url": "https://api.github.com/repos/DropNowOfficial/quant-detective/issues/8",
                        "html_url": "https://github.com/DropNowOfficial/quant-detective/issues/8"}
    state = _server(monkeypatch, issues=[_issue(), second])
    with pytest.raises(RuntimeError, match="[Aa]mbiguous.*daily"):
        _publish(_report())
    assert all(method == "GET" for method, _, _ in state["calls"])


@pytest.mark.parametrize("mutation", [
    {"user": {"id": 999, "type": "Bot", "login": "github-actions[bot]"}},
    {"user": {"id": 41898282, "type": "User"}},
    {"user": {"id": "41898282", "type": "Bot"}},
    {"issue_url": "https://api.github.com/repos/DropNowOfficial/quant-detective/issues/8"},
    {"issue_url": "https://api.github.com/repos/other/repo/issues/7"},
    {"url": "https://api.github.com/repos/other/repo/issues/comments/90"},
    {"html_url": "https://github.com/other/repo/issues/7#issuecomment-90"},
])
def test_untrusted_event_and_heartbeat_markers_do_not_control_publication(monkeypatch, mutation):
    event = _event()
    fake = _created_comment(90, github_alerts._event_mark(event["event_key"])) | mutation
    heartbeat = _created_comment(91, github_alerts.HEARTBEAT_MARK) | mutation
    state = _server(monkeypatch, comments=[fake, heartbeat])
    result = _publish(_report([event]))
    assert result["published"] == 1
    assert result["heartbeat"] == "created"
    assert not any(method == "PATCH" for method, _, _ in state["calls"])


def test_issue_body_and_embedded_comment_markers_never_suppress_events(monkeypatch):
    event = _event()
    marker = github_alerts._event_mark(event["event_key"])
    _server(monkeypatch, issues=[_issue() | {"body": marker}],
            comments=[_created_comment(90, "Quoted text:\n" + marker)])
    assert _publish(_report([event]))["published"] == 1


def test_trusted_heartbeat_is_selected_over_forgery(monkeypatch):
    fake = _created_comment(90, github_alerts.HEARTBEAT_MARK) | {"user": {"id": 9, "type": "User"}}
    state = _server(monkeypatch, comments=[fake, _created_comment(91, github_alerts.HEARTBEAT_MARK)])
    assert _publish(_report())["heartbeat"] == "updated"
    assert [path for method, path, _ in state["calls"] if method == "PATCH"] == [
        "/repos/DropNowOfficial/quant-detective/issues/comments/91"]


@pytest.mark.parametrize("bodies", [
    [github_alerts.HEARTBEAT_MARK, github_alerts.HEARTBEAT_MARK],
    ["<!-- qd-heartbeat broken -->"],
])
def test_ambiguous_or_corrupt_trusted_heartbeat_fails_closed(monkeypatch, bodies):
    state = _server(monkeypatch, comments=[_created_comment(90 + n, body) for n, body in enumerate(bodies)])
    with pytest.raises(RuntimeError, match="heartbeat"):
        _publish(_report([_event()]))
    assert all(method == "GET" for method, _, _ in state["calls"])


@pytest.mark.parametrize("repo,repo_id", [("other/repo", "1369484548"),
                                          ("DropNowOfficial/quant-detective", "1")])
def test_wrong_repository_runtime_never_reads_or_writes(monkeypatch, repo, repo_id):
    state = _server(monkeypatch)
    monkeypatch.setenv("GITHUB_REPOSITORY", repo)
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", repo_id)
    with pytest.raises(RuntimeError, match="repository"):
        _publish(_report())
    assert state["calls"] == []


@pytest.mark.parametrize("when", ["2026-10-05T20:00:00-04:00", "2026-10-05T03:59:59-04:00",
                                  "2026-10-04T12:00:00-04:00", "2026-07-03T12:00:00-04:00"])
def test_off_session_publication_stops_before_any_api_request(monkeypatch, when):
    state = _server(monkeypatch)
    report = _report([_event()]) | {"runtime_session": "POST"}
    result = _publish(report, clock=lambda: moment(when))
    assert state["calls"] == []
    assert result["published"] == 0 and result["reason"] == "session ended before publication completed"
    assert report["publication_result"] is result
    assert report["scan_status"] == "SESSION_ENDED"
    assert report["alerts"] == []


@pytest.mark.parametrize("boundary", ["issue_lookup", "comments_lookup", "heartbeat_return", "event_return"])
def test_close_guard_preserves_partial_publication_and_prevents_later_writes(monkeypatch, boundary):
    current = [moment("2026-10-05T19:59:58-04:00")]
    calls_at_close = []
    def close(method, path, body):
        matched = ((boundary == "issue_lookup" and method == "GET" and "issues?" in path)
                   or (boundary == "comments_lookup" and method == "GET" and "/comments?" in path)
                   or (boundary == "heartbeat_return" and body and github_alerts.HEARTBEAT_MARK in body["body"])
                   or (boundary == "event_return" and body and "<!-- qd-event:" in body["body"]))
        if matched:
            current[0] = moment("2026-10-05T20:00:00-04:00")
    def on_request(method, path, body):
        if method != "GET" and current[0].hour == 0:
            calls_at_close.append((method, path))
        if method == "GET":
            close(method, path, body)
    event = event_at("2026-10-05T19:59:58-04:00", rows=[bar("2026-10-05T19:55:00-04:00")])
    state = _server(monkeypatch, issues=[] if boundary == "issue_lookup" else None,
                    on_request=on_request, after_write=close)
    report = _report([event, event | {"symbol": "BBB", "event_key": "2026-10-05|BBB|ENTRY_ARMED"}]) | {
        "runtime_session": "POST", "generated_at_et": "2026-10-05T19:59:58-04:00"}
    result = _publish(report, clock=lambda: current[0])
    assert calls_at_close == []
    assert report["scan_status"] == "SESSION_ENDED"
    assert report["alerts"] == []
    assert report["publication_result"] is result
    expected = 1 if boundary == "event_return" else 0
    assert result["published"] == len(result["source_comments"]) == expected
    assert len(result["source_requests"]) == expected
    if expected:
        source = result["source_comments"][0]
        assert result["source_requests"] == [{"event_key": event["event_key"], "comment_id": source["id"],
                                               "requested_at_utc": moment("2026-10-05T19:59:58-04:00").isoformat()}]
    assert result["reason"] == "session ended before publication completed"


def test_failed_later_post_retains_confirmed_evidence_and_retry_deduplicates(monkeypatch):
    fail = [True]
    def on_request(method, path, body):
        if method == "POST" and body and "|TSM|" in body["body"] and fail[0]:
            raise RuntimeError("synthetic later post failed")
    state = _server(monkeypatch, on_request=on_request)
    report = _report([_event(), _event("2026-10-05|TSM|ENTRY_ARMED")])
    with pytest.raises(RuntimeError, match="synthetic later"):
        _publish(report)
    partial = report["publication_result"]
    assert partial["published"] == len(partial["source_comments"]) == 1
    assert partial["source_requests"][0]["comment_id"] == partial["source_comments"][0]["id"]
    assert partial["source_requests"][1]["comment_id"] is None
    fail[0] = False
    result = _publish(_report(report["alerts"]))
    assert result["published"] == 1
    event_bodies = [c["body"] for c in state["comments"] if c["body"].startswith("<!-- qd-event:")]
    assert len(event_bodies) == 2


def test_last_event_guard_runs_after_revalidation_and_formatting(monkeypatch):
    state = _server(monkeypatch)
    current = [moment("2026-10-05T19:59:57-04:00")]
    original = github_alerts._event_markdown
    rendered = []
    def render(*args, **kwargs):
        body = original(*args, **kwargs)
        rendered.append(body)
        current[0] = moment("2026-10-05T19:59:58-04:00" if len(rendered) == 1 else "2026-10-05T20:00:00-04:00")
        return body
    monkeypatch.setattr(github_alerts, "_event_markdown", render)
    event = event_at("2026-10-05T19:59:57-04:00", rows=[bar("2026-10-05T19:55:00-04:00")])
    report = _report([event]) | {"runtime_session": "POST", "generated_at_et": "2026-10-05T19:59:57-04:00"}
    result = _publish(report, clock=lambda: current[0])
    assert result["published"] == 0
    assert result["source_requests"] == []
    assert len(rendered) == 2
    assert report["scan_status"] == "SESSION_ENDED"
    assert not any(method == "POST" and body["body"].startswith("<!-- qd-event:")
                   for method, _, body in state["calls"])


def test_lost_event_post_response_retries_from_trusted_remote_marker(monkeypatch):
    fail = [True]
    def after_write(method, path, body):
        if method == "POST" and body["body"].startswith("<!-- qd-event:") and fail[0]:
            raise RuntimeError("synthetic lost response")
    state = _server(monkeypatch, after_write=after_write)
    report = _report([_event()])
    with pytest.raises(RuntimeError, match="lost response"):
        _publish(report)
    assert report["publication_result"]["published"] == 0
    assert report["publication_result"]["source_requests"][0]["comment_id"] is None
    fail[0] = False
    assert _publish(_report([_event()]))["published"] == 0
    assert len([c for c in state["comments"] if c["body"].startswith("<!-- qd-event:")]) == 1


@pytest.mark.parametrize("status", ["SKIPPED", "SESSION_ENDED"])
def test_terminal_nonpublishing_scan_status_cannot_restart_in_active_window(monkeypatch, status):
    state = _server(monkeypatch)
    report = _report([_event()]) | {"scan_status": status}
    result = _publish(report)
    assert state["calls"] == []
    assert report["scan_status"] == status
    assert result["published"] == 0
    assert result["source_comments"] == result["source_requests"] == []
    assert result["stopped"] is True


@pytest.mark.parametrize("repo_id", [None, "", "01369484548", "1369484549"])
def test_repository_numeric_id_is_required_before_any_api_request(monkeypatch, repo_id):
    state = _server(monkeypatch)
    if repo_id is None:
        monkeypatch.delenv("GITHUB_REPOSITORY_ID", raising=False)
    else:
        monkeypatch.setenv("GITHUB_REPOSITORY_ID", repo_id)
    with pytest.raises(RuntimeError, match="repository"):
        _publish(_report())
    assert state["calls"] == []


@pytest.mark.parametrize("failure,bar_start,final_time", [
    ("RECEIPT_EXPIRED", "19:55:00", "19:57:01"),
    ("BAR_EXPIRED", "19:45:00", "19:56:00"),
    ("STATE_NO_LONGER_SUPPORTED", "19:55:00", "19:56:01"),
])
def test_final_render_cannot_publish_expired_or_changed_evidence_mid_session(
        monkeypatch, failure, bar_start, final_time):
    from market_data import us_watch
    from market_data.quote_validity import quote_evidence
    from test_quote_validity import daily
    state = _server(monkeypatch)
    started = "2026-10-05T19:55:00-04:00"
    current = [moment(started)]
    event = event_at(started, rows=[bar(f"2026-10-05T{bar_start}-04:00")])
    if failure == "STATE_NO_LONGER_SUPPORTED":
        values = daily() | {"ma5_slope_1d": -1}
        event["classification_inputs"]["daily"] = values
        intra = event["classification_inputs"]["intraday"]
        intra.update(change_pct=0.7, premarket_change_pct=0.0, open_gap_pct=0.0, move_from_rth_open_pct=0.0)
        event.update(us_watch.classify(values, intra, qqq_change=0.0))
        event["event_key"] = f"2026-10-05|AAA|{event['state']}"
        event["market_context"] = {"QQQ": {"ok": True, "change_pct": 0.0,
            "quote_validity": quote_evidence(moment("2026-10-05T19:50:00-04:00").timestamp(), "2026-10-05T19:54:00-04:00", current[0])}}
    original = github_alerts._event_markdown
    rendered = []
    def render(*args, **kwargs):
        body = original(*args, **kwargs)
        rendered.append(body)
        current[0] = moment("2026-10-05T19:55:01-04:00" if len(rendered) == 1
                            else f"2026-10-05T{final_time}-04:00")
        return body
    monkeypatch.setattr(github_alerts, "_event_markdown", render)
    report = _report([event]) | {"runtime_session": "POST", "generated_at_et": started}
    result = _publish(report, clock=lambda: current[0])
    assert result["published"] == 0
    assert result["source_requests"] == []
    assert len(rendered) == 2
    assert failure in report["publication_rejections"][0]["reason"]
    assert report["publication_rejections"][0]["evaluated_at_utc"] == current[0].isoformat()
    assert not any(method == "POST" and body["body"].startswith("<!-- qd-event:")
                   for method, _, body in state["calls"])


@pytest.mark.parametrize("proxy_receipt,published", [("19:54:00", 0), ("19:55:00", 1)])
def test_final_gate_preserves_rendered_context_semantics_without_rerender_loop(
        monkeypatch, proxy_receipt, published):
    from market_data.quote_validity import publication_event, quote_evidence
    state = _server(monkeypatch)
    started = "2026-10-05T19:55:00-04:00"
    current = [moment(started)]
    event = event_at(started, rows=[bar(started)])
    assert event["state"] == "ENTRY_ARMED"
    event["market_context"] = {"QQQ": {"ok": True, "price": 100.0, "change_pct": 0.0,
        "quote_validity": quote_evidence(moment("2026-10-05T19:50:00-04:00").timestamp(),
                                         f"2026-10-05T{proxy_receipt}-04:00", current[0])}}
    original = github_alerts._event_markdown
    rendered = []
    def render(*args, **kwargs):
        body = original(*args, **kwargs)
        rendered.append(body)
        current[0] = moment("2026-10-05T19:55:01-04:00" if len(rendered) == 1
                            else "2026-10-05T19:56:01-04:00")
        return body
    monkeypatch.setattr(github_alerts, "_event_markdown", render)
    report = _report([event]) | {"runtime_session": "POST", "generated_at_et": started}
    result = _publish(report, clock=lambda: current[0])
    final_checked, reason = publication_event(event, now=current[0])
    assert reason is None and final_checked["state"] == "ENTRY_ARMED"
    assert final_checked["market_context"]["QQQ"]["ok"] is bool(published)
    assert len(rendered) == 2
    assert "QQQ=0.00%" in rendered[-1]
    assert result["published"] == published
    assert len(result["source_requests"]) == len(result["source_comments"]) == published
    if published:
        assert report["publication_rejections"] == []
        assert result["source_comments"][0]["body"] == rendered[-1]
    else:
        assert report["publication_rejections"] == [{
            "event_key": event["event_key"], "evaluated_at_utc": current[0].isoformat(),
            "reason": "MARKET_CONTEXT_CHANGED_DURING_RENDER"}]
        assert not any(method == "POST" and body["body"].startswith("<!-- qd-event:")
                       for method, _, body in state["calls"])
