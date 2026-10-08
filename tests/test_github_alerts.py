from copy import deepcopy
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from market_data import github_alerts
from test_quote_validity import bar, event_at, moment


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
    monkeypatch.setenv("GITHUB_REPOSITORY","owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN","token")
    monkeypatch.setenv("GITHUB_EVENT_NAME","schedule")
    monkeypatch.setenv("GITHUB_RUN_ID","12345")
    monkeypatch.setenv("GITHUB_SHA","abcdef1234567890")
    calls=[]
    issue={"number":7,"title":"Market Watch | 2026-10-05 ET","body":"","html_url":"https://example/7"}
    heartbeat={"id":99,"body":"<!-- qd-heartbeat --> old"}

    def fake_api(method,path,*,token,body=None):
        calls.append((method,path,body))
        if path.startswith("/repos/owner/repo/issues?"):
            return [issue]
        if path.startswith("/repos/owner/repo/issues/7/comments?"):
            return [heartbeat]
        if method=="PATCH" and path=="/repos/owner/repo/issues/comments/99":
            return {"id":99,"body":body["body"]}
        raise AssertionError((method,path,body))

    monkeypatch.setattr(github_alerts,"_api",fake_api)
    out=github_alerts.publish(_report())
    assert out["published"]==0
    assert out["heartbeat"]=="updated"
    patches=[c for c in calls if c[0]=="PATCH"]
    assert len(patches)==1
    assert "Last hosted fallback scan" in patches[0][2]["body"]
    assert "Trigger: **schedule**" in patches[0][2]["body"]
    assert "12345" in patches[0][2]["body"]
    assert "abcdef123456" in patches[0][2]["body"]
    assert "https://github.com/owner/repo/actions/runs/12345" in patches[0][2]["body"]


def test_heartbeat_created_when_missing(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY","owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN","token")
    calls=[]
    issue={"number":7,"title":"Market Watch | 2026-10-05 ET","body":"","html_url":"https://example/7"}

    def fake_api(method,path,*,token,body=None):
        calls.append((method,path,body))
        if path.startswith("/repos/owner/repo/issues?"):
            return [issue]
        if path.startswith("/repos/owner/repo/issues/7/comments?"):
            return []
        if method=="POST" and path=="/repos/owner/repo/issues/7/comments":
            return _created_comment(100, body["body"])
        raise AssertionError((method,path,body))

    monkeypatch.setattr(github_alerts,"_api",fake_api)
    out=github_alerts.publish(_report())
    assert out["heartbeat"]=="created"
    posts=[c for c in calls if c[0]=="POST"]
    assert len(posts)==1
    assert github_alerts.HEARTBEAT_MARK in posts[0][2]["body"]


def _runtime(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")


def _issue():
    return {
        "number": 7,
        "title": "Market Watch | 2026-10-05 ET",
        "body": "",
        "html_url": "https://example/7",
    }


def _event(key="2026-10-05|NVDA|ENTRY_ARMED"):
    # A fresh POST price supports an observation, never an RTH confirmation.
    event = event_at("2026-10-05T16:05:00-04:00", rows=[
        bar("2026-10-05T16:05:00-04:00", close=101.25)])
    return {**event, "event_key": key, "symbol": "NVDA"}


def _created_comment(comment_id, body):
    return {
        "id": comment_id,
        "body": body,
        "html_url": f"https://example/7#issuecomment-{comment_id}",
        "created_at": "2026-10-05T20:05:01Z",
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
        if method == "GET" and path.startswith("/repos/owner/repo/issues?"):
            return [_issue()]
        if method == "GET" and path.startswith("/repos/owner/repo/issues/7/comments?"):
            return [{"id": 90, "body": github_alerts._event_mark(existing["event_key"])}]
        if method == "POST" and path == "/repos/owner/repo/issues/7/comments":
            submitted_bodies.append(body["body"])
            created = _created_comment(100 + len(api_created_comments),
                                       body["body"] + "\nServer-confirmed response fixture.")
            created["user"] = {"id": 42}
            expected_api_comments.append(deepcopy(created))
            api_created_comments.append(created)
            return created
        raise AssertionError((method, path, body))

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    result = github_alerts.publish(report, clock=lambda: moment("2026-10-05T16:05:00-04:00"))
    api_created_comment = api_created_comments[1]
    assert result["source_comments"] == [expected_api_comments[1]]
    assert result["source_comments"][0] is api_created_comment
    assert api_created_comment == expected_api_comments[1]
    assert api_created_comment["body"] != submitted_bodies[1]
    assert result["published"] == 1
    assert result["heartbeat"] == "created"
    assert result["issue_number"] == 7
    assert result["issue_url"] == "https://example/7"
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
    comments = [{"id": index + 1, "body": "unrelated"} for index in range(201)]
    comments[100]["body"] = github_alerts.HEARTBEAT_MARK + " old"
    comments[200]["body"] = github_alerts._event_mark(event["event_key"])
    event_posts = []
    heartbeat_posts = []

    def fake_api(method, path, *, token, body=None):
        if method == "GET" and path.startswith("/repos/owner/repo/issues?"):
            return [_issue()]
        if method == "GET" and path.startswith("/repos/owner/repo/issues/7/comments?"):
            start = (_page(path) - 1) * 100
            return comments[start:start + 100]
        if method == "PATCH" and path == "/repos/owner/repo/issues/comments/101":
            return _created_comment(101, body["body"])
        if method == "POST" and path == "/repos/owner/repo/issues/7/comments":
            posts = heartbeat_posts if github_alerts.HEARTBEAT_MARK in body["body"] else event_posts
            posts.append(body["body"])
            return _created_comment(500 + len(event_posts) + len(heartbeat_posts), body["body"])
        raise AssertionError((method, path, body))

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    loaded_comments = github_alerts._comments("owner/repo", "token", _issue())
    result = github_alerts.publish(_report([event]))
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
        if method == "GET" and path.startswith("/repos/owner/repo/issues?"):
            assert parse_qs(urlsplit(path).query)["state"] == ["open"]
            start = (_page(path) - 1) * 100
            return issues[start:start + 100]
        if method == "POST" and path == "/repos/owner/repo/issues":
            issue_posts.append(body)
            return {**_issue(), "number": 999}
        if method == "GET" and "/comments?" in path:
            return [{"id": 99, "body": github_alerts.HEARTBEAT_MARK}]
        if method == "PATCH" and path == "/repos/owner/repo/issues/comments/99":
            return _created_comment(99, body["body"])
        raise AssertionError((method, path, body))

    monkeypatch.setattr(github_alerts, "_api", fake_api)
    result = github_alerts.publish(_report())
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
    assert github_alerts._comments("owner/repo", "token", _issue()) == comments
    assert requested_pages == [["1"], ["2"]]


@pytest.mark.parametrize("failed_collection", ["issues", "comments"])
def test_second_page_failure_prevents_new_event_posts(monkeypatch, failed_collection):
    _runtime(monkeypatch)
    writes = []

    def fake_api(method, path, *, token, body=None):
        if method != "GET":
            writes.append((method, path, body))
            if method == "POST" and path == "/repos/owner/repo/issues":
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
        github_alerts.publish(_report([_event()]))
    assert writes == []


def test_second_event_post_failure_keeps_first_remote_source(monkeypatch):
    _runtime(monkeypatch)
    remote_event_ids = []
    remote_event_bodies = []
    failure = RuntimeError("GitHub API 503: event unavailable")
    raised = False

    def fake_api(method, path, *, token, body=None):
        if method == "GET" and path.startswith("/repos/owner/repo/issues?"):
            return [_issue()]
        if method == "GET" and path.startswith("/repos/owner/repo/issues/7/comments?"):
            return [{"id": 99, "body": github_alerts.HEARTBEAT_MARK}]
        if method == "PATCH" and path == "/repos/owner/repo/issues/comments/99":
            return _created_comment(99, body["body"])
        if method == "POST" and path == "/repos/owner/repo/issues/7/comments":
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
            github_alerts.publish(_report([_event(), _event("2026-10-05|TSM|ENTRY_ARMED")]),
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
    result = github_alerts.publish(_report([_event()]))
    assert result["published"] == 0
    assert result["reason"] == "GitHub runtime credentials unavailable"
    assert result["source_comments"] == []
