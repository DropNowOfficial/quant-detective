"""Hosted-workflow contracts and offline producer -> durable forward integration.

Read the small, fixed YAML layout with the standard library: no YAML dependency
or live GitHub/Slack access is needed. Exact expressions pin a case-insensitive
string truth model to the Actions guards; separate real CLI tests prove the
exact trust policy and readiness output. This does not execute hosted Actions.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import shlex

import pytest

from market_data import cli, github_alerts, slack_alerts as sa, us_watch
from test_quote_validity import bar, daily, event_at
from test_slack_alerts import (
    BASE, NOW, REPO, DeliveryHTTP, FakeClock, FakeHTTP,
    cli_env, daily_issue, make_store,
)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/market-watch.yml"
TRUST = (
    "!cancelled() && github.repository == 'DropNowOfficial/quant-detective' "
    "&& github.repository_id == '1369484548' && github.ref == 'refs/heads/main' "
    "&& contains(fromJSON('[\"push\",\"schedule\",\"workflow_dispatch\"]'), github.event_name) "
    "&& vars.QD_SLACK_ENABLED == 'true'"
)
PREPARE_GUARD = "${{ " + TRUST + " }}"
FORWARD_GUARD = (
    "${{ " + TRUST + " "
    "&& steps.scan.outcome != 'cancelled' "
    "&& steps.slack_prepare.outputs.ledger_ready == 'true' }}"
)
INITIALIZE_ARGUMENT = (
    "${{ github.event_name == 'workflow_dispatch' "
    "&& inputs.slack_operation == 'initialize' && '--initialize' || '' }}"
)
SCAN_COMMAND = "uv run python -m market_data.cli watch --once --github-alerts"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Workflow tests must never use the network")
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)


def workflow():
    return WORKFLOW.read_text(encoding="utf-8")


def section(text, name):
    match = re.search(rf"^{re.escape(name)}:\n(.*?)(?=^\S|\Z)", text, re.M | re.S)
    assert match, f"Missing YAML section {name}"
    return match[1]


def steps():
    chunks = re.split(r"^      - name: ", workflow(), flags=re.M)[1:]
    return {chunk.splitlines()[0]: chunk for chunk in chunks}


def step(step_id):
    found = [block for block in steps().values() if scalar(block, "id") == step_id]
    assert len(found) == 1, f"Expected one workflow step with id={step_id}"
    return found[0]


def scalar(block, key):
    match = re.search(rf"^        {re.escape(key)}: (.+)$", block, re.M)
    return match[1] if match else None


def environment(block):
    match = re.search(r"^        env:\n((?:          .+\n)+)", block, re.M)
    assert match, "Missing step-scoped environment"
    return dict(line.strip().split(": ", 1) for line in match[1].splitlines())


def run_command(block):
    match = re.search(r"^        run: \|\n((?:          .*\n|\n)+)", block, re.M)
    assert match, "Missing literal run command"
    return "\n".join(line[10:] for line in match[1].splitlines()).strip()


def actions_prepare_allowed(*, cancelled=False, repo=REPO, ref="refs/heads/main",
                            event="schedule", enabled="true"):
    # Actions string comparisons and contains(array, value) ignore case.
    return (not cancelled and repo.casefold() == REPO.casefold()
            and ref.casefold() == "refs/heads/main"
            and event.casefold() in {"push", "schedule", "workflow_dispatch"}
            and enabled.casefold() == "true")


def actions_forward_allowed(*, scan_outcome="success", ledger_ready="true", **context):
    return (actions_prepare_allowed(**context)
            and scan_outcome.casefold() != "cancelled"
            and ledger_ready.casefold() == "true")


def test_workflow_has_exact_cancel_safe_trusted_guards_and_step_order():
    assert scalar(step("slack_prepare"), "if") == PREPARE_GUARD
    assert scalar(step("slack_forward"), "if") == FORWARD_GUARD
    ids = [scalar(block, "id") for block in steps().values()]
    assert ids.index("slack_prepare") < ids.index("scan") < ids.index("slack_forward")
    assert list(steps())[-2:] == ["Write compact run summary", "Preserve full scan evidence"]


@pytest.mark.parametrize("scan_outcome", ["success", "failure", "skipped", "cancelled"])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("ledger_ready", ["true", "false", ""])
def test_workflow_forwards_persisted_partial_results_but_never_cancelled(
        scan_outcome, cancelled, ledger_ready):
    assert scalar(step("slack_forward"), "if") == FORWARD_GUARD
    assert actions_forward_allowed(scan_outcome=scan_outcome, cancelled=cancelled, ledger_ready=ledger_ready) is (
        scan_outcome in {"success", "failure", "skipped"} and not cancelled and ledger_ready == "true")


@pytest.mark.parametrize("changes,expected", [
    ({"enabled": ""}, False), ({"enabled": "false"}, False), ({"enabled": "TRUE"}, True),
    ({"repo": REPO.swapcase()}, True), ({"ref": "refs/heads/MAIN"}, True),
    ({"event": "PUSH"}, True), ({"event": "SCHEDULE"}, True),
    ({"event": "WORKFLOW_DISPATCH"}, True), ({"ledger_ready": "TRUE"}, True),
    ({"scan_outcome": "SKIPPED"}, True), ({"scan_outcome": "CANCELLED"}, False),
    ({"repo": "fork/quant-detective"}, False), ({"ref": "refs/heads/topic"}, False),
    ({"ref": "refs/pull/1/merge"}, False), ({"ref": "refs/tags/v1"}, False),
    ({"event": "pull_request"}, False), ({"event": "pull_request_target"}, False),
    ({"event": "issue_comment"}, False), ({"event": "workflow_run"}, False),
    ({"event": "push"}, True), ({"event": "schedule"}, True),
    ({"event": "workflow_dispatch"}, True),
])
def test_workflow_default_off_and_trust_truth_table(changes, expected):
    assert scalar(step("slack_forward"), "if") == FORWARD_GUARD
    assert actions_forward_allowed(**changes) is expected


def test_forwarding_failure_does_not_hide_scan_failure():
    assert scalar(step("scan"), "continue-on-error") is None
    assert scalar(step("scan"), "if") == "${{ inputs.slack_operation != 'health-test' }}"
    assert scalar(step("slack_prepare"), "continue-on-error") == "true"
    assert scalar(step("slack_forward"), "continue-on-error") == "true"
    assert run_command(step("scan")) == SCAN_COMMAND
    assert "    timeout-minutes: 8\n" in workflow()
    assert workflow().count("continue-on-error:") == 2


def test_workflow_has_only_explicit_trusted_dispatch_initialization():
    triggers = section(workflow(), "on")
    dispatch = triggers.split("  workflow_dispatch:\n", 1)[1]
    assert dispatch == (
        "    inputs:\n"
        "      slack_operation:\n"
        "        description: 'Slack ledger operation (initialization requires separate authorization)'\n"
        "        type: choice\n"
        "        required: true\n"
        "        default: run\n"
        "        options:\n"
        "          - run\n"
        "          - initialize\n"
        "          - health-test\n\n"
    )
    assert run_command(step("slack_prepare")) == (
        "python3 -m market_data.slack_alerts prepare " + INITIALIZE_ARGUMENT)
    assert workflow().count("--initialize") == 1
    assert scalar(step("slack_prepare"), "if") == PREPARE_GUARD


def test_workflow_scopes_credentials_and_forwards_only_remaining_activity_budget():
    blocks = steps()
    prepare, scan, forward = (environment(step(name)) for name in ("slack_prepare", "scan", "slack_forward"))
    common = {
        "GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}",
        "QD_SLACK_ENABLED": "${{ vars.QD_SLACK_ENABLED }}",
        "QD_SLACK_LEDGER_ISSUE": "${{ vars.QD_SLACK_LEDGER_ISSUE }}",
        "QD_SLACK_PRODUCER_IDS": "${{ vars.QD_SLACK_PRODUCER_IDS }}",
    }
    assert prepare == common
    assert scan == {"GITHUB_TOKEN": "${{ secrets.GITHUB_TOKEN }}", "QD_WATCH_OUTPUT": "market-watch.json"}
    assert forward == common | {
        "QD_SLACK_WEBHOOK_URL": "${{ secrets.QD_SLACK_WEBHOOK_URL }}",
        "QD_SLACK_REMAINING_BUDGET": "${{ steps.slack_prepare.outputs.remaining_budget }}",
    }
    assert run_command(step("slack_forward")) == (
        'python3 -m market_data.slack_alerts forward --budget-seconds "$QD_SLACK_REMAINING_BUDGET"')
    assert workflow().count("secrets.QD_SLACK_WEBHOOK_URL") == 1
    assert workflow().count("secrets.GITHUB_TOKEN") == 4
    assert workflow().count("env:") == 4
    for block in blocks.values():
        if scalar(block, "id") not in {"slack_prepare", "scan", "scan_health", "slack_forward"}:
            assert "GITHUB_TOKEN" not in block and "QD_SLACK_WEBHOOK_URL" not in block


def test_workflow_preserves_schedule_concurrency_permissions_and_scan_summary():
    text = workflow()
    triggers = section(text, "on")
    assert re.findall(r"^  (\w+):", triggers, re.M) == ["push", "schedule", "workflow_dispatch"]
    assert triggers.split("  schedule:\n", 1)[1].split("  workflow_dispatch:", 1)[0] == (
        "    # Redundant public-data fallback. The VPS watcher is a separate runtime.\n"
        "    # Short five-minute jobs avoid an hours-long blind spot if one schedule is dropped.\n"
        "    - cron: '2,7,12,17,22,27,32,37,42,47,52,57 4-19 * * 1-5'\n"
        "      timezone: 'America/New_York'\n"
        "    - cron: '2 20 * * 1-5'\n"
        "      timezone: 'America/New_York'\n"
    )
    assert section(text, "permissions") == "  contents: read\n  issues: write\n\n"
    assert section(text, "concurrency") == "  group: hosted-core-watch-fallback\n  cancel-in-progress: false\n\n"
    assert "    branches: [main]\n" in triggers
    assert re.findall(r"^      - '(.+)'$", triggers, re.M) == [
        "market_data/github_alerts.py", "market_data/slack_alerts.py", "market_data/scan_health.py",
        "market_data/us_watch.py", "market_data/quote_validity.py", ".github/workflows/market-watch.yml"]
    summary = steps()["Write compact run summary"].split("\n", 1)[1].rstrip() + "\n"
    # Exact pre-existing scan-summary block from baseline 582db9d.
    assert "scan_status" in summary and "runtime_session" in summary and "skip_reason" in summary


@pytest.mark.parametrize("updates,command,reason", [
    ({"QD_SLACK_ENABLED": None}, ["prepare"], "disabled"),
    ({"GITHUB_TOKEN": None}, ["prepare"], "invalid_configuration"),
    ({"QD_SLACK_WEBHOOK_URL": None}, ["forward"], "invalid_configuration"),
])
def test_missing_credentials_or_disabled_cli_never_contact_service(monkeypatch, tmp_path, capsys,
                                                                 updates, command, reason):
    step("slack_prepare")
    step("slack_forward")
    cli_env(monkeypatch, tmp_path, **updates)
    http = DeliveryHTTP()
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: http)
    assert sa.main(command) == 0
    assert json.loads(capsys.readouterr().out)["paused_reason"] == reason
    assert http.requests == [] and http.server["slack_posts"] == []


@pytest.mark.parametrize("updates,context,reason", [
    ({"QD_SLACK_ENABLED": "TRUE"}, {"enabled": "TRUE"}, "disabled"),
    ({"GITHUB_REPOSITORY": REPO.swapcase()}, {"repo": REPO.swapcase()}, "untrusted_context"),
    ({"GITHUB_REF": "refs/heads/MAIN"}, {"ref": "refs/heads/MAIN"}, "untrusted_context"),
    ({"GITHUB_EVENT_NAME": "WORKFLOW_DISPATCH"}, {"event": "WORKFLOW_DISPATCH"}, "untrusted_context"),
])
def test_case_insensitive_actions_guard_still_requires_exact_cli_trust_and_ready_output(
        monkeypatch, tmp_path, capsys, updates, context, reason):
    assert scalar(step("slack_prepare"), "if") == PREPARE_GUARD
    assert scalar(step("slack_forward"), "if") == FORWARD_GUARD
    assert actions_prepare_allowed(**context)
    assert actions_forward_allowed(**context)
    cli_env(monkeypatch, tmp_path, **updates)
    http = DeliveryHTTP()
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: http)
    original_get = sa.os.environ.get

    def guarded_get(key, default=None):
        assert key not in {"GITHUB_TOKEN", "GH_TOKEN", "QD_SLACK_WEBHOOK_URL"}
        return original_get(key, default)

    monkeypatch.setattr(sa.os.environ, "get", guarded_get)
    assert sa.main(["prepare", "--initialize"]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert prepared == {"ledger_ready": False, "remaining_budget": 0.0, "paused_reason": reason}
    outputs = dict(line.split("=", 1) for line in (tmp_path / "outputs").read_text().splitlines())
    assert outputs["ledger_ready"] == "false"
    assert not actions_forward_allowed(**context, ledger_ready=outputs["ledger_ready"])
    assert sa.main(["forward", "--budget-seconds", outputs["remaining_budget"]]) == 0
    assert json.loads(capsys.readouterr().out)["paused_reason"] == reason
    assert http.requests == [] and http.server["slack_posts"] == []


def test_initialization_failure_cannot_enable_forwarding(monkeypatch, tmp_path, capsys):
    assert scalar(step("slack_forward"), "if") == FORWARD_GUARD
    cli_env(monkeypatch, tmp_path)
    http = DeliveryHTTP()
    http.server["issues"].clear()
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: http)
    assert sa.main(["prepare", "--initialize"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ledger_ready"] is False and data["paused_reason"] == "ledger_unavailable"
    assert "ledger_ready=false\n" in (tmp_path / "outputs").read_text()
    assert not actions_forward_allowed(ledger_ready="false")
    assert all(request["method"] == "GET" for request in http.requests)
    assert http.server["slack_posts"] == []


@pytest.mark.parametrize("publisher_fails", [True, False])
@pytest.mark.parametrize("forward_delay", [5, 660])
def test_independent_forward_uses_only_saved_comments_and_preserves_incomplete_scan_failure(
        monkeypatch, tmp_path, capsys, publisher_fails, forward_delay):
    """Real publisher, scan CLI, prepare CLI, ledger, discovery and forward CLI.

    Only market input and remote I/O are replaced. Failure on the second event
    POST leaves the first available through a new transport, never a local list.
    """
    assert scalar(step("slack_forward"), "if") == FORWARD_GUARD
    command = shlex.split(run_command(step("scan")))
    assert command == shlex.split(SCAN_COMMAND)
    cli_env(monkeypatch, tmp_path, GITHUB_RUN_ID="999")
    clock = FakeClock(NOW)
    server = DeliveryHTTP(clock=clock)
    monkeypatch.setattr(sa, "SystemClock", lambda: clock)
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: server)
    assert sa.main(["prepare", "--initialize"]) == 0
    prepare = json.loads(capsys.readouterr().out)
    assert prepare["ledger_ready"] is True
    budget = dict(line.split("=", 1) for line in (tmp_path / "outputs").read_text().splitlines())["remaining_budget"]
    issue = daily_issue(server)
    server.add_comment(90, github_alerts.HEARTBEAT_MARK + "\nprevious heartbeat", issue_number=7)
    fresh = event_at("2026-10-07T14:00:00-04:00", rows=[
        bar("2026-10-07T13:55:00-04:00")], values=daily() | {"previous_session_date": "2026-10-06"})
    assert fresh["state"] == "ENTRY_ARMED"
    stale = event_at("2026-10-07T13:57:00-04:00", rows=[
        bar("2026-10-07T13:55:00-04:00")], values=daily() | {"previous_session_date": "2026-10-06"})
    rejected = [
        stale | {"symbol": "STALE", "event_key": "2026-10-07|STALE|ENTRY_ARMED"},
        {"symbol": "MISSING", "state": "ENTRY_ARMED", "event_key": "2026-10-07|MISSING|ENTRY_ARMED"},
    ]
    report = {
        "generated_at_et": "2026-10-07T14:00:00-04:00",
        "rows": [{"symbol": "NVDA", "status": "OK"}, {"symbol": "TSM", "status": "ERROR"}],
        "alerts": rejected + [fresh | {"event_key": f"2026-10-07|{symbol}|ENTRY_ARMED", "symbol": symbol}
                              for symbol in ("NVDA", "TSM")],
    }
    monkeypatch.setattr(us_watch, "scan_once", lambda **kwargs: deepcopy(report))
    event_posts = []

    def publisher_api(method, path, *, token, body=None):
        if method == "GET" and path.startswith(BASE + "/issues?"):
            return [deepcopy(issue)]
        if method == "GET" and path.startswith(BASE + "/issues/7/comments?"):
            return [deepcopy(c) for c in server.server["comments"].values() if c["issue_url"].endswith("/7")]
        if method == "PATCH" and path == BASE + "/issues/comments/90":
            server.server["comments"][90]["body"] = body["body"]
            return deepcopy(server.server["comments"][90])
        if method == "POST" and path == BASE + "/issues/7/comments":
            event_posts.append(body["body"])
            if publisher_fails and len(event_posts) == 2:
                raise RuntimeError("offline second event publication failed")
            return deepcopy(server.add_comment(100 + len(event_posts), body["body"], issue_number=7,
                                               created_at=NOW + timedelta(seconds=1)))
        raise AssertionError((method, path))

    monkeypatch.setattr(github_alerts, "_api", publisher_api)
    real_publish = github_alerts.publish
    monkeypatch.setattr(github_alerts, "publish", lambda value: real_publish(value, clock=clock.now))
    scan_file = tmp_path / "market-watch.json"
    monkeypatch.setenv("QD_WATCH_OUTPUT", str(scan_file))
    monkeypatch.setattr("sys.argv", command[4:] + ["--symbols", "NVDA,TSM"])
    with pytest.raises(SystemExit) as failed:
        cli.main()
    assert failed.value.code == 2
    output = capsys.readouterr()
    assert ("second event publication failed" if publisher_fails else "one-shot scan incomplete: TSM") in output.err
    saved_report = json.loads(scan_file.read_text())
    assert saved_report["scan_status"] == "INCOMPLETE"
    assert saved_report["alerts"] == report["alerts"]
    assert [r["event_key"] for r in saved_report["publication_rejections"]] == [e["event_key"] for e in rejected]
    assert "RECEIPT_EXPIRED" in saved_report["publication_rejections"][0]["reason"]
    assert saved_report["publication_rejections"][1]["reason"] == "MISSING_CLASSIFICATION_INPUTS"
    assert len(event_posts) == 2
    assert not any("STALE" in body or "MISSING" in body for body in event_posts)
    assert all("Source bar:" in body and "HTTP receipt:" in body for body in event_posts)
    assert actions_forward_allowed(scan_outcome="failure")
    # Remove all local report data before independent forwarding.
    scan_file.unlink()
    clock.sleep(forward_delay)
    fresh = DeliveryHTTP(server.server, clock=clock)
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: fresh)
    assert sa.main(["forward", "--budget-seconds", budget]) == 0
    forwarded = json.loads(capsys.readouterr().out)
    expected_ids = {101} if publisher_fails else {101, 102}
    assert forwarded["delivered"] == len(expected_ids)
    assert {post[2] for post in fresh.server["slack_posts"]} == expected_ids
    restored = make_store(FakeHTTP(fresh.server)).load()
    assert set(restored.entries) == expected_ids
    assert all(entry.status == "delivered" for entry in restored.entries.values())
    for entry in restored.entries.values():
        assert entry.source.generated_at_et == report["generated_at_et"]
        assert entry.source.run_id == "999"
        assert entry.source.body.splitlines()[1] == '<!-- qd-source:{"schema":1,"generated_at_et":"2026-10-07T14:00:00-04:00","run_id":"999"} -->'
        assert "2026-10-07T17:55:00+00:00" in entry.source.body
        assert "2026-10-07T14:00:00-04:00" in entry.source.body
    for _, payload, source_id in fresh.server["slack_posts"]:
        assert payload["text"].startswith(sa.HISTORY_PREFIX) is (forward_delay > 600)
        snapshot = "".join(block["text"]["text"] for block in payload["blocks"][1:])
        assert snapshot == fresh.server["comments"][source_id]["body"]
        assert payload.get("channel") is None
    assert failed.value.code == 2  # Forward success never replaces scan failure.


def test_health_runs_after_failure_before_forwarding_without_webhook_access():
    block = step("scan_health")
    assert scalar(block, "if") == PREPARE_GUARD
    assert scalar(block, "continue-on-error") is None
    env = environment(block)
    assert "QD_SLACK_WEBHOOK_URL" not in env
    assert env["QD_SCAN_OUTCOME"] == "${{ steps.scan.outcome }}"
    assert 'python3 -m market_data.scan_health' in run_command(block)
    assert '--delivery-test' in run_command(block)
    assert '--initialize' not in run_command(block)
    ids = [scalar(value, "id") for value in steps().values()]
    assert ids.index('scan') < ids.index('scan_health') < ids.index('slack_forward')

def test_all_workflow_action_dependencies_are_commit_pinned():
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        for ref in re.findall(r'uses:\s+([^\s#]+)', path.read_text()):
            assert re.fullmatch(r'[\w.-]+/[\w.-]+@[0-9a-f]{40}', ref), (path, ref)
