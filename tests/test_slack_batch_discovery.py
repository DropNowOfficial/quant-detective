"""Batched persistence keeps the same guarded, crash-conservative ledger contract."""
from dataclasses import replace
from datetime import timedelta

import pytest

from market_data import slack_alerts as sa
from test_slack_alerts import (
    BASE, LEDGER_ISSUE, NOW, FakeBudget, FakeClock, FakeHTTP, ProcessStopped,
    daily_issue, decode_document, initialized, make_entry, make_source, make_store,
    mutate_document, run_discovery, shard_comments, source_comment,
)


def writes(http):
    return [request for request in http.requests if request["method"] != "GET"]


def shard_writes(http):
    return [request for request in writes(http) if "/comments" in request["path"]]


def test_put_many_roundtrip_writes_each_touched_shard_once_and_noop_is_read_only():
    http, store = initialized()
    entries = [make_entry(make_source(101 + index)) for index in range(25)]
    http.requests.clear()
    store.put_many(entries)
    restored = make_store(FakeHTTP(http.server)).load()
    assert restored.entries == {entry.source.comment_id: entry for entry in entries}
    assert len(shard_writes(http)) == len(shard_comments(http)) < len(entries)
    assert all(request["method"] == "POST" for request in shard_writes(http))
    assert all(len(comment["body"].encode()) <= sa.SHARD_BYTES
               and len(decode_document(comment["body"])["records"]) <= sa.SHARD_RECORDS
               for comment in shard_comments(http))

    changed = [replace(entry, status="needs_attention", reason="\x00" * 256,
                       attempt_id="\x00" * 128, attempts=3, next_attempt_at=NOW,
                       attempted_at=NOW, delivered_at=NOW,
                       canonical_source_id=9223372036854775807) for entry in entries]
    http.requests.clear()
    store.put_many(changed)
    assert len(shard_writes(http)) == len(shard_comments(http))
    assert all(request["method"] == "PATCH" for request in shard_writes(http))
    assert make_store(FakeHTTP(http.server)).load().entries == {
        entry.source.comment_id: entry for entry in changed}
    assert all(len(comment["body"].encode()) <= sa.SHARD_BYTES for comment in shard_comments(http))
    http.requests.clear()
    store.put_many(changed)
    assert writes(http) == []


@pytest.mark.parametrize("invalid", ["immutable", "duplicate_id", "malformed"])
def test_put_many_validates_entire_input_before_first_mutation(invalid):
    http, store = initialized()
    store.put(make_entry())
    entries = [make_entry(make_source(102))]
    if invalid == "immutable":
        entries.append(make_entry(make_source(101, body="replacement")))
    elif invalid == "duplicate_id":
        entries.append(replace(entries[0], status="unknown"))
    else:
        entries.append(make_entry(make_source(103), status="invalid"))
    http.requests.clear()
    with pytest.raises(sa.LedgerUnavailable):
        store.put_many(entries)
    assert writes(http) == []
    assert make_store(FakeHTTP(http.server)).load().entries == {101: make_entry()}


@pytest.mark.parametrize("target", ["manifest", "shard"])
@pytest.mark.parametrize("field", ["author", "id", "body", "revision"])
def test_put_many_preserves_manifest_and_touched_shard_guards(target, field):
    http, store = initialized()
    store.put(make_entry())
    with store.verified_run():
        store.load()
        item = (http.server["issues"][LEDGER_ISSUE] if target == "manifest"
                else shard_comments(http)[0])
        if field == "author":
            item["user"]["id"] = 999
        elif field == "id":
            item["number" if target == "manifest" else "id"] += 1
        elif field == "body":
            item["body"] += " "
        else:
            mutate_document(item, lambda document: document.update(revision=document["revision"] + 1))
        http.requests.clear()
        with pytest.raises(sa.LedgerUnavailable):
            store.put_many([replace(make_entry(), status="needs_attention", reason="review")])
        assert writes(http) == []


@pytest.mark.parametrize("existing", [False, True])
def test_put_many_requires_exact_authoritative_readback(existing):
    http, store = initialized()
    if existing:
        store.put(make_entry())
    mutated = False
    def corrupt_after_write(request):
        nonlocal mutated
        if request["method"] in {"POST", "PATCH"} and "/comments" in request["path"]:
            mutated = True
        elif mutated and request["method"] == "GET":
            mutate_document(shard_comments(http)[0],
                            lambda document: document["records"][0].update(reason="external change"))
            mutated = False
    http.on_request = corrupt_after_write
    with pytest.raises(sa.LedgerUncertain):
        store.put_many([replace(make_entry(), status="needs_attention", reason="review")])
    assert len(shard_writes(http)) == (2 if existing else 1)


@pytest.mark.parametrize("target", ["shard", "manifest"])
@pytest.mark.parametrize("after", [False, True])
def test_put_many_interruption_recovers_without_multiple_orphan_shards(target, after):
    http, store = initialized()
    entries = [make_entry(make_source(101 + index)) for index in range(40)]
    method = "POST" if target == "shard" else "PATCH"
    path = f"{BASE}/issues/{LEDGER_ISSUE}" + ("/comments" if target == "shard" else "")
    http.fail(method, path, after=after, error=ProcessStopped())
    with pytest.raises(ProcessStopped):
        store.put_many(entries)
    raw_count = decode_document(http.server["issues"][LEDGER_ISSUE]["body"])["manifest"]["shard_count"]
    assert len(shard_comments(http)) <= raw_count + 1
    restored = make_store(FakeHTTP(http.server)).load()
    assert restored.manifest.cursor == NOW
    assert set(restored.entries).issubset({entry.source.comment_id for entry in entries})
    make_store(FakeHTTP(http.server)).put_many(entries)
    complete = make_store(FakeHTTP(http.server)).load()
    assert complete.entries == {entry.source.comment_id: entry for entry in entries}
    assert complete.manifest.shard_count == len(shard_comments(http))


def test_discovery_admits_25_source_burst_within_shared_30_second_budget():
    http, _ = initialized()
    daily_issue(http)
    for index in range(25):
        source_comment(http, 101 + index, symbol=f"S{index}")
    http.date = NOW + timedelta(minutes=1)
    clock = FakeClock()
    budget = FakeBudget(clock)
    store = make_store(http, budget=budget)
    http.requests.clear()
    http.on_request = lambda request: clock.sleep(0.65)
    with store.verified_run():
        assert run_discovery(store, clock=clock, budget=budget) == 25
    restored = make_store(FakeHTTP(http.server)).load()
    assert set(restored.entries) == set(range(101, 126))
    assert all(entry.status == "pending" for entry in restored.entries.values())
    assert restored.manifest.cursor == http.date
    assert clock.elapsed < 30
    assert len(shard_writes(http)) == len(shard_comments(http))


@pytest.mark.parametrize("after", [False, True])
def test_discovery_retires_later_shard_root_before_promoting_earlier_shard(after):
    http, store = initialized()
    issue = daily_issue(http)
    early = source_comment(http, 101, created_at=NOW + timedelta(seconds=1))
    late = source_comment(http, 102, created_at=NOW + timedelta(seconds=2))
    early["body"] += "\n" + "x" * 30000
    late["body"] += "\n" + "y" * 30000
    early_entry = make_entry(sa.parse_source(early, issue, store.config),
                             status="duplicate_source", canonical_source_id=102)
    late_entry = make_entry(sa.parse_source(late, issue, store.config))
    store.put(early_entry)
    store.put(late_entry)
    assert len(shard_comments(http)) == 2
    early_shard, late_shard = shard_comments(http)
    http.date = NOW + timedelta(minutes=1)
    http.requests.clear()
    # Interrupt replacement, after the old root should already be retired.
    http.fail("PATCH", f"{BASE}/issues/comments/{early_shard['id']}",
              after=after, error=ProcessStopped())
    with pytest.raises(ProcessStopped):
        run_discovery(store)
    restored = make_store(FakeHTTP(http.server)).load()
    assert restored.entries[102].status == "duplicate_source"
    assert restored.entries[102].canonical_source_id == 101
    assert restored.entries[101].status == ("pending" if after else "duplicate_source")
    assert restored.manifest.cursor == NOW
    assert [request["path"] for request in shard_writes(http)] == [
        f"{BASE}/issues/comments/{late_shard['id']}",
        f"{BASE}/issues/comments/{early_shard['id']}"]
    run_discovery(make_store(http))
    completed = make_store(FakeHTTP(http.server)).load()
    assert [source_id for source_id, entry in completed.entries.items() if entry.status == "pending"] == [101]
    assert completed.manifest.cursor == http.date


def test_forward_refreshes_retired_root_after_partial_discovery_cursor_failure():
    from test_slack_alerts import DeliveryHTTP, forward_run, restored

    prior = DeliveryHTTP()
    store = make_store(prior)
    store.initialize()
    issue = daily_issue(prior)
    late = source_comment(prior, 102, created_at=NOW + timedelta(seconds=2))
    store.put(make_entry(sa.parse_source(late, issue, store.config)))
    source_comment(prior, 101, created_at=NOW + timedelta(seconds=1))
    failures = []

    def fail_cursor_guard(http, _):
        http.date = NOW + timedelta(seconds=2)
        promoted = False

        def fail(request):
            nonlocal promoted
            if request["method"] == "PATCH" and "/issues/comments/" in request["path"]:
                records = decode_document(request["document"]["body"])["records"]
                promoted |= any(record["source"]["comment_id"] == 101 and record["status"] == "pending"
                                for record in records)
            elif promoted and not failures and request["method"] == "GET" and request["path"] == f"{BASE}/issues/{LEDGER_ISSUE}":
                failures.append("cursor_guard")
                raise sa.TransportFailure("cursor_guard_read_failed", True)

        http.on_request = fail

    summary, http = forward_run(prior, setup=fail_cursor_guard)
    state = restored(http)
    assert failures == ["cursor_guard"]
    assert state.manifest.cursor == NOW
    assert state.entries[102].status == "duplicate_source"
    assert state.entries[102].canonical_source_id == 101
    assert state.entries[102].attempts == 0
    assert [post[2] for post in http.server["slack_posts"]] == [101]
    assert summary.delivered == 1 and summary.paused_reason == "discovery_unavailable"


def test_reserved_discovery_defers_without_mutation_and_restores_request_budget():
    http, store = initialized()
    daily_issue(http)
    for index in range(25):
        source_comment(http, 101 + index, symbol=f"S{index}")
    http.date = NOW + timedelta(minutes=1)
    clock = FakeClock()
    budget = FakeBudget(clock)
    store = make_store(http, budget=budget)
    with store.verified_run():
        before = store.load()
        http.requests.clear()
        http.on_request = lambda request: clock.sleep(0.65)
        with pytest.raises(sa.DiscoveryDeferred):
            sa.discover(store, store.github, clock, budget, reserve_send_seconds=29)
        assert writes(http) == []
        assert store.load() == before
        assert before.manifest.cursor == NOW and before.entries == {}
        assert budget.remaining() > 0
        assert store.github.budget is budget
        http.on_request = None
        assert run_discovery(store, clock=clock, budget=budget) == 25
    complete = make_store(FakeHTTP(http.server)).load()
    assert complete.manifest.cursor == http.date
    assert set(complete.entries) == set(range(101, 126))


def test_reserved_discovery_releases_read_reserve_before_batched_persistence():
    http, _ = initialized()
    daily_issue(http)
    for index in range(25):
        source_comment(http, 101 + index, symbol=f"S{index}")
    http.date = NOW + timedelta(minutes=1)
    clock = FakeClock()
    budget = FakeBudget(clock)
    store = make_store(http, budget=budget)
    http.on_request = lambda request: clock.sleep(0.65)
    with store.verified_run():
        assert sa.discover(store, store.github, clock, budget, reserve_send_seconds=24) == 25
    assert store.github.budget is budget
    assert 6 < clock.elapsed < 30
    complete = make_store(FakeHTTP(http.server)).load()
    assert complete.manifest.cursor == http.date
    assert set(complete.entries) == set(range(101, 126))
