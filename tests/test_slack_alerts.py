"""Durable ledger tests. Fake clients share server state, never Python store caches."""
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from market_data import slack_alerts as sa

UTC = timezone.utc
NOW = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
REPO = "DropNowOfficial/quant-detective"
BASE = f"/repos/{REPO}"
LEDGER_ISSUE = 9
AUTHOR = 42
UNINITIALIZED = "<!-- qd-slack-ledger:uninitialized:v1 -->"


class FakeClock:
    def __init__(self, now=NOW):
        self.current = now
        self.elapsed = 0.0

    def now(self):
        return self.current

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds
        self.current += timedelta(seconds=seconds)


class FakeBudget:
    def __init__(self, clock=None, seconds=30):
        self.clock = clock or FakeClock()
        self.deadline = self.clock.monotonic() + seconds

    def remaining(self):
        return max(0, self.deadline - self.clock.monotonic())

    def require(self, seconds=0):
        if self.remaining() <= 0 or self.remaining() < seconds:
            raise sa.BudgetExpired()


class FakeHTTP:
    """An in-memory HTTP server; new transports can reuse its persistent state.

    Faults identify method/path and before/after commit. after_commit can also
    simulate process death (BaseException), not merely a lost network response.
    """
    def __init__(self, server=None):
        self.server = server if server is not None else {
            "issues": {LEDGER_ISSUE: self.issue(LEDGER_ISSUE, UNINITIALIZED)},
            "comments": {}, "next_id": 1000,
        }
        self.requests = []
        self.faults = []
        self.date = NOW
        self.omit_date = False
        self.on_request = None

    @staticmethod
    def issue(number, body, author=AUTHOR):
        return {"number": number, "body": body, "user": {"id": author},
                "html_url": f"https://github.com/{REPO}/issues/{number}"}

    def add_comment(self, comment_id, body, issue_number=LEDGER_ISSUE, author=AUTHOR,
                    created_at=NOW, updated_at=None):
        value = {"id": comment_id, "body": body, "user": {"id": author},
                 "issue_url": f"https://api.github.com{BASE}/issues/{issue_number}",
                 "html_url": f"https://github.com/{REPO}/issues/{issue_number}#issuecomment-{comment_id}",
                 "created_at": created_at.isoformat(),
                 "updated_at": (updated_at or created_at).isoformat()}
        self.server["comments"][comment_id] = value
        self.server["next_id"] = max(self.server["next_id"], comment_id + 1)
        return value

    def fail(self, method, path, *, after=False, error=None):
        self.faults.append((method, path, after, error or sa.TransportFailure("fixture", not after)))

    def request(self, method, url, *, headers, body, timeout):
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "api.github.com"
        assert timeout > 0 and timeout <= 5
        document = json.loads(body) if body else None
        request = {"method": method, "path": parsed.path, "url": url,
                   "document": document, "headers": headers, "timeout": timeout}
        self.requests.append(request)
        if self.on_request:
            self.on_request(request)
        fault = None
        for index, item in enumerate(self.faults):
            if item[:2] == (method, parsed.path):
                fault = self.faults.pop(index)
                break
        if fault and not fault[2]:
            raise fault[3]
        response_headers = {} if self.omit_date else {"Date": format_datetime(self.date, usegmt=True)}
        tail = parsed.path.removeprefix(BASE + "/issues/")
        if method == "GET" and tail.isdigit():
            result = self.server["issues"].get(int(tail))
        elif method == "GET" and tail.startswith("comments/"):
            result = self.server["comments"].get(int(tail.split("/")[-1]))
        elif method == "GET" and (tail == "comments" or tail.endswith("/comments")):
            result = list(self.server["comments"].values())
            query = parse_qs(parsed.query)
            if tail != "comments":
                issue_url = f"https://api.github.com{BASE}/issues/{tail.split('/')[0]}"
                result = [c for c in result if c["issue_url"] == issue_url]
            if "since" in query:
                since = datetime.fromisoformat(query["since"][0])
                result = [c for c in result if datetime.fromisoformat(c["updated_at"]) > since]
            result.sort(key=lambda c: (c["created_at"], c["id"]))
            page = int(query.get("page", [1])[0])
            result = result[(page - 1) * 100:page * 100]
        elif method == "PATCH" and tail.isdigit():
            result = self.server["issues"][int(tail)]
            result.update(document)
        elif method == "PATCH" and tail.startswith("comments/"):
            result = self.server["comments"][int(tail.split("/")[-1])]
            result.update(document)
        elif method == "POST" and tail == f"{LEDGER_ISSUE}/comments":
            result = self.add_comment(self.server["next_id"], document["body"])
        else:
            raise AssertionError((method, url, document))
        if fault:
            raise fault[3]
        status = 404 if result is None else (201 if method == "POST" else 200)
        return sa.HttpResponse(status, response_headers, json.dumps(deepcopy(result)).encode())


def make_config(**changes):
    return sa.Config(**({"repo": REPO, "ledger_issue": LEDGER_ISSUE,
                        "producer_ids": frozenset({AUTHOR}), "channel_id": "C0C7HLTGQ0N"} | changes))


def make_source(comment_id=101, **changes):
    body = changes.get("body", "<!-- qd-event:2026-10-07:NVDA:ENTRY_ARMED -->\n### NVDA — ENTRY_ARMED\n观察 & <@U123>\n")
    values = dict(repo=REPO, comment_id=comment_id, issue_number=7,
                  event_key="2026-10-07:NVDA:ENTRY_ARMED", symbol="NVDA", state="ENTRY_ARMED",
                  created_at=NOW, updated_at=NOW, generated_at_et="2026-10-07T14:00:00-04:00",
                  run_id="12345", html_url=f"https://github.com/{REPO}/issues/7#issuecomment-{comment_id}",
                  body=body, body_sha256=hashlib.sha256((body or "").encode()).hexdigest())
    return sa.SourceEvent(**(values | changes))


def make_entry(source=None, **changes):
    values = dict(source=source or make_source(), status="pending", attempts=0,
                  next_attempt_at=None, attempt_id=None, attempted_at=None,
                  delivered_at=None, reason=None, canonical_source_id=None)
    return sa.Entry(**(values | changes))


def make_store(http=None, config=None, budget=None):
    http = http or FakeHTTP()
    config = config or make_config()
    return sa.LedgerStore(sa.GitHubClient(config, "fake-token", http, budget or FakeBudget()), config)


def initialized():
    http = FakeHTTP()
    store = make_store(http)
    store.initialize()
    return http, store


def shard_comments(http):
    return [c for c in http.server["comments"].values() if c["body"].startswith("<!-- qd-slack-shard:")]


def decode_document(body):
    return json.loads(body.split("\n", 1)[1])


def mutate_document(container, mutate):
    marker, raw = container["body"].split("\n", 1)
    document = json.loads(raw)
    mutate(document)
    container["body"] = marker + "\n" + json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def test_ledger_roundtrip_uses_fresh_clients_and_preserves_exact_snapshot():
    http, store = initialized()
    source = make_source(body='原文\n"quotes" \\ <@U1> ' * 100)
    store.put(make_entry(source))
    reloaded = make_store(FakeHTTP(http.server)).load()
    assert reloaded.entries[101] == make_entry(source)
    assert reloaded.entries[101].source.body == source.body
    assert reloaded.manifest.shard_count == 1
    assert all(r["path"].startswith(BASE) for r in http.requests)


def test_lost_write_response_is_read_back_before_success():
    http, store = initialized()
    http.fail("POST", f"{BASE}/issues/{LEDGER_ISSUE}/comments", after=True)
    store.put(make_entry(status="attempting", attempts=1, attempt_id="attempt-1", attempted_at=NOW))
    reloaded = make_store(FakeHTTP(http.server)).load()
    assert reloaded.entries[101].status == "attempting"
    assert len([r for r in http.requests if r["method"] == "POST"]) == 1
    assert not [r for r in http.requests if "slack.com" in r["url"]]


def test_same_second_baseline_preserves_new_ids():
    http = FakeHTTP()
    http.add_comment(100, "already present", issue_number=7)
    http.add_comment(99, "old but edited", issue_number=7, created_at=NOW - timedelta(days=1))
    ledger = make_store(http).initialize()
    http.add_comment(101, "arrived after baseline", issue_number=7)
    restored = make_store(FakeHTTP(http.server)).initialize()
    assert ledger.manifest.enabled_at == NOW
    assert ledger.manifest.cursor == NOW
    assert ledger.manifest.baseline_ids == frozenset({100})
    assert restored.manifest == ledger.manifest
    assert 101 not in restored.manifest.baseline_ids
    requests = [r for r in http.requests if "/issues/comments?" in r["url"]]
    assert parse_qs(urlsplit(requests[0]["url"]).query)["since"] == [(NOW - timedelta(seconds=1)).isoformat()]


def test_ledger_load_never_initializes_empty_or_missing_ledger():
    for body in ("", UNINITIALIZED):
        http = FakeHTTP()
        http.server["issues"][LEDGER_ISSUE]["body"] = body
        with pytest.raises(sa.LedgerUnavailable):
            make_store(http).load()
        assert not [r for r in http.requests if r["method"] != "GET"]
    http.server["issues"].clear()
    with pytest.raises(sa.LedgerUnavailable):
        make_store(http).load()


@pytest.mark.parametrize("body", ["", "hello", UNINITIALIZED + "\n", "<!-- qd-slack-ledger:uninitialized:v2 -->"])
def test_baseline_requires_exact_preprovisioned_marker(body):
    http = FakeHTTP()
    http.server["issues"][LEDGER_ISSUE]["body"] = body
    with pytest.raises(sa.LedgerUnavailable):
        make_store(http).initialize()
    assert not [r for r in http.requests if r["method"] != "GET"]


def test_baseline_requires_trusted_server_date():
    http = FakeHTTP()
    http.omit_date = True
    with pytest.raises(sa.LedgerUnavailable):
        make_store(http).initialize()
    assert http.server["issues"][LEDGER_ISSUE]["body"] == UNINITIALIZED


def test_baseline_failure_on_later_page_never_saves_manifest():
    http = FakeHTTP()
    for i in range(201):
        http.add_comment(i + 1, "baseline", issue_number=7)
    def fail_page(request):
        if request["method"] == "GET" and "page=2" in request["url"]:
            raise sa.TransportFailure("page_failure", False)
    http.on_request = fail_page
    with pytest.raises(sa.LedgerUnavailable):
        make_store(http).initialize()
    assert http.server["issues"][LEDGER_ISSUE]["body"] == UNINITIALIZED


def test_baseline_paginates_all_same_second_ids():
    http = FakeHTTP()
    for i in range(201):
        http.add_comment(i + 1, "baseline", issue_number=7)
    assert make_store(http).initialize().manifest.baseline_ids == frozenset(range(1, 202))


def test_ledger_load_paginates_past_one_hundred_comments():
    http, store = initialized()
    for i in range(201):
        http.add_comment(2000 + i, "ordinary discussion", author=999)
    store.put(make_entry())
    fresh_http = FakeHTTP(http.server)
    assert make_store(fresh_http).load().entries[101].status == "pending"
    assert any("page=3" in r["url"] for r in fresh_http.requests)


def test_shards_obey_count_and_serialized_byte_limits():
    http, store = initialized()
    original_body = "原文🛰️<&>\n" * 900
    for i in range(130):
        store.put(make_entry(make_source(i + 1)))
    store.put(make_entry(make_source(500, body=original_body)))
    oversized_body = "x" * (48 * 1024)
    store.put(make_entry(make_source(501, body=oversized_body)))
    ledger = make_store(FakeHTTP(http.server)).load()
    shards = shard_comments(http)
    assert len(shards) > 1
    assert all(len(decode_document(s["body"])["records"]) <= 64 for s in shards)
    assert all(len(s["body"].encode()) <= 48 * 1024 for s in shards)
    assert ledger.entries[500].source.body == original_body
    oversized = ledger.entries[501]
    assert oversized.status == "needs_attention"
    assert oversized.source.body is None
    assert oversized.source.body_sha256 == hashlib.sha256(oversized_body.encode()).hexdigest()
    assert oversized.source.html_url == make_source(501).html_url
    assert "oversized" in oversized.reason


def test_shard_admission_rejects_serialized_and_presentation_oversize():
    http, store = initialized()
    # Unicode hits serialized bytes; ASCII hits Slack's visible-text limit first.
    for comment_id, body in [(101, "界" * 14000), (102, "a" * 36001)]:
        store.put(make_entry(make_source(comment_id, body=body)))
    assert all(e.status == "needs_attention" and e.source.body is None
               for e in make_store(FakeHTTP(http.server)).load().entries.values())


def test_shards_reserve_room_for_later_state_updates():
    http, store = initialized()
    for i in range(80):
        store.put(make_entry(make_source(i + 1, body="snapshot" * 80)))
    for i in range(80):
        source = make_source(i + 1, body="snapshot" * 80)
        store.put(make_entry(source, status="needs_attention", attempts=3,
                             attempt_id="a" * 128, attempted_at=NOW, next_attempt_at=NOW,
                             reason="界" * 256))
    restored = make_store(FakeHTTP(http.server)).load()
    assert len(restored.entries) == 80
    assert all(e.attempts == 3 for e in restored.entries.values())
    assert all(len(s["body"].encode()) <= 48 * 1024 for s in shard_comments(http))


@pytest.mark.parametrize("mutation", [
    lambda d: d["manifest"].update(version=2),
    lambda d: d["manifest"].update(channel_id="COTHER"),
    lambda d: d["manifest"].update(repo="attacker/repo"),
    lambda d: d["manifest"].update(shard_count=2),
    lambda d: d["manifest"].update(enabled_at="2026-10-07T18:00:00"),
])
def test_corrupt_manifest_pauses_ledger(mutation):
    http, store = initialized()
    store.put(make_entry())
    mutate_document(http.server["issues"][LEDGER_ISSUE], mutation)
    with pytest.raises(sa.LedgerUnavailable):
        make_store(FakeHTTP(http.server)).load()


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(version=2),
    lambda d: d["records"][0]["source"].update(body="edited snapshot"),
    lambda d: d["records"][0].update(status="invented"),
    lambda d: d["records"][0].update(attempts=4),
    lambda d: d["records"][0].update(reason="x" * 257),
    lambda d: d["records"][0]["source"].update(body=None),
])
def test_corrupt_shard_pauses_ledger(mutation):
    http, store = initialized()
    store.put(make_entry())
    mutate_document(shard_comments(http)[0], mutation)
    with pytest.raises(sa.LedgerUnavailable):
        make_store(FakeHTTP(http.server)).load()


@pytest.mark.parametrize("target", ["manifest_author", "shard_author", "duplicate_shard", "shard_gap", "missing_shard", "unknown_marker"])
def test_untrusted_or_incomplete_shards_pause_ledger(target):
    http, store = initialized()
    store.put(make_entry())
    shard = shard_comments(http)[0]
    if target == "manifest_author":
        http.server["issues"][LEDGER_ISSUE]["user"]["id"] = 999
    elif target == "shard_author":
        shard["user"]["id"] = 999
    elif target == "duplicate_shard":
        http.add_comment(3000, shard["body"])
    elif target == "shard_gap":
        shard["body"] = shard["body"].replace("shard:v1:0", "shard:v1:2")
    elif target == "missing_shard":
        http.server["comments"].clear()
    elif target == "unknown_marker":
        shard["body"] = shard["body"].replace("shard:v1:", "shard:v2:")
    with pytest.raises(sa.LedgerUnavailable):
        make_store(FakeHTTP(http.server)).load()


class ProcessStopped(BaseException):
    pass


@pytest.mark.parametrize("method,path", [("POST", f"{BASE}/issues/{LEDGER_ISSUE}/comments"),
                                          ("PATCH", f"{BASE}/issues/{LEDGER_ISSUE}")])
def test_shard_create_crash_after_commit_recovers_confirmed_source(method, path):
    http, store = initialized()
    http.fail(method, path, after=True, error=ProcessStopped())
    with pytest.raises(ProcessStopped):
        store.put(make_entry())
    recovered = make_store(FakeHTTP(http.server)).load()
    assert recovered.entries[101].source == make_source()
    assert recovered.manifest.shard_count == 1
    assert len(shard_comments(http)) == 1


@pytest.mark.parametrize("after", [False, True])
def test_entry_write_interruption_keeps_last_confirmed_state(after):
    http, store = initialized()
    store.put(make_entry())
    shard_id = shard_comments(http)[0]["id"]
    http.fail("PATCH", f"{BASE}/issues/comments/{shard_id}", after=after, error=ProcessStopped())
    with pytest.raises(ProcessStopped):
        store.put(make_entry(status="attempting", attempts=1, attempt_id="a", attempted_at=NOW))
    restored = make_store(FakeHTTP(http.server)).load()
    assert restored.entries[101].status == ("attempting" if after else "pending")


@pytest.mark.parametrize("after", [False, True])
def test_manifest_write_interruption_never_loses_confirmed_entries(after):
    http, store = initialized()
    store.put(make_entry())
    manifest = store.load().manifest
    http.fail("PATCH", f"{BASE}/issues/{LEDGER_ISSUE}", after=after, error=ProcessStopped())
    with pytest.raises(ProcessStopped):
        store.update_manifest(replace(manifest, cursor=NOW + timedelta(minutes=1)))
    restored = make_store(FakeHTTP(http.server)).load()
    assert 101 in restored.entries
    assert restored.manifest.cursor == (NOW + timedelta(minutes=1) if after else NOW)


def test_lost_patch_response_verifies_exact_revision_and_full_content():
    http, store = initialized()
    store.put(make_entry())
    shard_id = shard_comments(http)[0]["id"]
    http.fail("PATCH", f"{BASE}/issues/comments/{shard_id}", after=True)
    store.put(make_entry(status="attempting", attempts=1, attempt_id="a", attempted_at=NOW))
    assert make_store(FakeHTTP(http.server)).load().entries[101].status == "attempting"
    assert len([r for r in http.requests if r["method"] == "PATCH" and r["path"].endswith(f"/comments/{shard_id}")]) == 1


def test_write_response_unconfirmed_stops_without_retry_post():
    http, store = initialized()
    path = f"{BASE}/issues/{LEDGER_ISSUE}/comments"
    http.fail("POST", path, after=True)
    def fail_readback(request):
        if request["method"] == "GET" and request["path"] == path and shard_comments(http):
            raise sa.TransportFailure("unavailable", False)
    http.on_request = fail_readback
    with pytest.raises(sa.LedgerUncertain):
        store.put(make_entry())
    assert len([r for r in http.requests if r["method"] == "POST"]) == 1
    assert 101 in make_store(FakeHTTP(http.server)).load().entries


def test_shard_post_failure_before_commit_never_retries_or_advances_manifest():
    http, store = initialized()
    http.fail("POST", f"{BASE}/issues/{LEDGER_ISSUE}/comments")
    with pytest.raises(sa.LedgerUncertain):
        store.put(make_entry())
    assert make_store(FakeHTTP(http.server)).load().entries == {}
    assert len([r for r in http.requests if r["method"] == "POST"]) == 1


def test_manifest_baseline_and_shard_count_cannot_be_reset_by_update():
    http, store = initialized()
    store.put(make_entry())
    manifest = store.load().manifest
    for changes in ({"shard_count": 0}, {"enabled_at": NOW + timedelta(seconds=1)},
                    {"baseline_ids": frozenset({777})}, {"cursor": NOW - timedelta(seconds=1)}):
        with pytest.raises(sa.LedgerUnavailable):
            store.update_manifest(replace(manifest, **changes))
    assert len(make_store(FakeHTTP(http.server)).load().entries) == 1


@pytest.mark.parametrize("changes", [{"repo": "other/repo"}, {"channel_id": "COTHER"},
                                     {"ledger_issue": 0}, {"producer_ids": frozenset()}])
def test_ledger_config_is_fixed_and_requires_verified_producers(changes):
    with pytest.raises(ValueError):
        make_config(**changes)


def test_budget_expired_starts_no_github_request():
    http = FakeHTTP()
    with pytest.raises(sa.BudgetExpired):
        make_store(http, budget=FakeBudget(seconds=0)).load()
    assert http.requests == []


def test_github_client_exposes_trusted_server_time_and_hides_token():
    http = FakeHTTP()
    client = sa.GitHubClient(make_config(), "private-token", http, FakeBudget())
    client.get_issue(LEDGER_ISSUE)
    assert client.server_time == NOW
    assert "private-token" not in repr(client)
    assert http.requests[0]["headers"]["Authorization"] == "Bearer private-token"


def test_ledger_metadata_admission_uses_exact_history_frame_boundary():
    http, store = initialized()
    source = make_source()
    frame = sa.source_metadata_text(source, historical=True)
    assert frame == (
        "历史结果／延迟补发，仅供回顾\nENTRY_ARMED | NVDA\n"
        "原扫描时间：2026-10-07T14:00:00-04:00\n"
        "GitHub 发布时间：2026-10-07T18:00:00+00:00\n"
        "GitHub 评论 ID：101\n"
        f"原文：https://github.com/{REPO}/issues/7#issuecomment-101\n"
        "观察用途，不创建或起草订单"
    )
    accepted = make_source(101, body="x" * (36_000 - len(frame)))
    rejected = make_source(102, body="x" * (36_001 - len(frame)))
    store.put(make_entry(accepted))
    store.put(make_entry(rejected))
    restored = make_store(FakeHTTP(http.server)).load()
    assert restored.entries[101].source.body == accepted.body
    assert restored.entries[101].status == "pending"
    assert restored.entries[102].status == "needs_attention"


def test_shard_reservation_accounts_for_six_byte_json_control_escapes():
    http, store = initialized()
    sources = [make_source(i, body="x" * 3500) for i in range(101, 109)]
    for source in sources:
        store.put(make_entry(source))
    for source in sources:
        store.put(make_entry(source, status="needs_attention", attempts=3, reason="\x00" * 256,
                             attempt_id="\x00" * 128, attempted_at=NOW, next_attempt_at=NOW,
                             delivered_at=NOW, canonical_source_id=9223372036854775807))
    restored = make_store(FakeHTTP(http.server)).load()
    assert all(entry.reason == "\x00" * 256 for entry in restored.entries.values())
    assert all(len(c["body"].encode()) <= sa.SHARD_BYTES for c in shard_comments(http))


@pytest.mark.parametrize("mutation", [
    lambda d: d["records"][0].update(status=[]),
    lambda d: d["records"][0]["source"].update(created_at=42),
    lambda d: d["records"][0]["source"].update(comment_id=True),
    lambda d: d["records"][0]["source"].update(html_url="https://evil.example/secret"),
    lambda d: d["records"][0].update(attempts=True),
    lambda d: d["records"][0].update(extra="unknown"),
])
def test_malformed_ledger_schema_always_raises_pause_error(mutation):
    http, store = initialized()
    store.put(make_entry())
    mutate_document(shard_comments(http)[0], mutation)
    with pytest.raises(sa.LedgerUnavailable):
        make_store(FakeHTTP(http.server)).load()


def test_ledger_pending_snapshot_cannot_be_replaced_even_with_matching_new_hash():
    http, store = initialized()
    store.put(make_entry())
    with pytest.raises(sa.LedgerUnavailable):
        store.put(make_entry(make_source(body="replacement")))
    assert make_store(FakeHTTP(http.server)).load().entries[101].source == make_source()


def test_baseline_write_response_lost_is_verified_without_reset():
    http = FakeHTTP()
    http.add_comment(100, "baseline", issue_number=7)
    http.fail("PATCH", f"{BASE}/issues/{LEDGER_ISSUE}", after=True)
    ledger = make_store(http).initialize()
    assert ledger.manifest.baseline_ids == frozenset({100})
    assert len([r for r in http.requests if r["method"] == "PATCH"]) == 1


def test_baseline_process_crash_after_commit_does_not_rebaseline():
    http = FakeHTTP()
    http.add_comment(100, "baseline", issue_number=7)
    http.fail("PATCH", f"{BASE}/issues/{LEDGER_ISSUE}", after=True, error=ProcessStopped())
    with pytest.raises(ProcessStopped):
        make_store(http).initialize()
    http.add_comment(101, "new", issue_number=7)
    assert make_store(FakeHTTP(http.server)).initialize().manifest.baseline_ids == frozenset({100})


@pytest.mark.parametrize("target", ["https://evil.example/steal", f"https://api.github.com{BASE}/issues/9/comments?per_page=100&page=3"])
def test_ledger_rejects_malicious_or_skipped_pagination_links(target):
    http, store = initialized()
    store.put(make_entry())
    original_request = http.request
    def linked(*args, **kwargs):
        response = original_request(*args, **kwargs)
        if urlsplit(args[1]).path.endswith("/comments"):
            return replace(response, headers=response.headers | {"Link": f'<{target}>; rel="next"'})
        return response
    http.request = linked
    with pytest.raises(sa.LedgerUnavailable):
        make_store(http).load()
    assert all(urlsplit(r["url"]).netloc == "api.github.com" for r in http.requests)


def test_ledger_missing_later_page_is_not_treated_as_end():
    http, store = initialized()
    store.put(make_entry())
    for i in range(100):
        http.add_comment(3000 + i, "discussion")
    original_request = http.request
    def missing(*args, **kwargs):
        if "page=2" in args[1]:
            return sa.HttpResponse(404, {}, b"{}")
        return original_request(*args, **kwargs)
    http.request = missing
    with pytest.raises(sa.LedgerUnavailable):
        make_store(http).load()


def test_shard_write_readback_must_match_entire_document():
    http, store = initialized()
    def tamper(request):
        if request["method"] == "GET" and request["path"].endswith("/comments") and shard_comments(http):
            mutate_document(shard_comments(http)[0], lambda d: d["records"][0].update(status="unknown"))
    http.on_request = tamper
    with pytest.raises(sa.LedgerUncertain):
        store.put(make_entry())
    assert len([r for r in http.requests if r["method"] == "POST"]) == 1


def test_budget_cannot_expire_into_an_unverified_write_success():
    http, _ = initialized()
    budget = FakeBudget()
    store = make_store(http, budget=budget)
    def expire(request):
        if request["method"] == "POST":
            budget.clock.sleep(30)
    http.on_request = expire
    with pytest.raises(sa.BudgetExpired):
        store.put(make_entry())
    assert len([r for r in http.requests if r["method"] == "POST"]) == 1
    assert make_store(FakeHTTP(http.server)).load().entries[101].status == "pending"


def test_ledger_advertised_next_page_must_not_disappear():
    http, store = initialized()
    store.put(make_entry())
    original_request = http.request
    def empty_next(*args, **kwargs):
        response = original_request(*args, **kwargs)
        if urlsplit(args[1]).path.endswith("/comments") and parse_qs(urlsplit(args[1]).query).get("page") == ["1"]:
            next_url = f"https://api.github.com{BASE}/issues/9/comments?per_page=100&page=2"
            return replace(response, headers=response.headers | {"Link": f'<{next_url}>; rel="next"'})
        return response
    http.request = empty_next
    with pytest.raises(sa.LedgerUnavailable):
        make_store(http).load()


def test_ledger_does_not_trust_oversized_pending_record_in_existing_shard():
    http, store = initialized()
    store.put(make_entry())
    def oversize(document):
        source = document["records"][0]["source"]
        source["body"] = "x" * 37000
        source["body_sha256"] = hashlib.sha256(source["body"].encode()).hexdigest()
    mutate_document(shard_comments(http)[0], oversize)
    with pytest.raises(sa.LedgerUnavailable):
        make_store(http).load()


# Discovery fixtures mirror the actual GitHub REST objects and producer body.
SOURCE_STATES = ("ENTRY_ARMED", "ENTRY_CONFIRMED", "LEADER_WATCH", "LEADER_HOT_NO_CHASE")


def daily_issue(http, number=7, day="2026-10-07", **changes):
    issue = FakeHTTP.issue(number, "Daily market observations") | {
        "title": f"Market Watch | {day} ET", "state": "open",
        "url": f"https://api.github.com{BASE}/issues/{number}",
    } | changes
    http.server["issues"][number] = issue
    return issue


def source_comment(http, comment_id=101, *, symbol="NVDA", state="ENTRY_ARMED",
                   day="2026-10-07", issue_number=7, generated="2026-10-07T14:00:00-04:00",
                   run_id="12345", metadata=True, **changes):
    from market_data.github_alerts import _event_markdown
    body = _event_markdown({"event_key": f"{day}|{symbol}|{state}", "symbol": symbol,
                           "state": state, "reason": "Fixture observation"},
                          generated_at_et=generated, run_id=run_id)
    if not metadata:
        body = "\n".join(line for line in body.split("\n") if not line.startswith("<!-- qd-source:"))
    return http.add_comment(comment_id, body, issue_number=issue_number, **changes)


def run_discovery(store, *, clock=None, budget=None):
    return sa.discover(store, store.github, clock or FakeClock(), budget or FakeBudget())


@pytest.mark.parametrize("state", SOURCE_STATES)
def test_source_accepts_four_states_and_exact_producer_snapshot(state):
    http = FakeHTTP()
    issue = daily_issue(http)
    comment = source_comment(http, state=state)
    source = sa.parse_source(comment, issue, make_config())
    assert source == sa.SourceEvent(REPO, 101, 7, f"2026-10-07|NVDA|{state}", "NVDA", state,
                                    NOW, NOW, "2026-10-07T14:00:00-04:00", "12345",
                                    comment["html_url"], comment["body"],
                                    hashlib.sha256(comment["body"].encode()).hexdigest())


@pytest.mark.parametrize("mutation", [
    lambda c, i: c.update(body="<!-- qd-heartbeat -->\n### Scanner heartbeat"),
    lambda c, i: c.update(body="An ordinary comment"),
    lambda c, i: c.update(body="\n" + c["body"]),
    lambda c, i: c.update(body=c["body"] + "\n<!-- qd-event:2026-10-07|NVDA|ENTRY_ARMED -->"),
    lambda c, i: c.update(body=c["body"] + "\n<!--qd-event:2026-10-07|NVDA|ENTRY_ARMED-->"),
    lambda c, i: c.update(body=c["body"].replace("2026-10-07|NVDA|ENTRY_ARMED", "2026-10-07:NVDA:ENTRY_ARMED")),
    lambda c, i: c.update(body=c["body"].replace("2026-10-07|NVDA|ENTRY_ARMED", "2026-10-07|NVDA|ENTRY_ARMED|extra")),
    lambda c, i: c.update(body=c["body"].replace("### NVDA", "### TSLA")),
    lambda c, i: c.update(body=c["body"].replace("### NVDA — ENTRY_ARMED", "### NVDA — ENTRY_CONFIRMED")),
    lambda c, i: c.update(body=c["body"].replace("ENTRY_ARMED", "HOLD")),
    lambda c, i: c.update(body=c["body"].replace("NVDA", "nvda")),
    lambda c, i: c.update(body=c["body"].replace("NVDA", "@here")),
    lambda c, i: c.update(body=c["body"].replace("NVDA", "A" * 33)),
    lambda c, i: c.update(body=c["body"].replace("2026-10-07|", "2026-10-06|")),
    lambda c, i: i.update(title="Market Watch | 2026-02-30 ET"),
    lambda c, i: i.update(title="Market Watch | 2026-10-07 ET extra"),
    lambda c, i: i.update(pull_request={"url": "https://api.github.com/pulls/7"}),
    lambda c, i: c.update(user={"id": 99, "login": "producer", "name": "same display name"}),
    lambda c, i: c.update(user={"login": "producer", "name": "same display name"}),
    lambda c, i: c.update(user={"id": str(AUTHOR)}),
    lambda c, i: c.update(user=None),
    lambda c, i: c.update(issue_url="https://api.github.com/repos/other/repo/issues/7"),
    lambda c, i: c.update(issue_url=f"https://api.github.com{BASE}/issues/8"),
    lambda c, i: c.update(html_url=f"https://evil.example/{REPO}/issues/7#issuecomment-101"),
    lambda c, i: c.update(html_url=c["html_url"].replace("github.com", "github.com@evil.example")),
    lambda c, i: c.update(html_url=c["html_url"].replace("101", "102")),
    lambda c, i: c.update(html_url=c["html_url"] + "?credential=secret"),
    lambda c, i: c.update(html_url=c["html_url"].replace("/issues/7", "/issues/8")),
    lambda c, i: i.update(html_url="https://github.com/other/repo/issues/7"),
    lambda c, i: c.update(id=True),
    lambda c, i: c.update(created_at="2026-10-07T18:00:00"),
    lambda c, i: c.update(created_at=None),
    lambda c, i: c.update(updated_at="2026-10-07T17:59:59Z"),
    lambda c, i: c.update(body=None),
])
def test_source_rejects_untrusted_or_malformed_identity(mutation):
    http = FakeHTTP()
    issue = daily_issue(http)
    comment = source_comment(http)
    mutation(comment, issue)
    assert sa.parse_source(comment, issue, make_config()) is None


@pytest.mark.parametrize("symbol", ["BRK.B", "^GSPC", "NQ=F", "A-B", "A" * 32])
def test_source_symbol_full_match_contract(symbol):
    http = FakeHTTP()
    parsed = sa.parse_source(source_comment(http, symbol=symbol), daily_issue(http), make_config())
    assert (parsed is not None) == (symbol != "^GSPC")


@pytest.mark.parametrize("generated", [None, "not a date", "2026-10-07T14:00:00", "__import__('os').system('false')"])
def test_source_missing_or_unparseable_scan_time_stays_unknown(generated):
    http = FakeHTTP()
    source = sa.parse_source(source_comment(http, generated=generated), daily_issue(http), make_config())
    assert source.generated_at_et is None
    assert source.run_id == "12345"


def test_source_legacy_body_and_malformed_metadata_do_not_invent_scan_time():
    http = FakeHTTP()
    issue = daily_issue(http)
    comment = source_comment(http, metadata=False)
    assert sa.parse_source(comment, issue, make_config()).generated_at_et is None
    comment = source_comment(http)
    comment["body"] = comment["body"].replace(comment["body"].split("\n")[1], "<!-- qd-source:not JSON -->")
    source = sa.parse_source(comment, issue, make_config())
    assert source.generated_at_et is None and source.run_id is None


@pytest.mark.parametrize("day,generated,accepted", [
    ("2026-10-07", "2026-10-08T00:30:00+00:00", True),
    ("2026-10-08", "2026-10-08T00:30:00+00:00", False),
    ("2026-03-08", "2026-03-08T01:59:00-05:00", True),
    ("2026-03-08", "2026-03-08T03:01:00-04:00", True),
    ("2026-11-01", "2026-11-01T01:30:00-04:00", True),
    ("2026-11-01", "2026-11-01T01:30:00-05:00", True),
    ("2026-10-07", "2026-10-08T00:00:00-04:00", False),
])
def test_source_scan_date_is_verified_in_eastern_time_across_midnight_and_dst(day, generated, accepted):
    http = FakeHTTP()
    source = sa.parse_source(source_comment(http, day=day, generated=generated),
                             daily_issue(http, day=day), make_config())
    assert (source is not None) == accepted
    if accepted:
        assert source.generated_at_et == generated


def test_baseline_excludes_old_ids_but_accepts_same_second_new_ids():
    http = FakeHTTP()
    issue = daily_issue(http)
    old = source_comment(http, 100)
    store = make_store(http)
    manifest = store.initialize().manifest
    new = source_comment(http, 101)
    edited_old = source_comment(http, 99, created_at=NOW - timedelta(seconds=1), updated_at=NOW)
    assert not sa.after_baseline(sa.parse_source(old, issue, store.config), manifest)
    assert not sa.after_baseline(sa.parse_source(edited_old, issue, store.config), manifest)
    assert sa.after_baseline(sa.parse_source(new, issue, store.config), manifest)
    assert run_discovery(store) == 1
    assert set(make_store(FakeHTTP(http.server)).load().entries) == {101}


@pytest.mark.parametrize("cached", [False, True], ids=["ordinary", "verified-run"])
@pytest.mark.parametrize("packing", ["first-shard", "existing-shard", "new-shard"])
def test_discovery_returns_new_root_count_independent_of_cache_and_shard_packing(cached, packing):
    http, store = initialized()
    daily_issue(http)
    original_ids = set()
    if packing != "first-shard":
        original = source_comment(http, 101)
        if packing == "new-shard":
            original["body"] += "\n" + "x" * 30000
        assert run_discovery(store) == 1
        original_ids.add(101)
        assert len(shard_comments(http)) == 1
    added = source_comment(http, 102, symbol="TSM")
    if packing == "new-shard":
        added["body"] += "\n" + "y" * 16000

    with store.verified_run() if cached else nullcontext():
        assert run_discovery(store) == 1
        # Read the server through a separate store, never the discovery cache.
        admitted = make_store(FakeHTTP(http.server)).load()
        assert set(admitted.entries) == original_ids | {102}
        assert admitted.entries[102].status == "pending"
        assert admitted.entries[102].source.body == added["body"]
        assert admitted.manifest.shard_count == (2 if packing == "new-shard" else 1)
        http.requests.clear()
        assert run_discovery(store) == 0
        assert not [request for request in http.requests if request["method"] != "GET"]
        assert make_store(FakeHTTP(http.server)).load().entries == admitted.entries


@pytest.mark.parametrize("count", [101, 201])
def test_discovery_traverses_all_pages_and_caches_daily_issue(count):
    http, store = initialized()
    daily_issue(http)
    for index in range(count):
        source_comment(http, 101 + index, symbol=f"S{index}")
    http.date = NOW + timedelta(minutes=2)
    http.requests.clear()
    assert run_discovery(store, clock=FakeClock(NOW + timedelta(days=5))) == count
    discovery_reads = [r for r in http.requests if r["path"] == BASE + "/issues/comments"]
    assert len(discovery_reads) == (count // 100) + 1
    assert all(parse_qs(urlsplit(r["url"]).query) == {
        "since": [(NOW - timedelta(seconds=1)).isoformat()], "sort": ["created"],
        "direction": ["asc"], "per_page": ["100"], "page": [str(i)],
    } for i, r in enumerate(discovery_reads, 1))
    assert len([r for r in http.requests if r["path"] == BASE + "/issues/7"]) == 1
    restored = make_store(FakeHTTP(http.server)).load()
    assert set(restored.entries) == set(range(101, 101 + count))
    assert restored.manifest.cursor == NOW + timedelta(minutes=2)
    assert all(e.source.body == http.server["comments"][i]["body"] for i, e in restored.entries.items())


def test_cursor_uses_first_page_server_time_and_new_arrivals_remain_visible():
    http, store = initialized()
    daily_issue(http)
    source_comment(http)
    started_at = NOW + timedelta(minutes=3)
    http.date = started_at
    def later(request):
        if request["path"] == BASE + "/issues/7":
            http.date = started_at + timedelta(minutes=2)
            source_comment(http, 102, symbol="TSLA", created_at=started_at + timedelta(seconds=1))
    http.on_request = later
    assert run_discovery(store, clock=FakeClock(NOW + timedelta(days=2))) == 1
    restored = make_store(FakeHTTP(http.server)).load()
    assert restored.manifest.cursor == started_at
    assert set(restored.entries) == {101}
    http.on_request = None
    http.requests.clear()
    assert run_discovery(store) == 1
    requests = [r for r in http.requests if r["path"] == BASE + "/issues/comments"]
    assert parse_qs(urlsplit(requests[0]["url"]).query)["since"] == [(started_at - timedelta(seconds=60)).isoformat()]
    assert set(make_store(FakeHTTP(http.server)).load().entries) == {101, 102}


@pytest.mark.parametrize("failure", ["second_page", "issue_read", "entry_write", "cursor_write"])
def test_cursor_advances_only_after_full_discovery_is_durable(failure):
    http, store = initialized()
    daily_issue(http)
    for i in range(101):
        source_comment(http, 101 + i, symbol=f"S{i}")
    old_cursor = store.load().manifest.cursor
    http.date = NOW + timedelta(minutes=5)
    original = http.request
    def broken(method, url, **kwargs):
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        document = json.loads(kwargs["body"]) if kwargs["body"] else {}
        is_cursor = method == "PATCH" and path == f"{BASE}/issues/{LEDGER_ISSUE}" and (
            decode_document(document["body"])["manifest"]["cursor"] != old_cursor.isoformat())
        if ((failure == "second_page" and path == BASE + "/issues/comments" and query.get("page") == ["2"])
                or (failure == "issue_read" and path == BASE + "/issues/7")
                or (failure == "entry_write" and method == "PATCH" and "/issues/comments/" in path)
                or (failure == "cursor_write" and is_cursor)):
            return sa.HttpResponse(500, {}, b"{}")
        return original(method, url, **kwargs)
    http.request = broken
    with pytest.raises(sa.LedgerUnavailable):
        run_discovery(store)
    failed = make_store(FakeHTTP(http.server)).load()
    assert failed.manifest.cursor == old_cursor
    if failure in {"second_page", "issue_read"}:
        assert failed.entries == {}
    elif failure == "entry_write":
        assert set(failed.entries) == {101}
    else:
        assert len(failed.entries) == 101
    http.request = original
    run_discovery(make_store(http))
    restored = make_store(FakeHTTP(http.server)).load()
    assert set(restored.entries) == set(range(101, 202))
    assert restored.manifest.cursor == NOW + timedelta(minutes=5)


@pytest.mark.parametrize("status", ["pending", "retry_wait", "unknown", "attempting", "delivered"])
@pytest.mark.parametrize("valid_edit", [False, True])
def test_edited_source_is_never_a_new_notification(status, valid_edit):
    http, store = initialized()
    daily_issue(http)
    comment = source_comment(http)
    assert run_discovery(store) == 1
    old = store.load().entries[101]
    store.put(replace(old, status=status, attempts=int(status != "pending")))
    comment["body"] = comment["body"] + "\nChanged" if valid_edit else "removed marker"
    comment["updated_at"] = (NOW + timedelta(minutes=1)).isoformat()
    assert run_discovery(store) == 0
    restored = make_store(FakeHTTP(http.server)).load().entries
    assert set(restored) == {101}
    assert restored[101].source == old.source
    assert restored[101].status == ("delivered" if status == "delivered" else "needs_attention")
    if status != "delivered":
        assert "changed" in restored[101].reason


def test_duplicate_sources_choose_earliest_creation_then_id_after_complete_discovery():
    http, store = initialized()
    daily_issue(http)
    source_comment(http, 102, created_at=NOW + timedelta(seconds=1))
    source_comment(http, 103)
    source_comment(http, 101)
    assert run_discovery(store) == 1
    entries = make_store(FakeHTTP(http.server)).load().entries
    assert entries[101].status == "pending"
    assert all(entries[i].status == "duplicate_source" and entries[i].canonical_source_id == 101 for i in (102, 103))
    assert run_discovery(store) == 0
    assert make_store(FakeHTTP(http.server)).load().entries == entries


def test_duplicate_earlier_source_replaces_unattempted_root_safely():
    http, store = initialized()
    daily_issue(http)
    source_comment(http, 102, created_at=NOW + timedelta(seconds=2))
    assert run_discovery(store) == 1
    source_comment(http, 101, created_at=NOW + timedelta(seconds=1))
    assert run_discovery(store) == 1
    entries = make_store(FakeHTTP(http.server)).load().entries
    assert entries[101].status == "pending"
    assert entries[102].status == "duplicate_source" and entries[102].canonical_source_id == 101


@pytest.mark.parametrize("status", ["attempting", "delivered", "retry_wait", "unknown", "needs_attention"])
def test_duplicate_earlier_source_never_reopens_delivery_after_attempt(status):
    http, store = initialized()
    daily_issue(http)
    source_comment(http, 102, created_at=NOW + timedelta(seconds=2))
    run_discovery(store)
    canonical = replace(store.load().entries[102], status=status, attempts=1, attempted_at=NOW)
    store.put(canonical)
    source_comment(http, 101, created_at=NOW + timedelta(seconds=1))
    assert run_discovery(store) == 0
    entries = make_store(FakeHTTP(http.server)).load().entries
    assert entries[102] == canonical
    assert entries[101].status == "duplicate_source" and entries[101].canonical_source_id == 102
    assert "earlier" in entries[101].reason and "attempt" in entries[101].reason


def test_discovery_keeps_yesterday_closed_issue_and_unfinished_entries():
    http, store = initialized()
    daily_issue(http, state="closed")
    source_comment(http)
    assert run_discovery(store) == 1
    next_day = datetime(2026, 10, 8, 4, 5, tzinfo=UTC)
    http.date = next_day
    daily_issue(http, number=8, day="2026-10-08")
    source_comment(http, 102, issue_number=8, day="2026-10-08", generated="2026-10-08T00:05:00-04:00", created_at=next_day)
    assert run_discovery(store) == 1
    entries = make_store(FakeHTTP(http.server)).load().entries
    assert entries[101].status == entries[102].status == "pending"
    assert entries[101].source.issue_number == 7 and entries[102].source.issue_number == 8


def test_discovery_oversized_snapshot_is_rejected_before_pending_with_exact_identity():
    http, store = initialized()
    daily_issue(http)
    comment = source_comment(http)
    comment["body"] += "\n" + "长" * 40_000
    assert run_discovery(store) == 1
    entry = make_store(FakeHTTP(http.server)).load().entries[101]
    assert entry.status == "needs_attention" and entry.reason.startswith("oversized:")
    assert entry.source.body is None
    assert entry.source.body_sha256 == hashlib.sha256(comment["body"].encode()).hexdigest()
    assert entry.source.html_url == comment["html_url"]
    assert entry.source.event_key == "2026-10-07|NVDA|ENTRY_ARMED"


def test_discovery_budget_expiration_preserves_cursor_without_starting_requests():
    http, store = initialized()
    http.requests.clear()
    with pytest.raises(sa.BudgetExpired):
        run_discovery(store, budget=FakeBudget(seconds=0))
    assert http.requests == []
    assert make_store(FakeHTTP(http.server)).load().manifest.cursor == NOW


def test_edited_oversized_source_retains_rejection_identity_and_original_hash():
    http, store = initialized()
    daily_issue(http)
    comment = source_comment(http)
    comment["body"] += "\n" + "x" * 40_000
    run_discovery(store)
    original = store.load().entries[101].source
    comment["body"] += "\nEdited later"
    assert run_discovery(store) == 0
    entry = make_store(FakeHTTP(http.server)).load().entries[101]
    assert entry.source == original
    assert entry.status == "needs_attention" and entry.reason.startswith("oversized:")
    assert "changed" in entry.reason


def test_discovery_duplicate_root_is_selected_after_all_pages_before_any_write():
    http, store = initialized()
    daily_issue(http)
    for i in range(201):
        source_comment(http, 101 + i)
    original = http.request
    saw_last_page = False
    def reversed_pages(method, url, **kwargs):
        nonlocal saw_last_page
        parsed = urlsplit(url)
        if method == "GET" and parsed.path == BASE + "/issues/comments":
            page = int(parse_qs(parsed.query)["page"][0])
            saw_last_page |= page == 3
            # All real-API-shaped candidates arrive in reverse order across pages.
            records = [deepcopy(http.server["comments"][i]) for i in range(301, 100, -1)]
            return sa.HttpResponse(200, {"Date": format_datetime(NOW, usegmt=True)},
                                   json.dumps(records[(page - 1) * 100:page * 100]).encode())
        if method != "GET":
            assert saw_last_page
        return original(method, url, **kwargs)
    http.request = reversed_pages
    assert run_discovery(store) == 1
    entries = make_store(FakeHTTP(http.server)).load().entries
    assert entries[101].status == "pending"
    assert all(e.status == "duplicate_source" and e.canonical_source_id == 101
               for i, e in entries.items() if i != 101)


def test_duplicate_root_replacement_crash_never_leaves_two_pending_notifications():
    http, store = initialized()
    daily_issue(http)
    source_comment(http, 102, created_at=NOW + timedelta(seconds=2))
    run_discovery(store)
    source_comment(http, 101, created_at=NOW + timedelta(seconds=1))
    original = http.request
    writes = 0
    def crash(method, url, **kwargs):
        nonlocal writes
        if method == "PATCH" and "/issues/comments/" in url:
            writes += 1
            if writes == 2:
                raise ProcessStopped()
        return original(method, url, **kwargs)
    http.request = crash
    with pytest.raises(ProcessStopped):
        run_discovery(store)
    interrupted = make_store(FakeHTTP(http.server)).load()
    assert interrupted.entries[102].status == "duplicate_source"
    assert interrupted.entries[102].canonical_source_id == 101
    assert 101 not in interrupted.entries
    assert interrupted.manifest.cursor == NOW
    http.request = original
    assert run_discovery(make_store(http)) == 1
    entries = make_store(FakeHTTP(http.server)).load().entries
    assert [i for i, e in entries.items() if e.status == "pending"] == [101]


@pytest.mark.parametrize("date", [None, NOW - timedelta(seconds=1)])
def test_discovery_requires_fresh_trusted_first_page_date(date):
    http, store = initialized()
    daily_issue(http)
    source_comment(http)
    http.omit_date = date is None
    http.date = date or NOW
    with pytest.raises(sa.LedgerUnavailable):
        run_discovery(store)
    restored = make_store(FakeHTTP(http.server)).load()
    assert restored.entries == {} and restored.manifest.cursor == NOW


def test_discovery_budget_expiration_between_puts_leaves_cursor_and_resume_safe():
    http, store = initialized()
    daily_issue(http)
    source_comment(http, 101)
    source_comment(http, 102, symbol="TSLA")
    budget = FakeBudget()
    def expire(request):
        if request["method"] == "POST":
            budget.clock.sleep(30)
    http.on_request = expire
    with pytest.raises(sa.BudgetExpired):
        run_discovery(store, budget=budget)
    restored = make_store(FakeHTTP(http.server)).load()
    assert set(restored.entries) == {101} and restored.manifest.cursor == NOW
    http.on_request = None
    assert run_discovery(make_store(http)) == 1
    assert set(make_store(FakeHTTP(http.server)).load().entries) == {101, 102}


def test_discovery_ignores_heartbeat_and_untrusted_author_without_fetching_issue():
    http, store = initialized()
    http.add_comment(101, "<!-- qd-heartbeat -->\n### Scanner heartbeat", issue_number=7)
    source_comment(http, 102, author=999)
    http.add_comment(103, "ordinary conversation", issue_number=8)
    http.requests.clear()
    assert run_discovery(store) == 0
    assert not [r for r in http.requests if r["path"] in {BASE + "/issues/7", BASE + "/issues/8"}]
    assert make_store(FakeHTTP(http.server)).load().entries == {}


def test_source_malformed_utf8_is_rejected_without_breaking_discovery():
    http = FakeHTTP()
    issue = daily_issue(http)
    comment = source_comment(http)
    comment["body"] += "\ud800"
    assert sa.parse_source(comment, issue, make_config()) is None


def test_source_excessive_issue_number_is_rejected_without_integer_conversion_error():
    http = FakeHTTP()
    issue = daily_issue(http)
    comment = source_comment(http)
    comment["issue_url"] = f"https://api.github.com{BASE}/issues/" + "1" * 5000
    assert sa.parse_source(comment, issue, make_config()) is None


def test_edited_source_with_invalid_utf8_is_quarantined_with_original_snapshot():
    http, store = initialized()
    daily_issue(http)
    comment = source_comment(http)
    run_discovery(store)
    original = store.load().entries[101].source
    comment["body"] += "\ud800"
    assert run_discovery(store) == 0
    entry = make_store(FakeHTTP(http.server)).load().entries[101]
    assert entry.status == "needs_attention" and entry.source == original


@pytest.mark.parametrize("field", ["run_id", "generated_at_et"])
def test_source_invalid_unicode_metadata_remains_unknown_and_durably_admissible(field):
    http, store = initialized()
    issue = daily_issue(http)
    value = "run-\ud800" if field == "run_id" else "2026-10-07\ud80014:00:00-04:00"
    comment = source_comment(http, **({"run_id": value} if field == "run_id" else {"generated": value}))
    parsed = sa.parse_source(comment, issue, make_config())
    assert getattr(parsed, field) is None
    assert run_discovery(store) == 1
    assert make_store(FakeHTTP(http.server)).load().entries[101].source == parsed


def test_source_deeply_malformed_metadata_stays_unknown_without_parser_exception():
    http = FakeHTTP()
    issue = daily_issue(http)
    comment = source_comment(http)
    old_metadata = comment["body"].split("\n")[1]
    deeply_nested = "[" * 10000 + "0" + "]" * 10000
    # Establish the decoder boundary rather than inferring it from Python's
    # recursion limit, which is not the JSON decoder's depth limit on 3.13.
    with pytest.raises(RecursionError):
        json.loads(deeply_nested)
    comment["body"] = comment["body"].replace(old_metadata, "<!-- qd-source:" + deeply_nested + " -->")
    parsed = sa.parse_source(comment, issue, make_config())
    assert parsed.generated_at_et is None and parsed.run_id is None
    assert parsed.body == comment["body"]
    assert parsed.body_sha256 == hashlib.sha256(comment["body"].encode()).hexdigest()


def test_oversized_earlier_duplicate_preserves_attempted_canonical_and_review_reason():
    http, store = initialized()
    daily_issue(http)
    source_comment(http, 102, created_at=NOW + timedelta(seconds=2))
    run_discovery(store)
    canonical = replace(store.load().entries[102], status="delivered", attempts=1, attempted_at=NOW)
    store.put(canonical)
    comment = source_comment(http, 101, created_at=NOW + timedelta(seconds=1))
    comment["body"] += "\n" + "x" * 40_000
    assert run_discovery(store) == 0
    entries = make_store(FakeHTTP(http.server)).load().entries
    assert entries[102] == canonical
    assert entries[101].source.body is None and entries[101].canonical_source_id == 102
    assert entries[101].status == "needs_attention" and entries[101].reason.startswith("oversized:")
    assert "earlier" in entries[101].reason and "attempt" in entries[101].reason
    assert run_discovery(store) == 0
    assert make_store(FakeHTTP(http.server)).load().entries == entries


def test_duplicate_attention_annotation_respects_bounded_reason_without_touching_source():
    http, store = initialized()
    daily_issue(http)
    source_comment(http, 102, created_at=NOW + timedelta(seconds=2))
    run_discovery(store)
    store.put(replace(store.load().entries[102], status="delivered", attempts=1))
    comment = source_comment(http, 101, created_at=NOW + timedelta(seconds=1))
    comment["body"] += "\n" + "x" * 40_000
    run_discovery(store)
    original = store.load().entries[101]
    store.put(replace(original, reason="oversized:" + "x" * 246))
    assert run_discovery(store) == 0
    restored = make_store(FakeHTTP(http.server)).load().entries[101]
    assert restored.source == original.source and restored.canonical_source_id == 102
    assert len(restored.reason) <= 256 and restored.reason.startswith("oversized:")
    assert "earlier" in restored.reason and "attempt" in restored.reason


def test_duplicate_record_boundary_retains_earlier_attempt_reason_during_admission():
    from dataclasses import asdict
    http, store = initialized()
    issue = daily_issue(http)
    source_comment(http, 102, created_at=NOW + timedelta(seconds=2))
    run_discovery(store)
    store.put(replace(store.load().entries[102], status="delivered", attempts=1))
    comment = source_comment(http, 101, created_at=NOW + timedelta(seconds=1))
    source = sa.parse_source(comment, issue, store.config)
    size = len(sa._json(sa._encode(asdict(make_entry(source)))).encode())
    comment["body"] += "界" * ((sa.RECORD_BYTES - size - 10) // 3)
    source = sa.parse_source(comment, issue, store.config)
    assert sa._admit(make_entry(source), store.config).source.body is not None
    assert run_discovery(store) == 0
    entry = make_store(FakeHTTP(http.server)).load().entries[101]
    assert entry.status == "needs_attention" and entry.source.body is None
    assert entry.canonical_source_id == 102 and entry.reason.startswith("oversized:")
    assert "earlier" in entry.reason and "attempt" in entry.reason


def test_discovery_decoder_depth_failure_preserves_neighbor_and_advances_cursor():
    http, store = initialized()
    daily_issue(http)
    comment = source_comment(http, 101)
    neighbor = source_comment(http, 102, symbol="TSLA")
    old_metadata = comment["body"].split("\n")[1]
    comment["body"] = comment["body"].replace(
        old_metadata, "<!-- qd-source:" + "[" * 10000 + "0" + "]" * 10000 + " -->")
    http.date = NOW + timedelta(minutes=1)
    assert run_discovery(store) == 2
    restored = make_store(FakeHTTP(http.server)).load()
    assert restored.manifest.cursor == http.date
    assert restored.entries[101].source.generated_at_et is None
    assert restored.entries[101].source.run_id is None
    assert restored.entries[101].source.body == comment["body"]
    assert restored.entries[101].source.body_sha256 == hashlib.sha256(comment["body"].encode()).hexdigest()
    assert restored.entries[102].source.body == neighbor["body"]
    assert restored.entries[101].status == restored.entries[102].status == "pending"


@pytest.mark.parametrize("error", [sa.BudgetExpired(), ProcessStopped()])
def test_source_optional_decoder_does_not_swallow_budget_or_termination(monkeypatch, error):
    http = FakeHTTP()
    issue = daily_issue(http)
    comment = source_comment(http)
    original = json.loads
    def stop_optional_metadata(value, *args, **kwargs):
        if isinstance(value, str) and value.startswith('{"schema":1,'):
            raise error
        return original(value, *args, **kwargs)
    monkeypatch.setattr(sa.json, "loads", stop_optional_metadata)
    with pytest.raises(type(error)):
        sa.parse_source(comment, issue, make_config())


@pytest.mark.parametrize("run_id,retained", [
    ("x" * 256, True), ("x" * 257, False),
    ("🧪" * 64, True), ("🧪" * 65, False),
    ("\x00" * 256, True), ("\x00" * 257, False),
], ids=["ascii-limit", "ascii-over", "utf8-limit", "utf8-over", "escaped-limit", "escaped-over"])
def test_source_run_id_has_utf8_byte_bound_without_changing_snapshot(run_id, retained):
    http, store = initialized()
    issue = daily_issue(http)
    comment = source_comment(http, run_id=run_id)
    source = sa.parse_source(comment, issue, make_config())
    assert source.run_id == (run_id if retained else None)
    assert source.generated_at_et == "2026-10-07T14:00:00-04:00"
    assert source.body == comment["body"]
    assert source.body_sha256 == hashlib.sha256(comment["body"].encode()).hexdigest()
    assert run_discovery(store) == 1
    restored = make_store(FakeHTTP(http.server)).load().entries[101]
    assert restored.source == source and restored.status == "pending"


@pytest.mark.parametrize("fraction_digits,separator,retained", [
    (102, "T", True), (103, "T", False), (99, "🧪", True), (100, "🧪", False),
], ids=["ascii-limit", "ascii-over", "utf8-limit", "utf8-over"])
def test_source_scan_time_has_byte_bound_without_normalizing_snapshot(fraction_digits, separator, retained):
    http, store = initialized()
    issue = daily_issue(http)
    generated = f"2026-10-07{separator}14:00:00." + "1" * fraction_digits + "-04:00"
    assert len(generated.encode()) == 25 + len(separator.encode()) + fraction_digits
    assert datetime.fromisoformat(generated).utcoffset() == timedelta(hours=-4)
    comment = source_comment(http, generated=generated)
    source = sa.parse_source(comment, issue, make_config())
    assert source.generated_at_et == (generated if retained else None)
    assert source.run_id == "12345"
    assert source.body == comment["body"]
    assert source.body_sha256 == hashlib.sha256(comment["body"].encode()).hexdigest()
    assert run_discovery(store) == 1
    restored = make_store(FakeHTTP(http.server)).load().entries[101]
    assert restored.source == source and restored.status == "pending"


def test_source_excessive_parseable_scan_time_still_requires_matching_eastern_date():
    http = FakeHTTP()
    issue = daily_issue(http)
    generated = "2026-10-08T00:00:00." + "1" * 50000 + "-04:00"
    assert datetime.fromisoformat(generated).date().isoformat() == "2026-10-08"
    assert sa.parse_source(source_comment(http, generated=generated), issue, make_config()) is None


@pytest.mark.parametrize("fields", [("run_id",), ("generated_at_et",), ("run_id", "generated_at_et")])
def test_discovery_excessive_optional_metadata_rejection_and_neighbor_are_durable(fields):
    http, store = initialized()
    issue = daily_issue(http)
    run_id = "x" * 50000 if "run_id" in fields else "12345"
    generated = ("2026-10-07T14:00:00." + "1" * 50000 + "-04:00"
                 if "generated_at_et" in fields else "2026-10-07T14:00:00-04:00")
    assert datetime.fromisoformat(generated).utcoffset() == timedelta(hours=-4)
    comment = source_comment(http, run_id=run_id, generated=generated)
    neighbor = source_comment(http, 102, symbol="TSLA")
    expected_source = sa.parse_source(comment, issue, make_config())
    assert expected_source.body == comment["body"]
    assert expected_source.body_sha256 == hashlib.sha256(comment["body"].encode()).hexdigest()
    # Keep both comments inside the next overlap read to exercise idempotence.
    http.date = NOW + timedelta(seconds=30)

    assert run_discovery(store) == 2

    fresh_http = FakeHTTP(http.server)
    fresh_http.date = http.date
    fresh_store = make_store(fresh_http)
    restored = fresh_store.load()
    assert restored.manifest.cursor == http.date
    assert set(restored.entries) == {101, 102}
    rejection = restored.entries[101]
    assert rejection.status == "needs_attention" and rejection.reason.startswith("oversized:")
    assert rejection.source == replace(expected_source, body=None)
    assert rejection.source.body_sha256 == hashlib.sha256(comment["body"].encode()).hexdigest()
    assert rejection.source.html_url == comment["html_url"]
    for field in fields:
        assert getattr(rejection.source, field) is None
    assert restored.entries[102].status == "pending"
    assert restored.entries[102].source.body == neighbor["body"]
    assert all(len(shard["body"].encode()) <= sa.SHARD_BYTES for shard in shard_comments(http))
    assert run_discovery(fresh_store) == 0
    assert fresh_store.load() == restored


def test_discovery_oversized_rejection_with_maximum_optional_metadata_fits_shard():
    http, store = initialized()
    daily_issue(http)
    generated = "2026-10-07T14:00:00." + "1" * 102 + "-04:00"
    comment = source_comment(http, run_id="\x00" * 256, generated=generated)
    comment["body"] += "x" * 50000
    assert run_discovery(store) == 1
    rejection = make_store(FakeHTTP(http.server)).load().entries[101]
    assert rejection.status == "needs_attention" and rejection.source.body is None
    assert rejection.source.run_id == "\x00" * 256
    assert rejection.source.generated_at_et == generated
    assert rejection.source.body_sha256 == hashlib.sha256(comment["body"].encode()).hexdigest()
    assert all(len(shard["body"].encode()) <= sa.SHARD_BYTES for shard in shard_comments(http))


# Task 4: payload and HTTP boundary behavior; all network operations are fake.
def payload_source(**changes):
    http = FakeHTTP()
    issue = daily_issue(http)
    source = sa.parse_source(source_comment(http), issue, make_config())
    if "body" in changes:
        changes["body_sha256"] = hashlib.sha256(changes["body"].encode()).hexdigest()
    return replace(source, **changes)


def payload_snapshot(payload):
    return "".join(block["text"]["text"] for block in payload["blocks"][1:])


def test_payload_preserves_snapshot_without_mention_parsing():
    body = '原文🧪\n"quotes" \\ <@U123> <!here> <!channel> <https://example.com|文字>\n' * 120
    source = payload_source(body=body)
    payload = sa.format_payload(make_entry(source), NOW)
    assert payload_snapshot(payload) == body
    assert all(block["type"] == "section" and block["text"]["type"] == "plain_text"
               and block["text"]["emoji"] is False for block in payload["blocks"])
    assert all(0 < len(block["text"]["text"]) <= 3000 for block in payload["blocks"])
    assert "<!here>" not in payload["text"] and "<!channel>" not in payload["text"]
    assert "<@U123>" not in payload["text"] and "example.com" not in payload["text"]
    assert "channel" not in payload
    assert payload["mrkdwn"] is payload["link_names"] is False
    assert payload["unfurl_links"] is payload["unfurl_media"] is False
    assert "观察用途，不创建或起草订单" in payload["text"]
    assert source.html_url in payload["text"] and "GitHub 评论 ID：101" in payload["text"]
    assert "GitHub 发布时间：2026-10-07T18:00:00+00:00" in payload["text"]
    assert payload["blocks"][0]["text"]["text"] == payload["text"]
    assert len(payload["blocks"]) <= 20
    assert sum(len(block["text"]["text"]) for block in payload["blocks"]) <= 36000


@pytest.mark.parametrize("seconds,attempts,expected", [
    (0, 0, False), (600, 0, False), (601, 0, True), (0, 1, True), (0, 2, True),
])
def test_history_boundary_is_strict_and_retry_is_always_historical(seconds, attempts, expected):
    entry = make_entry(payload_source(), attempts=attempts)
    payload = sa.format_payload(entry, NOW + timedelta(seconds=seconds))
    assert payload["text"].startswith("历史结果／延迟补发，仅供回顾\n") is expected
    assert payload_snapshot(payload) == entry.source.body


@pytest.mark.parametrize("generated", [None, "invalid", "2026-10-07T14:00:00", "x" * 129])
def test_payload_missing_or_invalid_time_is_unknown_without_fabrication(generated):
    entry = make_entry(payload_source(generated_at_et=generated))
    payload = sa.format_payload(entry, NOW)
    assert "原扫描时间未知" in payload["text"]
    assert "原扫描时间：2026" not in payload["text"]
    assert payload_snapshot(payload) == entry.source.body


def test_history_earlier_eastern_day_applies_even_under_ten_minutes_or_unknown_time():
    source = payload_source(event_key="2026-10-07|NVDA|ENTRY_ARMED",
                            generated_at_et="2026-10-07T23:59:00-04:00")
    now = datetime(2026, 10, 8, 4, 1, tzinfo=UTC)
    for generated in (source.generated_at_et, None):
        payload = sa.format_payload(make_entry(replace(source, generated_at_et=generated)), now)
        assert payload["text"].startswith("历史结果／延迟补发，仅供回顾\n")


def test_history_uses_eastern_day_instead_of_utc_day():
    source = payload_source(generated_at_et="2026-10-07T19:59:00-04:00")
    payload = sa.format_payload(make_entry(source), datetime(2026, 10, 8, 0, 1, tzinfo=UTC))
    assert not payload["text"].startswith("历史结果")


@pytest.mark.parametrize("changes", [
    {"state": "<!here>"}, {"symbol": "<@U1>"}, {"html_url": "https://evil.example/secret"},
    {"repo": "someone/else"}, {"body_sha256": "0" * 64},
    {"event_key": "2026-10-07|NVDA|HEARTBEAT"},
])
def test_payload_rejects_unvalidated_source_identity(changes):
    with pytest.raises(ValueError, match="Invalid Slack source"):
        sa.format_payload(make_entry(replace(payload_source(), **changes)), NOW)


def test_payload_visible_text_limit_reserves_retry_prefix_and_never_truncates():
    base = payload_source()
    maximum = 36000 - len(sa.source_metadata_text(base, historical=True))
    source = payload_source(body="x" * maximum)
    admitted = sa._admit(make_entry(source), make_config())
    assert admitted.source.body is not None
    for attempts in (0, 1):
        payload = sa.format_payload(replace(admitted, attempts=attempts), NOW)
        assert payload_snapshot(payload) == source.body
    oversized = payload_source(body="x" * (maximum + 1))
    assert sa._admit(make_entry(oversized), make_config()).source.body is None
    with pytest.raises(sa.PayloadTooLarge):
        sa.format_payload(make_entry(oversized), NOW)
    with pytest.raises(sa.PayloadTooLarge):
        sa.format_payload(sa._admit(make_entry(oversized), make_config()), NOW)


def test_payload_record_byte_limit_matches_admission_without_truncating_unicode():
    source = payload_source(body="🧪" * 11000)
    assert sa._admit(make_entry(source), make_config()).source.body is None
    with pytest.raises(sa.PayloadTooLarge):
        sa.format_payload(make_entry(source), NOW)


@pytest.mark.parametrize("constant,value", [("MAX_BLOCKS", 2), ("BLOCK_TEXT_CHARS", 100)])
def test_payload_block_boundaries_match_shared_admission(monkeypatch, constant, value):
    monkeypatch.setattr(sa, constant, value)
    source = payload_source(body="x" * 3001)
    assert sa._admit(make_entry(source), make_config()).source.body is None
    with pytest.raises(sa.PayloadTooLarge):
        sa.format_payload(make_entry(source), NOW)


WEBHOOK = "https://hooks.slack.com/services/TTEST/BTEST/secret-token"


class WebhookHTTP:
    def __init__(self, response=None, error=None):
        self.response = response or sa.HttpResponse(200, {}, b"ok")
        self.error = error
        self.requests = []

    def request(self, method, url, *, headers, body, timeout):
        self.requests.append((method, url, headers, body, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def webhook_result(status, body, headers=None):
    http = WebhookHTTP(sa.HttpResponse(status, headers or {}, body))
    result = sa.send_webhook(WEBHOOK, {"text": "safe"}, http=http, budget=FakeBudget())
    assert len(http.requests) == 1
    return result


@pytest.mark.parametrize("status,body,kind", [
    (200, b"ok", "accepted"), (200, b" \r\nok\t ", "accepted"),
    (200, b"unexpected", "unknown"), (200, b"OK", "unknown"), (200, b"ok\x00", "unknown"),
    (201, b"ok", "unknown"), (204, b"", "unknown"), (301, b"ok", "unknown"),
    (302, b"ok", "unknown"), (307, b"ok", "unknown"), (308, b"ok", "unknown"),
    (500, b"error", "unknown"), (503, b"error", "unknown"),
    (400, b"invalid_payload", "permanent"), (400, b"channel_is_archived", "permanent"),
    (401, b"error", "permanent"), (403, b"error", "permanent"), (404, b"error", "permanent"),
    (410, b"error", "permanent"), (422, b"error", "permanent"),
])
def test_webhook_result_requires_exact_acceptance_evidence(status, body, kind):
    result = webhook_result(status, body)
    assert result.kind == kind and result.status == status
    assert result.retry_after is None


@pytest.mark.parametrize("raw,seconds,valid", [
    ("0", 0, True), ("5", 5, True), ("300", 300, True), (" 7 ", 7, True),
    (None, 300, False), ("-1", 300, False), ("+1", 300, False),
    ("1.5", 300, False), ("Wed, 07 Oct 2026 18:00:00 GMT", 300, False),
    ("1e3", 300, False), ("", 300, False), ("１２", 300, False), ("1,2", 300, False),
    ("9" * 5000, 300, False),
])
def test_retry_after_accepts_only_nonnegative_integer_seconds(raw, seconds, valid):
    result = webhook_result(429, b"rate_limited", {} if raw is None else {"rEtRy-AfTeR": raw})
    assert result.kind == "retryable" and result.status == 429 and result.retry_after == seconds
    assert ("invalid_or_missing" in result.reason) is not valid


@pytest.mark.parametrize("url", [
    "http://hooks.slack.com/services/T/B/secret", "https://hooks.slack.com.evil.test/services/T/B/secret",
    "https://user:secret@hooks.slack.com/services/T/B/secret", WEBHOOK + "?token=secret",
    WEBHOOK + "#secret", WEBHOOK + "/extra", "https://hooks.slack.com:443/services/T/B/secret",
    "https://hooks.slack.com/services/T//secret", "https://hooks.slack.com/services/T/B/%2Fsecret",
    WEBHOOK + "\n", " https://hooks.slack.com/services/T/B/secret", "https://hooks.slack.com/other/T/B/secret",
])
def test_webhook_rejects_unsafe_credentials_url_without_request_or_secret(url):
    http = WebhookHTTP()
    result = sa.send_webhook(url, {"text": "safe"}, http=http, budget=FakeBudget())
    assert result.kind == "permanent" and result.status is None
    assert http.requests == [] and "secret" not in repr(result)


@pytest.mark.parametrize("remaining,want", [(30, 5), (1.25, 1.25)])
def test_webhook_request_is_single_budget_bounded_json_post_without_github_token(remaining, want):
    http = WebhookHTTP()
    payload = {"text": "观察🧪", "mrkdwn": False}
    result = sa.send_webhook(WEBHOOK, payload, http=http, budget=FakeBudget(seconds=remaining))
    assert result.kind == "accepted" and len(http.requests) == 1
    method, url, headers, body, timeout = http.requests[0]
    assert method == "POST" and url == WEBHOOK and timeout == want
    assert headers["Content-Type"] == "application/json"
    assert all(key.lower() != "authorization" for key in headers)
    assert json.loads(body) == payload and "观察🧪" in body.decode()


def test_webhook_budget_expiration_prevents_request_and_is_not_retryable():
    http = WebhookHTTP()
    with pytest.raises(sa.BudgetExpired):
        sa.send_webhook(WEBHOOK, {}, http=http, budget=FakeBudget(seconds=0))
    assert http.requests == []


@pytest.mark.parametrize("error,kind", [
    (sa.TransportFailure("connect_failure", True), "retryable"),
    (sa.TransportFailure("write_failure", False), "unknown"),
    (TimeoutError(WEBHOOK), "unknown"), (ConnectionError(WEBHOOK), "unknown"),
    (RuntimeError(WEBHOOK), "unknown"), (sa.TransportFailure(WEBHOOK, True), "retryable"),
])
def test_webhook_transport_failures_are_conservative_and_secret_redacted(error, kind, caplog):
    http = WebhookHTTP(error=error)
    result = sa.send_webhook(WEBHOOK, {}, http=http, budget=FakeBudget())
    assert result.kind == kind and result.status is None and len(http.requests) == 1
    assert "secret-token" not in repr(result) + caplog.text and "hooks.slack.com" not in repr(result) + caplog.text


@pytest.mark.parametrize("status", [200, 302, 400, 429, 503])
def test_webhook_response_secret_is_never_echoed(status, caplog):
    result = webhook_result(status, WEBHOOK.encode(), {"Retry-After": WEBHOOK, "Location": WEBHOOK})
    assert "secret-token" not in repr(result) + caplog.text
    assert "hooks.slack.com" not in repr(result) + caplog.text


@pytest.mark.parametrize("error", [sa.BudgetExpired(), ProcessStopped()])
def test_webhook_does_not_swallow_budget_or_process_termination(error):
    with pytest.raises(type(error)):
        sa.send_webhook(WEBHOOK, {}, http=WebhookHTTP(error=error), budget=FakeBudget())


class AdapterResponse:
    def __init__(self, body=b"ok", status=200, headers=None, error=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.error = error
        self.read_sizes = []
        self.length = len(body)
        self.closed = False

    def close(self):
        self.closed = True

    def getheaders(self):
        return list(self.headers.items())

    def read(self, size):
        self.read_sizes.append(size)
        if self.error is not None:
            raise self.error
        chunk = self.body[:size]
        self.length -= len(chunk)
        return chunk


class AdapterConnection:
    def __init__(self, response=None, fail_at=None, error=None):
        self.response = response or AdapterResponse()
        self.fail_at = fail_at
        self.error = error or ConnectionError(WEBHOOK)
        self.events = []
        self.request_args = None

    def _event(self, event):
        self.events.append(event)
        if self.fail_at == event:
            raise self.error

    def connect(self):
        self._event("connect")

    def request(self, method, target, body=None, headers=None):
        self._event("request")
        self.request_args = (method, target, body, headers)

    def getresponse(self):
        self._event("getresponse")
        return self.response

    def close(self):
        self._event("close")


def install_adapter_connection(monkeypatch, connection):
    constructed = []
    def factory(host, *, timeout):
        constructed.append((host, timeout))
        return connection
    monkeypatch.setattr("http.client.HTTPSConnection", factory)
    return constructed


def test_http_adapter_connects_before_write_and_closes_without_following_redirect(monkeypatch):
    connection = AdapterConnection(AdapterResponse(b"redirect", 302, {"Location": "https://evil.example/token"}))
    constructed = install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {"text": "safe"}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == "unknown" and result.status == 302
    assert constructed == [("hooks.slack.com", 5)]
    assert connection.events == ["connect", "request", "getresponse", "close"]
    method, target, body, headers = connection.request_args
    assert method == "POST" and target == "/services/TTEST/BTEST/secret-token"
    assert json.loads(body) == {"text": "safe"} and "Authorization" not in headers


@pytest.mark.parametrize("stage", ["connect", "request", "getresponse", "read"])
@pytest.mark.parametrize("error_type", [ConnectionRefusedError, TimeoutError, RuntimeError])
def test_http_adapter_failure_boundary_is_conservative_and_secret_redacted(monkeypatch, stage, error_type, caplog):
    import traceback
    error = error_type(WEBHOOK + " Bearer fake-token")
    response = AdapterResponse(error=error if stage == "read" else None)
    connection = AdapterConnection(response, fail_at=stage if stage != "read" else None, error=error)
    install_adapter_connection(monkeypatch, connection)
    with pytest.raises(sa.TransportFailure) as caught:
        sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=2)
    assert caught.value.proven_unwritten is (stage == "connect" and error_type is ConnectionRefusedError)
    rendered = "".join(traceback.format_exception(caught.value)) + str(caught.value) + repr(caught.value) + caught.value.code + caplog.text
    assert "secret-token" not in rendered and "fake-token" not in rendered and "hooks.slack.com" not in rendered
    assert connection.events[-1] == "close"
    if stage == "connect":
        assert "request" not in connection.events


def test_http_adapter_generic_urlerror_is_not_unwritten_evidence(monkeypatch):
    from urllib.error import URLError
    connection = AdapterConnection(fail_at="connect", error=URLError(WEBHOOK))
    install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == "unknown"
    assert connection.events == ["connect", "close"]


@pytest.mark.parametrize("stage", ["request", "getresponse", "read"])
def test_http_adapter_post_connect_errors_never_prove_request_unwritten(monkeypatch, stage):
    error = sa.TransportFailure("fixture", True)
    connection = AdapterConnection(AdapterResponse(error=error if stage == "read" else None),
                                   fail_at=stage if stage != "read" else None, error=error)
    install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == "unknown" and connection.events[-1] == "close"


@pytest.mark.parametrize("timeout,want", [(1.25, 1.25), (9, 5)])
def test_http_adapter_bounds_its_timeout(monkeypatch, timeout, want):
    connection = AdapterConnection()
    constructed = install_adapter_connection(monkeypatch, connection)
    response = sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=timeout)
    assert response.body == b"ok" and constructed == [("hooks.slack.com", want)]


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_http_adapter_rejects_invalid_timeout_before_connection(monkeypatch, timeout):
    connection = AdapterConnection()
    constructed = install_adapter_connection(monkeypatch, connection)
    with pytest.raises(sa.TransportFailure):
        sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=timeout)
    assert constructed == [] and connection.events == []


@pytest.mark.parametrize("url", [
    "http://api.github.com/repos/example", "https://user:secret@api.github.com/repos/example",
    "https://api.github.com:443/repos/example", "https://api.github.com/repos/example#token",
    "https://api.github.com/repos/example\n", "https://api.github.com.evil.example/repos/example",
    WEBHOOK + "?token=secret", WEBHOOK + "#secret",
])
def test_http_adapter_rejects_unsafe_targets_before_connection(monkeypatch, url):
    connection = AdapterConnection()
    constructed = install_adapter_connection(monkeypatch, connection)
    with pytest.raises(sa.TransportFailure) as caught:
        sa.StdlibHttpClient().request("GET", url, headers={}, body=None, timeout=5)
    assert "secret" not in str(caught.value) + repr(caught.value) + caught.value.code
    assert constructed == [] and connection.events == []


def test_http_adapter_never_sends_github_authorization_to_slack(monkeypatch):
    connection = AdapterConnection()
    constructed = install_adapter_connection(monkeypatch, connection)
    with pytest.raises(sa.TransportFailure):
        sa.StdlibHttpClient().request("POST", WEBHOOK, headers={"aUtHoRiZaTiOn": "Bearer fake-token"}, body=b"{}", timeout=5)
    assert constructed == [] and connection.events == []


@pytest.mark.parametrize("extra", [0, 1])
def test_http_adapter_response_byte_cap_is_finite_and_enforced(monkeypatch, extra):
    # Exercise the real cap policy with a small injected limit to avoid huge allocation.
    monkeypatch.setattr(sa, "MAX_RESPONSE_BYTES", 100)
    connection = AdapterConnection(AdapterResponse(b"x" * (100 + extra)))
    install_adapter_connection(monkeypatch, connection)
    if extra:
        with pytest.raises(sa.TransportFailure) as caught:
            sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=5)
        assert caught.value.proven_unwritten is False and caught.value.code == "response_too_large"
    else:
        response = sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=5)
        assert response.body == b"x" * 100
    assert connection.response.read_sizes == [101] and connection.events[-1] == "close"


def test_http_adapter_cap_accepts_full_github_page_of_near_limit_json_encoded_shards(monkeypatch):
    from dataclasses import asdict
    records = [sa._encode(asdict(make_entry(payload_source(body="界" * 7000)))) for _ in range(2)]
    store = make_store()
    shard = store._shard_body(0, records, 1, "operation")
    padding = (48 * 1024 - len(shard.encode())) // 3
    records[-1]["source"]["body"] += "界" * padding
    records[-1]["source"]["body_sha256"] = hashlib.sha256(records[-1]["source"]["body"].encode()).hexdigest()
    shard = store._shard_body(0, records, 1, "operation")
    assert 48 * 1024 - 3 < len(shard.encode()) <= 48 * 1024
    server = FakeHTTP()
    for number in range(100):
        server.add_comment(1000 + number, shard)
    body = json.dumps(list(server.server["comments"].values())).encode()
    assert len(body) > 8 * 1024 * 1024
    connection = AdapterConnection(AdapterResponse(body, headers={"Date": format_datetime(NOW, usegmt=True)}))
    constructed = install_adapter_connection(monkeypatch, connection)
    github = sa.GitHubClient(make_config(), "fake-token", sa.StdlibHttpClient(), FakeBudget())
    result, response = github._request("GET", f"{BASE}/issues/{LEDGER_ISSUE}/comments?per_page=100&page=1")
    assert len(result) == 100 and all(item["body"] == shard for item in result)
    assert response.body == body and len(body) <= sa.MAX_RESPONSE_BYTES < 64 * 1024 * 1024
    assert constructed == [("api.github.com", 5)]
    assert connection.request_args[1] == f"{BASE}/issues/{LEDGER_ISSUE}/comments?per_page=100&page=1"
    assert connection.request_args[3]["Authorization"] == "Bearer fake-token"


@pytest.mark.parametrize("error", [sa.BudgetExpired(), ProcessStopped()])
def test_http_adapter_preserves_budget_and_termination_while_closing(monkeypatch, error):
    connection = AdapterConnection(fail_at="request", error=error)
    install_adapter_connection(monkeypatch, connection)
    with pytest.raises(type(error)):
        sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=5)
    assert connection.events == ["connect", "request", "close"]


class WireSocket:
    """Bytes-only socket boundary for the real standard-library HTTP parser."""
    def __init__(self, wire):
        self.wire = wire

    def makefile(self, mode):
        import io
        assert mode == "rb"
        return io.BytesIO(self.wire)


def test_http_adapter_incomplete_content_length_cannot_look_like_slack_acceptance(monkeypatch):
    import http.client
    response = http.client.HTTPResponse(WireSocket(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nok"))
    response.begin()
    connection = AdapterConnection(response)
    install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == "unknown" and result.status is None
    assert connection.events == ["connect", "request", "getresponse", "close"]


@pytest.mark.parametrize("wire,kind", [
    (b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", "accepted"),
    (b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nok", "accepted"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nok\r\n0\r\n\r\n", "accepted"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nok\r\n", "unknown"),
])
def test_http_adapter_real_response_framing_requires_complete_body(monkeypatch, wire, kind):
    import http.client
    response = http.client.HTTPResponse(WireSocket(wire))
    response.begin()
    connection = AdapterConnection(response)
    install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == kind


def test_retry_after_ambiguous_duplicate_headers_use_conservative_fallback(monkeypatch):
    import http.client
    wire = b"HTTP/1.1 429 Rate Limited\r\nContent-Length: 0\r\nRetry-After: 100\r\nRetry-After: 1\r\n\r\n"
    response = http.client.HTTPResponse(WireSocket(wire))
    response.begin()
    connection = AdapterConnection(response)
    install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == "retryable" and result.retry_after == 300
    assert "invalid_or_missing" in result.reason


@pytest.mark.parametrize("payload", [{"text": WEBHOOK + "\ud800"}, {"value": float("nan")}, {"value": object()}])
def test_webhook_unserializable_payload_is_rejected_without_request_or_secret(payload, caplog):
    http = WebhookHTTP()
    result = sa.send_webhook(WEBHOOK, payload, http=http, budget=FakeBudget())
    assert result.kind == "permanent" and result.status is None
    assert result.reason == "invalid_payload" and http.requests == []
    assert "secret-token" not in repr(result) + caplog.text


def test_payload_invalid_utf8_cannot_pass_with_a_missing_digest():
    source = replace(payload_source(), body="unsafe\ud800", body_sha256=None)
    with pytest.raises(ValueError, match="Invalid Slack source"):
        sa.format_payload(make_entry(source), NOW)


def test_payload_exact_record_byte_boundary_survives_durable_attempt_metadata():
    from dataclasses import asdict
    source = payload_source(body="界" * 10000)
    original_size = len(sa._json(sa._encode(asdict(make_entry(source)))).encode())
    source = payload_source(body=source.body + "x" * (40 * 1024 - original_size))
    entry = make_entry(source)
    assert len(sa._json(sa._encode(asdict(entry))).encode()) == 40 * 1024
    http, store = initialized()
    store.put(entry)
    attempted = replace(store.load().entries[101], status="retry_wait", attempts=1,
                        attempt_id="a" * 128, attempted_at=NOW, reason="界" * 256)
    store.put(attempted)
    restored = make_store(FakeHTTP(http.server)).load().entries[101]
    payload = sa.format_payload(restored, NOW)
    assert payload_snapshot(payload) == source.body
    assert payload["text"].startswith("历史结果／延迟补发，仅供回顾\n")
    over = payload_source(body=source.body + "x")
    assert sa._admit(make_entry(over), make_config()).source.body is None
    with pytest.raises(sa.PayloadTooLarge):
        sa.format_payload(make_entry(over), NOW)


@pytest.mark.parametrize("now,generated,expected", [
    (datetime(2026, 11, 1, 6, 5, tzinfo=UTC), "2026-11-01T01:55:00-04:00", False),
    (datetime(2026, 11, 1, 6, 5, 1, tzinfo=UTC), "2026-11-01T01:55:00-04:00", True),
    (datetime(2026, 3, 8, 7, 5, tzinfo=UTC), "2026-03-08T01:55:00-05:00", False),
    (datetime(2026, 3, 8, 7, 5, 1, tzinfo=UTC), "2026-03-08T01:55:00-05:00", True),
])
def test_history_age_uses_elapsed_instants_across_eastern_dst(now, generated, expected):
    source = payload_source(event_key=generated[:10] + "|NVDA|ENTRY_ARMED", generated_at_et=generated)
    payload = sa.format_payload(make_entry(source), now)
    assert payload["text"].startswith("历史结果／延迟补发，仅供回顾\n") is expected


@pytest.mark.parametrize("failure", [None, "oversized", "read"])
def test_http_adapter_closes_response_even_when_connection_no_longer_owns_it(monkeypatch, failure):
    monkeypatch.setattr(sa, "MAX_RESPONSE_BYTES", 100)
    response = AdapterResponse(b"x" * (101 if failure == "oversized" else 2),
                               error=ConnectionError(WEBHOOK) if failure == "read" else None)
    connection = AdapterConnection(response)
    install_adapter_connection(monkeypatch, connection)
    if failure:
        with pytest.raises(sa.TransportFailure):
            sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=5)
    else:
        sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=5)
    assert response.closed and connection.events[-1] == "close"


INVALID_RESPONSE_WIRES = [
    ("conflicting-lengths", b"Content-Length: 2\r\nContent-Length: 5\r\n", b"okNO!"),
    ("duplicate-lengths", b"Content-Length: 2\r\nContent-Length: 2\r\n", b"ok"),
    ("comma-lengths", b"Content-Length: 2, 5\r\n", b"ok"),
    ("negative-length", b"Content-Length: -2\r\n", b"ok"),
    ("signed-length", b"Content-Length: +2\r\n", b"ok"),
    ("nondecimal-length", b"Content-Length: 0x2\r\n", b"ok"),
    ("empty-length", b"Content-Length:\r\n", b"ok"),
    ("conflicting-encodings", b"Transfer-Encoding: chunked\r\nTransfer-Encoding: gzip\r\n", b"2\r\nok\r\n0\r\n\r\n"),
    ("duplicate-encodings", b"Transfer-Encoding: chunked\r\nTransfer-Encoding: chunked\r\n", b"2\r\nok\r\n0\r\n\r\n"),
    ("encoding-and-length", b"Transfer-Encoding: chunked\r\nContent-Length: 2\r\n", b"2\r\nok\r\n0\r\n\r\n"),
    ("unsupported-encoding", b"Transfer-Encoding: gzip\r\n", b"ok"),
    ("empty-encoding", b"Transfer-Encoding:\r\n", b"ok"),
    ("bad-chunk-delimiter", b"Transfer-Encoding: chunked\r\n", b"2\r\nokXX0\r\n\r\n"),
    ("missing-trailer-terminator", b"Transfer-Encoding: chunked\r\n", b"2\r\nok\r\n0\r\n"),
    ("lf-only-trailer-terminator", b"Transfer-Encoding: chunked\r\n", b"2\r\nok\r\n0\r\n\n"),
    ("lf-only-chunk-size", b"Transfer-Encoding: chunked\r\n", b"2\nok\r\n0\r\n\r\n"),
    ("signed-chunk-size", b"Transfer-Encoding: chunked\r\n", b"+2\r\nok\r\n0\r\n\r\n"),
    ("prefixed-chunk-size", b"Transfer-Encoding: chunked\r\n", b"0x2\r\nok\r\n0\r\n\r\n"),
    ("bad-chunk-extension", b"Transfer-Encoding: chunked\r\n", b"2;=missing-name\r\nok\r\n0\r\n\r\n"),
    ("invalid-trailer", b"Transfer-Encoding: chunked\r\n", b"2\r\nok\r\n0\r\nnot-a-field\r\n\r\n"),
    ("framing-in-trailer", b"Transfer-Encoding: chunked\r\n", b"2\r\nok\r\n0\r\nContent-Length: 99\r\n\r\n"),
]


def wire_http_response(headers, body):
    import http.client
    response = http.client.HTTPResponse(WireSocket(b"HTTP/1.1 200 OK\r\n" + headers + b"\r\n" + body))
    response.begin()
    return response


@pytest.mark.parametrize("name,headers,body", INVALID_RESPONSE_WIRES, ids=[item[0] for item in INVALID_RESPONSE_WIRES])
def test_http_adapter_invalid_wire_framing_never_confirms_delivery(monkeypatch, name, headers, body):
    import traceback
    response = wire_http_response(headers, body)
    connection = AdapterConnection(response)
    install_adapter_connection(monkeypatch, connection)
    with pytest.raises(sa.TransportFailure) as caught:
        sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=5)
    assert caught.value.proven_unwritten is False
    assert caught.value.code == "invalid_response_framing"
    assert "secret-token" not in "".join(traceback.format_exception(caught.value))
    assert connection.events == ["connect", "request", "getresponse", "close"]
    assert response.isclosed()

    connection = AdapterConnection(wire_http_response(headers, body))
    install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == "unknown" and result.status is None
    assert connection.events == ["connect", "request", "getresponse", "close"]


@pytest.mark.parametrize("headers,body", [
    (b"Content-Length: 2\r\n", b"ok"),
    (b"Content-Length: 0002\r\n", b"ok"),
    (b"Connection: close\r\n", b"ok"),
    (b"Transfer-Encoding: chunked\r\n", b"1\r\no\r\n1\r\nk\r\n0\r\n\r\n"),
    (b"Transfer-Encoding: CHUNKED\r\n", b"2;name=value;quoted=\"space and \\\"quote\"\r\nok\r\n0;last\r\n\r\n"),
    (b"Transfer-Encoding: chunked\r\n", b"2\r\nok\r\n0\r\nX-Trailer: safe\r\n\r\n"),
])
def test_http_adapter_strict_framing_preserves_valid_response_forms(monkeypatch, headers, body):
    connection = AdapterConnection(wire_http_response(headers, body))
    install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == "accepted"


@pytest.mark.parametrize("chunked", [False, True])
@pytest.mark.parametrize("extra", [0, 1])
def test_http_adapter_strict_framing_preserves_response_cap(monkeypatch, chunked, extra):
    monkeypatch.setattr(sa, "MAX_RESPONSE_BYTES", 100)
    data = b"x" * (100 + extra)
    if chunked:
        headers = b"Transfer-Encoding: chunked\r\n"
        body = hex(len(data))[2:].encode() + b"\r\n" + data + b"\r\n0\r\n\r\n"
    else:
        headers = b"Content-Length: " + str(len(data)).encode() + b"\r\n"
        body = data
    connection = AdapterConnection(wire_http_response(headers, body))
    install_adapter_connection(monkeypatch, connection)
    if extra:
        with pytest.raises(sa.TransportFailure) as caught:
            sa.StdlibHttpClient().request("GET", sa.API + "/repos/example", headers={}, body=None, timeout=5)
        assert caught.value.proven_unwritten is False and caught.value.code == "response_too_large"
    else:
        result = sa.StdlibHttpClient().request("GET", sa.API + "/repos/example", headers={}, body=None, timeout=5)
        assert result.body == data


@pytest.mark.parametrize("headers", [
    b"Content-Length: 2\r\nContent-Length: 5\r\n",
    b"Content-Length: -2\r\n",
    b"Transfer-Encoding: chunked\r\nTransfer-Encoding: gzip\r\n",
    b"Transfer-Encoding: chunked\r\nContent-Length: 2\r\n",
])
def test_http_adapter_rejects_bad_headers_before_consuming_body(monkeypatch, headers):
    response = wire_http_response(headers, b"ok")
    reads = []
    original = response.fp.read
    def read(size=-1):
        reads.append(size)
        return original(size)
    response.fp.read = read
    connection = AdapterConnection(response)
    install_adapter_connection(monkeypatch, connection)
    result = sa.send_webhook(WEBHOOK, {}, http=sa.StdlibHttpClient(), budget=FakeBudget())
    assert result.kind == "unknown" and reads == []


@pytest.mark.parametrize("body", [
    b"2;" + b"x" * 65536 + b"\r\nok\r\n0\r\n\r\n",
    b"2\r\nok\r\n0\r\nX-Trailer: " + b"x" * 65536 + b"\r\n\r\n",
    b"2\r\nok\r\n0\r\n" + b"X-Trailer: safe\r\n" * 101 + b"\r\n",
    b"2\r\nok\r\n0\r\nX-Trailer: value\n\r\n",
])
def test_http_adapter_chunk_framing_metadata_is_bounded_and_crlf_terminated(monkeypatch, body):
    connection = AdapterConnection(wire_http_response(b"Transfer-Encoding: chunked\r\n", body))
    install_adapter_connection(monkeypatch, connection)
    with pytest.raises(sa.TransportFailure) as caught:
        sa.StdlibHttpClient().request("POST", WEBHOOK, headers={}, body=b"{}", timeout=5)
    assert caught.value.code == "invalid_response_framing" and caught.value.proven_unwritten is False


# Task 5: every run constructs fresh clients against the same durable fake server.
class DeliveryHTTP(FakeHTTP):
    def __init__(self, server=None, *, clock=None, latency=0):
        super().__init__(server)
        self.clock = clock or FakeClock(NOW + timedelta(seconds=2))
        self.latency = latency
        self.server.setdefault("slack_posts", [])
        self.slack_results = []
        self.boundary = None
        self.trace = []

    def request(self, method, url, *, headers, body, timeout):
        if self.latency:
            if self.latency > timeout:
                self.clock.sleep(timeout)
                raise sa.BudgetExpired()
            self.clock.sleep(self.latency)
        if url == WEBHOOK:
            stage = "slack_post"
        elif method in {"PATCH", "POST"}:
            raw = json.loads(body)["body"]
            doc = decode_document(raw)
            stage = "manifest" if "manifest" in doc else doc["records"][-1]["status"]
        else:
            stage = "get"
        self.trace.append((stage, "before", url))
        if self.boundary:
            self.boundary(stage, "before", url)
        if url == WEBHOOK:
            # Persisted attempting and channel cooldown must precede the POST.
            ledger = make_store(FakeHTTP(self.server)).load()
            payload = json.loads(body)
            source_id = int(payload["text"].split("GitHub 评论 ID：")[1].split("\n")[0])
            record = ledger.entries[source_id]
            assert record.status == "attempting" and record.attempt_id and record.attempts > 0
            assert ledger.manifest.next_send_at >= self.clock.now()
            self.server["slack_posts"].append((self.clock.now(), payload, source_id))
            response = self.slack_results.pop(0) if self.slack_results else sa.HttpResponse(200, {}, b"ok")
            if isinstance(response, BaseException):
                raise response
        else:
            response = super().request(method, url, headers=headers, body=body, timeout=timeout)
        self.trace.append((stage, "after", url))
        if self.boundary:
            self.boundary(stage, "after", url)
        return response


def delivery_fixture(count=1, *, queued=True):
    http = DeliveryHTTP()
    store = make_store(http)
    store.initialize()
    issue = daily_issue(http)
    for index in range(count):
        source = sa.parse_source(source_comment(http, 101 + index, symbol=f"S{index}",
                                               created_at=NOW + timedelta(seconds=1)), issue, make_config())
        if queued:
            store.put(make_entry(source))
    http.requests.clear()
    http.trace.clear()
    return http


def forward_run(previous, *, seconds=30, now=None, latency=0, setup=None):
    clock = FakeClock(now or previous.clock.now())
    http = DeliveryHTTP(previous.server, clock=clock, latency=latency)
    budget = FakeBudget(clock, seconds)
    store = make_store(http, budget=budget)
    if setup:
        setup(http, store)
    summary = sa.forward(make_config(), store=store, github=store.github,
                         webhook_url=WEBHOOK, http=http, clock=clock, budget=budget)
    return summary, http


def restored(http):
    return make_store(FakeHTTP(http.server)).load()


def test_forward_attempt_is_verified_before_post_and_payload_uses_previous_attempt_count():
    summary, http = forward_run(delivery_fixture())
    record = restored(http).entries[101]
    assert summary.delivered == 1 and summary.pending == 0
    assert record.status == "delivered" and record.attempts == 1
    assert not http.server["slack_posts"][0][1]["text"].startswith(sa.HISTORY_PREFIX)
    assert next(i for i, x in enumerate(http.trace) if x[0] == "attempting") < next(
        i for i, x in enumerate(http.trace) if x[0] == "slack_post")
    summary2, _ = forward_run(http)
    assert summary2.delivered == 0 and len(http.server["slack_posts"]) == 1


@pytest.mark.parametrize("stage,side,queued", [
    ("pending", "before", False), ("pending", "after", False),
    ("attempting", "before", True), ("attempting", "after", True),
    ("slack_post", "before", True), ("slack_post", "after", True),
    ("delivered", "before", True), ("delivered", "after", True),
    ("manifest", "before", True), ("manifest", "after", True),
])
def test_forward_restart_at_every_persistent_send_boundary(stage, side, queued):
    prior = delivery_fixture(queued=queued)
    def setup(http, _):
        def stop(got_stage, got_side, url):
            if (got_stage, got_side) == (stage, side):
                raise ProcessStopped()
        http.boundary = stop
    with pytest.raises(ProcessStopped):
        forward_run(prior, setup=setup)
    before = restored(prior).entries.get(101)
    accepted_before = len(prior.server["slack_posts"])
    _, fresh = forward_run(prior, now=NOW + timedelta(minutes=1))
    record = restored(fresh).entries[101]
    assert len(prior.server["slack_posts"]) <= 1
    if before is not None and before.status == "attempting":
        assert record.status == "unknown"
        assert len(prior.server["slack_posts"]) == accepted_before
    else:
        assert record.status == "delivered"


@pytest.mark.parametrize("after", [False, True])
def test_forward_restart_around_ok_receipt_never_replays(monkeypatch, after):
    prior = delivery_fixture()
    original = sa.send_webhook
    def stopped(*args, **kwargs):
        if after:
            original(*args, **kwargs)
        raise ProcessStopped()
    monkeypatch.setattr(sa, "send_webhook", stopped)
    with pytest.raises(ProcessStopped):
        forward_run(prior)
    monkeypatch.setattr(sa, "send_webhook", original)
    _, fresh = forward_run(prior)
    assert restored(fresh).entries[101].status == "unknown"
    assert len(fresh.server["slack_posts"]) == int(after)


def test_forward_attempt_write_unverified_never_sends():
    prior = delivery_fixture()
    def setup(http, _):
        def unavailable(stage, side, url):
            if stage == "attempting" and side == "after":
                http.fail("GET", urlsplit(url).path)
        http.boundary = unavailable
    summary, http = forward_run(prior, setup=setup)
    assert summary.paused_reason == "ledger_unavailable"
    assert not http.server["slack_posts"]
    _, fresh = forward_run(http)
    assert restored(fresh).entries[101].status == "unknown"
    assert not fresh.server["slack_posts"]


@pytest.mark.parametrize("readback", ["delivered", "attempting", "unavailable"])
def test_forward_accepted_lost_receipt_resolves_without_second_post(readback):
    prior = delivery_fixture()
    def setup(http, _):
        snapshot = None
        def lose(stage, side, url):
            nonlocal snapshot
            if stage == "delivered" and side == "before":
                snapshot = deepcopy(shard_comments(http)[0])
            if stage == "delivered" and side == "after":
                if readback == "attempting":
                    http.server["comments"][snapshot["id"]] = snapshot
                if readback == "unavailable":
                    http.fail("GET", urlsplit(url).path)
                    http.fail("GET", f"{BASE}/issues/{LEDGER_ISSUE}")
                raise sa.TransportFailure("lost-receipt")
        http.boundary = lose
    summary, http = forward_run(prior, setup=setup)
    if readback == "delivered":
        assert summary.delivered == 1
    else:
        assert summary.delivered == 0
    _, fresh = forward_run(http)
    assert restored(fresh).entries[101].status in {"unknown", "delivered"}
    assert len(fresh.server["slack_posts"]) == 1


def test_forward_retry_budget_durable_and_bad_entry_does_not_starve_good():
    prior = delivery_fixture(2)
    def fail_first(http, _):
        http.slack_results = [sa.TransportFailure("connect", True)]
    one, h1 = forward_run(prior, setup=fail_first)
    rec = restored(h1).entries[101]
    assert rec.status == "retry_wait" and rec.attempts == 1
    assert rec.next_attempt_at == NOW + timedelta(seconds=62)
    assert restored(h1).entries[102].status == "delivered" and one.delivered == 1
    _, h2 = forward_run(h1, now=rec.next_attempt_at, setup=fail_first)
    rec2 = restored(h2).entries[101]
    assert rec2.status == "retry_wait" and rec2.attempts == 2
    assert rec2.next_attempt_at == rec.next_attempt_at + timedelta(seconds=300)
    _, h3 = forward_run(h2, now=rec2.next_attempt_at, setup=fail_first)
    rec3 = restored(h3).entries[101]
    assert rec3.status == "needs_attention" and rec3.attempts == 3
    _, h4 = forward_run(h3, now=rec2.next_attempt_at + timedelta(days=1))
    assert len(h4.server["slack_posts"]) == 4
    posts = h4.server["slack_posts"]
    assert min((b[0] - a[0]).total_seconds() for a, b in zip(posts, posts[1:])) >= 1
    assert all(p[1]["text"].startswith(sa.HISTORY_PREFIX) for p in posts if p[2] == 101 and p != posts[0])


@pytest.mark.parametrize("retry_after", [0, 120, 10**100])
def test_forward_429_channel_budget_survives_restart_without_waiting(retry_after):
    prior = delivery_fixture(2)
    def limited(http, _):
        http.slack_results = [sa.HttpResponse(429, {"Retry-After": str(retry_after)}, b"private")]
    summary, http = forward_run(prior, setup=limited)
    assert len(http.server["slack_posts"]) == 1
    state = restored(http)
    rec = state.entries[101]
    assert rec.status == "retry_wait" and rec.next_attempt_at > NOW + timedelta(seconds=2)
    assert state.manifest.next_send_at == rec.next_attempt_at
    assert http.clock.elapsed == 0
    _, fresh = forward_run(http)
    assert len(fresh.server["slack_posts"]) == 1
    if retry_after < 1000:
        _, later = forward_run(fresh, now=rec.next_attempt_at)
        assert restored(later).entries[101].status == "delivered"
        assert restored(later).entries[102].status == "delivered"


@pytest.mark.parametrize("change", ["delete", "body", "author", "identity"])
def test_forward_revalidates_old_source_outside_overlap_before_attempt(change):
    prior = delivery_fixture(2)
    comment = prior.server["comments"][101]
    original = restored(prior).entries[101].source
    if change == "delete":
        del prior.server["comments"][101]
    elif change == "body":
        comment["body"] += "\nEdited outside overlap"
    elif change == "author":
        comment["user"]["id"] = 99
    else:
        comment["html_url"] += "wrong"
    summary, http = forward_run(prior, now=NOW + timedelta(days=2))
    entry = restored(http).entries[101]
    assert entry.status == "needs_attention" and entry.source == original and entry.attempts == 0
    assert summary.delivered == 1
    assert [p[2] for p in http.server["slack_posts"]] == [102]


def test_forward_source_read_failure_skips_entry_and_unknown_does_not_starve_queue():
    prior = delivery_fixture(3)
    store = make_store(prior)
    entry = store.load().entries[101]
    store.put(replace(entry, status="unknown", attempts=1))
    def fail_source(http, _):
        http.fail("GET", f"{BASE}/issues/comments/102")
    summary, http = forward_run(prior, setup=fail_source)
    assert summary.unknown == 1 and summary.pending == 1 and summary.delivered == 1
    assert [p[2] for p in http.server["slack_posts"]] == [103]


def test_forward_budget_too_short_for_safe_request_starts_no_attempt():
    summary, http = forward_run(delivery_fixture(), seconds=0.9)
    assert not http.server["slack_posts"]
    assert restored(http).entries[101].attempts == 0
    assert summary.paused_reason == "budget_exhausted"


def test_forward_corrupt_ledger_stops_whole_run():
    prior = delivery_fixture(2)
    mutate_document(shard_comments(prior)[0], lambda d: d.update(revision="bad"))
    summary, http = forward_run(prior)
    assert summary.paused_reason == "ledger_unavailable" and not http.server["slack_posts"]


def test_forward_discovery_failure_keeps_old_queue_progress_and_cursor():
    prior = delivery_fixture()
    cursor = restored(prior).manifest.cursor
    def fail_discovery(http, _):
        http.fail("GET", f"{BASE}/issues/comments")
    summary, http = forward_run(prior, setup=fail_discovery)
    assert summary.delivered == 1 and restored(http).manifest.cursor == cursor
    assert summary.paused_reason == "discovery_unavailable"


def test_prepare_requires_explicit_initialization_and_never_resets_baseline():
    http = DeliveryHTTP()
    store = make_store(http)
    assert sa.prepare(make_config(), store=store, initialize=False) is False
    assert not [r for r in http.requests if r["method"] != "GET"]
    assert sa.prepare(make_config(), store=store, initialize=True) is True
    baseline = restored(http).manifest
    assert sa.prepare(make_config(), store=make_store(FakeHTTP(http.server)), initialize=True) is True
    assert restored(http).manifest == baseline


def large_delivery_fixture(completed_shards=2400, pending=4):
    """Valid retained shards, each with one completed entry; not store caches."""
    http = delivery_fixture(pending)
    store = make_store(http)
    ledger = store.load()
    records = list(ledger.entries.values())
    # Underfilled shards can result from near-limit snapshots; shard count, not
    # the count of live queue entries, determines pagination/network overhead.
    for index in range(completed_shards):
        source_http = FakeHTTP()
        issue = daily_issue(source_http)
        source = sa.parse_source(source_comment(source_http, 20000 + index, symbol=f"H{index}"),
                                 issue, make_config())
        records.append(make_entry(source, status="delivered", attempts=1,
                                  attempted_at=NOW, delivered_at=NOW, attempt_id=f"old-{index}"))
    for c in shard_comments(http):
        del http.server["comments"][c["id"]]
    for number, entry in enumerate(records):
        body = store._shard_body(number, [sa._encode(sa.asdict(entry))], 1, f"seed-{number}")
        http.add_comment(100000 + number, body)
    http.server["issues"][LEDGER_ISSUE]["body"] = store._manifest_body(
        replace(ledger.manifest, shard_count=len(records)), 2, "seed-manifest")
    for index in range(220):
        http.add_comment(300000 + index, "historical unrelated comment", issue_number=7)
    source_comment(http, 400000, symbol="NEW", created_at=NOW + timedelta(seconds=1))
    return http


def test_forward_budget_large_existing_ledger_makes_progress_before_discovery_and_across_restarts():
    prior = large_delivery_fixture()
    summary, first = forward_run(prior, latency=0.3)
    assert summary.delivered >= 1, (summary, first.clock.elapsed, len(first.requests))
    assert first.clock.elapsed <= 30
    assert first.server["slack_posts"][0][2] == 101
    # No historical traversal may run before at least one old entry is sent.
    discovery = [i for i, r in enumerate(first.trace) if r[2].startswith(sa.API + BASE + "/issues/comments?")]
    first_post = next(i for i, r in enumerate(first.trace) if r[0] == "slack_post")
    assert not discovery or min(discovery) > first_post
    old_count = sum(e.status == "delivered" for e in restored(first).entries.values())
    _, second = forward_run(first, latency=0.3, now=first.clock.now() + timedelta(seconds=60))
    assert sum(e.status == "delivered" for e in restored(second).entries.values()) > old_count
    assert second.clock.elapsed <= 30
    assert len({p[2] for p in second.server["slack_posts"]}) == len(second.server["slack_posts"])


@pytest.mark.parametrize("target", ["manifest", "shard"])
@pytest.mark.parametrize("mutation", ["revision", "content"])
def test_forward_run_snapshot_detects_concurrent_revision_or_content_change(target, mutation):
    prior = delivery_fixture()
    def setup(http, _):
        changed = False
        def tamper(stage, side, url):
            nonlocal changed
            if not changed and side == "after" and url == sa.API + BASE + "/issues/comments/101":
                changed = True
                container = http.server["issues"][LEDGER_ISSUE] if target == "manifest" else shard_comments(http)[0]
                def change(document):
                    if mutation == "revision":
                        document["revision"] += 1
                    elif target == "manifest":
                        document["manifest"]["cursor"] = (NOW + timedelta(seconds=1)).isoformat()
                    else:
                        document["records"][0]["status"] = "unknown"
                mutate_document(container, change)
        http.boundary = tamper
    summary, http = forward_run(prior, setup=setup)
    assert summary.paused_reason == "ledger_unavailable"
    assert not http.server["slack_posts"]


def test_forward_run_snapshot_is_discarded_after_interrupt_and_regular_load_is_fresh():
    prior = delivery_fixture()
    store = make_store(prior)
    original = store.github.http
    def stop(stage, side, url):
        if stage == "attempting" and side == "after":
            raise ProcessStopped()
    prior.boundary = stop
    with pytest.raises(ProcessStopped):
        sa.forward(make_config(), store=store, github=store.github, webhook_url=WEBHOOK,
                   http=prior, clock=prior.clock, budget=store.github.budget)
    prior.boundary = None
    assert store.github.http is original
    assert store.load().entries[101].status == "attempting"
    mutate_document(shard_comments(prior)[0], lambda d: d["records"][0].update(status="unknown"))
    assert store.load().entries[101].status == "unknown"


def cli_env(monkeypatch, tmp_path, **updates):
    values = {"QD_SLACK_ENABLED": "true", "GITHUB_REPOSITORY": REPO,
              "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
              "QD_SLACK_LEDGER_ISSUE": "9", "QD_SLACK_PRODUCER_IDS": "42,43",
              "GITHUB_TOKEN": "github-private-secret", "QD_SLACK_WEBHOOK_URL": WEBHOOK,
              "GITHUB_OUTPUT": str(tmp_path / "outputs"),
              "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}
    values.update(updates)
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return values


@pytest.mark.parametrize("updates", [{"QD_SLACK_ENABLED": None}, {"QD_SLACK_ENABLED": "TRUE"},
    {"GITHUB_REPOSITORY": "fork/repo"}, {"GITHUB_REF": "refs/heads/topic"},
    {"GITHUB_EVENT_NAME": "pull_request"}, {"GITHUB_EVENT_NAME": "pull_request_target"}])
def test_cli_disabled_or_untrusted_guards_precede_any_credential_read(monkeypatch, tmp_path, capsys, updates):
    import os
    cli_env(monkeypatch, tmp_path, **updates)
    original = os.environ.get
    def guarded_get(key, default=None):
        assert key not in {"GITHUB_TOKEN", "GH_TOKEN", "QD_SLACK_WEBHOOK_URL"}
        return original(key, default)
    monkeypatch.setattr(os.environ, "get", guarded_get)
    assert sa.main(["forward", "--budget-seconds", "30"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["paused_reason"] in {"disabled", "untrusted_context"}


@pytest.mark.parametrize("updates,argv", [
    ({"QD_SLACK_LEDGER_ISSUE": "0"}, ["prepare"]),
    ({"QD_SLACK_PRODUCER_IDS": "42,bad"}, ["prepare"]),
    ({"GITHUB_TOKEN": None}, ["prepare"]),
    ({"QD_SLACK_WEBHOOK_URL": None}, ["forward"]),
    ({"GITHUB_EVENT_NAME": "push"}, ["prepare", "--initialize"]),
    ({}, ["forward", "--budget-seconds", "nan"]),
    ({}, ["forward", "--budget-seconds", "31"]),
    ({}, ["forward", "--budget-seconds", "-1"]),
    ({}, ["forward", "--budget-seconds", "SECRET_INVALID"]),
])
def test_cli_invalid_configuration_is_safe_pause(monkeypatch, tmp_path, capsys, updates, argv):
    cli_env(monkeypatch, tmp_path, **updates)
    assert sa.main(argv) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["paused_reason"] == "invalid_configuration"
    assert not output.err
    assert "SECRET_INVALID" not in output.out and "github-private-secret" not in output.out


def test_cli_prepare_outputs_budget_and_scan_time_is_excluded(monkeypatch, tmp_path, capsys):
    values = cli_env(monkeypatch, tmp_path)
    clock = FakeClock()
    http = DeliveryHTTP(clock=clock)
    monkeypatch.setattr(sa, "SystemClock", lambda: clock)
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: http)
    # Eight requests at 0.5 seconds each; measure rather than assuming a count.
    http.latency = 0.5
    assert sa.main(["prepare", "--initialize"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ledger_ready"] is True
    assert data["remaining_budget"] == pytest.approx(30 - clock.elapsed)
    assert 0 < data["remaining_budget"] < 30
    outputs = (tmp_path / "outputs").read_text()
    assert "ledger_ready=true\n" in outputs and "remaining_budget=" in outputs
    before = clock.elapsed
    clock.sleep(600)
    assert sa.main(["forward", "--budget-seconds", str(data["remaining_budget"])]) == 0
    data2 = json.loads(capsys.readouterr().out)
    assert data2["paused_reason"] is None
    assert clock.elapsed - before - 600 < 30
    text = (tmp_path / "summary").read_text()
    assert "Newly confirmed delivered this run" in text
    assert "Current durable queue" in text and "Rejected (subset of needs_attention)" in text
    assert values["GITHUB_TOKEN"] not in text and WEBHOOK not in text


def test_cli_prepare_missing_ledger_reports_false_without_initialization(monkeypatch, tmp_path, capsys):
    cli_env(monkeypatch, tmp_path)
    http = DeliveryHTTP()
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: http)
    assert sa.main(["prepare"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ledger_ready"] is False
    assert "ledger_ready=false" in (tmp_path / "outputs").read_text()
    assert not [r for r in http.requests if r["method"] != "GET"]


def test_cli_linux_hard_budget_interrupts_blocking_http(monkeypatch, tmp_path, capsys):
    import sys
    import time
    import signal
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux hard deadline")
    cli_env(monkeypatch, tmp_path)
    class BlockingHTTP:
        def request(self, *args, **kwargs):
            time.sleep(4)
            raise AssertionError("Hard deadline failed")
    monkeypatch.setattr(sa, "StdlibHttpClient", BlockingHTTP)
    old_handler = signal.getsignal(signal.SIGALRM)
    start = time.monotonic()
    assert sa.main(["forward", "--budget-seconds", "0.05"]) == 0
    assert time.monotonic() - start < 0.7
    assert json.loads(capsys.readouterr().out)["paused_reason"] == "budget_exhausted"
    assert signal.getsignal(signal.SIGALRM) == old_handler
    assert signal.getitimer(signal.ITIMER_REAL) == (0, 0)


def test_cli_hard_budget_in_send_window_recovers_unknown_without_replay(monkeypatch, tmp_path, capsys):
    import time
    cli_env(monkeypatch, tmp_path, QD_SLACK_PRODUCER_IDS="42")
    prior = delivery_fixture()
    class BlockSlack(DeliveryHTTP):
        def request(self, method, url, **kwargs):
            response = super().request(method, url, **kwargs)
            if url == WEBHOOK:
                time.sleep(4)
            return response
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: BlockSlack(prior.server))
    assert sa.main(["forward", "--budget-seconds", "1.1"]) == 0
    assert json.loads(capsys.readouterr().out)["paused_reason"] == "budget_exhausted"
    assert restored(prior).entries[101].status == "attempting"
    _, fresh = forward_run(prior, now=NOW + timedelta(days=1))
    assert restored(fresh).entries[101].status == "unknown"
    assert len(fresh.server["slack_posts"]) == 1


def test_budget_signal_propagates_from_write_readback():
    http, _ = initialized()
    store = make_store(http)
    def expire(request):
        if request["method"] == "POST":
            http.fail("GET", f"{BASE}/issues/{LEDGER_ISSUE}/comments", error=sa.BudgetExpired())
    http.on_request = expire
    with pytest.raises(sa.BudgetExpired):
        store.put(make_entry())


def test_budget_signal_propagates_from_adapter_cleanup(monkeypatch):
    response = AdapterResponse(b"ok")
    def expire():
        raise sa.BudgetExpired()
    response.close = expire
    install_adapter_connection(monkeypatch, AdapterConnection(response))
    with pytest.raises(sa.BudgetExpired):
        sa.send_webhook(WEBHOOK, {"text": "safe"}, http=sa.StdlibHttpClient(), budget=FakeBudget())


@pytest.mark.parametrize("result,status", [
    (sa.HttpResponse(400, {}, b"private-permanent-error"), "needs_attention"),
    (sa.HttpResponse(500, {}, b"private-error"), "unknown"),
    (sa.TransportFailure("uncertain", False), "unknown"),
])
def test_forward_permanent_or_unknown_entry_does_not_starve_neighbor(result, status):
    prior = delivery_fixture(2)
    def setup(http, _):
        http.slack_results = [result]
    summary, http = forward_run(prior, setup=setup)
    assert restored(http).entries[101].status == status
    assert restored(http).entries[102].status == "delivered"
    assert summary.delivered == 1
    assert summary.rejected == (status == "needs_attention")


@pytest.mark.parametrize("side", ["before", "after"])
def test_forward_restart_during_ok_body_inspection_does_not_replay(side):
    class InterruptedOK(bytes):
        def strip(self):
            if side == "before":
                raise ProcessStopped()
            result = super().strip()
            assert result == b"ok"
            raise ProcessStopped()
    prior = delivery_fixture()
    def setup(http, _):
        http.slack_results = [sa.HttpResponse(200, {}, InterruptedOK(b"ok"))]
    with pytest.raises(ProcessStopped):
        forward_run(prior, setup=setup)
    _, fresh = forward_run(prior)
    assert len(fresh.server["slack_posts"]) == 1
    assert restored(fresh).entries[101].status == "unknown"


@pytest.mark.parametrize("side", ["before", "after"])
def test_forward_restart_during_discovery_cursor_update_retains_pending(side):
    prior = delivery_fixture(queued=False)
    original_cursor = restored(prior).manifest.cursor
    def setup(http, _):
        http.date = NOW + timedelta(seconds=10)
        def stop(stage, got_side, url):
            if stage == "manifest" and got_side == side:
                writes = [r for r in http.requests if r["method"] == "PATCH" and r["path"].endswith("/9")]
                # The first manifest update only records the newly created shard.
                if (side == "before" and len(writes) == 1) or (side == "after" and len(writes) == 2):
                    raise ProcessStopped()
        http.boundary = stop
    with pytest.raises(ProcessStopped):
        forward_run(prior, setup=setup)
    state = restored(prior)
    assert state.entries[101].status == "pending" and not prior.server["slack_posts"]
    assert state.manifest.cursor == (original_cursor if side == "before" else NOW + timedelta(seconds=10))
    def advance_server(http, _):
        http.date = NOW + timedelta(seconds=20)
    summary, fresh = forward_run(prior, setup=advance_server)
    assert summary.delivered == 1 and len(fresh.server["slack_posts"]) == 1


def test_forward_oversized_rejection_and_bad_payload_continue_to_healthy(monkeypatch):
    prior = delivery_fixture(3)
    # An oversized rejection is already durable and non-sendable.
    store = make_store(prior)
    entry = store.load().entries[101]
    # Construct it through actual source admission, never weaken load validation.
    huge = entry.source.body + "x" * 50000
    source = replace(entry.source, comment_id=104, symbol="HUGE", event_key="2026-10-07|HUGE|ENTRY_ARMED",
                     body=huge, body_sha256=hashlib.sha256(huge.encode()).hexdigest(),
                     html_url=f"https://github.com/{REPO}/issues/7#issuecomment-104")
    store.put(make_entry(source))
    original = sa.format_payload
    def reject_one(entry, now):
        if entry.source.comment_id == 101:
            raise sa.PayloadTooLarge("untrusted text must not appear")
        return original(entry, now)
    monkeypatch.setattr(sa, "format_payload", reject_one)
    summary, http = forward_run(prior)
    assert summary.delivered == 2 and summary.rejected == 2
    assert restored(http).entries[104].source.body is None
    assert restored(http).entries[101].attempts == 0
    assert [p[2] for p in http.server["slack_posts"]] == [102, 103]


def test_forward_due_order_and_cross_run_send_spacing():
    prior = delivery_fixture(3)
    store = make_store(prior)
    records = store.load().entries
    store.put(replace(records[103], status="retry_wait", attempts=1, next_attempt_at=NOW))
    store.put(replace(records[102], status="retry_wait", attempts=1, next_attempt_at=NOW + timedelta(days=1)))
    summary, first = forward_run(prior, seconds=1.01)
    assert summary.delivered == 1 and first.server["slack_posts"][0][2] == 103
    _, second = forward_run(first)
    posts = second.server["slack_posts"]
    assert [p[2] for p in posts] == [103, 101]
    assert (posts[1][0] - posts[0][0]).total_seconds() >= 1
    assert restored(second).entries[102].attempts == 1


def test_hard_deadline_cannot_disable_alarm_when_budget_expires_while_arming(monkeypatch):
    class ExpiringBudget:
        def require(self, seconds=0):
            pass
        def remaining(self):
            return 0
    with pytest.raises(sa.BudgetExpired):
        with sa._hard_deadline(ExpiringBudget()):
            pytest.fail("Expired budget entered blocking work")


def test_prepare_output_never_rounds_remaining_activity_budget_up(monkeypatch, tmp_path, capsys):
    cli_env(monkeypatch, tmp_path)
    remaining = 25.1234567
    sa._cli_report({"ledger_ready": True, "remaining_budget": remaining, "paused_reason": None}, preparing=True)
    output = (tmp_path / "outputs").read_text()
    exported = float(output.split("remaining_budget=")[1].strip())
    assert exported <= remaining
    assert remaining - exported < 0.000001


def test_forward_initial_load_scale_limit_is_safe_and_diagnostic():
    prior = large_delivery_fixture()
    summary, http = forward_run(prior, latency=1.2)
    assert summary.paused_reason == "budget_exhausted"
    assert summary.delivered == 0 and not http.server["slack_posts"]
    assert http.clock.elapsed == pytest.approx(30)


def test_forward_large_ledger_progress_shares_thirty_seconds_with_prepare():
    prior = large_delivery_fixture()
    preparation_clock = FakeClock(prior.clock.now())
    http = DeliveryHTTP(prior.server, clock=preparation_clock, latency=0.3)
    budget = FakeBudget(preparation_clock)
    assert sa.prepare(make_config(), store=make_store(http, budget=budget), initialize=False)
    prepare_elapsed = preparation_clock.elapsed
    remaining = budget.remaining()
    # This represents the independent scan, excluded from Slack activity time.
    summary, forwarded = forward_run(http, seconds=remaining, latency=0.3,
                                     now=http.clock.now() + timedelta(minutes=5))
    assert summary.delivered >= 1
    assert prepare_elapsed + forwarded.clock.elapsed <= 30 + 1e-9
    assert forwarded.server["slack_posts"][0][2] == 101


@pytest.mark.parametrize("first_status", [200, 400, 500])
@pytest.mark.parametrize("fresh_runner", [False, True])
def test_forward_actual_post_spacing_after_unequal_connect_latency(monkeypatch, first_status, fresh_runner):
    """Use the real adapter; observe writes after its separate connect phase."""
    prior = delivery_fixture(1 if fresh_runner else 2)
    writes = []
    clocks = []
    connections = []

    class DelayedConnection(AdapterConnection):
        def __init__(self, index):
            status = first_status if index == 0 else 200
            super().__init__(AdapterResponse(b"ok" if status == 200 else b"error", status))
            self.index = index

        def connect(self):
            super().connect()
            clocks[-1].sleep(1.5 if self.index == 0 else 0)

        def request(self, method, target, body=None, headers=None):
            assert method == "POST"
            # This is the actual write boundary, not entry into HttpClient.
            writes.append(clocks[-1].now())
            super().request(method, target, body=body, headers=headers)

    def factory(host, *, timeout):
        assert host == "hooks.slack.com"
        connection = DelayedConnection(len(connections))
        connections.append(connection)
        return connection

    monkeypatch.setattr("http.client.HTTPSConnection", factory)

    def run(now):
        clock = FakeClock(now)
        clocks.append(clock)
        budget = FakeBudget(clock)
        # Every call has fresh GitHub/store/Slack transport instances.
        store = make_store(FakeHTTP(prior.server), budget=budget)
        return sa.forward(make_config(), store=store, github=store.github,
                          webhook_url=WEBHOOK, http=sa.StdlibHttpClient(), clock=clock, budget=budget)

    first = run(NOW + timedelta(seconds=2))
    if fresh_runner:
        issue = daily_issue(prior)
        source = sa.parse_source(source_comment(prior, 102, symbol="NEXT", created_at=NOW + timedelta(seconds=1)),
                                 issue, make_config())
        make_store(FakeHTTP(prior.server)).put(make_entry(source))
        second = run(clocks[-1].now())
        assert second.delivered == 1
    else:
        assert first.delivered == (2 if first_status == 200 else 1)
    assert len(writes) == 2
    assert (writes[1] - writes[0]).total_seconds() >= sa.MIN_SEND_INTERVAL, writes
    assert restored(prior).manifest.next_send_at >= clocks[-1].now() + timedelta(seconds=sa.MIN_SEND_INTERVAL)


@pytest.mark.parametrize("retry_after", [0, 120])
def test_forward_actual_post_spacing_retains_429_completion_hold(monkeypatch, retry_after):
    prior = delivery_fixture(2)
    clock = FakeClock(NOW + timedelta(seconds=2))
    writes = []
    connections = []

    class LimitedConnection(AdapterConnection):
        def __init__(self, index):
            super().__init__(AdapterResponse(b"limited" if index == 0 else b"ok",
                                             429 if index == 0 else 200,
                                             {"Retry-After": str(retry_after)} if index == 0 else {}))
            self.index = index

        def connect(self):
            super().connect()
            clock.sleep(1.5 if self.index == 0 else 0)

        def request(self, method, target, body=None, headers=None):
            writes.append(clock.now())
            super().request(method, target, body=body, headers=headers)

    def factory(host, *, timeout):
        connection = LimitedConnection(len(connections))
        connections.append(connection)
        return connection

    monkeypatch.setattr("http.client.HTTPSConnection", factory)

    def run():
        budget = FakeBudget(clock)
        store = make_store(FakeHTTP(prior.server), budget=budget)
        return sa.forward(make_config(), store=store, github=store.github,
                          webhook_url=WEBHOOK, http=sa.StdlibHttpClient(), clock=clock, budget=budget)

    run()
    completed = clock.now()
    gate = restored(prior).manifest.next_send_at
    assert gate == completed + timedelta(seconds=max(1, retry_after))
    assert clock.elapsed == 1.5 and len(writes) == 1
    run()  # Fresh durable state cannot bypass even Retry-After: 0.
    assert clock.now() == completed and len(writes) == 1
    clock = FakeClock(gate)
    run()
    assert len(writes) == 3
    assert all((b - a).total_seconds() >= 1 for a, b in zip(writes, writes[1:]))
