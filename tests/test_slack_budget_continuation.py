"""Offline reproduction: discovery can spend the first slice before any POST."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import pytest
from market_data import slack_alerts as sa, slack_drain as sd
from test_slack_alerts import delivery_fixture, forward_run, restored, make_store, FakeClock, FakeBudget, DeliveryHTTP, make_config
from test_slack_drain import execute, forward, prepare


def test_discovery_budget_pause_is_explicit_and_safe_to_continue():
    summary, http = forward_run(delivery_fixture(queued=False), seconds=15, latency=.3)
    assert summary.delivered == 0 and summary.pending == 1 and summary.discovery_complete
    assert restored(http).entries[101].attempts == 0
    assert not http.server['slack_posts']
    assert summary.budget_stop_stage == 'pre_attempt_reserve'
    assert summary.safe_pending_continuation is True


def test_real_sender_and_drain_deliver_in_same_job_after_discovery_pause(monkeypatch, capsys):
    prior = delivery_fixture(queued=False)
    reports = []
    def child(command, **kwargs):
        nonlocal prior
        if command[3] == 'prepare':
            clock = FakeClock(prior.clock.now())
            http = DeliveryHTTP(prior.server, clock=clock, latency=.3)
            budget = FakeBudget(clock)
            assert sa.prepare(make_config(), store=make_store(http, budget=budget), initialize=False)
            prior = http
            data = prepare(remaining_budget=budget.remaining())
        else:
            summary, prior = forward_run(prior, seconds=float(command[-1]), latency=.3)
            data = asdict(summary)
            reports.append(data)
        return SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr='')
    code, report, runner = execute(sd, monkeypatch, capsys, [child, child, child], first='15')
    print(json.dumps({'slices': reports, 'drain': report}, sort_keys=True))
    assert code == 0 and report['paused_reason'] == 'drained'
    assert report['slices_started'] == 2 and report['delivered'] == 1
    assert len(prior.server['slack_posts']) == 1
    assert restored(prior).entries[101].status == 'delivered'


def safe_report(**changes):
    return forward(delivered=0, budget_stop_stage='pre_attempt_reserve',
                   safe_pending_continuation=True, **changes)


def test_only_one_no_delivery_budget_continuation_is_allowed(monkeypatch, capsys):
    _, report, runner = execute(sd, monkeypatch, capsys, [safe_report(), prepare(), safe_report()])
    assert len(runner.calls) == 3 and report['slices_started'] == 2
    assert report['paused_reason'] == 'no_verified_progress'


@pytest.mark.parametrize('changes', [
    {'budget_stop_stage':'attempt_write'}, {'budget_stop_stage':'post'},
    {'budget_stop_stage':'receipt'}, {'safe_pending_continuation':False},
    {'safe_pending_continuation':1}, {'safe_pending_continuation':'true'},
    {'discovery_complete':False}, {'paused_reason':'delivery_uncertain'},
    {'paused_reason':'channel_hold'}, {'retry_wait':1},
])
def test_unsafe_reports_never_get_zero_delivery_continuation(monkeypatch, capsys, changes):
    data = safe_report() | changes
    _, report, runner = execute(sd, monkeypatch, capsys, [data])
    assert len(runner.calls) == 1


def test_prior_attempt_evidence_excludes_pending_from_safe_continuation():
    prior = delivery_fixture()
    store = make_store(prior)
    old = store.load().entries[101]
    store.put(replace(old, attempt_id='historical-attempt'))
    summary, http = forward_run(prior, seconds=10, latency=.3)
    assert summary.safe_pending_continuation is False
    assert not http.server['slack_posts']


def test_budget_expiry_during_attempt_write_never_authorizes_continuation():
    def setup(http, store):
        def stop(stage, side, url):
            if stage == 'attempting' and side == 'after':
                raise sa.BudgetExpired()
        http.boundary = stop
    summary, http = forward_run(delivery_fixture(), latency=.3, setup=setup)
    assert summary.budget_stop_stage == 'attempt_write'
    assert summary.safe_pending_continuation is False
    assert not http.server['slack_posts']


def test_safe_continuation_still_respects_global_wall_limit(monkeypatch, capsys):
    clock = [0.0]
    monkeypatch.setattr(sd.time, 'monotonic', lambda: clock[0])
    def first(*args, **kwargs):
        clock[0] = sd.MAX_WALL_SECONDS - 1
        return SimpleNamespace(returncode=0, stdout=json.dumps(safe_report()), stderr='')
    _, report, runner = execute(sd, monkeypatch, capsys, [first])
    assert report['paused_reason'] == 'wall_limit' and len(runner.calls) == 1


def test_pending_next_to_historical_unknown_does_not_replay_unknown():
    from test_slack_alerts import make_entry
    prior = delivery_fixture(2, queued=False)
    store = make_store(prior)
    entry = make_entry(sa.parse_source(prior.server["comments"][102],
                                       prior.server["issues"][7], make_config()))
    store.put(replace(entry, status='unknown', attempts=1, attempt_id='old', attempted_at=prior.clock.now()))
    first, prior = forward_run(prior, seconds=10, latency=.3)
    assert first.safe_pending_continuation and first.unknown == 1
    second, prior = forward_run(prior, latency=.3)
    assert second.delivered == 1 and second.unknown == 1
    assert [p[2] for p in prior.server['slack_posts']] == [101]


def test_budget_expiry_during_receipt_is_not_safe_continuation():
    def setup(http, store):
        def stop(stage, side, url):
            if stage == 'delivered' and side == 'before':
                raise sa.BudgetExpired()
        http.boundary = stop
    summary, prior = forward_run(delivery_fixture(), latency=.3, setup=setup)
    assert summary.budget_stop_stage == 'receipt'
    assert not summary.safe_pending_continuation
    _, prior = forward_run(prior)
    assert len(prior.server['slack_posts']) == 1
    assert restored(prior).entries[101].status == 'unknown'


def test_final_drain_report_preserves_budget_stage_that_triggered_continuation(monkeypatch, capsys):
    _, report, _ = execute(sd, monkeypatch, capsys, [safe_report(), prepare(), forward(pending=0, paused_reason=None)])
    assert report['budget_stops'] == [{'slice': 1, 'stage': 'pre_attempt_reserve'}]


def test_post_deadline_reports_stage_and_never_authorizes_continuation():
    def setup(http, store):
        def stop(stage, side, url):
            if stage == 'slack_post' and side == 'after':
                raise sa.BudgetExpired()
        http.boundary = stop
    summary, prior = forward_run(delivery_fixture(), latency=.3, setup=setup)
    assert summary.budget_stop_stage == 'post'
    assert summary.paused_reason == 'delivery_uncertain'
    assert not summary.safe_pending_continuation
    assert restored(prior).entries[101].status == 'unknown'
