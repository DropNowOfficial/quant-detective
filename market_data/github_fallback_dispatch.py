"""External failover trigger for the GitHub hosted market-watch fallback.

This is a secondary scheduler. It never treats dispatch acceptance, a queued
workflow, or process liveness as proof that a market scan succeeded.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


API = "https://api.github.com"
DEFAULT_REPO = "DropNowOfficial/quant-detective"
DEFAULT_WORKFLOW = "market-watch.yml"
DEFAULT_REF = "main"

ET = ZoneInfo("America/New_York")
WORKDAY_START_MINUTE = 4 * 60
# Native workflow runs 04:02..19:57 plus 20:02 ET. Keep a small grace window.
WORKDAY_END_MINUTE = 20 * 60 + 10

ACTIVE_STATUSES = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})
DISPATCH_COOLDOWN_SECONDS = 240
NATIVE_SUCCESS_FRESH_SECONDS = 420
FALLBACK_SUCCESS_FRESH_SECONDS = 240


class DispatchError(RuntimeError):
    pass


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _iso(value):
    return value.astimezone(timezone.utc).isoformat() if value else None


def _request(method, path, *, token=None, body=None, opener=urlopen, timeout=12):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "quant-detective-fallback-dispatch/0.2",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(API + path, data=data, method=method, headers=headers)
    try:
        with opener(req, timeout=timeout) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise DispatchError(f"GitHub HTTP {exc.code}: {detail}") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise DispatchError(f"GitHub unavailable: {type(exc).__name__}: {exc}") from exc
    if status not in {200, 201, 202, 204}:
        raise DispatchError(f"GitHub HTTP {status}")
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DispatchError("GitHub returned invalid JSON") from exc


def _load_state(path):
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(path, value):
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        handle.write(payload)
        temp = Path(handle.name)
    os.replace(temp, target)


def _persistent(previous):
    return {
        "last_api_success_at_utc": previous.get("last_api_success_at_utc"),
        "last_native_scan_success_at_utc": previous.get("last_native_scan_success_at_utc"),
        "last_fallback_scan_success_at_utc": previous.get("last_fallback_scan_success_at_utc"),
        "last_dispatch_accepted_at_utc": previous.get("last_dispatch_accepted_at_utc"),
        "last_dispatch_run_id": previous.get("last_dispatch_run_id"),
        "last_dispatch_url": previous.get("last_dispatch_url"),
    }


def _validation():
    return {
        "native_scan_succeeded": False,
        "fallback_dispatch_accepted": False,
        "fallback_run_created": False,
        "fallback_scan_succeeded": False,
        # Not checked by this component. These remain separate acceptance stages.
        "heartbeat_fresh": None,
        "notification_delivered": None,
    }


def _base_result(now, repo, workflow, previous):
    local = now.astimezone(ET)
    return {
        "checked_at_utc": now.isoformat(),
        "checked_at_et": local.isoformat(),
        "repo": repo,
        "workflow": workflow,
        "status": None,
        "reason": None,
        "work_window": "Mon-Fri 04:00-20:10 America/New_York",
        "native_schedule": None,
        "workflow_dispatch": None,
        "dispatched": False,
        "dispatch_run_id": None,
        "dispatch_url": None,
        "validation": _validation(),
        **_persistent(previous),
    }


def in_work_window(now):
    local = now.astimezone(ET)
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and WORKDAY_START_MINUTE <= minute < WORKDAY_END_MINUTE


def _run_summary(row):
    if not isinstance(row, dict):
        return None
    return {
        "id": row.get("id"),
        "event": row.get("event"),
        "created_at": row.get("created_at"),
        "run_started_at": row.get("run_started_at"),
        "updated_at": row.get("updated_at"),
        "status": row.get("status"),
        "conclusion": row.get("conclusion"),
        "html_url": row.get("html_url"),
    }


def list_runs(event, repo=DEFAULT_REPO, workflow=DEFAULT_WORKFLOW, *, token=None, opener=urlopen):
    query = urlencode({"event": event, "per_page": 10})
    data = _request(
        "GET",
        f"/repos/{repo}/actions/workflows/{workflow}/runs?{query}",
        token=token or None,
        opener=opener,
    )
    rows = data.get("workflow_runs") or []
    return [item for row in rows if (item := _run_summary(row)) is not None]


def _result_time(run):
    if not run:
        return None
    return (
        _parse_time(run.get("updated_at"))
        or _parse_time(run.get("run_started_at"))
        or _parse_time(run.get("created_at"))
    )


def _age_seconds(run, now):
    stamp = _result_time(run)
    return None if stamp is None else (now - stamp).total_seconds()


def _run_phase(run):
    if run is None:
        return "MISSING"
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if status in ACTIVE_STATUSES:
        return status.upper()
    if status == "completed":
        return "SUCCESS" if conclusion == "success" else f"COMPLETED_{(conclusion or 'UNKNOWN').upper()}"
    return (status or "UNKNOWN").upper()


def _recent_success(run, now, max_age_seconds):
    if _run_phase(run) != "SUCCESS":
        return False
    age = _age_seconds(run, now)
    return age is not None and -60 <= age <= max_age_seconds


def _cooldown_active(previous, now, cooldown_seconds):
    stamp = _parse_time(previous.get("last_dispatch_accepted_at_utc"))
    if stamp is None:
        return False
    age = (now - stamp).total_seconds()
    return -60 <= age < cooldown_seconds


def dispatch(
    *,
    token,
    repo=DEFAULT_REPO,
    workflow=DEFAULT_WORKFLOW,
    ref=DEFAULT_REF,
    opener=urlopen,
):
    if not token:
        raise ValueError("token required")
    return _request(
        "POST",
        f"/repos/{repo}/actions/workflows/{workflow}/dispatches",
        token=token,
        body={"ref": ref, "return_run_details": True},
        opener=opener,
    )


def _api_error(result, previous, now, stage, exc, status_path):
    result.update(_persistent(previous))
    result["status"] = "API_ERROR"
    result["reason"] = f"{stage}: {type(exc).__name__}: {str(exc)[:240]}"
    result["api_error_at_utc"] = now.isoformat()
    result["api_error_stage"] = stage
    _write_state(status_path, result)
    return result


def run_once(
    *,
    token=None,
    repo=DEFAULT_REPO,
    workflow=DEFAULT_WORKFLOW,
    ref=DEFAULT_REF,
    status_path="/var/lib/quant-detective/github-fallback-dispatch.json",
    now=None,
    native_success_fresh_seconds=NATIVE_SUCCESS_FRESH_SECONDS,
    fallback_success_fresh_seconds=FALLBACK_SUCCESS_FRESH_SECONDS,
    dispatch_cooldown_seconds=DISPATCH_COOLDOWN_SECONDS,
    opener=urlopen,
):
    now = now or datetime.now(timezone.utc)
    token = token if token is not None else os.getenv("QD_GITHUB_FALLBACK_TOKEN", "").strip()
    previous = _load_state(status_path)
    result = _base_result(now, repo, workflow, previous)

    if not in_work_window(now):
        result["status"] = "OUTSIDE_MARKET_WINDOW"
        result["reason"] = "normal_no_run_outside_et_weekday_window"
        _write_state(status_path, result)
        return result

    try:
        native_runs = list_runs("schedule", repo, workflow, token=token or None, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "list_native_schedule_runs", exc, status_path)

    native = native_runs[0] if native_runs else None
    result["native_schedule"] = native
    native_phase = _run_phase(native)
    native_age = _age_seconds(native, now)
    result["native_result_age_seconds"] = native_age
    result["native_result_fresh"] = bool(
        native_phase == "SUCCESS"
        and native_age is not None
        and -60 <= native_age <= native_success_fresh_seconds
    )

    # A completed successful scan is the only native state called healthy.
    if _recent_success(native, now, native_success_fresh_seconds):
        stamp = _result_time(native)
        result["status"] = "NATIVE_SCHEDULE_SUCCESS"
        result["reason"] = "recent_completed_success"
        result["last_api_success_at_utc"] = now.isoformat()
        result["last_native_scan_success_at_utc"] = _iso(stamp)
        result["validation"]["native_scan_succeeded"] = True
        _write_state(status_path, result)
        return result

    # Queued/in-progress/etc. exists but is not scan success. Suppress duplicate
    # dispatch while GitHub is already trying to execute that native run.
    if native_phase in {s.upper() for s in ACTIVE_STATUSES}:
        result["status"] = f"NATIVE_SCHEDULE_{native_phase}"
        result["reason"] = "native_run_exists_but_scan_not_completed"
        result["last_api_success_at_utc"] = now.isoformat()
        _write_state(status_path, result)
        return result

    try:
        fallback_runs = list_runs("workflow_dispatch", repo, workflow, token=token or None, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "list_workflow_dispatch_runs", exc, status_path)

    fallback = fallback_runs[0] if fallback_runs else None
    result["workflow_dispatch"] = fallback
    result["last_api_success_at_utc"] = now.isoformat()
    fallback_phase = _run_phase(fallback)
    fallback_age = _age_seconds(fallback, now)
    result["fallback_result_age_seconds"] = fallback_age
    result["fallback_result_fresh"] = bool(
        fallback_phase == "SUCCESS"
        and fallback_age is not None
        and -60 <= fallback_age <= fallback_success_fresh_seconds
    )

    if fallback_phase in {s.upper() for s in ACTIVE_STATUSES}:
        result["status"] = f"FALLBACK_RUN_{fallback_phase}"
        result["reason"] = "existing_workflow_dispatch_is_active"
        result["dispatch_run_id"] = fallback.get("id")
        result["dispatch_url"] = fallback.get("html_url")
        result["validation"]["fallback_run_created"] = True
        _write_state(status_path, result)
        return result

    if _recent_success(fallback, now, fallback_success_fresh_seconds):
        stamp = _result_time(fallback)
        result["status"] = "FALLBACK_SCAN_SUCCESS"
        result["reason"] = "recent_workflow_dispatch_completed_successfully"
        result["last_fallback_scan_success_at_utc"] = _iso(stamp)
        result["dispatch_run_id"] = fallback.get("id")
        result["dispatch_url"] = fallback.get("html_url")
        result["validation"]["fallback_run_created"] = True
        result["validation"]["fallback_scan_succeeded"] = True
        _write_state(status_path, result)
        return result

    # GitHub listing can lag just after dispatch acceptance. Persistent cooldown
    # prevents a serial duplicate even when no run is visible yet.
    if _cooldown_active(previous, now, dispatch_cooldown_seconds):
        result["status"] = "DISPATCH_COOLDOWN"
        result["reason"] = "recent_dispatch_acceptance_waiting_for_visibility_or_completion"
        result["dispatch_run_id"] = previous.get("last_dispatch_run_id")
        result["dispatch_url"] = previous.get("last_dispatch_url")
        result["validation"]["fallback_dispatch_accepted"] = True
        result["validation"]["fallback_run_created"] = bool(previous.get("last_dispatch_run_id"))
        _write_state(status_path, result)
        return result

    if not token:
        result["status"] = "TOKEN_MISSING"
        result["reason"] = "fallback_needed_but_actions_write_token_not_configured"
        _write_state(status_path, result)
        return result

    try:
        response = dispatch(
            token=token,
            repo=repo,
            workflow=workflow,
            ref=ref,
            opener=opener,
        )
    except Exception as exc:
        return _api_error(result, previous, now, "create_workflow_dispatch", exc, status_path)

    run_id = response.get("workflow_run_id")
    run_url = response.get("html_url")
    result["dispatched"] = True
    result["dispatch_run_id"] = run_id
    result["dispatch_url"] = run_url
    result["last_api_success_at_utc"] = now.isoformat()
    result["last_dispatch_accepted_at_utc"] = now.isoformat()
    result["last_dispatch_run_id"] = run_id
    result["last_dispatch_url"] = run_url
    result["validation"]["fallback_dispatch_accepted"] = True
    result["validation"]["fallback_run_created"] = bool(run_id)
    if run_id:
        result["status"] = "DISPATCH_RUN_CREATED"
        result["reason"] = "workflow_dispatch_accepted_and_run_id_returned"
    else:
        result["status"] = "DISPATCH_ACCEPTED_UNCONFIRMED"
        result["reason"] = "workflow_dispatch_accepted_but_run_creation_not_confirmed"
    _write_state(status_path, result)
    return result


def main():
    result = run_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "TOKEN_MISSING":
        raise SystemExit(3)
    if result["status"] == "API_ERROR":
        raise SystemExit(4)


if __name__ == "__main__":
    main()
