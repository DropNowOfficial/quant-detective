import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest

from market_data.github_fallback_dispatch import run_once


class Response:
    def __init__(self, payload=None, status=200):
        self.payload=b"" if payload is None else json.dumps(payload).encode()
        self.status=status
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self, size=-1): return self.payload if size < 0 else self.payload[:size]


def run_row(
    *,
    event,
    status,
    conclusion=None,
    created_at="2026-10-05T23:37:00Z",
    updated_at="2026-10-05T23:38:00Z",
    run_id=99,
    head_branch="main",
):
    return {
        "id":run_id,
        "run_attempt":1,
        "head_branch":head_branch,
        "event":event,
        "created_at":created_at,
        "run_started_at":created_at if status!="queued" else None,
        "updated_at":updated_at,
        "status":status,
        "conclusion":conclusion,
        "html_url":f"https://github.com/owner/repo/actions/runs/{run_id}",
    }


def scan_jobs(run_id, *, steps=None):
    return {"total_count": 1, "jobs": [{
        "run_id": run_id, "run_attempt": 1, "status": "completed", "conclusion": "success",
        "steps": steps if steps is not None else [{
            "name": "One-shot hosted fallback scan", "status": "completed", "conclusion": "success",
        }],
    }]}


def opener_with(*, schedule_runs=None, dispatch_runs=None, dispatch_response=None, fail_event=None,
                jobs_by_run=None):
    schedule_runs=list(schedule_runs or [])
    dispatch_runs=list(dispatch_runs or [])
    jobs_by_run=dict(jobs_by_run or {})
    calls=[]

    def opener(req, timeout=None):
        calls.append((req.method, req.full_url, req.data, dict(req.header_items())))
        parsed=urlparse(req.full_url)
        if parsed.path.endswith("/jobs"):
            parts=parsed.path.split("/")
            run_id=int(parts[parts.index("runs") + 1])
            payload=jobs_by_run.get(run_id, {"total_count": 0, "jobs": []})
            if isinstance(payload, Exception):
                raise payload
            return Response(payload)
        if parsed.path.endswith("/runs"):
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
    ], jobs_by_run={99: scan_jobs(99)})
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
        jobs_by_run={88: scan_jobs(88)},
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
        jobs_by_run={10: scan_jobs(10)},
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


def test_stale_native_queued_does_not_block_fallback(tmp_path):
    opener=opener_with(schedule_runs=[
        run_row(
            event="schedule",
            status="queued",
            created_at="2026-10-05T23:00:00Z",
            updated_at="2026-10-05T23:00:00Z",
        )
    ])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        active_run_max_age_seconds=900,
        opener=opener,
    )
    assert out["status"]=="DISPATCH_RUN_CREATED"
    assert out["validation"]["fallback_dispatch_accepted"] is True


def test_stale_fallback_in_progress_does_not_block_new_dispatch(tmp_path):
    opener=opener_with(
        schedule_runs=[],
        dispatch_runs=[
            run_row(
                event="workflow_dispatch",
                status="in_progress",
                created_at="2026-10-05T23:00:00Z",
                updated_at="2026-10-05T23:00:00Z",
                run_id=70,
            )
        ],
    )
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        active_run_max_age_seconds=900,
        opener=opener,
    )
    assert out["status"]=="DISPATCH_RUN_CREATED"
    assert out["dispatch_run_id"]==123


def test_native_recovery_between_checks_suppresses_dispatch(tmp_path):
    calls=[]
    schedule_responses=[
        [],
        [run_row(event="schedule",status="completed",conclusion="success",run_id=201)],
    ]

    def opener(req, timeout=None):
        calls.append((req.method, req.full_url))
        parsed=urlparse(req.full_url)
        if parsed.path.endswith("/jobs"):
            return Response(scan_jobs(201))
        if "/runs" in parsed.path:
            event=parse_qs(parsed.query).get("event", [None])[0]
            if event=="schedule":
                rows=schedule_responses.pop(0)
                return Response({"workflow_runs":rows,"total_count":len(rows)})
            if event=="workflow_dispatch":
                return Response({"workflow_runs":[],"total_count":0})
        if parsed.path.endswith("/dispatches"):
            raise AssertionError("dispatch should be suppressed after native recovery")
        raise AssertionError(req.full_url)

    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="NATIVE_SCHEDULE_SUCCESS"
    assert out["reason"]=="native_recovered_before_external_dispatch"
    assert out["validation"]["native_scan_succeeded"] is True
    assert out["native_result_fresh"] is True
    assert out["native_result_age_seconds"] == 120.0


def test_external_run_appearing_between_checks_suppresses_duplicate(tmp_path):
    dispatch_responses=[
        [],
        [run_row(event="workflow_dispatch",status="queued",run_id=301)],
    ]

    def opener(req, timeout=None):
        parsed=urlparse(req.full_url)
        if "/runs" in parsed.path:
            event=parse_qs(parsed.query).get("event", [None])[0]
            if event=="schedule":
                return Response({"workflow_runs":[],"total_count":0})
            if event=="workflow_dispatch":
                rows=dispatch_responses.pop(0)
                return Response({"workflow_runs":rows,"total_count":len(rows)})
        if parsed.path.endswith("/dispatches"):
            raise AssertionError("duplicate dispatch should be suppressed")
        raise AssertionError(req.full_url)

    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="FALLBACK_RUN_QUEUED"
    assert out["reason"]=="workflow_dispatch_appeared_before_duplicate_request"
    assert out["dispatch_run_id"]==301
    assert out["fallback_result_age_seconds"] == 120.0
    assert out["fallback_result_fresh"] is False


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
@pytest.mark.parametrize("status,conclusion", [("queued", None), ("completed", "success")])
def test_runs_on_another_ref_do_not_suppress_requested_dispatch(tmp_path, event, status, conclusion):
    unrelated = run_row(event=event, status=status, conclusion=conclusion, head_branch="experimental")
    opener = opener_with(schedule_runs=[unrelated] if event == "schedule" else [],
                         dispatch_runs=[unrelated] if event == "workflow_dispatch" else [])
    out = run_once(token="secret", repo="owner/repo", ref="release/review",
                   now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                   status_path=tmp_path / "status.json", opener=opener)
    assert out["status"] == "DISPATCH_RUN_CREATED"
    for method, url, data, headers in opener.calls:
        if method == "GET":
            assert parse_qs(urlparse(url).query)["branch"] == ["release/review"]
        elif method == "POST":
            assert json.loads(data)["ref"] == "release/review"


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
@pytest.mark.parametrize("status,conclusion", [("in_progress", None), ("completed", "success")])
def test_race_recheck_refreshes_observed_age_and_freshness(tmp_path, event, status, conclusion):
    counts = {"schedule": 0, "workflow_dispatch": 0}
    recovered = run_row(event=event, status=status, conclusion=conclusion, run_id=401)

    def opener(req, timeout=None):
        assert req.method == "GET", "recovered run should suppress dispatch"
        if urlparse(req.full_url).path.endswith("/jobs"):
            return Response(scan_jobs(401))
        requested_event = parse_qs(urlparse(req.full_url).query)["event"][0]
        counts[requested_event] += 1
        rows = [recovered] if requested_event == event and counts[requested_event] == 2 else []
        return Response({"workflow_runs": rows})

    out = run_once(token="secret", repo="owner/repo",
                   now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                   status_path=tmp_path / "status.json", opener=opener)
    prefix = "native" if event == "schedule" else "fallback"
    field = "native_schedule" if event == "schedule" else "workflow_dispatch"
    assert out[field]["id"] == 401
    assert out[field]["head_branch"] == "main"
    assert out[f"{prefix}_result_age_seconds"] == 120.0
    assert out[f"{prefix}_result_fresh"] is (conclusion == "success")
    assert out["validation"]["heartbeat_fresh"] is None
    assert out["validation"]["notification_delivered"] is None


def test_systemd_timer_uses_established_et_weekday_window():
    root=Path(__file__).resolve().parents[1]
    text=(root/"deploy/systemd/quant-detective-github-fallback.timer").read_text()
    assert "Mon..Fri *-*-* 04..19:00/5:00 America/New_York" in text
    assert "Mon..Fri *-*-* 20:00:00 America/New_York" in text
    assert "Mon..Fri *-*-* 20:05:00 America/New_York" in text
    assert "OnUnitInactiveSec=5min" not in text


def test_dispatch_http_and_transport_failures_never_persist_secret_text(tmp_path):
    import io
    from urllib.error import HTTPError
    secret = 'PRIVATE_TEST_SECRET'
    for error in (HTTPError('https://api.github.com/private', 403, secret, {}, io.BytesIO(secret.encode())),
                  URLError(secret), RuntimeError(secret)):
        def failed(req, timeout=None, error=error):
            raise error
        out = run_once(token='secret', repo='owner/repo',
                       now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                       status_path=tmp_path / 'safe-status.json', opener=failed)
        assert out['status'] == 'API_ERROR'
        assert secret not in json.dumps(out)
        assert secret not in (tmp_path / 'safe-status.json').read_text()


def test_dispatch_rejects_oversized_api_response_before_json_decode():
    from market_data import github_fallback_dispatch as dispatch
    class Oversized(Response):
        def read(self, size=-1):
            return b' ' * (8 * 1024 * 1024 + 1)
    with pytest.raises(dispatch.DispatchError, match='limit'):
        dispatch._request('GET', '/repos/owner/repo/actions/workflows/market-watch.yml/runs',
                          token='secret', opener=lambda *args, **kwargs: Oversized())


def test_dispatch_rejects_path_injection_before_opening():
    from market_data import github_fallback_dispatch as dispatch
    def forbidden(*args, **kwargs):
        pytest.fail('untrusted path reached HTTP opener')
    for path in ('/repos/owner/repo/../secrets', '//evil.test/path',
                 '/repos/owner/repo/actions/workflows/../dispatches',
                 '/repos/owner/repo/actions/workflows/a.yml/runs\nAuthorization: secret'):
        with pytest.raises(dispatch.DispatchError):
            dispatch._request('GET', path, token='secret', opener=forbidden)


def test_dispatch_default_opener_denies_redirect_without_forwarding_credentials():
    from market_data import github_fallback_dispatch as dispatch
    from urllib.request import Request
    handlers = getattr(dispatch.urlopen, '__self__', None)
    assert handlers is not None
    redirect = next(handler for handler in handlers.handlers
                    if hasattr(handler, 'redirect_request'))
    req = Request('https://api.github.com/repos/owner/repo/actions/workflows/a.yml/runs',
                  headers={'Authorization': 'Bearer private'})
    assert redirect.redirect_request(req, None, 302, 'redirect', {}, 'https://evil.test') is None


@pytest.mark.parametrize('event', ['schedule', 'workflow_dispatch'])
@pytest.mark.parametrize('steps', [
    [],
    [{'name': 'One-shot hosted fallback scan', 'status': 'completed', 'conclusion': 'skipped'}],
    [{'name': 'One-shot hosted fallback scan', 'status': 'completed', 'conclusion': 'failure'}],
    [{'name': 'One-shot hosted fallback scan', 'status': 'in_progress', 'conclusion': None}],
    [{'name': 'Verify actual notification delivery', 'status': 'completed', 'conclusion': 'success'}],
])
def test_workflow_success_without_successful_scan_step_does_not_suppress_fallback(tmp_path, event, steps):
    row = run_row(event=event, status='completed', conclusion='success')
    opener = opener_with(schedule_runs=[row] if event == 'schedule' else [],
                         dispatch_runs=[row] if event == 'workflow_dispatch' else [],
                         jobs_by_run={99: scan_jobs(99, steps=steps)})
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                   status_path=tmp_path / 'status.json', opener=opener)
    assert out['status'] == 'DISPATCH_RUN_CREATED'
    assert out['validation']['native_scan_succeeded'] is False
    assert out['validation']['fallback_scan_succeeded'] is False
    assert out['last_native_scan_success_at_utc'] is None
    assert out['last_fallback_scan_success_at_utc'] is None


@pytest.mark.parametrize('event', ['schedule', 'workflow_dispatch'])
@pytest.mark.parametrize('jobs', [
    URLError('PRIVATE_JOB_ERROR'),
    {},
    {'jobs': 'invalid', 'total_count': 0},
    {'jobs': [], 'total_count': 1},
    scan_jobs(100),
    {'jobs': [{'run_id': 99, 'run_attempt': 2, 'steps': []}], 'total_count': 1},
])
def test_scan_evidence_failure_is_unknown_and_never_dispatches(tmp_path, event, jobs):
    row = run_row(event=event, status='completed', conclusion='success')
    state = tmp_path / 'status.json'
    state.write_text(json.dumps({'last_native_scan_success_at_utc': '2026-10-05T22:00:00Z'}))
    opener = opener_with(schedule_runs=[row] if event == 'schedule' else [],
                         dispatch_runs=[row] if event == 'workflow_dispatch' else [],
                         jobs_by_run={99: jobs})
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                   status_path=state, opener=opener)
    assert out['status'] == 'API_ERROR'
    assert out['validation']['native_scan_succeeded'] is False
    assert out['validation']['fallback_scan_succeeded'] is False
    assert out['last_native_scan_success_at_utc'] == '2026-10-05T22:00:00Z'
    assert 'PRIVATE_JOB_ERROR' not in state.read_text()
    assert not any(method == 'POST' for method, _, _, _ in opener.calls)


def test_verified_scan_uses_authenticated_fixed_repo_jobs_and_leaves_coverage_unknown(tmp_path):
    row = run_row(event='workflow_dispatch', status='completed', conclusion='success')
    row['html_url'] = 'https://untrusted.invalid/jobs'
    opener = opener_with(dispatch_runs=[row], jobs_by_run={99: scan_jobs(99)})
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                   status_path=tmp_path / 'status.json', opener=opener)
    assert out['status'] == 'FALLBACK_SCAN_SUCCESS'
    assert out['validation']['fallback_scan_succeeded'] is True
    assert out['validation']['market_coverage_complete'] is None
    job_calls = [call for call in opener.calls if urlparse(call[1]).path.endswith('/jobs')]
    assert len(job_calls) == 1
    method, url, body, headers = job_calls[0]
    assert method == 'GET'
    assert urlparse(url).netloc == 'api.github.com'
    assert urlparse(url).path == '/repos/owner/repo/actions/runs/99/attempts/1/jobs'
    assert parse_qs(urlparse(url).query) == {'per_page': ['100']}
    assert headers['Authorization'] == 'Bearer secret'
    assert body is None


@pytest.mark.parametrize('event', ['schedule', 'workflow_dispatch'])
@pytest.mark.parametrize('jobs,expected_status', [
    (scan_jobs(501, steps=[]), 'DISPATCH_RUN_CREATED'),
    (URLError('unavailable'), 'API_ERROR'),
])
def test_race_recheck_requires_scan_step_evidence(tmp_path, event, jobs, expected_status):
    counts = {'schedule': 0, 'workflow_dispatch': 0}
    row = run_row(event=event, status='completed', conclusion='success', run_id=501)
    base = opener_with(jobs_by_run={501: jobs})

    def opener(req, timeout=None):
        parsed = urlparse(req.full_url)
        if parsed.path.endswith('/runs'):
            requested_event = parse_qs(parsed.query)['event'][0]
            counts[requested_event] += 1
            rows = [row] if requested_event == event and counts[requested_event] == 2 else []
            return Response({'workflow_runs': rows})
        return base(req, timeout=timeout)

    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                   status_path=tmp_path / 'status.json', opener=opener)
    assert out['status'] == expected_status
    assert out['validation']['native_scan_succeeded'] is False
    assert out['validation']['fallback_scan_succeeded'] is False
    assert any(method == 'POST' for method, _, _, _ in base.calls) is (expected_status == 'DISPATCH_RUN_CREATED')


@pytest.mark.parametrize('value,session,reason', [
    ('2026-07-03T12:00:00-04:00', 'CLOSED', 'NON_TRADING_DAY'),
    ('2026-11-26T12:00:00-05:00', 'CLOSED', 'NON_TRADING_DAY'),
    ('2026-10-05T20:00:00-04:00', 'OVERNIGHT', 'OUTSIDE_SESSION_HOURS'),
    ('2026-10-05T20:02:00-04:00', 'OVERNIGHT', 'OUTSIDE_SESSION_HOURS'),
])
def test_closed_market_session_never_requests_or_claims_a_scan(tmp_path, value, session, reason):
    opener = opener_with()
    out = run_once(token='secret', repo='owner/repo', now=datetime.fromisoformat(value),
                   status_path=tmp_path / 'status.json', opener=opener)
    assert out['status'] == 'OUTSIDE_MARKET_SESSION'
    assert out['runtime_session'] == session
    assert out['skip_reason'] == reason
    assert out['validation']['native_scan_succeeded'] is False
    assert out['validation']['fallback_scan_succeeded'] is False
    assert opener.calls == []


def test_jobs_payload_need_not_repeat_attempt_when_exact_attempt_is_requested(tmp_path):
    jobs = scan_jobs(99)
    del jobs['jobs'][0]['run_attempt']  # Optional in the documented GitHub job response.
    opener = opener_with(dispatch_runs=[run_row(event='workflow_dispatch', status='completed', conclusion='success')],
                         jobs_by_run={99: jobs})
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                   status_path=tmp_path / 'status.json', opener=opener)
    assert out['status'] == 'FALLBACK_SCAN_SUCCESS'


def uncertain_dispatch(tmp_path):
    state = tmp_path / 'status.json'
    base = opener_with()
    posts = []

    def lost_response(req, timeout=None):
        if req.method == 'POST':
            # The attempt must already exist before the network can accept it.
            # Capture this outside assertions: run_once intentionally catches
            # transport exceptions, so assertions in an opener can be hidden.
            posts.append(read_state(state) if state.exists() else None)
            raise URLError('PRIVATE_LOST_RESPONSE')
        return base(req, timeout=timeout)

    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc),
                   status_path=state, opener=lost_response)
    return state, posts, out


def test_lost_post_response_persists_attempt_and_never_replays_after_cooldown(tmp_path):
    state, posts, first = uncertain_dispatch(tmp_path)
    assert len(posts) == 1
    assert posts[0]['dispatch_attempt']['state'] == 'PENDING'
    assert posts[0]['dispatch_attempt']['repo'] == 'owner/repo'
    assert posts[0]['dispatch_attempt']['workflow'] == 'market-watch.yml'
    assert posts[0]['dispatch_attempt']['ref'] == 'main'
    assert first['status'] == 'DISPATCH_OUTCOME_UNKNOWN'
    assert first['validation']['fallback_dispatch_accepted'] is False
    assert first['validation']['fallback_run_created'] is False
    assert first['validation']['fallback_scan_succeeded'] is False
    assert first['last_dispatch_accepted_at_utc'] is None
    assert 'PRIVATE_LOST_RESPONSE' not in state.read_text()
    restarted = opener_with()
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc),
                   status_path=state, opener=restarted)
    assert out['status'] == 'DISPATCH_OUTCOME_UNKNOWN'
    assert out['validation']['fallback_dispatch_accepted'] is False
    assert not any(method == 'POST' for method, _, _, _ in restarted.calls)


def test_accepted_without_run_id_remains_quarantined_after_cooldown(tmp_path):
    state = tmp_path / 'status.json'
    first = run_once(token='secret', repo='owner/repo',
                     now=datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc),
                     status_path=state, opener=opener_with(dispatch_response={}))
    assert first['status'] == 'DISPATCH_ACCEPTED_UNCONFIRMED'
    restarted = opener_with()
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc),
                   status_path=state, opener=restarted)
    assert out['status'] == 'DISPATCH_OUTCOME_UNKNOWN'
    assert not any(method == 'POST' for method, _, _, _ in restarted.calls)


@pytest.mark.parametrize('event', ['schedule', 'workflow_dispatch'])
def test_verified_subsequent_scan_reconciles_quarantine_without_claiming_post_acceptance(tmp_path, event):
    state, _, _ = uncertain_dispatch(tmp_path)
    row = run_row(event=event, status='completed', conclusion='success', run_id=601,
                  created_at='2026-10-05T16:58:00Z', updated_at='2026-10-05T16:59:00Z')
    recovered = opener_with(schedule_runs=[row] if event == 'schedule' else [],
                            dispatch_runs=[row] if event == 'workflow_dispatch' else [],
                            jobs_by_run={601: scan_jobs(601)})
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc),
                   status_path=state, opener=recovered)
    assert out['status'] == ('NATIVE_SCHEDULE_SUCCESS' if event == 'schedule' else 'FALLBACK_SCAN_SUCCESS')
    assert out['dispatch_attempt']['state'] == 'RECONCILED'
    assert out['dispatch_attempt']['reconciled_run_id'] == 601
    assert out['validation']['fallback_dispatch_accepted'] is False
    assert out['last_dispatch_accepted_at_utc'] is None
    assert not any(method == 'POST' for method, _, _, _ in recovered.calls)
    after_recovery = opener_with()
    later = run_once(token='secret', repo='owner/repo',
                     now=datetime(2026, 10, 5, 17, 10, tzinfo=timezone.utc),
                     status_path=state, opener=after_recovery)
    assert later['status'] == 'DISPATCH_RUN_CREATED'


@pytest.mark.parametrize('change', [
    {'created_at': '2026-10-05T15:59:00Z'},
    {'repo': 'owner/other'},
    {'workflow': 'other.yml'},
    {'ref': 'other', 'head_branch': 'other'},
    {'steps': []},
])
def test_unrelated_or_non_scan_success_does_not_clear_quarantine(tmp_path, change):
    state, _, _ = uncertain_dispatch(tmp_path)
    row = run_row(event='workflow_dispatch', status='completed', conclusion='success', run_id=601,
                  created_at=change.get('created_at', '2026-10-05T16:58:00Z'),
                  updated_at='2026-10-05T16:59:00Z', head_branch=change.get('head_branch', 'main'))
    recovered = opener_with(dispatch_runs=[row], jobs_by_run={601: scan_jobs(601, steps=change.get('steps'))})
    out = run_once(token='secret', repo=change.get('repo', 'owner/repo'),
                   workflow=change.get('workflow', 'market-watch.yml'), ref=change.get('ref', 'main'),
                   now=datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc),
                   status_path=state, opener=recovered)
    assert out['dispatch_attempt']['state'] != 'RECONCILED'
    assert not any(method == 'POST' for method, _, _, _ in recovered.calls)


def test_failure_to_persist_attempt_prevents_post(monkeypatch, tmp_path):
    from market_data import github_fallback_dispatch as dispatch
    opener = opener_with()

    def disk_failure(path, value):
        raise OSError('PRIVATE_DISK_ERROR')

    monkeypatch.setattr(dispatch, '_write_state', disk_failure)
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc),
                   status_path=tmp_path / 'status.json', opener=opener)
    assert out['status'] == 'STATE_ERROR'
    assert 'PRIVATE_DISK_ERROR' not in json.dumps(out)
    assert not any(method == 'POST' for method, _, _, _ in opener.calls)


@pytest.mark.parametrize('saved', ['{broken', '[]'])
def test_unreadable_attempt_history_does_not_reset_to_permission_to_dispatch(tmp_path, saved):
    state = tmp_path / 'status.json'
    state.write_text(saved)
    opener = opener_with()
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc),
                   status_path=state, opener=opener)
    assert out['status'] == 'STATE_ERROR'
    assert state.read_text() == saved
    assert opener.calls == []


def test_dispatch_requires_durable_state_path():
    opener = opener_with()
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc),
                   status_path=None, opener=opener)
    assert out['status'] == 'STATE_ERROR'
    assert not any(method == 'POST' for method, _, _, _ in opener.calls)


def test_preexisting_native_identity_cannot_reconcile_same_second_unknown_attempt(tmp_path):
    state = tmp_path / 'status.json'
    existing = run_row(event='schedule', status='completed', conclusion='failure', run_id=701,
                       created_at='2026-10-05T16:00:00Z', updated_at='2026-10-05T16:00:00Z')
    base = opener_with(schedule_runs=[existing])

    def lost_response(req, timeout=None):
        if req.method == 'POST':
            raise URLError('unavailable')
        return base(req, timeout=timeout)

    first = run_once(token='secret', repo='owner/repo',
                     now=datetime(2026, 10, 5, 16, 0, tzinfo=timezone.utc),
                     status_path=state, opener=lost_response)
    assert first['status'] == 'DISPATCH_OUTCOME_UNKNOWN'
    existing.update(conclusion='success', updated_at='2026-10-05T16:01:00Z')
    recovered = opener_with(schedule_runs=[existing], jobs_by_run={701: scan_jobs(701)})
    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 16, 2, tzinfo=timezone.utc),
                   status_path=state, opener=recovered)
    assert out['status'] == 'NATIVE_SCHEDULE_SUCCESS'
    assert out['dispatch_attempt']['state'] == 'UNKNOWN'
    assert 701 in out['dispatch_attempt']['known_run_ids']


def test_scan_evidence_request_is_bound_to_the_observed_run_attempt(tmp_path):
    row = run_row(event='workflow_dispatch', status='completed', conclusion='success')
    row['run_attempt'] = 2
    jobs = scan_jobs(99)
    del jobs['jobs'][0]['run_attempt']
    calls = []
    base = opener_with(dispatch_runs=[row])

    def opener(req, timeout=None):
        if urlparse(req.full_url).path.endswith('/jobs'):
            calls.append(req.full_url)
            return Response(jobs)
        return base(req, timeout=timeout)

    out = run_once(token='secret', repo='owner/repo',
                   now=datetime(2026, 10, 5, 23, 40, tzinfo=timezone.utc),
                   status_path=tmp_path / 'status.json', opener=opener)
    assert out['status'] == 'FALLBACK_SCAN_SUCCESS'
    assert [urlparse(url).path for url in calls] == ['/repos/owner/repo/actions/runs/99/attempts/2/jobs']
