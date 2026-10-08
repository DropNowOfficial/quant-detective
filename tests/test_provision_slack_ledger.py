"""Offline provisioning contracts: one exact bot-owned Issue, never Slack I/O."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from market_data import slack_alerts as sa
from scripts import provision_slack_ledger as p

REPO = "DropNowOfficial/quant-detective"
ROOT = f"/repos/{REPO}/issues"
TITLE = "Quant Detective Slack delivery ledger"
LABEL = "qd-slack-ledger"
BOT = {"id": 41898282, "login": "github-actions[bot]", "type": "Bot"}
TOKEN = "secret-fixture-token-never-log"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def issue(number=7, **changes):
    return {"id": number + 1000, "number": number, "title": TITLE,
            "body": sa.UNINITIALIZED_MARKER, "user": deepcopy(BOT),
            "state": "open", "labels": [],
            "url": f"{sa.API}{ROOT}/{number}",
            "repository_url": f"{sa.API}/repos/{REPO}",
            "html_url": f"https://github.com/{REPO}/issues/{number}", **changes}


def initialized(**changes):
    manifest = sa.Manifest(1, REPO, sa.CHANNEL_ID, NOW, frozenset({123}), NOW, 0, None)
    return sa.MANIFEST_MARKER + "\n" + sa._json({
        "revision": 3, "operation_id": "existing-operation",
        "manifest": sa._encode(asdict(manifest)) | changes})


class API:
    """Stateful local REST fixture; mutations survive lost responses and new clients."""
    def __init__(self, items=()):
        self.items = {item["number"]: deepcopy(item) for item in items}
        self.calls = []
        self.before = None
        self.after_create = None
        self.comments = []
        self.page_size = 100
        self.links = {}

    def request(self, method, url, *, headers, body, timeout):
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "api.github.com"
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        assert 0 < timeout <= 5
        data = json.loads(body) if body else None
        call = {"method": method, "path": parsed.path,
                "query": parse_qs(parsed.query), "data": data}
        self.calls.append(call)
        if self.before:
            answer = self.before(call)
            if answer is not None:
                return answer
        response_headers = {}
        if method == "GET" and parsed.path == ROOT:
            assert call["query"]["state"] == ["all"]
            assert call["query"]["sort"] == ["created"]
            assert call["query"]["direction"] == ["asc"]
            assert call["query"]["per_page"] == ["100"]
            page = int(call["query"]["page"][0])
            values = list(self.items.values())
            start = (page - 1) * self.page_size
            result = values[start:start + self.page_size]
            if page in self.links:
                response_headers["Link"] = self.links[page]
        elif method == "GET" and parsed.path.endswith("/comments"):
            result = self.comments
        elif method == "GET" and parsed.path.startswith(ROOT + "/"):
            result = self.items.get(int(parsed.path.rsplit("/", 1)[1]))
            if result is None:
                return sa.HttpResponse(404, {}, b"{}")
        elif method == "POST" and parsed.path == ROOT:
            assert data == {"title": TITLE, "body": sa.UNINITIALIZED_MARKER}
            number = max(self.items, default=0) + 1
            result = issue(number)
            self.items[number] = deepcopy(result)
            if self.after_create:
                answer = self.after_create(result)
                if answer is not None:
                    return answer
        else:
            raise AssertionError(f"Unexpected mutation or endpoint: {method} {parsed.path}")
        return sa.HttpResponse(201 if method == "POST" else 200, response_headers,
                               json.dumps(result).encode())

    @property
    def posts(self):
        return [c for c in self.calls if c["method"] != "GET"]


def client(api):
    config = sa.Config(REPO, 1, frozenset({BOT["id"]}), sa.CHANNEL_ID)
    return sa.GitHubClient(config, TOKEN, api, sa.MonotonicBudget(sa.SystemClock()))


def next_link(page=2, **query_changes):
    from urllib.parse import urlencode
    query = {"state": "all", "sort": "created", "direction": "asc",
             "per_page": 100, "page": page} | query_changes
    return f'<{sa.API}{ROOT}?{urlencode(query)}>; rel="next"'


def test_create_exact_initial_issue_and_verify_complete_readback():
    api = API()
    assert p.provision(client(api)) == (1, f"https://github.com/{REPO}/issues/1")
    assert len(api.posts) == 1
    assert api.items[1]["body"] == "<!-- qd-slack-ledger:uninitialized:v1 -->"
    assert [c["path"] for c in api.calls].count(ROOT) == 3  # scan, create, rediscover
    assert api.calls[-1]["path"] == ROOT + "/1"


@pytest.mark.parametrize("state", ["open", "closed"])
@pytest.mark.parametrize("body", [sa.UNINITIALIZED_MARKER, initialized()])
def test_existing_ledger_is_idempotent_even_after_initialization_or_closure(state, body):
    api = API([issue(state=state, body=body)])
    before = deepcopy(api.items)
    for _ in range(2):
        assert p.provision(client(api))[0] == 7
    assert api.items == before
    assert api.posts == []


@pytest.mark.parametrize("updates", [
    {"body": sa.UNINITIALIZED_MARKER + "\n"}, {"body": ""}, {"body": None},
    {"body": "<!-- qd-slack-ledger:v2 -->"}, {"body": initialized(repo="attacker/repo")},
    {"body": initialized(channel_id="other")}, {"body": initialized(version=2)},
    {"body": initialized(shard_count=-1)}, {"body": initialized(baseline_ids=[True])},
    {"user": {"id": 123, "login": "github-actions[bot]", "type": "Bot"}},
    {"user": BOT | {"id": str(BOT["id"])}}, {"user": BOT | {"login": "someone"}},
    {"user": BOT | {"type": "User"}}, {"user": None},
    {"html_url": "https://github.com/other/repo/issues/7"},
    {"repository_url": "https://api.github.com/repos/other/repo"},
    {"url": "https://api.github.com/repos/other/repo/issues/7"},
])
def test_candidate_corruption_or_wrong_identity_blocks_creation(updates):
    api = API([issue(**updates)])
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


@pytest.mark.parametrize("updates", [
    {"title": "Renamed", "labels": [{"name": LABEL}], "body": "corrupt"},
    {"title": "Renamed", "body": "text <!-- qd-slack-ledger:v2 -->"},
])
def test_label_or_body_marker_prevents_duplicate_despite_renamed_title(updates):
    api = API([issue(**updates)])
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_multiple_candidates_fail_closed_even_if_one_is_human_or_closed():
    api = API([issue(), issue(9, state="closed", user={"id": 999})])
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_pull_requests_are_excluded_from_issue_discovery():
    api = API([issue(pull_request={"url": "unused"})])
    assert p.provision(client(api))[0] == 8
    assert len(api.posts) == 1


def test_all_pages_checked_including_closed_match_after_one_hundred():
    api = API([issue(n, title="ordinary", body="ordinary") for n in range(1, 101)]
              + [issue(101, state="closed")])
    assert p.provision(client(api))[0] == 101
    assert [c["query"].get("page") for c in api.calls[:2]] == [["1"], ["2"]]
    assert not api.posts


def test_short_page_with_next_link_is_followed_before_creating():
    api = API([issue(1, title="ordinary", body="ordinary"), issue(7, state="closed")])
    api.page_size = 1
    api.links[1] = next_link()
    assert p.provision(client(api))[0] == 7
    assert not api.posts


@pytest.mark.parametrize("link", [next_link().replace("api.github.com", "evil.example"),
    next_link(state="open"), next_link(page=3), next_link() + ", " + next_link(),
    next_link().replace('rel="next"', 'rel=next'), "malformed"])
def test_invalid_pagination_never_authorizes_create(link):
    api = API([issue(1, title="ordinary", body="ordinary")])
    api.links[1] = link
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_duplicate_pages_fail_closed():
    api = API([issue(1, title="ordinary", body="ordinary")])
    api.links[1] = next_link()
    api.before = lambda call: sa.HttpResponse(200, {}, json.dumps([api.items[1]]).encode()) \
        if call["query"].get("page") == ["2"] else None
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


@pytest.mark.parametrize("response", [sa.HttpResponse(403, {}, TOKEN.encode()),
    sa.HttpResponse(500, {}, b"{}"), sa.HttpResponse(200, {}, b"not-json"),
    sa.HttpResponse(200, {}, b"{}")])
def test_unavailable_or_incomplete_initial_scan_cannot_create(response):
    api = API()
    api.before = lambda call: response
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


@pytest.mark.parametrize("response", [sa.HttpResponse(500, {}, b"failure"),
    sa.HttpResponse(201, {}, b"not-json"), TimeoutError(TOKEN)])
def test_uncertain_post_is_never_retried_and_can_be_recovered_by_rediscovery(response):
    api = API()
    def after_create(item):
        if isinstance(response, Exception):
            raise response
        return response
    api.after_create = after_create
    assert p.provision(client(api))[0] == 1
    assert len(api.posts) == 1
    assert p.provision(client(api))[0] == 1
    assert len(api.posts) == 1


def test_uncertain_post_without_visible_issue_stops_without_second_post():
    api = API()
    api.before = lambda call: sa.HttpResponse(500, {}, TOKEN.encode()) if call["method"] == "POST" else None
    with pytest.raises(sa.LedgerUnavailable, match="unverified"):
        p.provision(client(api))
    assert len(api.posts) == 1
    assert api.calls[-1]["method"] == "GET"


def test_post_readback_wrong_author_or_multiple_candidates_never_succeeds():
    for fault in ("author", "duplicates", "readback"):
        api = API()
        def corrupt(item):
            if fault == "author":
                api.items[item["number"]]["user"] = {"id": 999}
            elif fault == "duplicates":
                api.items[9] = issue(9)
            else:
                api.before = lambda call: sa.HttpResponse(404, {}, b"{}") \
                    if call["path"] == ROOT + "/1" else None
        api.after_create = corrupt
        with pytest.raises(sa.LedgerUnavailable):
            p.provision(client(api))
        assert len(api.posts) == 1


def test_configured_issue_is_verified_even_if_all_discovery_markers_were_removed():
    api = API([issue(title="renamed", body="corrupt")])
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api), configured_issue=7)
    assert not api.posts


def test_configured_number_cannot_adopt_a_different_or_missing_ledger():
    for configured in (7, 42):
        api = API([issue(9)])
        with pytest.raises(sa.LedgerUnavailable):
            p.provision(client(api), configured_issue=configured)
        assert not api.posts


def test_workflow_rerun_can_only_rediscover_not_create():
    api = API()
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api), allow_create=False)
    assert not api.posts
    api.items[7] = issue()
    assert p.provision(client(api), allow_create=False)[0] == 7


def trusted_env(monkeypatch):
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPO,
        "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/provision-slack-ledger.yml@refs/heads/main",
        "GITHUB_RUN_ATTEMPT": "1", "QD_SLACK_PROVISION_CONFIRMED": "true",
        "GITHUB_TOKEN": TOKEN, "QD_SLACK_LEDGER_ISSUE": ""}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.mark.parametrize("key,value", [
    ("GITHUB_REPOSITORY", "untrusted/fork"), ("GITHUB_REF", "refs/heads/feature"),
    ("GITHUB_EVENT_NAME", "push"), ("GITHUB_EVENT_NAME", "schedule"),
    ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_ACTIONS", "false"),
    ("GITHUB_WORKFLOW_REF", "other"), ("QD_SLACK_PROVISION_CONFIRMED", "false"),
    ("QD_SLACK_PROVISION_CONFIRMED", "True"), ("GITHUB_TOKEN", ""),
    ("QD_SLACK_LEDGER_ISSUE", "42\nmalicious"), ("GITHUB_RUN_ATTEMPT", "invalid"),
])
def test_context_and_input_guards_precede_any_network(monkeypatch, capsys, key, value):
    trusted_env(monkeypatch)
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: pytest.fail("guard reached network"))
    assert p.main() == 1
    assert TOKEN not in capsys.readouterr().out


def test_main_emits_only_verified_number_and_link_without_changing_enablement(monkeypatch, tmp_path, capsys):
    trusted_env(monkeypatch)
    monkeypatch.setenv("QD_SLACK_ENABLED", "false")
    monkeypatch.setenv("QD_SLACK_PRODUCER_IDS", "999")  # cannot expand trusted writers
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    api = API()
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: api)
    assert p.main() == 0
    link = f"https://github.com/{REPO}/issues/1"
    assert (tmp_path / "output").read_text() == f"ledger_issue=1\nledger_url={link}\n"
    assert link in (tmp_path / "summary").read_text()
    assert "ready" not in (tmp_path / "summary").read_text().lower()
    assert TOKEN not in capsys.readouterr().out
    import os
    assert os.environ["QD_SLACK_ENABLED"] == "false"
    assert len(api.posts) == 1


def test_failures_are_sanitized_and_never_export_an_issue(monkeypatch, tmp_path, capsys):
    trusted_env(monkeypatch)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    api = API()
    def fail(call):
        raise RuntimeError(TOKEN)
    api.before = fail
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: api)
    assert p.main() == 1
    assert TOKEN not in capsys.readouterr().out
    assert not (tmp_path / "output").exists()
    assert not api.posts


def test_workflow_is_manual_opt_in_serialized_and_minimum_permission():
    text = Path(".github/workflows/provision-slack-ledger.yml").read_text()
    assert "  workflow_dispatch:" in text
    assert "  push:" not in text and "  schedule:" not in text and "  pull_request:" not in text
    assert "        type: boolean\n        required: true\n        default: false" in text
    assert "github.repository == 'DropNowOfficial/quant-detective'" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "inputs.confirm_provision == true" in text
    assert "permissions:\n  contents: read\n  issues: write\n" in text
    assert "concurrency:\n  group: hosted-core-watch-fallback\n  cancel-in-progress: false" in text
    assert "persist-credentials: false" in text
    assert text.count("secrets.GITHUB_TOKEN") == 1
    assert "QD_SLACK_PROVISION_CONFIRMED: ${{ inputs.confirm_provision }}" in text
    assert "QD_SLACK_WEBHOOK_URL" not in text and "QD_SLACK_ENABLED" not in text
    assert "run: uv run python -m scripts.provision_slack_ledger" in text


def test_untrusted_producer_configuration_is_rejected_before_lookup():
    api = API()
    config = sa.Config(REPO, 1, frozenset({BOT["id"], 999}), sa.CHANNEL_ID)
    github = sa.GitHubClient(config, TOKEN, api, sa.MonotonicBudget(sa.SystemClock()))
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(github)
    assert api.calls == []


@pytest.mark.parametrize("configured", [True, 0, -1, "7"])
def test_invalid_configured_identity_precedes_lookup(configured):
    api = API()
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api), configured_issue=configured)
    assert api.calls == []


def test_configured_pull_request_cannot_be_adopted():
    api = API([issue(pull_request={"url": "unused"})])
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api), configured_issue=7)
    assert not api.posts


@pytest.mark.parametrize("value", [
    [{"id": 1}], [issue(labels=None)], [issue(labels=["qd-slack-ledger"])],
    [issue(title=None)], [issue(body={})], [issue(number=True)], [issue(id=True)],
    [issue()] * 101,
])
def test_malformed_scan_cannot_hide_a_candidate_and_authorize_creation(value):
    api = API()
    api.before = lambda call: sa.HttpResponse(200, {}, json.dumps(value).encode())
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_advertised_empty_page_is_incomplete():
    api = API([issue(1, title="ordinary", body="ordinary")])
    api.links[1] = next_link()
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_exhausted_budget_blocks_network_and_writes():
    api = API()
    github = client(api)
    github.budget = sa.MonotonicBudget(sa.SystemClock(), seconds=0)
    with pytest.raises(sa.BudgetExpired):
        p.provision(github)
    assert api.calls == []


def test_budget_expiring_after_create_never_causes_retry(monkeypatch, capsys):
    trusted_env(monkeypatch)
    api = API()
    def expired(item):
        raise sa.BudgetExpired()
    api.after_create = expired
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: api)
    assert p.main() == 1
    assert "may have committed" in capsys.readouterr().out
    assert len(api.posts) == 1
    assert p.provision(client(api))[0] == 1
    assert len(api.posts) == 1


def test_cli_rerun_never_creates_missing_issue(monkeypatch):
    trusted_env(monkeypatch)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    api = API()
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: api)
    assert p.main() == 1
    assert not api.posts
    api.items[7] = issue()
    assert p.main() == 0
    assert not api.posts


def test_manifest_validation_never_recovers_or_writes_shards():
    api = API([issue(body=initialized(shard_count=1))])
    api.comments = [{"body": "corrupt or orphaned shard"}]
    assert p.provision(client(api))[0] == 7
    assert not api.posts
    assert all(not call["path"].endswith("/comments") for call in api.calls)
    # This is intentionally manifest identity validation, never readiness.


def test_canonical_numeric_repository_pagination_is_supported():
    api = API([issue(1, title="ordinary", body="ordinary"), issue(7)])
    api.page_size = 1
    api.links[1] = next_link().replace(ROOT, "/repositories/1369484548/issues")
    assert p.provision(client(api))[0] == 7
    assert not api.posts
    assert all(call["path"].startswith(ROOT) for call in api.calls)


@pytest.mark.parametrize("link", [
    next_link().replace('rel="next"', 'rel="last"'),
    next_link(page=1).replace('rel="next"', 'rel="prev"'),
    next_link().replace('rel="next"', 'rel="first"'),
    next_link() + ", " + next_link(page=1).replace('rel="next"', 'rel="last"'),
])
def test_contradictory_pagination_never_authorizes_create(link):
    api = API([issue(1, title="ordinary", body="ordinary"), issue(7)])
    api.page_size = 1
    api.links[1] = link
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_foreign_numeric_repository_pagination_is_rejected_before_create():
    api = API([issue(1, title="ordinary", body="ordinary"), issue(7)])
    api.page_size = 1
    api.links[1] = next_link().replace(ROOT, "/repositories/999/issues")
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_missing_body_field_cannot_hide_renamed_unlabeled_ledger():
    incomplete = issue(title="renamed")
    del incomplete["body"]
    api = API([incomplete])
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


@pytest.mark.parametrize("response", [issue(99), issue(1, id=99999), {}, [],
                                    issue(1, user=BOT | {"id": 999})])
def test_create_response_identity_must_match_authoritative_rediscovery(response):
    api = API()
    api.after_create = lambda item: sa.HttpResponse(201, {}, json.dumps(response).encode())
    with pytest.raises(sa.LedgerUncertain):
        p.provision(client(api))
    assert len(api.posts) == 1
    assert api.calls[-1]["method"] == "GET"


def test_created_issue_must_have_exact_uninitialized_body_on_readback():
    api = API()
    def initialize_out_of_band(item):
        api.items[item["number"]]["body"] = initialized()
    api.after_create = initialize_out_of_band
    with pytest.raises(sa.LedgerUncertain):
        p.provision(client(api))
    assert len(api.posts) == 1


def test_consistent_first_previous_next_last_links_allow_complete_lookup():
    api = API([issue(1, title="ordinary", body="ordinary"),
               issue(2, title="ordinary", body="ordinary"), issue(7)])
    api.page_size = 1
    last = next_link(3).replace('rel="next"', 'rel="last"')
    first = next_link(1).replace('rel="next"', 'rel="first"')
    api.links[1] = next_link(2) + ", " + last
    api.links[2] = next_link(3) + ", " + last + ", " + first + ", " + next_link(1).replace('rel="next"', 'rel="prev"')
    api.links[3] = first + ", " + next_link(2).replace('rel="next"', 'rel="prev"')
    assert p.provision(client(api))[0] == 7
    assert not api.posts


@pytest.mark.parametrize("second_link", [None, next_link(2).replace('rel="next"', 'rel="last"')])
def test_earlier_last_page_promise_cannot_be_silently_dropped(second_link):
    api = API([issue(1, title="ordinary", body="ordinary"),
               issue(2, title="ordinary", body="ordinary"), issue(7)])
    api.page_size = 1
    api.links[1] = next_link(2) + ", " + next_link(3).replace('rel="next"', 'rel="last"')
    if second_link:
        api.links[2] = second_link
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_empty_previously_announced_last_page_cannot_authorize_creation():
    api = API([issue(n, title="ordinary", body="ordinary") for n in range(1, 201)]
              + [issue(201)])
    api.links[1] = next_link(2) + ", " + next_link(3).replace('rel="next"', 'rel="last"')
    api.before = lambda call: sa.HttpResponse(200, {}, b"[]") \
        if call["query"].get("page") == ["3"] else None
    with pytest.raises(sa.LedgerUnavailable):
        p.provision(client(api))
    assert not api.posts


def test_unadvertised_empty_sentinel_after_full_pages_is_legitimate():
    api = API([issue(n, title="ordinary", body="ordinary") for n in range(1, 201)])
    assert p.provision(client(api))[0] == 201
    assert len(api.posts) == 1
