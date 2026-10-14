"""Offline contracts for the single-writer bounded subprocess drain."""
import importlib
import json
import signal
import subprocess
from types import SimpleNamespace

import pytest

from test_slack_forwarding_workflow import (
    FORWARD_GUARD, SCAN_COMMAND, run_command, scalar, section, step, workflow,
)


@pytest.fixture
def drain():
    if importlib.util.find_spec("market_data.slack_drain") is None:
        pytest.skip("bounded drain is not implemented yet")
    return importlib.import_module("market_data.slack_drain")


def test_bounded_drain_is_available():
    assert importlib.util.find_spec("market_data.slack_drain"), "bounded drain is missing"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Drain tests must never use the network")
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)


def forward(**changes):
    return dict(delivered=1, pending=24, retry_wait=0, unknown=0,
                needs_attention=0, duplicate_source=0, rejected=0,
                paused_reason="budget_exhausted", discovery_complete=True) | changes


def prepare(**changes):
    return dict(ledger_ready=True, remaining_budget=26.5, paused_reason=None) | changes


class Runner:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            return result(command, **kwargs)
        if isinstance(result, subprocess.CompletedProcess):
            return result
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")


def execute(drain, monkeypatch, capsys, results, first="17.25", job_started=None):
    runner = Runner(results)
    monkeypatch.setenv("QD_SLACK_JOB_STARTED_MONOTONIC",
                       str(drain.time.monotonic()) if job_started is None else job_started)
    monkeypatch.setattr(drain.subprocess, "run", runner)
    code = drain.main(["--first-budget-seconds", first])
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out), runner


def test_first_forward_consumes_exported_remainder_and_next_pair_shares_30_seconds(
        drain, monkeypatch, capsys):
    code, report, runner = execute(drain, monkeypatch, capsys, [
        forward(delivered=2), prepare(remaining_budget=24.1256789),
        forward(delivered=3, pending=0, paused_reason=None),
    ])
    assert code == 0
    assert report["delivered"] == 5 and report["slices_started"] == 2
    assert report["paused_reason"] == "drained"
    commands = [command for command, _ in runner.calls]
    assert [command[3] for command in commands] == ["forward", "prepare", "forward"]
    assert commands[0][4:] == ["--budget-seconds", "17.250000"]
    assert commands[1][4:] == []  # Never initialize or reset an existing ledger.
    assert commands[2][4:] == ["--budget-seconds", "24.125678"]
    assert all(command[:3] == [drain.sys.executable, "-m", "market_data.slack_alerts"]
               for command in commands)


def test_eight_slice_cap_includes_initial_prepare_allowance(drain, monkeypatch, capsys):
    results = [forward()]
    for _ in range(7):
        results += [prepare(remaining_budget=30), forward()]
    code, report, runner = execute(drain, monkeypatch, capsys, results, first="30")
    assert code == 0 and report["paused_reason"] == "slice_limit"
    assert report["slices_started"] == 8 and report["delivered"] == 8
    assert len(runner.calls) == 15
    assert drain.MAX_SLICES * drain.SLICE_SECONDS == 240
    assert drain.MAX_WALL_SECONDS == 270


@pytest.mark.parametrize("changes,reason", [
    ({"pending": 0, "paused_reason": None}, "drained"),
    ({"delivered": 0}, "no_verified_progress"),
    ({"retry_wait": 1}, "retry_wait"),
    ({"paused_reason": "channel_hold"}, "channel_hold"),
    ({"paused_reason": "delivery_uncertain"}, "delivery_uncertain"),
    ({"paused_reason": "ledger_unavailable"}, "ledger_unavailable"),
    ({"paused_reason": "discovery_unavailable"}, "discovery_unavailable"),
    ({"paused_reason": None}, "forward_paused"),
    ({"paused_reason": "https://secret.invalid/credential"}, "forward_paused"),
])
def test_stop_conditions_never_start_another_process(drain, monkeypatch, capsys, changes, reason):
    code, report, runner = execute(drain, monkeypatch, capsys, [forward(**changes)])
    assert report["paused_reason"] == reason and len(runner.calls) == 1
    assert "secret.invalid" not in json.dumps(report)


def test_historical_unknown_and_attention_do_not_block_confirmed_progress(drain, monkeypatch, capsys):
    code, report, runner = execute(drain, monkeypatch, capsys, [
        forward(unknown=15, needs_attention=4), prepare(),
        forward(delivered=2, pending=0, unknown=15, needs_attention=4, paused_reason=None),
    ])
    assert code == 0 and report["delivered"] == 3 and len(runner.calls) == 3
    assert report["unknown"] == 15 and report["needs_attention"] == 4


@pytest.mark.parametrize("prepared", [
    prepare(ledger_ready=False), prepare(paused_reason="ledger_unavailable"),
    prepare(remaining_budget=0), prepare(remaining_budget=31),
    prepare(remaining_budget=float("nan")), prepare(remaining_budget=True),
    {"ledger_ready": True},
])
def test_prepare_failure_stops_before_another_forward(drain, monkeypatch, capsys, prepared):
    _, report, runner = execute(drain, monkeypatch, capsys, [forward(), prepared])
    assert len(runner.calls) == 2 and report["slices_started"] == 1
    assert report["paused_reason"] in {"prepare_failed", "invalid_prepare_report"}


@pytest.mark.parametrize("phase", ["prepare", "forward"])
@pytest.mark.parametrize("failure", [
    subprocess.CompletedProcess([], 1, "raw secret stdout", "raw secret stderr"),
    subprocess.CompletedProcess([], -signal.SIGTERM, "secret", "secret"),
    OSError("secret exception details"),
    subprocess.TimeoutExpired("secret command", 30, output="secret", stderr="secret"),
    subprocess.CompletedProcess([], 0, "not JSON secret", "secret"),
])
def test_child_failures_are_sanitized_and_stop(drain, monkeypatch, capsys, phase, failure):
    results = [forward(), failure] if phase == "prepare" else [failure]
    code, report, runner = execute(drain, monkeypatch, capsys, results)
    assert code != 0 and len(runner.calls) == len(results)
    assert "secret" not in json.dumps(report)
    assert report["paused_reason"] in {
        "prepare_failed", "forward_failed", "child_timeout", "cancelled",
        "invalid_prepare_report", "invalid_forward_report",
    }


@pytest.mark.parametrize("bad", [
    forward(delivered=True), forward(delivered=-1), forward(pending="4"),
    forward(retry_wait=None), {"delivered": 2, "pending": 3}, [],
])
def test_unverified_summary_never_proves_progress(drain, monkeypatch, capsys, bad):
    code, report, runner = execute(drain, monkeypatch, capsys, [bad])
    assert code == 1 and report["paused_reason"] == "invalid_forward_report"
    assert report["delivered"] == 0 and len(runner.calls) == 1


@pytest.mark.parametrize("first", ["-1", "31", "nan", "inf", "credential-secret", ""])
def test_invalid_initial_allowance_starts_no_process(drain, monkeypatch, capsys, first):
    code, report, runner = execute(drain, monkeypatch, capsys, [], first=first)
    assert code == 1 and report["paused_reason"] == "invalid_configuration"
    assert runner.calls == []


def test_zero_allowance_does_not_refresh_the_first_slice(drain, monkeypatch, capsys):
    code, report, runner = execute(drain, monkeypatch, capsys, [], first="0")
    assert code == 0 and report["paused_reason"] == "no_initial_budget"
    assert runner.calls == []


def test_wall_deadline_stops_before_starting_next_slice(drain, monkeypatch, capsys):
    clock = [0.0]
    monkeypatch.setattr(drain.time, "monotonic", lambda: clock[0])
    def first_result(*args, **kwargs):
        clock[0] = drain.MAX_WALL_SECONDS
        return SimpleNamespace(returncode=0, stdout=json.dumps(forward()), stderr="")
    _, report, runner = execute(drain, monkeypatch, capsys, [first_result])
    assert report["paused_reason"] == "wall_limit" and len(runner.calls) == 1


def test_near_deadline_stops_before_starting_another_prepare(drain, monkeypatch, capsys):
    clock = [0.0]
    monkeypatch.setattr(drain.time, "monotonic", lambda: clock[0])
    def first_result(*args, **kwargs):
        clock[0] = drain.MAX_WALL_SECONDS - 1
        return SimpleNamespace(returncode=0, stdout=json.dumps(forward()), stderr="")
    _, report, runner = execute(drain, monkeypatch, capsys, [
        first_result, subprocess.TimeoutExpired("safe", 1),
    ])
    assert len(runner.calls) == 1
    assert runner.calls[0][1]["timeout"] <= 19.25
    assert report["paused_reason"] == "wall_limit"


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_cancellation_restores_signal_handlers_and_starts_no_continuation(
        drain, monkeypatch, capsys, signum):
    previous = signal.getsignal(signum)
    def cancelled(*args, **kwargs):
        signal.getsignal(signum)(signum, None)
        pytest.fail("Cancellation handler must interrupt the active child")
    code, report, runner = execute(drain, monkeypatch, capsys, [cancelled])
    assert code == 128 + signum and report["paused_reason"] == "cancelled"
    assert len(runner.calls) == 1 and signal.getsignal(signum) == previous


def test_children_inherit_trust_context_without_overwriting_step_outputs(drain, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_OUTPUT", "private-output")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", "private-summary")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/example")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    _, _, runner = execute(drain, monkeypatch, capsys, [forward(pending=0, paused_reason=None)])
    _, options = runner.calls[0]
    assert options["env"]["GITHUB_REF"] == "refs/heads/example"
    assert options["env"]["GITHUB_TOKEN"] == "secret-token"
    assert "GITHUB_OUTPUT" not in options["env"] and "GITHUB_STEP_SUMMARY" not in options["env"]
    assert options["capture_output"] is True and options["text"] is True
    assert options.get("shell", False) is False


def test_workflow_integrates_drain_without_repeating_or_masking_scan_exit_2():
    assert run_command(step("slack_forward")) == (
        'python3 -m market_data.slack_drain --first-budget-seconds "$QD_SLACK_REMAINING_BUDGET"')
    assert scalar(step("slack_forward"), "if") == FORWARD_GUARD
    assert scalar(step("scan"), "continue-on-error") is None
    assert run_command(step("scan")) == SCAN_COMMAND
    assert workflow().count(SCAN_COMMAND) == 1
    assert "    timeout-minutes: 8\n" in workflow()
    assert section(workflow(), "concurrency") == (
        "  group: hosted-core-watch-fallback\n  cancel-in-progress: false\n\n")
    assert "      - 'market_data/slack_drain.py'\n" in workflow()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_real_cancelled_drain_kills_and_reaps_its_active_child(tmp_path, signum):
    """Exercise stdlib subprocess cleanup, not just the simulated runner."""
    import os
    from pathlib import Path
    import sys
    import time

    pid_file = tmp_path / "child-pid"
    worker_code = (
        "import os,time; from pathlib import Path; "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    supervisor_code = (
        "import subprocess,sys,os,time; from market_data import slack_drain; "
        "os.environ[\"QD_SLACK_JOB_STARTED_MONOTONIC\"]=str(time.monotonic()); "
        "real_run=subprocess.run; "
        f"slack_drain.subprocess.run=lambda command, **kwargs: real_run([sys.executable, '-c', {worker_code!r}], **kwargs); "
        "raise SystemExit(slack_drain.main(['--first-budget-seconds','30']))"
    )
    process = subprocess.Popen([sys.executable, "-c", supervisor_code],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               cwd=Path(__file__).resolve().parents[1])
    try:
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists(), "Offline child did not start"
        child_pid = int(pid_file.read_text())
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 128 + signum
        assert json.loads(stdout)["paused_reason"] == "cancelled"
        assert stderr == ""
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_verified_queue_continues_after_discovery_deferral_but_is_never_reported_complete(
        drain, monkeypatch, capsys):
    code, report, runner = execute(drain, monkeypatch, capsys, [
        forward(paused_reason="discovery_deferred", discovery_complete=False), prepare(),
        forward(delivered=2, pending=0, paused_reason="discovery_deferred", discovery_complete=False),
    ])
    assert code == 0 and len(runner.calls) == 3
    assert report["delivered"] == 3 and report["paused_reason"] == "discovery_deferred"


def test_discovery_deferral_without_confirmed_delivery_does_not_spin(drain, monkeypatch, capsys):
    _, report, runner = execute(drain, monkeypatch, capsys, [
        forward(delivered=0, paused_reason="discovery_deferred"),
    ])
    assert len(runner.calls) == 1 and report["paused_reason"] == "no_verified_progress"


def test_missing_prepare_pause_status_is_not_verified_readiness(drain, monkeypatch, capsys):
    prepared = prepare()
    del prepared["paused_reason"]
    code, report, runner = execute(drain, monkeypatch, capsys, [forward(), prepared])
    assert code == 1 and report["paused_reason"] == "invalid_prepare_report"
    assert len(runner.calls) == 2


@pytest.mark.parametrize("reason", [None, "budget_exhausted"])
def test_empty_verified_queue_cannot_hide_incomplete_discovery(drain, monkeypatch, capsys, reason):
    code, report, runner = execute(drain, monkeypatch, capsys, [
        forward(pending=0, discovery_complete=False, paused_reason=reason),
    ])
    assert code == 0 and len(runner.calls) == 1
    assert report["paused_reason"] == "discovery_incomplete"
    assert report["discovery_complete"] is False


@pytest.mark.parametrize("value", [None, 1, "true"])
def test_discovery_completion_must_be_verified_boolean(drain, monkeypatch, capsys, value):
    code, report, runner = execute(drain, monkeypatch, capsys, [forward(discovery_complete=value)])
    assert code == 1 and report["paused_reason"] == "invalid_forward_report"
    assert len(runner.calls) == 1


@pytest.mark.parametrize("started", ["-1", "nan", "inf", "secret-invalid", "101"])
def test_invalid_or_future_job_start_fails_closed(drain, monkeypatch, capsys, started):
    monkeypatch.setattr(drain.time, "monotonic", lambda: 100.0)
    code, report, runner = execute(drain, monkeypatch, capsys, [], job_started=started)
    assert code == 1 and report["paused_reason"] == "invalid_configuration"
    assert runner.calls == []


@pytest.mark.parametrize("elapsed", [420, 435, 480])
def test_slow_setup_or_stale_job_leaves_artifact_reserve_untouched(
        drain, monkeypatch, capsys, elapsed):
    monkeypatch.setattr(drain.time, "monotonic", lambda: 100.0 + elapsed)
    code, report, runner = execute(drain, monkeypatch, capsys, [], job_started="100")
    assert code == 0 and report["paused_reason"] == "wall_limit"
    assert runner.calls == []


def test_elapsed_job_budget_prevents_following_slice_despite_local_wall_room(
        drain, monkeypatch, capsys):
    clock = [500.0]  # Job began at 100; 35 seconds remain before artifact reserve.
    monkeypatch.setattr(drain.time, "monotonic", lambda: clock[0])
    def first_result(*args, **kwargs):
        clock[0] += 10
        return SimpleNamespace(returncode=0, stdout=json.dumps(forward()), stderr="")
    code, report, runner = execute(drain, monkeypatch, capsys, [first_result],
                                   first="10", job_started="100")
    assert code == 0 and len(runner.calls) == 1
    assert report["paused_reason"] == "wall_limit"


def test_workflow_records_start_before_setup_and_passes_it_only_to_drain():
    from test_slack_forwarding_workflow import environment, steps
    assert next(iter(steps().values())).splitlines()[0] == "Record hosted job start"
    assert scalar(step("job_budget"), "id") == "job_budget"
    assert "time.monotonic()" in run_command(step("job_budget"))
    assert environment(step("slack_forward"))["QD_SLACK_JOB_STARTED_MONOTONIC"] == (
        "${{ steps.job_budget.outputs.started_monotonic }}")
