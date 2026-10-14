"""Acceptance regressions for the Oct 13 notification diagnosis.

These are unmodified-production acceptance tests, not xfail tests. The five
known failures document missing behavior until the proposed fix is implemented.
All HTTP I/O uses the repository's persistent fake server. No real messages.
"""
from dataclasses import replace
from datetime import timedelta
import pytest

from market_data import slack_alerts as sa
from test_slack_alerts import (
    NOW, delivery_fixture, forward_run, restored, make_store, source_comment,
    daily_issue, make_config, make_entry,
)


def mixed_backlog(*, fresh_queued):
    prior = delivery_fixture(6)
    now = NOW + timedelta(minutes=20)
    prior.date = now
    issue = daily_issue(prior)
    comment = source_comment(prior, 999, symbol='FRESH', created_at=now,
                             generated=now.astimezone(sa.ET).isoformat())
    if fresh_queued:
        make_store(prior).put(make_entry(sa.parse_source(comment, issue, make_config())))
    return prior, now


def test_current_pending_signal_precedes_historical_backlog():
    prior, now = mixed_backlog(fresh_queued=True)
    _, http = forward_run(prior, now=now, latency=0.3)
    assert http.server['slack_posts'][0][2] == 999
    assert not http.server['slack_posts'][0][1]['text'].startswith(sa.HISTORY_PREFIX)


def test_new_github_signal_is_discovered_before_backlog_exhausts_budget():
    prior, now = mixed_backlog(fresh_queued=False)
    _, http = forward_run(prior, now=now, latency=0.3)
    assert http.server['slack_posts'][0][2] == 999


def test_opening_burst_is_fully_admitted_with_delivery_capacity_left():
    prior = delivery_fixture(25, queued=False)
    prior.date = NOW + timedelta(seconds=2)
    summary, http = forward_run(prior, latency=0.3,
                                setup=lambda http, _: setattr(http, "date", NOW + timedelta(seconds=2)))
    state = restored(http)
    admitted = [sid for sid in state.entries if 101 <= sid <= 125]
    assert len(admitted) == 25
    assert state.manifest.cursor >= NOW + timedelta(seconds=2)
    assert summary.delivered >= 1
    assert http.clock.elapsed <= 30


def test_short_remaining_budget_does_not_start_unfinishable_attempt():
    prior = delivery_fixture()
    _, first = forward_run(prior, seconds=3.5, latency=0.3)
    _, second = forward_run(first, now=NOW + timedelta(minutes=5))
    assert restored(second).entries[101].status == 'delivered'
    assert len(second.server['slack_posts']) == 1


def test_history_label_is_evaluated_after_durable_attempt_writes():
    prior = delivery_fixture()
    _, http = forward_run(prior, now=NOW + timedelta(seconds=599), latency=0.3)
    actual_write, payload, _ = http.server['slack_posts'][0]
    assert (actual_write-NOW).total_seconds() > 600
    assert payload['text'].startswith(sa.HISTORY_PREFIX)


@pytest.mark.parametrize('seconds,historical', [(0,False),(600,False),(600.001,True),(2400,True)])
def test_history_age_boundary_preserves_source_identity(seconds,historical):
    prior = delivery_fixture()
    record = restored(prior).entries[101]
    payload = sa.format_payload(record, NOW + timedelta(seconds=seconds))
    assert payload['text'].startswith(sa.HISTORY_PREFIX) is historical
    assert 'GitHub 评论 ID：101' in payload['text']
    assert record.source.body == ''.join(b['text']['text'] for b in payload['blocks'][1:])


def test_retry_payload_is_historical_even_when_recent():
    record = restored(delivery_fixture()).entries[101]
    record = replace(record, status='retry_wait', attempts=1, attempted_at=NOW,
                     attempt_id='previous-attempt', next_attempt_at=NOW)
    assert sa.format_payload(record,NOW)['text'].startswith(sa.HISTORY_PREFIX)


def test_previous_day_payload_is_historical():
    record = restored(delivery_fixture()).entries[101]
    assert sa.format_payload(record,NOW+timedelta(days=1))['text'].startswith(sa.HISTORY_PREFIX)


@pytest.mark.parametrize('status',['delivered','unknown','attempting'])
def test_fresh_process_does_not_replay_confirmed_or_uncertain_attempt(status):
    prior = delivery_fixture()
    store = make_store(prior)
    record = store.load().entries[101]
    store.put(replace(record,status=status,attempts=1,attempt_id='persisted-attempt',
                      attempted_at=NOW,delivered_at=NOW if status=='delivered' else None,
                      reason='interrupted_attempt_unconfirmed' if status=='unknown' else None))
    _, first = forward_run(prior, now=NOW+timedelta(minutes=5))
    _, second = forward_run(first, now=NOW+timedelta(minutes=10))
    assert second.server['slack_posts'] == []
    assert restored(second).entries[101].status == ('delivered' if status=='delivered' else 'unknown')


def test_duplicate_github_source_has_one_canonical_slack_post():
    prior = delivery_fixture(queued=False)
    source_comment(prior,102,symbol='S0',created_at=NOW+timedelta(seconds=2))
    prior.date = NOW+timedelta(seconds=3)
    _, first = forward_run(prior,now=NOW+timedelta(seconds=3))
    _, second = forward_run(first,now=NOW+timedelta(minutes=5))
    assert [p[2] for p in second.server['slack_posts']] == [101]
    assert restored(second).entries[102].status == 'duplicate_source'


def test_429_channel_hold_blocks_neighbors_and_survives_restart():
    prior = delivery_fixture(2)
    def limited(http,_):
        http.slack_results=[sa.HttpResponse(429,{'Retry-After':'120'},b'rate_limited')]
    _, first=forward_run(prior,setup=limited)
    state=restored(first)
    assert len(first.server['slack_posts'])==1
    retry=state.entries[first.server['slack_posts'][0][2]]
    assert retry.status=='retry_wait' and state.manifest.next_send_at==retry.next_attempt_at
    _, second=forward_run(first)
    assert len(second.server['slack_posts'])==1
    _, third=forward_run(second,now=retry.next_attempt_at)
    assert all(e.status=='delivered' for e in restored(third).entries.values())
    assert len(third.server['slack_posts'])==3  # one explicit 429, two accepted posts


def test_ambiguous_slack_response_is_quarantined_without_replay():
    prior=delivery_fixture()
    def uncertain(http,_):
        http.slack_results=[sa.TransportFailure('fixture_timeout',proven_unwritten=False)]
    _, first=forward_run(prior,setup=uncertain)
    assert restored(first).entries[101].status=='unknown'
    _, second=forward_run(first,now=NOW+timedelta(minutes=5))
    assert len(second.server['slack_posts'])==1
