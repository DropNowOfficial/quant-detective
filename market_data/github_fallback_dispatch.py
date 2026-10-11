"""External failover trigger for the GitHub hosted market-watch fallback.

This is a secondary scheduler. It never treats dispatch acceptance, a queued
workflow, or process liveness as proof that a market scan succeeded.
Success requires the hosted scan step to complete successfully. That proves
process execution, not complete market coverage, heartbeat, or delivery.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from zoneinfo import ZoneInfo

from .quote_validity import session_name


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
ACTIVE_RUN_MAX_AGE_SECONDS = 900


class DispatchError(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


urlopen = build_opener(_NoRedirect()).open
MAX_API_BYTES = 8 * 1024 * 1024
SCAN_STEP_NAME = "One-shot hosted fallback scan"


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
    parsed = urlsplit(path)
    workflow_path = re.fullmatch(r'/repos/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+/actions/workflows/[A-Za-z0-9_.-]+/(runs|dispatches)', parsed.path)
    jobs_path = method == 'GET' and re.fullmatch(r'/repos/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*/jobs', parsed.path)
    if (method not in {'GET', 'POST'} or parsed.scheme or parsed.netloc or parsed.fragment
            or re.search(r'[\x00-\x20\x7f]', path)
            or not (workflow_path or jobs_path)
            or any(part in {'.', '..'} for part in parsed.path.split('/'))):
        raise DispatchError('Invalid GitHub dispatch path')
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
            raw = response.read(MAX_API_BYTES + 1)
            if len(raw) > MAX_API_BYTES:
                raise DispatchError('GitHub response exceeds limit')
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        exc.close()
        raise DispatchError(f"GitHub HTTP {exc.code}") from None
    except (URLError, OSError, TimeoutError):
        raise DispatchError('GitHub transport unavailable') from None
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
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise DispatchError("Dispatch state unreadable") from None
    if not isinstance(data, dict):
        raise DispatchError("Dispatch state invalid")
    return data


def _write_state(path, value):
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temp = Path(handle.name)
    os.replace(temp, target)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _persistent(previous):
    return {
        "last_api_success_at_utc": previous.get("last_api_success_at_utc"),
        "last_native_scan_success_at_utc": previous.get("last_native_scan_success_at_utc"),
        "last_fallback_scan_success_at_utc": previous.get("last_fallback_scan_success_at_utc"),
        "last_dispatch_accepted_at_utc": previous.get("last_dispatch_accepted_at_utc"),
        "last_dispatch_run_id": previous.get("last_dispatch_run_id"),
        "last_dispatch_url": previous.get("last_dispatch_url"),
        "dispatch_attempt": previous.get("dispatch_attempt"),
    }


def _validation():
    return {
        "native_scan_succeeded": False,
        "fallback_dispatch_accepted": False,
        "fallback_run_created": False,
        "fallback_scan_succeeded": False,
        # Not checked by this component. These remain separate acceptance stages.
        "market_coverage_complete": None,
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
        "run_attempt": row.get("run_attempt"),
        "head_branch": row.get("head_branch"),
        "event": row.get("event"),
        "created_at": row.get("created_at"),
        "run_started_at": row.get("run_started_at"),
        "updated_at": row.get("updated_at"),
        "status": row.get("status"),
        "conclusion": row.get("conclusion"),
        "html_url": row.get("html_url"),
    }


def list_runs(event, repo=DEFAULT_REPO, workflow=DEFAULT_WORKFLOW, *, ref=DEFAULT_REF, token=None, opener=urlopen):
    query = urlencode({"event": event, "branch": ref, "per_page": 10})
    data = _request(
        "GET",
        f"/repos/{repo}/actions/workflows/{workflow}/runs?{query}",
        token=token or None,
        opener=opener,
    )
    rows = data.get("workflow_runs") or []
    return [item for row in rows if (item := _run_summary(row)) is not None and item["head_branch"] == ref]


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


def _recent_scan_success(run, now, max_age_seconds, *, repo, token, opener):
    """Verify execution, never infer scan success from a green workflow alone."""
    if not _recent_success(run, now, max_age_seconds):
        return False
    run_id = run.get("id")
    attempt = run.get("run_attempt")
    if not token or type(run_id) is not int or run_id <= 0:
        raise DispatchError("Scan evidence requires authentication and a valid run ID")
    if type(attempt) is not int or attempt <= 0:
        raise DispatchError("Scan evidence requires a valid run attempt")
    # The attempt-specific endpoint binds evidence even when job payloads omit
    # run_attempt: https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt
    query = urlencode({"per_page": 100})
    data = _request("GET", f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?{query}",
                    token=token, opener=opener)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise DispatchError("Invalid scan evidence")
    jobs = data["jobs"]
    count = data.get("total_count")
    # This workflow has one job. Refuse incomplete/truncated evidence rather
    # than treating a missing step on the first page as proof it did not run.
    if type(count) is not int or count != len(jobs) or count > 100:
        raise DispatchError("Incomplete scan evidence")
    scan_steps = []
    for job in jobs:
        if (not isinstance(job, dict) or job.get("run_id") != run_id
                or job.get("run_attempt", attempt) != attempt
                or not isinstance(job.get("steps"), list)):
            raise DispatchError("Invalid scan evidence identity")
        for step in job["steps"]:
            if not isinstance(step, dict):
                raise DispatchError("Invalid scan step evidence")
            if step.get("name") == SCAN_STEP_NAME:
                scan_steps.append(job.get("status") == "completed"
                                  and job.get("conclusion") == "success"
                                  and step.get("status") == "completed"
                                  and step.get("conclusion") == "success")
    return len(scan_steps) == 1 and scan_steps[0]


def _recent_active(run, now, max_age_seconds):
    if _run_phase(run) not in {s.upper() for s in ACTIVE_STATUSES}:
        return False
    age = _age_seconds(run, now)
    return age is not None and -60 <= age <= max_age_seconds


def _observe_run(result, field, run, now, success_fresh_seconds):
    """Keep workflow-result freshness separate from verified scan execution."""
    prefix = "native" if field == "native_schedule" else "fallback"
    result[field] = run
    result[f"{prefix}_result_age_seconds"] = _age_seconds(run, now)
    result[f"{prefix}_result_fresh"] = _recent_success(run, now, success_fresh_seconds)


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


def _api_error(result, previous, now, stage, exc, status_path, *, status="API_ERROR"):
    result.update(_persistent(previous))
    result["status"] = status
    result["reason"] = f"{stage}: GITHUB_REQUEST_FAILED"
    result["api_error_at_utc"] = now.isoformat()
    result["api_error_stage"] = stage
    _write_state(status_path, result)
    return result


def _state_error(result, stage):
    result["status"] = "STATE_ERROR"
    result["reason"] = f"{stage}: DISPATCH_STATE_UNAVAILABLE"
    return result


def _unresolved_attempt(result):
    attempt = result.get("dispatch_attempt")
    return (attempt is not None and (not isinstance(attempt, dict)
            or attempt.get("state") not in {"CONFIRMED", "RECONCILED"}))


def _reconcile_scan_attempt(result, run, *, repo, workflow, ref):
    """A subsequent verified scan restores service without attributing a POST."""
    attempt = result.get("dispatch_attempt")
    if not _unresolved_attempt(result) or not isinstance(attempt, dict):
        return
    if (attempt.get("repo"), attempt.get("workflow"), attempt.get("ref")) != (repo, workflow, ref):
        return
    started = _parse_time(attempt.get("started_at_utc"))
    created = _parse_time(run.get("created_at"))
    if (started is None or created is None or created < started.replace(microsecond=0)
            or run.get("id") in attempt.get("known_run_ids", [])):
        return
    result["dispatch_attempt"] = {**attempt, "state": "RECONCILED",
                                  "reconciled_run_id": run["id"],
                                  "resolution": "subsequent_scan_step_verified"}


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
    active_run_max_age_seconds=ACTIVE_RUN_MAX_AGE_SECONDS,
    opener=urlopen,
):
    now = now or datetime.now(timezone.utc)
    token = token if token is not None else os.getenv("QD_GITHUB_FALLBACK_TOKEN", "").strip()
    try:
        previous = _load_state(status_path)
    except DispatchError:
        return _state_error(_base_result(now, repo, workflow, {}), "read_dispatch_state")
    result = _base_result(now, repo, workflow, previous)
    result["ref"] = ref

    if not in_work_window(now):
        result["status"] = "OUTSIDE_MARKET_WINDOW"
        result["reason"] = "normal_no_run_outside_et_weekday_window"
        _write_state(status_path, result)
        return result

    result["runtime_session"] = session_name(now.timestamp())
    if result["runtime_session"] not in {"PRE", "RTH", "POST"}:
        result["status"] = "OUTSIDE_MARKET_SESSION"
        result["reason"] = "normal_no_run_closed_market_session"
        result["skip_reason"] = ("NON_TRADING_DAY" if result["runtime_session"] == "CLOSED"
                                 else "OUTSIDE_SESSION_HOURS")
        _write_state(status_path, result)
        return result

    try:
        native_runs = list_runs("schedule", repo, workflow, ref=ref, token=token or None, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "list_native_schedule_runs", exc, status_path)

    native = native_runs[0] if native_runs else None
    _observe_run(result, "native_schedule", native, now, native_success_fresh_seconds)
    native_phase = _run_phase(native)

    try:
        native_succeeded = _recent_scan_success(native, now, native_success_fresh_seconds,
                                                repo=repo, token=token, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "verify_native_scan_step", exc, status_path)
    if native_succeeded:
        _reconcile_scan_attempt(result, native, repo=repo, workflow=workflow, ref=ref)
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
    if _recent_active(native, now, active_run_max_age_seconds):
        result["status"] = f"NATIVE_SCHEDULE_{native_phase}"
        result["reason"] = "recent_native_run_exists_but_scan_not_completed"
        result["last_api_success_at_utc"] = now.isoformat()
        _write_state(status_path, result)
        return result

    try:
        fallback_runs = list_runs("workflow_dispatch", repo, workflow, ref=ref, token=token or None, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "list_workflow_dispatch_runs", exc, status_path)

    fallback = fallback_runs[0] if fallback_runs else None
    _observe_run(result, "workflow_dispatch", fallback, now, fallback_success_fresh_seconds)
    result["last_api_success_at_utc"] = now.isoformat()
    fallback_phase = _run_phase(fallback)

    if _recent_active(fallback, now, active_run_max_age_seconds):
        result["status"] = f"FALLBACK_RUN_{fallback_phase}"
        result["reason"] = "recent_workflow_dispatch_is_active"
        result["dispatch_run_id"] = fallback.get("id")
        result["dispatch_url"] = fallback.get("html_url")
        result["validation"]["fallback_run_created"] = True
        _write_state(status_path, result)
        return result

    try:
        fallback_succeeded = _recent_scan_success(fallback, now, fallback_success_fresh_seconds,
                                                  repo=repo, token=token, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "verify_fallback_scan_step", exc, status_path)
    if fallback_succeeded:
        _reconcile_scan_attempt(result, fallback, repo=repo, workflow=workflow, ref=ref)
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

    # An accepted request can lose its response or run listing. Time alone never
    # proves it was not sent. Keep this quarantine across restarts until a later
    # scan is verified above, or an operator reviews and repairs local state.
    if _unresolved_attempt(result):
        result["status"] = "DISPATCH_OUTCOME_UNKNOWN"
        result["reason"] = "previous_dispatch_requires_scan_verification_or_operator_review"
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

    # Recovery race guard: re-read both event streams immediately before POST.
    # If native schedule recovered or another external actor already dispatched,
    # suppress this request rather than relying on the earlier snapshot.
    try:
        native_recheck = list_runs("schedule", repo, workflow, ref=ref, token=token, opener=opener)
        fallback_recheck = list_runs("workflow_dispatch", repo, workflow, ref=ref, token=token, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "pre_dispatch_race_recheck", exc, status_path)

    native_latest = native_recheck[0] if native_recheck else None
    fallback_latest = fallback_recheck[0] if fallback_recheck else None
    _observe_run(result, "native_schedule", native_latest, now, native_success_fresh_seconds)
    _observe_run(result, "workflow_dispatch", fallback_latest, now, fallback_success_fresh_seconds)
    try:
        native_succeeded = _recent_scan_success(native_latest, now, native_success_fresh_seconds,
                                                repo=repo, token=token, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "pre_dispatch_verify_native_scan_step", exc, status_path)
    if native_succeeded:
        _reconcile_scan_attempt(result, native_latest, repo=repo, workflow=workflow, ref=ref)
        stamp = _result_time(native_latest)
        result["status"] = "NATIVE_SCHEDULE_SUCCESS"
        result["reason"] = "native_recovered_before_external_dispatch"
        result["last_api_success_at_utc"] = now.isoformat()
        result["last_native_scan_success_at_utc"] = _iso(stamp)
        result["validation"]["native_scan_succeeded"] = True
        _write_state(status_path, result)
        return result
    if _recent_active(native_latest, now, active_run_max_age_seconds):
        phase = _run_phase(native_latest)
        result["status"] = f"NATIVE_SCHEDULE_{phase}"
        result["reason"] = "native_run_appeared_before_external_dispatch"
        result["last_api_success_at_utc"] = now.isoformat()
        _write_state(status_path, result)
        return result
    if _recent_active(fallback_latest, now, active_run_max_age_seconds):
        phase = _run_phase(fallback_latest)
        result["status"] = f"FALLBACK_RUN_{phase}"
        result["reason"] = "workflow_dispatch_appeared_before_duplicate_request"
        result["last_api_success_at_utc"] = now.isoformat()
        result["dispatch_run_id"] = fallback_latest.get("id")
        result["dispatch_url"] = fallback_latest.get("html_url")
        result["validation"]["fallback_run_created"] = True
        _write_state(status_path, result)
        return result
    try:
        fallback_succeeded = _recent_scan_success(fallback_latest, now, fallback_success_fresh_seconds,
                                                  repo=repo, token=token, opener=opener)
    except Exception as exc:
        return _api_error(result, previous, now, "pre_dispatch_verify_fallback_scan_step", exc, status_path)
    if fallback_succeeded:
        _reconcile_scan_attempt(result, fallback_latest, repo=repo, workflow=workflow, ref=ref)
        stamp = _result_time(fallback_latest)
        result["status"] = "FALLBACK_SCAN_SUCCESS"
        result["reason"] = "fallback_completed_before_duplicate_request"
        result["last_api_success_at_utc"] = now.isoformat()
        result["last_fallback_scan_success_at_utc"] = _iso(stamp)
        result["dispatch_run_id"] = fallback_latest.get("id")
        result["dispatch_url"] = fallback_latest.get("html_url")
        result["validation"]["fallback_run_created"] = True
        result["validation"]["fallback_scan_succeeded"] = True
        _write_state(status_path, result)
        return result

    # Write ahead of the non-idempotent POST. If the process stops at any point
    # after this record, a restart must not silently replay the request.
    if not status_path:
        return _state_error(result, "persist_dispatch_attempt")
    attempt = {"repo": repo, "workflow": workflow, "ref": ref,
               "started_at_utc": now.isoformat(), "state": "PENDING",
               "known_run_ids": [row["id"] for row in native_recheck + fallback_recheck]}
    result["dispatch_attempt"] = attempt
    result["status"] = "DISPATCH_OUTCOME_UNKNOWN"
    result["reason"] = "dispatch_attempt_recorded_before_request"
    try:
        _write_state(status_path, result)
    except OSError:
        return _state_error(result, "persist_dispatch_attempt")

    try:
        response = dispatch(
            token=token,
            repo=repo,
            workflow=workflow,
            ref=ref,
            opener=opener,
        )
        if not isinstance(response, dict):
            raise DispatchError("Invalid dispatch response")
        run_id = response.get("workflow_run_id")
        if run_id is not None and (type(run_id) is not int or run_id <= 0):
            raise DispatchError("Invalid dispatch run ID")
    except Exception as exc:
        attempt = {**attempt, "state": "UNKNOWN"}
        return _api_error(result, {**previous, "dispatch_attempt": attempt}, now,
                          "create_workflow_dispatch", exc, status_path, status="DISPATCH_OUTCOME_UNKNOWN")

    run_url = response.get("html_url")
    result["dispatch_attempt"] = {**attempt,
                                  "state": "CONFIRMED" if run_id else "ACCEPTED_UNCONFIRMED",
                                  "run_id": run_id}
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
    if result["status"] in {"API_ERROR", "STATE_ERROR", "DISPATCH_OUTCOME_UNKNOWN"}:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
