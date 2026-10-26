"""Targeted, network-free recovery gate checks for the Oct 20--26 outage.

Tests use the repository's existing persistent fake GitHub and Slack transports.
They neither send Slack messages nor modify live GitHub issues.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from market_data import slack_alerts as sa, slack_drain
from test_scan_health import publish, report, runtime
from test_slack_alerts import (
    NOW, WEBHOOK, FakeBudget, delivery_fixture, forward_run, make_config,
    make_store, restored,
)


LAG = timedelta(days=6)


def test_stale_cursor_blocks_discovery_and_all_posts_including_new_market_source():
    prior = delivery_fixture(queued=False)
    before = deepcopy(prior.server)
    paused, http = forward_run(prior, now=NOW + LAG)

    assert paused.paused_reason == "recovery_hold"
    assert paused.delivered == 0 and not paused.discovery_complete
    assert http.server == before
    assert http.server["slack_posts"] == []
    assert all(request["method"] == "GET" for request in http.requests)
    assert restored(http).manifest.cursor == NOW


def test_stale_pending_blocks_even_after_cursor_was_advanced_separately():
    prior = delivery_fixture()
    original = make_store(prior)
    entry = original.load().entries[101]
    new_cursor = NOW + LAG
    original.update_manifest(replace(original.load().manifest, cursor=new_cursor))
    assert entry.source.created_at < new_cursor - timedelta(days=4)
    before = deepcopy(prior.server)

    paused, http = forward_run(prior, now=new_cursor)
    assert paused.paused_reason == "recovery_hold"
    assert paused.pending == 1 and paused.delivered == 0
    assert http.server == before
    assert http.server["slack_posts"] == []
    assert restored(http).entries[101] == entry


def test_unknown_attempt_never_reopens_on_outage_recovery():
    prior = delivery_fixture()
    store = make_store(prior)
    existing = store.load().entries[101]
    unknown = replace(existing, status="unknown", attempts=1,
                      attempted_at=NOW, attempt_id="previous-unknown",
                      reason="delivery_unconfirmed")
    store.put(unknown)
    before = deepcopy(prior.server)

    summary, http = forward_run(prior, now=NOW + LAG)
    assert summary.paused_reason == "recovery_hold"
    assert summary.unknown == 1
    assert http.server == before
    assert not http.server["slack_posts"]
    assert restored(http).entries[101] == unknown


def test_recent_first_attempt_delivers_once_and_repeated_job_is_idempotent():
    prior = delivery_fixture()
    first, transport = forward_run(prior, now=NOW + timedelta(seconds=2))
    assert first.delivered == 1 and first.paused_reason is None
    assert transport.server["slack_posts"][0][1]["text"].startswith(
        "ENTRY_ARMED |"
    )
    assert restored(transport).entries[101].status == "delivered"

    second, transport2 = forward_run(transport, now=NOW + timedelta(minutes=3))
    assert second.delivered == 0
    assert len(transport2.server["slack_posts"]) == 1
    assert restored(transport2).entries[101].status == "delivered"


def test_health_is_durable_but_not_silently_sent_during_recovery_hold():
    clock, transport, store = runtime()
    result = publish(report("INCOMPLETE", ("NVDA",), ("TSM",)),
                     clock, transport, store, outcome="failure")
    assert result["published"] == 1
    before = deepcopy(transport.server)
    clock.sleep(LAG.total_seconds())
    fresh = make_store(transport, budget=FakeBudget(clock))
    summary = sa.forward(make_config(), store=fresh, github=fresh.github,
                         webhook_url=WEBHOOK, http=transport,
                         clock=clock, budget=FakeBudget(clock))
    assert summary.paused_reason == "recovery_hold"
    assert summary.delivered == 0
    assert transport.server == before
    assert not transport.server["slack_posts"]
    assert any(comment["body"].startswith("<!-- qd-event:")
               for comment in transport.server["comments"].values())


@pytest.mark.parametrize("hold,expect_hold", [
    (timedelta(days=4), False),
    (timedelta(days=4, seconds=1), True),
])
def test_cursor_lag_boundary_without_attempting_a_post(hold, expect_hold):
    prior = delivery_fixture(queued=False)
    summary, http = forward_run(prior, now=NOW + hold)
    assert (summary.paused_reason == "recovery_hold") is expect_hold
    assert not http.server["slack_posts"]


def test_drain_keeps_specific_recovery_hold_code():
    assert "recovery_hold" in slack_drain.SAFE_PAUSES
