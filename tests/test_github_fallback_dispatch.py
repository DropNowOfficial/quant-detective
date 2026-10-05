import json
from datetime import datetime, timezone

from market_data.github_fallback_dispatch import run_once


class Response:
    def __init__(self, payload, status=200):
        self.payload=json.dumps(payload).encode()
        self.status=status
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def read(self): return self.payload


def opener_with(schedule_runs, dispatch_response=None):
    calls=[]
    def opener(req, timeout=None):
        calls.append((req.method, req.full_url, req.data, dict(req.header_items())))
        if "/runs?" in req.full_url:
            return Response({"workflow_runs":schedule_runs,"total_count":len(schedule_runs)})
        if req.full_url.endswith("/dispatches") and req.method=="POST":
            return Response(dispatch_response or {
                "workflow_run_id":123,
                "html_url":"https://github.com/owner/repo/actions/runs/123",
            })
        raise AssertionError((req.method,req.full_url))
    opener.calls=calls
    return opener


def test_dispatches_when_no_native_schedule_exists(tmp_path):
    opener=opener_with([])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="DISPATCHED"
    assert out["dispatch_run_id"]==123
    assert any(method=="POST" and url.endswith("/dispatches") for method,url,_,_ in opener.calls)
    assert "secret" not in (tmp_path/"status.json").read_text()


def test_recent_native_schedule_suppresses_external_dispatch(tmp_path):
    opener=opener_with([{
        "id":99,
        "created_at":"2026-10-05T23:37:00Z",
        "status":"completed",
        "conclusion":"success",
        "html_url":"https://github.com/owner/repo/actions/runs/99",
    }])
    out=run_once(
        token="secret",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="NATIVE_SCHEDULE_HEALTHY"
    assert not out["dispatched"]
    assert not any(method=="POST" for method,_,_,_ in opener.calls)


def test_missing_token_is_explicit_and_does_not_dispatch(tmp_path):
    opener=opener_with([])
    out=run_once(
        token="",
        repo="owner/repo",
        now=datetime(2026,10,5,23,40,tzinfo=timezone.utc),
        status_path=tmp_path/"status.json",
        opener=opener,
    )
    assert out["status"]=="TOKEN_MISSING"
    assert not out["dispatched"]
    assert not any(method=="POST" for method,_,_,_ in opener.calls)
