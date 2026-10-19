"""Finite redacted capacity evidence; repeated peaks remain safely bounded."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import pytest
from market_data import slack_alerts as sa, slack_drain as sd
from test_slack_alerts import delivery_fixture, forward_run, restored, FakeClock, FakeBudget, DeliveryHTTP, make_store, make_config
from test_slack_deferred_budget import one_slow_read
from test_slack_drain import execute, prepare


@pytest.mark.parametrize("latency,expected_delivered",[(.1,9),(2.5,0)])
def test_repeated_peak_real_driver_reallocates_or_stops_safely(monkeypatch, capsys,latency,expected_delivered):
    prior = delivery_fixture(10)
    store = make_store(prior)
    old = store.load().entries[110]
    store.put(replace(old,status="unknown",attempts=1,attempt_id="old",attempted_at=prior.clock.now()))
    initial = restored(prior)
    summaries = []
    def child(command, **kwargs):
        nonlocal prior
        if command[3] == 'prepare':
            clock = FakeClock(prior.clock.now())
            http = DeliveryHTTP(prior.server, clock=clock, latency=.1)
            budget = FakeBudget(clock)
            assert sa.prepare(make_config(), store=make_store(http,budget=budget), initialize=False)
            prior = http
            data = prepare(remaining_budget=budget.remaining())
        else:
            summary, prior = forward_run(prior,seconds=float(command[5]),latency=latency,setup=one_slow_read)
            data = asdict(summary)
            summaries.append(data)
        return SimpleNamespace(returncode=0,stdout=json.dumps(data),stderr='')
    code, report, _ = execute(sd,monkeypatch,capsys,[child]*8,first='28.563334')
    print(json.dumps({'summaries':summaries,'drain':report},sort_keys=True))
    assert code == 0
    assert report['paused_reason'] == ('drained' if expected_delivered else 'no_verified_progress')
    assert report['delivered'] == expected_delivered and report['pending'] == 9-expected_delivered
    assert report['slices_started'] == 2 and report['pre_attempt_continuations'] == 1
    assert report['unknown'] == 1 and restored(prior).entries[110] == initial.entries[110]
    assert len(prior.server['slack_posts']) == expected_delivered
    assert report['allocated_activity_seconds'] == (90 if expected_delivered else 60)
    if not expected_delivered:
        assert restored(prior) == initial
    first = summaries[0]['budget_diagnostic']
    assert first['phase'] == 'pre_attempt_reserve'
    assert first['observed_peak_seconds'] == pytest.approx(latency+1.4)
    assert first['required_reserve_seconds'] == pytest.approx(20*(latency+1.4)+5)
    assert first['remaining_seconds'] < 30
    assert first['request_count'] == 4
    assert first['phases']['ledger_load']['request_count'] == 2
    assert first['phases']['source_validation']['request_count'] == 2
    assert first['request_seconds'] == pytest.approx(4*latency+1.4)
    assert report['budget_stops'][0]['diagnostic'] == first



@pytest.mark.parametrize('value',[float('nan'),float('inf'),-1,True,'secret-token',None,{},[]])
def test_numeric_diagnostics_drop_untrusted_values(value):
    raw = {'phase':'pre_attempt_reserve','remaining_seconds':value,
           'required_reserve_seconds':35.,'observed_peak_seconds':1.5,
           'request_count':4,'request_seconds':1.8,
           'phases':{'ledger_load':{'request_count':2,'request_seconds':value},
                     'secret-url':{'request_count':1,'request_seconds':1}},
           'token':'secret-token'}
    safe = sa.safe_budget_diagnostic(raw)
    assert 'remaining_seconds' not in safe
    assert 'token' not in safe and 'secret-url' not in safe['phases']
    assert 'request_seconds' not in safe['phases']['ledger_load']
    assert safe['required_reserve_seconds'] == 35
    assert 'secret' not in json.dumps(safe,allow_nan=False)


@pytest.mark.parametrize('value',[None,[],True,'secret',{'phase':'https://secret'}])
def test_invalid_budget_diagnostic_is_omitted(value):
    assert sa.safe_budget_diagnostic(value) is None


def test_same_run_ledger_loads_are_already_network_free():
    prior = delivery_fixture(3)
    clock = FakeClock(prior.clock.now())
    http = DeliveryHTTP(prior.server,clock=clock,latency=.1)
    timed = sa.TimedHTTP(http,clock)
    store = make_store(http)
    store.github.http = timed
    with store.verified_run():
        first = store.load()
        count = timed.request_count
        assert count == 2
        for _ in range(5):
            assert store.load() is first
        assert timed.request_count == count
        store.refresh()
        assert timed.request_count == count + 2


def test_timed_http_counts_failed_request_without_error_content():
    clock = FakeClock()
    class Failed:
        def request(self,*args,**kwargs):
            clock.sleep(.2)
            raise RuntimeError('secret-token-url')
    timed = sa.TimedHTTP(Failed(),clock)
    timed.phase = 'source_validation'
    with pytest.raises(RuntimeError):
        timed.request('GET','https://secret')
    assert timed.request_count == 1 and timed.request_seconds == pytest.approx(.2)
    assert timed.phases == {'source_validation':{'request_count':1,'request_seconds':.2}}


def test_drain_sanitizes_nested_diagnostic_without_changing_control_flow(monkeypatch,capsys):
    from test_slack_deferred_budget import deferred_report
    raw = {'phase':'pre_attempt_reserve','remaining_seconds':float('nan'),
           'request_count':True,'request_seconds':1.2,'secret':'token',
           'phases':{'receipt':{'request_count':2,'secret':'url'},'https://secret':{}}}
    data = deferred_report(budget_diagnostic=raw)
    _,report,_ = execute(sd,monkeypatch,capsys,[data,prepare(),data])
    assert report['pre_attempt_continuations'] == 1
    assert report['paused_reason'] == 'no_verified_progress'
    assert report['budget_stops'][0]['diagnostic'] == {
        'phase':'pre_attempt_reserve','request_seconds':1.2,'phases':{'receipt':{'request_count':2}}}
    assert 'secret' not in json.dumps(report,allow_nan=False)
