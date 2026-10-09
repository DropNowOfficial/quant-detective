"""Offline contracts for the manual operational-Issue writer diagnostic."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

from market_data import issue_writer_probe as p
from market_data import slack_alerts as sa

REPO = "DropNowOfficial/quant-detective"
ROOT = f"/repos/{REPO}/issues"
BOT = {"id": 41898282, "type": "Bot", "login": "github-actions[bot]"}
IDS = {43: 5755475722, 44: 5756285011, 45: 5773164000}
TOKEN = "fixture-secret-must-not-appear"
NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


def issue(number):
    return {"id": IDS[number], "number": number, "title": "Operational fixture",
            "body": "Ordinary observation issue.", "user": deepcopy(BOT), "locked": False,
            "url": f"{sa.API}{ROOT}/{number}", "repository_url": f"{sa.API}/repos/{REPO}",
            "html_url": f"https://github.com/{REPO}/issues/{number}"}


def entry(number):
    body = f"<!-- qd-event:fixture-{number} -->\nImmutable original {number}"
    source = sa.SourceEvent(REPO, number, 44, f"fixture-{number}", "AAPL", "ENTRY_ARMED",
                            NOW, NOW, NOW.isoformat(), "321",
                            f"https://github.com/{REPO}/issues/44#issuecomment-{number}",
                            body, hashlib.sha256(body.encode()).hexdigest())
    return sa.Entry(source, "delivered" if number < 575 else "unknown", 1, None,
                    f"attempt-{number}", NOW, NOW if number < 575 else None, None, None)


class API:
    """A persistent fake REST server, including commits with lost responses."""
    def __init__(self):
        self.issues = {number: issue(number) for number in IDS}
        self.comments = {}
        self.calls = []
        self.before = None
        self.after = None
        self.next_id = 10000
        records = [sa._encode(asdict(entry(number))) for number in range(500, 587)]
        manifest = sa.Manifest(1, REPO, sa.CHANNEL_ID, NOW, frozenset({101, 102}), NOW, 9, None)
        self.issues[43]["body"] = sa.MANIFEST_MARKER + "\n" + sa._json({
            "revision": 7, "operation_id": "untouched-manifest",
            "manifest": sa._encode(asdict(manifest))})
        for shard in range(9):
            self.add_comment(43, f"<!-- qd-slack-shard:v1:{shard} -->\n" + sa._json({
                "version": 1, "operation_id": f"untouched-shard-{shard}", "revision": 5,
                "records": records[shard * 10:(shard + 1) * 10]}), comment_id=200 + shard)

    def add_comment(self, number, body, *, comment_id=None, user=None):
        comment_id = comment_id or self.next_id
        self.next_id = max(self.next_id, comment_id + 1)
        value = {"id": comment_id, "body": body, "user": deepcopy(user or BOT),
                 "url": f"{sa.API}{ROOT}/comments/{comment_id}",
                 "issue_url": f"{sa.API}{ROOT}/{number}",
                 "html_url": f"https://github.com/{REPO}/issues/{number}#issuecomment-{comment_id}",
                 "created_at": NOW.isoformat(), "updated_at": NOW.isoformat()}
        self.comments[comment_id] = value
        return value

    def request(self, method, url, *, headers, body, timeout):
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "api.github.com"
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        assert 0 < timeout <= 5
        data = json.loads(body) if body else None
        call = {"method": method, "path": parsed.path, "query": parse_qs(parsed.query), "data": data}
        self.calls.append(call)
        if self.before:
            result = self.before(call)
            if result is not None:
                return result
        tail = parsed.path.removeprefix(ROOT + "/")
        status = 200
        if method == "GET" and tail.isdigit():
            result = self.issues.get(int(tail))
        elif method == "GET" and tail.startswith("comments/"):
            result = self.comments.get(int(tail.split("/")[1]))
        elif method == "GET" and tail.endswith("/comments"):
            number = int(tail.split("/")[0])
            result = [c for c in self.comments.values() if c["issue_url"] == f"{sa.API}{ROOT}/{number}"]
            page = int(call["query"].get("page", ["1"])[0])
            result = result[(page - 1) * 100:page * 100]
        elif method == "POST" and tail.endswith("/comments"):
            result = self.add_comment(int(tail.split("/")[0]), data["body"])
            status = 201
        elif method == "PATCH" and tail.startswith("comments/"):
            result = self.comments[int(tail.split("/")[1])]
            result.update(data)
        elif method == "PUT" and tail.endswith("/lock"):
            self.issues[int(tail.split("/")[0])]["locked"] = True
            result, status = None, 204
        else:
            pytest.fail(f"Unapproved write or request: {method} {parsed.path}")
        if self.after:
            answer = self.after(call, result)
            if answer is not None:
                return answer
        return sa.HttpResponse(status, {}, b"" if status == 204 else json.dumps(deepcopy(result)).encode())

    @property
    def writes(self):
        return [call for call in self.calls if call["method"] != "GET"]


def client(api):
    config = sa.Config(REPO, 43, frozenset({41898282}), sa.CHANNEL_ID)
    return sa.GitHubClient(config, TOKEN, api, p.ProbeBudget())


def run(api, operation="probe", **kwargs):
    return p.run(client(api), operation=operation, run_id="12345", **kwargs)


def probes(api):
    return [c for c in api.comments.values() if c["body"].startswith("<!-- qd-writer-probe:")]


def test_probe_creates_updates_and_exactly_reads_back_each_fixed_target():
    api = API()
    before = deepcopy((api.issues, api.comments))
    result = run(api)
    assert result["verified_probes"] == 3
    assert result["created_comments"] == 3 and result["updated_comments"] == 3
    assert result["verified_locks"] == 0 and result["ledger_unchanged"] is True
    assert result["ledger"] == {"shards": 9, "entries": 87, "delivered": 75, "unknown": 12}
    assert [c["method"] for c in api.writes] == ["POST", "PATCH"] * 3
    assert api.issues == before[0]
    assert all(api.comments[key] == value for key, value in before[1].items())
    assert len(result["comment_urls"]) == 3
    for comment in probes(api):
        assert "diagnostic" in comment["body"].lower() and "nontrade" in comment["body"].lower()
        assert "https://github.com/DropNowOfficial/quant-detective/actions/runs/12345" in comment["body"]
        assert "qd-event:" not in comment["body"] and "qd-slack-shard:" not in comment["body"]
        assert "verified" in comment["body"].lower()
        assert any(call["method"] == "GET" and call["path"] == f"{ROOT}/comments/{comment['id']}" for call in api.calls)


def test_lock_operation_proves_all_prelock_writes_before_locking_and_probes_after_each_lock():
    api = API()
    result = run(api, "lock-and-probe")
    assert result["verified_probes"] == 6 and result["verified_locks"] == 3
    assert result["created_comments"] == 6 and result["updated_comments"] == 6
    first_lock = next(i for i, call in enumerate(api.writes) if call["method"] == "PUT")
    assert [c["method"] for c in api.writes[:first_lock]] == ["POST", "PATCH"] * 3
    assert [c["method"] for c in api.writes[first_lock:]] == ["PUT", "POST", "PATCH"] * 3
    assert all(value["locked"] is True for value in api.issues.values())
    assert not any(call["method"] == "DELETE" for call in api.calls)


def test_rerun_reuses_exact_trusted_markers_without_a_second_post():
    api = API()
    run(api)
    count = len(api.writes)
    result = run(api, allow_create=False)
    assert result["created_comments"] == 0 and result["reused_comments"] == 3
    assert len(probes(api)) == 3
    assert [call["method"] for call in api.writes[count:]] == ["PATCH"] * 6


@pytest.mark.parametrize("number", [43, 44, 45])
@pytest.mark.parametrize("changes", [
    {"id": 1}, {"id": True}, {"number": True}, {"user": {"id": "41898282", "type": "Bot"}},
    {"user": {"id": 999, "type": "Bot", "login": "github-actions[bot]"}},
    {"user": BOT | {"type": "User"}}, {"url": "https://api.github.com/repos/evil/repo/issues/43"},
    {"repository_url": "https://api.github.com/repos/evil/repo"}, {"html_url": "https://evil.example"},
    {"pull_request": {}}, {"locked": "false"},
])
def test_all_fixed_identities_are_revalidated_before_any_writes(number, changes):
    api = API()
    api.issues[number].update(changes)
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert api.writes == []


def test_untrusted_ordinary_comments_do_not_block_probe():
    api = API()
    api.add_comment(44, "Ordinary public discussion", user={"id": 999, "type": "User"})
    assert run(api)["verified_probes"] == 3


@pytest.mark.parametrize("body", ["<!-- qd-writer-probe:12345:43:pre-lock -->", "<!-- qd-slack-shard:v1:0 -->\n{}", "<!-- qd-event:forged -->\nnot real"])
def test_untrusted_operational_marker_cannot_deduplicate_or_authorize_locks(body):
    api = API()
    api.add_comment(43, body, user={"id": 999, "type": "Bot"})
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert api.writes == []


def test_malformed_or_conflicting_trusted_probe_markers_fail_closed():
    for body in ("text <!-- qd-writer-probe:12345:43:pre-lock -->", "<!-- qd-writer-probe:12345:43:pre-lock -->\nwrong body"):
        api = API()
        api.add_comment(43, body)
        with pytest.raises(sa.LedgerUnavailable):
            run(api)
        assert not api.writes
    api = API()
    run(api)
    existing = probes(api)[0]
    api.add_comment(43, existing["body"])
    before = len(api.writes)
    with pytest.raises(sa.LedgerUnavailable):
        run(api)
    assert len(api.writes) == before


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_lost_write_response_is_recovered_only_by_authoritative_readback(method):
    api = API()
    fired = False
    def after(call, result):
        nonlocal fired
        if call["method"] == method and not fired:
            fired = True
            raise TimeoutError(TOKEN)
    api.after = after
    assert run(api)["verified_probes"] == 3
    assert len([c for c in api.writes if c["method"] == "POST"]) == 3


def test_indeterminate_post_never_blindly_retries_or_locks():
    api = API()
    api.before = lambda call: sa.HttpResponse(500, {}, TOKEN.encode()) if call["method"] == "POST" else None
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert [call["method"] for call in api.writes] == ["POST"]
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe", allow_create=False)
    assert [call["method"] for call in api.writes] == ["POST"]


@pytest.mark.parametrize("method", ["POST", "PATCH"])
@pytest.mark.parametrize("changes", [{"id": 999}, {"user": BOT | {"id": 999}}, {"body": "wrong"}, {"issue_url": f"{sa.API}{ROOT}/99"}])
def test_contradictory_write_response_fails_even_if_server_state_is_correct(method, changes):
    api = API()
    api.after = lambda call, result: sa.HttpResponse(200, {}, json.dumps(result | changes).encode()) if call["method"] == method else None
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert not any(call["method"] == "PUT" for call in api.writes)


def test_exact_get_readback_must_match_body_author_and_comment_identity():
    for change in ({"body": "changed"}, {"user": BOT | {"id": 999}}, {"id": 999}):
        api = API()
        def before(call):
            if call["method"] == "GET" and call["path"].startswith(ROOT + "/comments/"):
                existing = api.comments[int(call["path"].rsplit("/", 1)[1])]
                return sa.HttpResponse(200, {}, json.dumps(existing | change).encode())
        api.before = before
        with pytest.raises(sa.LedgerUnavailable):
            run(api, "lock-and-probe")
        assert not any(call["method"] == "PUT" for call in api.writes)


def test_failure_on_final_prelock_probe_prevents_all_locks():
    api = API()
    api.before = lambda call: sa.HttpResponse(403, {}, b"{}") if call["method"] == "POST" and call["path"] == ROOT + "/45/comments" else None
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert not any(call["method"] == "PUT" for call in api.writes)


@pytest.mark.parametrize("failure", ["lock-readback", "post-lock-write"])
def test_lock_or_postlock_failure_stops_remaining_locks_and_never_unlocks(failure):
    api = API()
    def after(call, result):
        if failure == "lock-readback" and call["method"] == "PUT":
            api.issues[43]["locked"] = False
    def before(call):
        if failure == "post-lock-write" and call["method"] == "POST" and "post-lock" in call["data"]["body"]:
            return sa.HttpResponse(403, {}, b"{}")
    api.after, api.before = after, before
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert [c["path"] for c in api.writes if c["method"] == "PUT"] == [ROOT + "/43/lock"]
    assert not api.issues[44]["locked"] and not api.issues[45]["locked"]
    assert not any(c["method"] == "DELETE" for c in api.calls)


def test_already_locked_targets_are_probed_without_unlocking_or_relocking():
    api = API()
    for value in api.issues.values():
        value["locked"] = True
    result = run(api, "lock-and-probe")
    assert result["verified_locks"] == 3 and result["verified_probes"] == 6
    assert not any(call["method"] in {"PUT", "DELETE"} for call in api.writes)


def test_strict_load_will_not_auto_repair_or_mutate_orphan_manifest():
    api = API()
    marker, raw = api.issues[43]["body"].split("\n", 1)
    document = json.loads(raw)
    document["manifest"]["shard_count"] = 8
    api.issues[43]["body"] = marker + "\n" + json.dumps(document)
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert not api.writes


@pytest.mark.parametrize("change", ["unknown-status", "source-hash", "shard-id", "manifest-revision"])
def test_ledger_drift_is_detected_before_any_lock(change):
    api = API()
    fired = False
    def after(call, result):
        nonlocal fired
        if call["method"] != "PATCH" or fired:
            return
        fired = True
        if change == "shard-id":
            item = api.comments.pop(208)
            item["id"] = 9999
            item["url"] = f"{sa.API}{ROOT}/comments/9999"
            item["html_url"] = f"https://github.com/{REPO}/issues/43#issuecomment-9999"
            api.comments[9999] = item
        else:
            item = api.issues[43] if change == "manifest-revision" else api.comments[208]
            marker, raw = item["body"].split("\n", 1)
            value = json.loads(raw)
            if change == "manifest-revision":
                value["revision"] += 1
            elif change == "unknown-status":
                value["records"][0]["status"] = "delivered"
            else:
                source = value["records"][0]["source"]
                source["body"] = "replacement source"
                source["body_sha256"] = hashlib.sha256(source["body"].encode()).hexdigest()
            item["body"] = marker + "\n" + json.dumps(value)
    api.after = after
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert not any(call["method"] == "PUT" for call in api.writes)


def trusted_env(monkeypatch):
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPO,
        "GITHUB_REPOSITORY_ID": "1369484548", "GITHUB_REF": "refs/heads/main",
        "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_REF": REPO + "/.github/workflows/operational-issue-writer-test.yml@refs/heads/main",
        "QD_ISSUE_WRITER_OPERATION": "probe", "GITHUB_TOKEN": TOKEN}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.mark.parametrize("key,value", [
    ("GITHUB_ACTIONS", "false"), ("GITHUB_REPOSITORY", "attacker/fork"), ("GITHUB_REPOSITORY_ID", "999"), ("GITHUB_REPOSITORY_ID", ""),
    ("GITHUB_REF", "refs/heads/topic"), ("GITHUB_EVENT_NAME", "push"), ("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_WORKFLOW_REF", "other"), ("GITHUB_TOKEN", ""), ("GITHUB_RUN_ID", "bad\nvalue"),
    ("GITHUB_RUN_ATTEMPT", "0"), ("QD_ISSUE_WRITER_OPERATION", "arbitrary"),
])
def test_runtime_guards_precede_network_and_sanitize_errors(monkeypatch, capsys, key, value):
    trusted_env(monkeypatch)
    monkeypatch.setenv(key, value)
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: pytest.fail("network reached"))
    assert p.main() == 1
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err
    assert json.loads(output.out)["ok"] is False


def test_main_exports_counts_links_and_no_ledger_source_data(monkeypatch, tmp_path, capsys):
    trusted_env(monkeypatch)
    api = API()
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: api)
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert p.main() == 0
    output = capsys.readouterr().out
    value = json.loads(output)
    assert value["ok"] is True and value["verified_probes"] == 3
    assert TOKEN not in output and "Immutable original" not in output
    assert "verified_probes" in summary.read_text()


def test_cli_workflow_rerun_may_only_reconcile_existing_comments(monkeypatch):
    trusted_env(monkeypatch)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    api = API()
    monkeypatch.setattr(sa, "StdlibHttpClient", lambda: api)
    assert p.main() == 1 and not api.writes
    run(api)
    assert p.main() == 0


def test_budget_exhaustion_prevents_requests():
    api = API()
    github = client(api)
    github.budget = p.ProbeBudget(clock=lambda: 100.0)
    github.budget.deadline = 99.0
    with pytest.raises(sa.BudgetExpired):
        p.run(github, operation="probe", run_id="12345")
    assert not api.calls


def test_stdlib_only_import_without_site_packages():
    result = subprocess.run([sys.executable, "-S", "-c", "import market_data.issue_writer_probe"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_workflow_is_manual_fixed_target_serialized_and_minimal_permission():
    text = Path(".github/workflows/operational-issue-writer-test.yml").read_text()
    assert "  workflow_dispatch:" in text
    assert all(trigger not in text for trigger in ("  push:", "  schedule:", "  pull_request:"))
    assert "        default: probe" in text and "          - lock-and-probe" in text
    assert "permissions:\n  contents: read\n  issues: write\n" in text
    assert "concurrency:\n  group: hosted-core-watch-fallback\n  cancel-in-progress: false" in text
    assert "github.repository == 'DropNowOfficial/quant-detective'" in text
    assert "github.repository_id == '1369484548'" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.event_name == 'workflow_dispatch'" in text
    assert "timeout-minutes: 4" in text and "persist-credentials: false" in text
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in text
    assert "python3 -m market_data.issue_writer_probe" in text
    assert "QD_ISSUE_WRITER_OPERATION: ${{ inputs.operation }}" in text
    assert text.count("secrets.GITHUB_TOKEN") == 1
    assert all(forbidden not in text for forbidden in ("uv sync", "pip install", "SLACK", "GH_TOKEN", "contents: write"))


def test_rerun_cannot_claim_writer_permission_from_an_unchanged_old_verified_body():
    api = API()
    run(api)
    api.before = lambda call: sa.HttpResponse(403, {}, b"{}") if call["method"] == "PATCH" else None
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe", allow_create=False)
    assert not any(call["method"] == "PUT" for call in api.writes)


def test_reconciliation_only_rerun_does_not_lock_without_existing_postlock_probe():
    api = API()
    run(api)
    before = len(api.writes)
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe", allow_create=False)
    assert not any(call["method"] == "PUT" for call in api.writes[before:])


def test_postlock_probe_requires_target_still_locked_before_writing():
    api = API()
    reads_after_lock = 0
    def before(call):
        nonlocal reads_after_lock
        if call["method"] == "GET" and call["path"] == ROOT + "/43" and any(c["method"] == "PUT" for c in api.calls):
            reads_after_lock += 1
            if reads_after_lock == 2:
                api.issues[43]["locked"] = False
    api.before = before
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
    assert [c["path"] for c in api.writes if c["method"] == "PUT"] == [ROOT + "/43/lock"]
    assert not any(c["method"] == "POST" and "post-lock" in c["data"]["body"] for c in api.writes)


def test_success_requires_all_targets_still_locked_at_final_readback():
    api = API()
    def after(call, result):
        if call["method"] == "PATCH" and "12345:45:post-lock" in call["data"]["body"]:
            api.issues[44]["locked"] = False
    api.after = after
    with pytest.raises(sa.LedgerUnavailable):
        run(api, "lock-and-probe")
