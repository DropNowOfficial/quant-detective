from dataclasses import replace
from datetime import timedelta
from market_data import slack_alerts as sa
from test_slack_alerts import NOW, delivery_fixture, forward_run, restored, make_store, source_comment, daily_issue, make_config, make_entry

def test_slow_transactions_remain_pending_instead_of_starting_without_closeout():
    _, http = forward_run(delivery_fixture(), latency=1.2)
    record=restored(http).entries[101]
    assert record.status == 'pending'
    assert record.attempts == 0
    assert http.clock.elapsed <= 30

def test_fairness_survives_a_new_slice_after_two_fresh_deliveries():
    historical=delivery_fixture(1)
    now=NOW+timedelta(minutes=20)
    historical.date=now
    issue=daily_issue(historical)
    st=make_store(historical)
    for sid in [901,902,999]:
        c=source_comment(historical,sid,symbol=f'F{sid}',created_at=now-timedelta(seconds=10),generated=(now-timedelta(seconds=10)).astimezone(sa.ET).isoformat())
        e=make_entry(sa.parse_source(c,issue,make_config()))
        if sid!=999:e=replace(e,status='delivered',attempts=1,attempted_at=now-timedelta(seconds=4-(sid-901)),delivered_at=now-timedelta(seconds=2),attempt_id=str(sid))
        st.put(e)
    _,http=forward_run(historical,now=now,latency=.3)
    assert http.server['slack_posts'][0][2]==101


def test_post_phase_restores_outer_timer_without_extending_deadline(monkeypatch):
    from test_slack_alerts import FakeBudget, FakeClock
    timers=[]; handlers=[]; ticks=iter([100.,102.])
    monkeypatch.setattr(sa.time,'monotonic',lambda:next(ticks))
    monkeypatch.setattr(sa.signal,'getitimer',lambda _: (20.,0.))
    sentinel=object()
    monkeypatch.setattr(sa.signal,'getsignal',lambda _:sentinel)
    monkeypatch.setattr(sa.signal,'signal',lambda sig,handler:handlers.append(handler))
    monkeypatch.setattr(sa.signal,'setitimer',lambda *args:timers.append(args))
    with sa._post_deadline(FakeBudget(FakeClock(),10)):
        pass
    assert timers[0][1]==5
    assert timers[-1][1]==18
    assert handlers[-1] is sentinel


def test_post_timeout_persists_unknown_and_never_replays_with_receipt_reserve():
    from test_slack_alerts import WEBHOOK
    prior=delivery_fixture()
    def setup(http,_):
        def timeout(stage,side,url):
            if url==WEBHOOK and side=='after':
                raise sa.BudgetExpired()
        http.boundary=timeout
    summary,http=forward_run(prior,latency=.3,setup=setup)
    record=restored(http).entries[101]
    assert record.status=='unknown' and record.reason=='post_deadline_unconfirmed'
    assert summary.paused_reason=='delivery_uncertain'
    _,http=forward_run(http,now=NOW+timedelta(minutes=1))
    assert len(http.server['slack_posts'])==1


def test_observed_slack_latency_does_not_inflate_github_closeout_estimate():
    # Real adapter connect-delay regression is also covered by original suite.
    prior=delivery_fixture(2)
    def setup(http,_):
        def delay(stage,side,url):
            if stage=='slack_post' and side=='after':http.clock.sleep(1.5)
        http.boundary=delay
    summary,http=forward_run(prior,latency=.3,setup=setup)
    assert summary.delivered==2


def test_twenty_five_burst_drains_with_retained_shards_within_eight_slices():
    from test_slack_alerts import large_delivery_fixture, DeliveryHTTP, FakeClock, FakeBudget
    prior=large_delivery_fixture(completed_shards=25,pending=0)
    issue=daily_issue(prior)
    for i in range(25):source_comment(prior,101+i,symbol=f'B{i}',created_at=NOW+timedelta(seconds=1))
    total=0
    for _ in range(8):
        clock=FakeClock(prior.clock.now())
        http=DeliveryHTTP(prior.server,clock=clock,latency=.3)
        http.date=clock.now()
        budget=FakeBudget(clock)
        assert sa.prepare(make_config(),store=make_store(http,budget=budget),initialize=False)
        prep=clock.elapsed
        summary,prior=forward_run(http,seconds=budget.remaining(),latency=.3,
                                  now=clock.now(),setup=lambda h,_:setattr(h,'date',clock.now()))
        total+=prep+prior.clock.elapsed
        assert prep+prior.clock.elapsed<=30
        assert not summary.unknown
        if not summary.pending:break
    assert summary.pending==0
    assert len(prior.server['slack_posts'])==26  # burst plus fixture's new source
    assert len({post[2] for post in prior.server['slack_posts']})==26
    assert total<=240


def test_new_uncertainty_survives_later_neighbor_budget_exhaustion():
    prior=delivery_fixture(6)
    def setup(http,_):http.slack_results=[sa.HttpResponse(500,{},b'unknown')]
    summary,http=forward_run(prior,latency=.3,setup=setup)
    assert summary.unknown==1 and summary.delivered>0 and summary.pending>0
    assert summary.paused_reason=='delivery_uncertain'


def test_discovery_error_survives_later_neighbor_budget_exhaustion():
    from test_slack_alerts import BASE
    prior=delivery_fixture(6)
    def setup(http,_):http.fail('GET',f'{BASE}/issues/comments')
    summary,http=forward_run(prior,latency=.3,setup=setup)
    assert summary.delivered>0 and summary.pending>0
    assert summary.paused_reason=='discovery_unavailable'


def test_prior_attempt_evidence_is_not_fresh_even_if_attempt_counter_is_zero():
    entry=restored(delivery_fixture()).entries[101]
    entry=replace(entry,attempted_at=NOW,attempt_id='prior')
    assert not sa._fresh(entry,NOW+timedelta(seconds=2))
