import json
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from market_data.github_fallback_dispatch import run_once


class Response:
    def __init__(self, payload=None, status=200):
        self.payload=b"" if payload is None else json.dumps(payload).encode()
        self.status=status
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return self.payload


def run_row(
    *,
    event,
    status,
    conclusion=None,
    created_at="2026-10-05T23:37:00Z",
    updated_at="2026-10-05T23:38:00Z",
    run_id=99,
):
    return {
        "id":run_id,
        "event":event,
        "created_at":created_at,
        "run_started_at":created_at if status!="queued" else None,
        "updated_at":updated_at,
        "status":status,
        "conclusion":conclusion,
        "html_url":f"https://github.com/owner/repo/actions/runs/{run_id}",
    }


def opener_with(*, schedule_runs=None, dispatch_runs=None, dispatch_response=None, fail_event=None):
    schedule_runs=list(schedule_runs or [])
    dispatch_runs=list(dispatch_runs or [])
    calls=[]

    def opener(req, timeout=None):
        calls.append((req.method, req.full_url, req.data, dict(req.header_items())))
        parsed=urlparse(req.full_url)
        if "/runs" in parsed.path:
            event=parse_qs(parsed.query).get("event", [None])[0]
            if fail_event==event:
                raise URLError(f"{event} unavailable")
            rows=schedule_runs if event=="schedule" else dispatch_runs if event=="workflow_dispatch" else []
            return Response({"workflow_runs":rows,"total_count":len(rows)})
        if parsed.path.endswith("/dispatches") and req.method=="POST":
            return Response(dispatch_response if dispatch_response is not None else {
                "workflow_run_id":123,
                "html_url":"https://github.com/owner/repo/actions/runs/123",
            })
        raise AssertionError((req.method,req.full_url))

    opener.calls=calls
    return opener


def read_state(path):
    return json.loads(path.read_text())


def test_outside_et_weekday_window_records_normal_no_run_without_api(tmp_path):
    opener=opener_with()
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,7,59,tzinfo=timezone.utc),  # 03:59 ET Monday
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="OUTSIDE_MARKET_WINDOW"
    assert out["reason"]=="normal_no_run_outside_et_weekday_window"
    assert opener.calls==[]


def test_weekend_records_normal_no_run_without_api(tmp_path):
    opener=opener_with()
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,4,15,0,tzinfo=timezone.utc),  # Sunday
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="OUTSIDE_MARKET_WINDOW"
    assert opener.calls==[]


def test_recent_native_success_is_healthy_by_result_time(tmp_path):
    opener=opener_with(schedule_runs=[
        run_row(event="schedule",status="completed",conclusion="success")
    ])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="NATIVE_SCHEDULE_SUCCESS"
    assert out["validation"]["native_scan_succeeded"] is True
    assert out["native_result_fresh"] is True
    assert out["native_result_age_seconds"]==120.0
    assert out["last_native_scan_success_at_utc"]=="2026-10-05T23:38:00+00:00"
    assert not any(method=="POST" for method,_,_,_ in opener.calls)


def test_recent_native_queued_is_not_called_healthy_and_not_duplicated(tmp_path):
    opener=opener_with(schedule_runs=[
        run_row(event="schedule",status="queued",updated_at="2026-10-05T23:39:00Z")
    ])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="NATIVE_SCHEDULE_QUEUED"
    assert out["validation"]["native_scan_succeeded"] is False
    assert not any(method=="POST" for method,_,_,_ in opener.calls)


def test_native_in_progress_is_not_called_success(tmp_path):
    opener=opener_with(schedule_runs=[
        run_row(event="schedule",status="in_progress",updated_at="2026-10-05T23:39:30Z")
    ])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="NATIVE_SCHEDULE_IN_PROGRESS"
    assert out["validation"]["native_scan_succeeded"] is False


def test_failed_native_run_allows_fallback_dispatch(tmp_path):
    opener=opener_with(schedule_runs=[
        run_row(event="schedule",status="completed",conclusion="failure")
    ])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="DISPATCH_RUN_CREATED"
    assert out["validation"]["fallback_dispatch_accepted"] is True
    assert out["validation"]["fallback_run_created"] is True
    assert out["validation"]["fallback_scan_succeeded"] is False


def test_existing_workflow_dispatch_queued_suppresses_duplicate(tmp_path):
    opener=opener_with(
        schedule_runs=[],
        dispatch_runs=[run_row(event="workflow_dispatch",status="queued",run_id=77)],
    )
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="FALLBACK_RUN_QUEUED"
    assert out["dispatch_run_id"]==77
    assert out["validation"]["fallback_run_created"] is True
    assert out["validation"]["fallback_scan_succeeded"] is False
    assert not any(method=="POST" for method,_,_,_ in opener.calls)


def test_existing_workflow_dispatch_in_progress_suppresses_duplicate(tmp_path):
    opener=opener_with(
        schedule_runs=[],
        dispatch_runs=[run_row(event="workflow_dispatch",status="in_progress",run_id=78)],
    )
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="FALLBACK_RUN_IN_PROGRESS"
    assert out["dispatch_run_id"]==78
    assert not any(method=="POST" for method,_,_,_ in opener.calls)


def test_recent_successful_dispatch_is_scan_success_not_dispatch_acceptance(tmp_path):
    opener=opener_with(
        schedule_runs=[],
        dispatch_runs=[
            run_row(event="workflow_dispatch",status="completed",conclusion="success",run_id=88)
        ],
    )
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="FALLBACK_SCAN_SUCCESS"
    assert out["validation"]["fallback_scan_succeeded"] is True
    assert out["fallback_result_fresh"] is True
    assert out["fallback_result_age_seconds"]==120.0
    assert out["validation"]["heartbeat_fresh"] is None
    assert out["validation"]["notification_delivered"] is None
    assert not any(method=="POST" for method,_,_,_ in opener.calls)


def test_persistent_cooldown_blocks_serial_duplicate_when_run_listing_lags(tmp_path):
    state=tmp_path/"status.json"
    state.write_text(json.dumps({
        "last_dispatch_accepted_at_utc":"2026-10-05T23:38:30+00:00",
        "last_dispatch_run_id":123,
        "last_dispatch_url":"https://github.com/owner/repo/actions/runs/123",
        "last_fallback_scan_success_at_utc":"2026-10-05T23:30:00+00:00",
    }))
    opener=opener_with(schedule_runs=[],dispatch_runs=[])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=state,
        opener=opener,
    )
    assert out["status"]=="DISPATCH_COOLDOWN"
    assert out["dispatch_run_id"]==123
    assert out["last_fallback_scan_success_at_utc"]=="2026-10-05T23:30:00+00:00"
    assert not any(method=="POST" for method,_,_,_ in opener.calls)


def test_native_recovery_wins_over_existing_fallback_activity(tmp_path):
    opener=opener_with(
        schedule_runs=[run_row(event="schedule",status="completed",conclusion="success",run_id=10)],
        dispatch_runs=[run_row(event="workflow_dispatch",status="queued",run_id=11)],
    )
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="NATIVE_SCHEDULE_SUCCESS"
    # Native recovery suppresses any new external request. Existing fallback can finish separately.
    assert not any(method=="POST" for method,_,_,_ in opener.calls)


def test_dispatch_acceptance_with_run_id_is_not_scan_success(tmp_path):
    opener=opener_with(schedule_runs=[],dispatch_runs=[])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="DISPATCH_RUN_CREATED"
    assert out["dispatch_run_id"]==123
    assert out["validation"]["fallback_dispatch_accepted"] is True
    assert out["validation"]["fallback_run_created"] is True
    assert out["validation"]["fallback_scan_succeeded"] is False


def test_dispatch_acceptance_without_run_id_is_unconfirmed(tmp_path):
    opener=opener_with(schedule_runs=[],dispatch_runs=[],dispatch_response={})
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="DISPATCH_ACCEPTED_UNCONFIRMED"
    assert out["validation"]["fallback_dispatch_accepted"] is True
    assert out["validation"]["fallback_run_created"] is False
    assert out["validation"]["fallback_scan_succeeded"] is False


def test_api_error_overwrites_old_dispatched_status_and_preserves_last_success(tmp_path):
    state=tmp_path/"status.json"
    state.write_text(json.dumps({
        "status":"DISPATCH_RUN_CREATED",
        "last_api_success_at_utc":"2026-10-05T23:30:00+00:00",
        "last_native_scan_success_at_utc":"2026-10-05T23:20:00+00:00",
        "last_fallback_scan_success_at_utc":"2026-10-05T23:25:00+00:00",
        "last_dispatch_accepted_at_utc":"2026-10-05T23:26:00+00:00",
        "last_dispatch_run_id":55,
    }))
    opener=opener_with(fail_event="schedule")
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=state,
        opener=opener,
    )
    persisted=read_state(state)
    assert out["status"]=="API_ERROR"
    assert persisted["status"]=="API_ERROR"
    assert persisted["api_error_stage"]=="list_native_schedule_runs"
    assert persisted["last_api_success_at_utc"]=="2026-10-05T23:30:00+00:00"
    assert persisted["last_fallback_scan_success_at_utc"]=="2026-10-05T23:25:00+00:00"


def test_missing_token_is_explicit_and_not_recovery(tmp_path):
    opener=opener_with(schedule_runs=[],dispatch_runs=[])
    out=run_once(
        token="",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="TOKEN_MISSING"
    assert out["validation"]["fallback_dispatch_accepted"] is False
    assert out["validation"]["fallback_scan_succeeded"] is False
    assert not any(method=="POST" for method,_,_,_ in opener.calls)
