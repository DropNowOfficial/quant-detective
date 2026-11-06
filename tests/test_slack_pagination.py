"""Offline regressions for GitHub's repository-ID pagination links."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from market_data import slack_alerts as sa
from test_scan_health import publish, report, runtime
from test_slack_alerts import (
    BASE, LEDGER_ISSUE, NOW, FakeHTTP, delivery_fixture, forward_run,
    initialized, make_entry, make_source, make_store,
)

REPOSITORY_ID = 1369484548  # Independently verified repository metadata.
ID_BASE = f"/repositories/{REPOSITORY_ID}"
COMMENTS = f"/issues/{LEDGER_ISSUE}/comments"
NEXT = f"{sa.API}{ID_BASE}{COMMENTS}?per_page=100&page=2"


def github_links(http, *, id_alias=True, override=None):
    """Emulate real Link headers; the existing fake only paginates by count."""
    original = http.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        parsed = urlsplit(url)
        if method != "GET" or not parsed.path.endswith("/comments"):
            return response
        query = parse_qs(parsed.query)
        page = int(query["page"][0])
        if override is not None and page == 1:
            link = override
        else:
            comments = list(http.server["comments"].values())
            if parsed.path != BASE + "/issues/comments":
                issue_url = sa.API + parsed.path.removesuffix("/comments")
                comments = [c for c in comments if c["issue_url"] == issue_url]
            if "since" in query:
                comments = [c for c in comments if c["updated_at"] > query["since"][0]]
            target_path = parsed.path.replace(BASE, ID_BASE, 1) if id_alias else parsed.path
            def relation(target_page, name):
                target_query = {key: values[0] for key, values in query.items()} | {"page": target_page}
                return f'<{sa.API}{target_path}?{urlencode(target_query)}>; rel="{name}"'

            links = [relation(1, "first"), relation(page - 1, "prev")] if page > 1 else []
            if len(comments) > page * 100:
                links += [relation(page + 1, "next"), relation((len(comments) + 99) // 100, "last")]
            if not links:
                return response
            link = ", ".join(links)
        return replace(response, headers=response.headers | {"Link": link})

    http.request = request


@pytest.mark.parametrize("count", [101, 201])
@pytest.mark.parametrize("id_alias", [False, True])
def test_all_comment_pages_accept_only_the_equivalent_repository_alias(count, id_alias):
    http = FakeHTTP()
    for comment_id in range(1, count + 1):
        http.add_comment(comment_id, "ordinary comment")
    github_links(http, id_alias=id_alias)
    github = make_store(http).github
    assert [c["id"] for c in github.list_issue_comments(LEDGER_ISSUE)] == list(range(1, count + 1))
    assert [c["id"] for c in github.list_repo_comments(NOW.replace(hour=17))] == list(range(1, count + 1))
    assert all(r["path"].startswith(BASE + "/issues/") for r in http.requests)
    assert all(r["method"] == "GET" for r in http.requests)


def test_paginated_ledger_preserves_delivered_unknown_and_stale_pending_snapshots():
    http, store = initialized()
    def source(comment_id):
        return make_source(comment_id, event_key="2026-10-07|NVDA|ENTRY_ARMED")

    entries = [make_entry(source(101), status="delivered", attempts=1,
                          attempt_id="delivered-1", attempted_at=NOW, delivered_at=NOW),
               make_entry(source(102), status="unknown", attempts=1,
                          attempt_id="unknown-1", attempted_at=NOW, reason="unconfirmed")]
    entries += [make_entry(source(i)) for i in range(103, 111)]
    for entry in entries:
        store.put(entry)
    for comment_id in range(2000, 2201):
        http.add_comment(comment_id, "ordinary comment", created_at=NOW.replace(hour=17))
    before = deepcopy(http.server)
    http.requests.clear()
    github_links(http)
    ledger = make_store(http).load()
    assert ledger.entries == {e.source.comment_id: e for e in entries}
    assert http.server == before
    assert all(r["method"] == "GET" for r in http.requests)
    assert any("page=3" in r["url"] for r in http.requests)
    for entry in entries[2:]:
        assert sa.format_payload(entry, NOW.replace(day=8))["text"].startswith(sa.HISTORY_PREFIX)


def test_health_write_crossing_one_hundred_is_confirmed_and_next_read_is_complete():
    clock, http, store = runtime()
    for comment_id in range(1, 101):
        http.add_comment(comment_id, "ordinary comment")
    before = deepcopy(http.server)
    github_links(http)
    result = publish(report("UNAVAILABLE", (), ("NVDA", "TSM")), clock, http, store, outcome="failure")
    assert result["published"] == 1
    assert len(http.server["comments"]) == 101
    assert all(http.server["comments"][i] == value for i, value in before["comments"].items())
    fresh = make_store(http)
    assert fresh.load().entries == {}
    comments = fresh.github.list_issue_comments(LEDGER_ISSUE)
    assert len(comments) == 101 and comments[-1]["id"] == result["source_comment_id"]
    assert publish(report("UNAVAILABLE", (), ("NVDA", "TSM")), clock, http, fresh, outcome="failure") == {
        "published": 0, "reason": "unchanged"}
    assert len([r for r in http.requests if r["method"] == "POST"]) == 1
    assert not http.server["slack_posts"]


@pytest.mark.parametrize("target", [
    NEXT.replace("https:", "http:"),
    NEXT.replace("api.github.com", "evil.example"),
    NEXT.replace("api.github.com", "api.github.com.evil.example"),
    NEXT.replace("api.github.com", "user@api.github.com"),
    NEXT.replace("api.github.com", "api.github.com:443"),
    NEXT.replace("api.github.com", "[broken"),
    NEXT.replace(ID_BASE, "/repositories/1369484549"),
    NEXT.replace(ID_BASE, "/repositories/01369484548"),
    NEXT.replace(ID_BASE, "/repos/other/repository"),
    NEXT.replace(COMMENTS, "/issues/10/comments"),
    NEXT.replace(COMMENTS, "/issues/comments"),
    NEXT.replace(COMMENTS, COMMENTS + "/"),
    NEXT.replace("page=2", "page=1"),
    NEXT.replace("page=2", "page=3"),
    NEXT.replace("page=2", "page=02"),
    NEXT.replace("per_page=100", "per_page=99"),
    NEXT.replace("per_page=100&", ""),
    NEXT + "&page=2",
    NEXT + "&unexpected=1",
    NEXT + "&unexpected=",
    NEXT + "&broken",
    NEXT + "#fragment",
    NEXT + "#",
    NEXT.replace("api.github.com", "api.\tgithub.com"),
    NEXT.replace("?", "?since=2026-10-07&"),
    NEXT.removeprefix(sa.API),
])
@pytest.mark.parametrize("id_alias", [False, True])
def test_invalid_next_target_fails_closed_before_another_request(target, id_alias):
    if not id_alias:
        target = target.replace(ID_BASE, BASE)
    http = FakeHTTP()
    http.add_comment(1, "ordinary comment")
    github_links(http, override=f'<{target}>; rel="next"')
    before = deepcopy(http.server)
    with pytest.raises(sa.LedgerUnavailable) as caught:
        make_store(http).github.list_issue_comments(LEDGER_ISSUE)
    assert caught.value.reason == "pagination_invalid"
    assert caught.value.http_status == 200 and caught.value.page == 1
    assert len(http.requests) == 1 and http.server == before


@pytest.mark.parametrize("link", [
    f'<{NEXT}>; rel="next", <{NEXT}>; rel="next"',
    f'<{NEXT}; rel="next"',
    f'<{NEXT}>; rel=next',
    f'<{NEXT}>; rel="next',
])
def test_ambiguous_or_malformed_next_link_cannot_look_like_the_last_page(link):
    http = FakeHTTP()
    http.add_comment(1, "ordinary comment")
    github_links(http, override=link)
    with pytest.raises(sa.LedgerUnavailable) as caught:
        make_store(http).github.list_issue_comments(LEDGER_ISSUE)
    assert caught.value.reason == "pagination_invalid" and caught.value.page == 1
    assert len(http.requests) == 1


@pytest.mark.parametrize("later_page", [[], [{"id": 1}]])
def test_repository_alias_does_not_allow_empty_or_duplicate_later_page(later_page):
    http = FakeHTTP()
    http.add_comment(1, "ordinary comment")
    github_links(http, override=f'<{NEXT}>; rel="next"')
    original = http.request

    def request(method, url, **kwargs):
        response = original(method, url, **kwargs)
        if parse_qs(urlsplit(url).query).get("page") == ["2"]:
            return replace(response, body=json.dumps(later_page).encode())
        return response

    http.request = request
    with pytest.raises(sa.LedgerUnavailable) as caught:
        make_store(http).github.list_issue_comments(LEDGER_ISSUE)
    assert caught.value.reason == "pagination_invalid" and caught.value.page == 2


def test_initial_ledger_failure_retains_safe_pagination_evidence_without_raw_link():
    secret = "https://secret.invalid/token?private=raw-response"

    def setup(http, store):
        github_links(http, override=f'<{secret}>; rel="next"')

    summary, http = forward_run(delivery_fixture(), setup=setup)
    assert summary.paused_reason == "ledger_unavailable"
    assert summary.discovery_failure == {"stage": "load_ledger", "reason": "pagination_invalid",
                                         "http_status": 200, "page": 1}
    assert secret not in json.dumps(asdict(summary))
    assert not http.server["slack_posts"]
    assert all(r["method"] == "GET" for r in http.requests)
