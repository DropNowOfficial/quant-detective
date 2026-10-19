"""Read-only discovery deferral is not an unverified/failed discovery write."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import pytest
from market_data import slack_alerts as sa, slack_drain as sd
from test_slack_alerts import (delivery_fixture, forward_run, restored, FakeClock,
    FakeBudget, DeliveryHTTP, make_store, make_config, BASE)
from test_slack_drain import execute, forward, prepare


def one_slow_read(http, store):
    hit = False
    def delay(stage, side, url):
        nonlocal hit
        if stage == 'get' and side == 'after' and not hit:
            hit = True
            http.clock.sleep(1.4)
    http.boundary = delay


def test_deferred_discovery_proves_safe_pending_without_advancing_cursor():
    prior = delivery_fixture(10)
    cursor = restored(prior).manifest.cursor
    summary, http = forward_run(prior, seconds=28.563334, latency=.1, setup=one_slow_read)
    assert summary.delivered == 0 and summary.pending == 10
    assert not summary.discovery_complete
    assert summary.budget_stop_stage == 'pre_attempt_reserve'
    assert http.clock.elapsed < 3 and not http.server['slack_posts']
    assert restored(http).manifest.cursor == cursor
    assert summary.discovery_deferred is True
    assert summary.safe_pending_continuation is True


def test_real_sender_drain_resumes_verified_queue_after_transient_read_peak(monkeypatch,capsys):
    prior = delivery_fixture(10)
    # Historical unknown stays quarantined even while safe pending neighbors drain.
    store = make_store(prior)
    old = store.load().entries[110]
    store.put(replace(old,status='unknown',attempts=1,attempt_id='old',attempted_at=prior.clock.now()))
    summaries=[]
    def child(command,**kwargs):
        nonlocal prior
        if command[3]=='prepare':
            clock=FakeClock(prior.clock.now());http=DeliveryHTTP(prior.server,clock=clock,latency=.1)
            budget=FakeBudget(clock)
            assert sa.prepare(make_config(),store=make_store(http,budget=budget),initialize=False)
            prior=http;data=prepare(remaining_budget=budget.remaining())
        else:
            summary,prior=forward_run(prior,seconds=float(command[5]),latency=.1,
                                     setup=one_slow_read if not summaries else None)
            data=asdict(summary);summaries.append(data)
        return SimpleNamespace(returncode=0,stdout=json.dumps(data),stderr='')
    code,report,runner=execute(sd,monkeypatch,capsys,[child]*15,first='28.563334')
    print(json.dumps({'slices':summaries,'drain':report},sort_keys=True))
    assert code==0 and report['paused_reason']=='drained'
    assert report['delivered']==9 and report['pending']==0 and report['unknown']==1
    assert report['pre_attempt_continuations']==1 and report['slices_started']==2
    assert len(prior.server['slack_posts'])==9
    assert all(post[2]!=110 for post in prior.server['slack_posts'])


def deferred_report(**changes):
    return forward(delivered=0,discovery_complete=False,discovery_deferred=True,
                   budget_stop_stage='pre_attempt_reserve',safe_pending_continuation=True)|changes


def test_repeated_deferred_capacity_failure_gets_only_one_extra_slice(monkeypatch,capsys):
    _,report,runner=execute(sd,monkeypatch,capsys,[deferred_report(),prepare(),deferred_report()])
    assert len(runner.calls)==3 and report['pre_attempt_continuations']==1
    assert report['paused_reason']=='no_verified_progress'


@pytest.mark.parametrize('changes',[
    {'discovery_deferred':False},{'discovery_deferred':None},{'discovery_deferred':1},
    {'discovery_deferred':'true'},{'discovery_complete':True},
    {'paused_reason':'discovery_unavailable'},{'paused_reason':'ledger_unavailable'},
    {'paused_reason':'delivery_uncertain'},{'paused_reason':'channel_hold'},
    {'retry_wait':1},{'safe_pending_continuation':False},{'budget_stop_stage':'attempt_write'},
])
def test_unsafe_incomplete_reports_never_renew_budget(monkeypatch,capsys,changes):
    _,report,runner=execute(sd,monkeypatch,capsys,[deferred_report(**changes)])
    assert len(runner.calls)==1


def test_actual_discovery_failure_is_not_safe_deferral():
    def setup(http,store):http.fail('GET',BASE+'/issues/comments')
    summary,http=forward_run(delivery_fixture(10),latency=.3,setup=setup)
    assert summary.paused_reason=='discovery_unavailable'
    assert summary.discovery_deferred is False
    assert summary.safe_pending_continuation is False
