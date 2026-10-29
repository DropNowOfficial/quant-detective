"""Bounded, sequential continuation of the existing durable Slack sender.

The workflow prepares once before scanning. Its first forward uses exactly that
prepare's remaining allowance. Each later prepare/forward pair gets one new
30-second allocation by default, in fresh CLI processes with the same checks.
One proved pre-attempt capacity stop may choose 60-second pairs, debited from the
same 240-second activity pool. Initial prepare costs a full 30-second allocation;
later prepare keeps its 30-second ceiling and forward uses its remainder plus
borrowed capacity. At most eight slices remain, independently limited by a
270-second wall ceiling and the eight-minute job's final 45-second reserve.
A full next pair must fit before it starts.
The retained-ledger 25-symbol burst fixture fits this eight-slice allowance.

Confirmed deliveries justify continuation. Once per drain, a verified budget stop
before any attempt may also grant a fresh slice for an unattempted pending source,
after either complete discovery or proven read-only discovery deferral.
Other discovery-only progress, holds, uncertainty, invalid reports and process
failures stop this job's drain. Existing
quarantined unknown entries remain untouched and do not block healthy work.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
import signal
import subprocess
import sys
import time

from market_data.slack_alerts import EXTENDED_SLICE_SECONDS, safe_budget_diagnostic, safe_discovery_diagnostic

SLICE_SECONDS = 30.0
MAX_SLICES = 8
MAX_ACTIVITY_SECONDS = 240.0
MAX_WALL_SECONDS = 270.0
PROCESS_GRACE_SECONDS = 2.0
JOB_TIMEOUT_SECONDS = 480.0
ARTIFACT_RESERVE_SECONDS = 45.0
COUNTS = ("delivered", "pending", "retry_wait", "unknown", "needs_attention",
          "duplicate_source", "rejected")
SAFE_PAUSES = frozenset({
    "budget_exhausted", "channel_hold", "delivery_uncertain", "ledger_unavailable",
    "discovery_unavailable", "discovery_deferred", "invalid_configuration", "disabled", "untrusted_context",
    "recovery_hold",
    "internal_error",
})


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # Never echo arbitrary arguments, paths or credentials.
        raise ValueError("Invalid arguments")


class _Cancelled(BaseException):
    def __init__(self, signum):
        self.signum = signum


class _ChildFailure(Exception):
    def __init__(self, reason, code=1):
        self.reason = reason
        self.code = code


@contextmanager
def _cancellation():
    def stop(signum, frame):
        raise _Cancelled(signum)
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _allowance(value, maximum=SLICE_SECONDS):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid allowance")
    if not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("Invalid allowance")
    # Match prepare's exported precision without rounding a budget upwards.
    return math.floor(value * 1_000_000) / 1_000_000


def _child(phase, allowance, deadline):
    remaining = deadline - time.monotonic()
    if remaining < allowance + PROCESS_GRACE_SECONDS:
        raise _ChildFailure("wall_limit", 0)
    command = [sys.executable, "-m", "market_data.slack_alerts", phase]
    if phase == "forward":
        command += ["--budget-seconds", f"{allowance:.6f}"]
        if allowance > SLICE_SECONDS:
            command += ["--extended-slice"]
    env = dict(os.environ)
    # Continuation must not replace the original prepare step's readiness, or
    # fill the job summary with multiple misleading per-process queue totals.
    env.pop("GITHUB_OUTPUT", None)
    env.pop("GITHUB_STEP_SUMMARY", None)
    try:
        # run() kills and reaps its child on timeout or BaseException, including
        # our cancellation handler. It never leaves another writer running.
        result = subprocess.run(
            command, capture_output=True, text=True, env=env,
            timeout=min(allowance + PROCESS_GRACE_SECONDS, remaining),
        )
    except subprocess.TimeoutExpired:
        raise _ChildFailure("child_timeout") from None
    except (OSError, ValueError):
        raise _ChildFailure(f"{phase}_failed") from None
    if result.returncode < 0:
        raise _ChildFailure("cancelled", 128 - result.returncode)
    if result.returncode:
        raise _ChildFailure(f"{phase}_failed")
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise _ChildFailure(f"invalid_{phase}_report") from None
    if not isinstance(data, dict):
        raise _ChildFailure(f"invalid_{phase}_report")
    return data


def _drain(first_allowance, report, job_started):
    deadline = min(time.monotonic() + MAX_WALL_SECONDS,
                   job_started + JOB_TIMEOUT_SECONDS - ARTIFACT_RESERVE_SECONDS)
    if first_allowance <= 0:
        report["paused_reason"] = "no_initial_budget"
        return
    allowance = first_allowance
    slice_allocation = SLICE_SECONDS
    allocated = SLICE_SECONDS  # Includes the already completed initial prepare.
    report["allocated_activity_seconds"] = allocated
    unused_pre_attempt_continuation = True
    for index in range(MAX_SLICES):
        if time.monotonic() >= deadline:
            report["paused_reason"] = "wall_limit"
            return
        if index:
            if allocated + slice_allocation > MAX_ACTIVITY_SECONDS:
                report["paused_reason"] = "activity_limit"
                return
            if deadline - time.monotonic() < slice_allocation + 2 * PROCESS_GRACE_SECONDS:
                report["paused_reason"] = "wall_limit"
                return
            allocated += slice_allocation
            report["allocated_activity_seconds"] = allocated
            prepared = _child("prepare", SLICE_SECONDS, deadline)
            if "paused_reason" not in prepared:
                raise _ChildFailure("invalid_prepare_report")
            try:
                prepared_remainder = _allowance(prepared["remaining_budget"])
                # Prepare keeps its 30s ceiling; only its unspent remainder plus
                # borrowed pool capacity reaches forward. No double allocation.
                allowance = _allowance(prepared_remainder + slice_allocation - SLICE_SECONDS,
                                       maximum=slice_allocation)
            except (KeyError, ValueError):
                raise _ChildFailure("invalid_prepare_report") from None
            if (prepared.get("ledger_ready") is not True
                    or prepared.get("paused_reason") is not None or prepared_remainder <= 0):
                raise _ChildFailure("prepare_failed")
        report["slices_started"] += 1
        forwarded = _child("forward", allowance, deadline)
        if (not all(type(forwarded.get(key)) is int and forwarded[key] >= 0 for key in COUNTS)
                or type(forwarded.get("discovery_complete")) is not bool
                or type(forwarded.get("discovery_deferred", False)) is not bool
                or (forwarded.get("discovery_deferred", False) and forwarded.get("discovery_complete"))
                or "paused_reason" not in forwarded
                or not isinstance(forwarded["paused_reason"], (str, type(None)))):
            raise _ChildFailure("invalid_forward_report")
        report["delivered"] += forwarded["delivered"]
        report["discovery_complete"] = forwarded["discovery_complete"]
        report["discovery_deferred"] = forwarded.get("discovery_deferred", False)
        report.update({key: forwarded[key] for key in COUNTS if key != "delivered"})
        report["discovery_failure"] = safe_discovery_diagnostic(forwarded.get("discovery_failure"))
        reason = forwarded["paused_reason"]
        stage = forwarded.get("budget_stop_stage")
        # Fixed vocabulary only: never echo arbitrary child diagnostics.
        report["budget_stop_stage"] = stage if isinstance(stage, str) and stage in {
            "ledger_load", "recover_attempts", "discovery", "queue_select",
            "channel_wait", "source_validation", "pre_attempt_reserve",
            "attempt_write", "channel_reservation", "post", "receipt", "channel_closeout",
        } else None
        if report["budget_stop_stage"] is not None:
            stop = {"slice": index + 1, "stage": report["budget_stop_stage"]}
            diagnostic = safe_budget_diagnostic(forwarded.get("budget_diagnostic"))
            if diagnostic is not None and diagnostic["phase"] == report["budget_stop_stage"]:
                stop["diagnostic"] = diagnostic
            report["budget_stops"].append(stop)
        # A new uncertainty/hold/error always stops, even if pending is zero.
        if reason not in {None, "budget_exhausted", "discovery_deferred"}:
            report["paused_reason"] = reason if reason in SAFE_PAUSES else "forward_paused"
            return
        if forwarded["retry_wait"]:
            report["paused_reason"] = "retry_wait"
            return
        if not forwarded["pending"]:
            report["paused_reason"] = ("discovery_deferred" if reason == "discovery_deferred" else
                                       "drained" if forwarded["discovery_complete"] else "discovery_incomplete")
            return
        if reason not in {"budget_exhausted", "discovery_deferred"}:
            report["paused_reason"] = "forward_paused"
            return
        if not forwarded["delivered"]:
            if (unused_pre_attempt_continuation and reason == "budget_exhausted"
                    and (forwarded["discovery_complete"] or forwarded.get("discovery_deferred") is True)
                    and stage == "pre_attempt_reserve"
                    and forwarded.get("safe_pending_continuation") is True):
                unused_pre_attempt_continuation = False
                report["pre_attempt_continuations"] = 1
                evidence = safe_budget_diagnostic(forwarded.get("budget_diagnostic"))
                if (evidence is not None and evidence["phase"] == "pre_attempt_reserve"
                        and SLICE_SECONDS < evidence.get("required_reserve_seconds", 0) < EXTENDED_SLICE_SECONDS
                        and evidence.get("remaining_seconds") is not None
                        and evidence["remaining_seconds"] < evidence["required_reserve_seconds"]):
                    # Keep this allocation after confirmed progress; never grant
                    # another zero-progress exception. All pairs debit the pool.
                    slice_allocation = EXTENDED_SLICE_SECONDS
            else:
                report["paused_reason"] = "no_verified_progress"
                return
    report["paused_reason"] = "slice_limit"


def main(argv=None):
    report = {key: None for key in COUNTS}
    report.update(delivered=0, slices_started=0, discovery_complete=None,
                  pre_attempt_continuations=0, allocated_activity_seconds=0, budget_stop_stage=None, budget_stops=[], discovery_deferred=False, discovery_failure=None,
                  paused_reason="invalid_configuration")
    code = 0
    try:
        parser = _SafeParser(description="Bounded sequential durable Slack notification drain")
        parser.add_argument("--first-budget-seconds", required=True, type=float)
        args = parser.parse_args(argv)
        allowance = _allowance(args.first_budget_seconds)
        job_started = float(os.environ["QD_SLACK_JOB_STARTED_MONOTONIC"])
        if not math.isfinite(job_started) or not 0 <= job_started <= time.monotonic():
            raise ValueError("Invalid job start")
        with _cancellation():
            _drain(allowance, report, job_started)
    except _Cancelled as error:
        report["paused_reason"] = "cancelled"
        code = 128 + error.signum
    except _ChildFailure as error:
        report["paused_reason"] = error.reason
        code = error.code
    except (KeyError, ValueError, TypeError):
        code = 1
    except Exception:
        report["paused_reason"] = "internal_error"
        code = 1
    # Only validated counts and fixed codes; never raw child stdout/stderr.
    report["count_scope"] = {
        "delivered": "newly_confirmed_across_drain",
        "queue": "last_verified_durable_snapshot",
    }
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
