"""External failover trigger for the GitHub hosted market-watch fallback.

The primary GitHub workflow keeps its native schedule trigger. This module is a
secondary dispatcher intended for a VPS/systemd timer. It dispatches the
workflow only when GitHub has no recent native schedule run.

Required secret:
- QD_GITHUB_FALLBACK_TOKEN: fine-grained PAT scoped to this repository with
  Actions: write. Never commit the token.
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


API = "https://api.github.com"
DEFAULT_REPO = "DropNowOfficial/quant-detective"
DEFAULT_WORKFLOW = "market-watch.yml"
DEFAULT_REF = "main"


class DispatchError(RuntimeError):
    pass


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _request(method, path, *, token=None, body=None, opener=urlopen, timeout=12):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "quant-detective-fallback-dispatch/0.1",
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


def latest_native_schedule(repo=DEFAULT_REPO, workflow=DEFAULT_WORKFLOW, *, token=None, opener=urlopen):
    query = urlencode({"event": "schedule", "per_page": 1})
    data = _request(
        "GET",
        f"/repos/{repo}/actions/workflows/{workflow}/runs?{query}",
        token=token or None,
        opener=opener,
    )
    runs = data.get("workflow_runs") or []
    if not runs:
        return None
    row = runs[0] if isinstance(runs[0], dict) else None
    if not row:
        return None
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "status": row.get("status"),
        "conclusion": row.get("conclusion"),
        "html_url": row.get("html_url"),
    }


def should_dispatch(last_run, *, now=None, max_native_age_seconds=420):
    now = now or datetime.now(timezone.utc)
    if last_run is None:
        return True, "no_native_schedule_run"
    created = _parse_time(last_run.get("created_at"))
    if created is None:
        return True, "native_schedule_timestamp_invalid"
    age = (now - created).total_seconds()
    if age < -60:
        return True, "native_schedule_timestamp_in_future"
    if age <= max_native_age_seconds:
        return False, "recent_native_schedule_exists"
    return True, "native_schedule_stale"


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


def run_once(
    *,
    token=None,
    repo=DEFAULT_REPO,
    workflow=DEFAULT_WORKFLOW,
    ref=DEFAULT_REF,
    status_path="/var/lib/quant-detective/github-fallback-dispatch.json",
    now=None,
    max_native_age_seconds=420,
    opener=urlopen,
):
    now = now or datetime.now(timezone.utc)
    token = token if token is not None else os.getenv("QD_GITHUB_FALLBACK_TOKEN", "").strip()

    last = latest_native_schedule(repo, workflow, token=token or None, opener=opener)
    do_dispatch, reason = should_dispatch(
        last,
        now=now,
        max_native_age_seconds=max_native_age_seconds,
    )

    result = {
        "checked_at_utc": now.isoformat(),
        "repo": repo,
        "workflow": workflow,
        "native_schedule": last,
        "reason": reason,
        "dispatched": False,
        "dispatch_run_id": None,
        "dispatch_url": None,
    }

    if do_dispatch:
        if not token:
            result["status"] = "TOKEN_MISSING"
        else:
            response = dispatch(
                token=token,
                repo=repo,
                workflow=workflow,
                ref=ref,
                opener=opener,
            )
            result["status"] = "DISPATCHED"
            result["dispatched"] = True
            result["dispatch_run_id"] = response.get("workflow_run_id")
            result["dispatch_url"] = response.get("html_url")
    else:
        result["status"] = "NATIVE_SCHEDULE_HEALTHY"

    if status_path:
        target = Path(status_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            handle.write(payload)
            temp = Path(handle.name)
        os.replace(temp, target)
    return result


def main():
    result = run_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "TOKEN_MISSING":
        raise SystemExit(3)


if __name__ == "__main__":
    main()
