"""Reallocate existing active-time pool without weakening attempt safety."""
import pytest
from market_data import slack_alerts as sa, slack_drain as sd
from test_slack_drain import execute, prepare, forward
from test_slack_deferred_budget import deferred_report


def capacity(**changes):
    return deferred_report(budget_diagnostic={'phase':'pre_attempt_reserve',
        'required_reserve_seconds':35.,'remaining_seconds':26.,'observed_peak_seconds':1.5}) | changes


def test_extended_pair_borrows_only_unused_activity_pool(monkeypatch,capsys):
    _,report,runner = execute(sd,monkeypatch,capsys,[capacity(),prepare(remaining_budget=24.25),
        forward(delivered=1,pending=0,paused_reason=None)])
    assert report['delivered'] == 1 and report['allocated_activity_seconds'] == 90
    assert runner.calls[1][0][4:] == []
    assert runner.calls[2][0][4:] == ['--budget-seconds','54.250000','--extended-slice']


def test_extended_pool_exhaustion_stops_before_prepare(monkeypatch,capsys):
    results=[capacity()]
    for _ in range(3):results += [prepare(),forward()]
    _,report,runner=execute(sd,monkeypatch,capsys,results)
    assert report['paused_reason']=='activity_limit'
    assert report['allocated_activity_seconds']==210
    assert report['slices_started']==4 and len(runner.calls)==7


def test_extended_slice_does_not_grant_second_zero_progress_exception(monkeypatch,capsys):
    _,report,runner=execute(sd,monkeypatch,capsys,[capacity(),prepare(),forward(),prepare(),capacity()])
    assert report['paused_reason']=='no_verified_progress'
    assert report['pre_attempt_continuations']==1 and len(runner.calls)==5


@pytest.mark.parametrize('number',[float('nan'),float('inf'),-1,True,'35',None,60,1000,30])
def test_invalid_or_unfit_reserve_cannot_expand(monkeypatch,capsys,number):
    data=capacity();data['budget_diagnostic']['required_reserve_seconds']=number
    _,report,runner=execute(sd,monkeypatch,capsys,[data,prepare(),forward(pending=0)])
    assert '--extended-slice' not in runner.calls[2][0]


def test_extended_forward_budget_is_explicit_and_bounded():
    clock=sa.SystemClock()
    with pytest.raises(ValueError):sa.MonotonicBudget(clock,31)
    assert sa.MonotonicBudget(clock,60,maximum=60).remaining() <= 60
    with pytest.raises(ValueError):sa.MonotonicBudget(clock,61,maximum=60)


def test_late_job_cannot_start_extended_pair(monkeypatch,capsys):
    # A freshly booted CI runner may have less uptime than this synthetic job.
    monkeypatch.setattr(sd.time, "monotonic", lambda: 1000.0)
    started=sd.time.monotonic() - (sd.JOB_TIMEOUT_SECONDS-sd.ARTIFACT_RESERVE_SECONDS-50)
    _,report,runner=execute(sd,monkeypatch,capsys,[capacity()],job_started=str(started))
    assert report['paused_reason']=='wall_limit' and len(runner.calls)==1
    assert report['allocated_activity_seconds']==30


@pytest.mark.parametrize('changes',[
    {'safe_pending_continuation':False},{'budget_stop_stage':'attempt_write'},
    {'paused_reason':'delivery_uncertain'},{'paused_reason':'ledger_unavailable'},
    {'discovery_deferred':False},{'retry_wait':1}])
def test_extended_allocation_never_bypasses_existing_proofs(monkeypatch,capsys,changes):
    _,report,runner=execute(sd,monkeypatch,capsys,[capacity(**changes)])
    assert report['allocated_activity_seconds']==30 and len(runner.calls)==1


def test_extended_prepare_cannot_spend_twice_its_allowance(monkeypatch,capsys):
    _,report,runner=execute(sd,monkeypatch,capsys,[capacity(),prepare(remaining_budget=31)])
    assert report['paused_reason']=='invalid_prepare_report' and len(runner.calls)==2
