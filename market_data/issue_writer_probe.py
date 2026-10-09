"""Bounded, manual-only GITHUB_TOKEN diagnostics for three operational Issues.

Comments are inert audit records, never event/shard/heartbeat delivery evidence.
No ledger repair, reset, replay, comment deletion, or automatic unlock is allowed.
A successful run proves only the writes explicitly listed in its JSON result.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import time

from . import slack_alerts as sa

REPOSITORY_ID = "1369484548"
TARGETS = {43: 5755475722, 44: 5756285011, 45: 5773164000}
BOT_ID = 41898282
ROOT = f"/repos/{sa.REPO}/issues"
WORKFLOW = sa.REPO + "/.github/workflows/operational-issue-writer-test.yml@refs/heads/main"
BUDGET_SECONDS = 90
PROBE_PATTERN = re.compile(r"<!-- qd-writer-probe:([1-9][0-9]{0,31}):(43|44|45):(pre-lock|post-lock) -->")
OPERATIONAL_MARKERS = ("qd-writer-probe:", "qd-slack-shard:", "qd-slack-ledger:",
                       "qd-event:", "qd-source:", "qd-heartbeat")


class ProbeBudget:
    """One cooperative budget, plus the shared Linux hard wall deadline."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + BUDGET_SECONDS

    def remaining(self):
        return max(0.0, self.deadline - self.clock())

    def require(self, seconds=0):
        if self.remaining() <= 0 or self.remaining() < seconds:
            raise sa.BudgetExpired()


class _ReadOnlyGitHub(sa.GitHubClient):
    def _request(self, method, path, document=None):
        if method != "GET":
            raise sa.LedgerUnavailable("Diagnostic refuses automatic ledger repair")
        return super()._request(method, path, document)


@dataclass(frozen=True)
class _Snapshot:
    ledger: sa.Ledger
    manifest_sha256: str
    shards: tuple


def _issue(github, number):
    value, _ = github.get_issue(number)
    if (not sa._trusted_issue(value, github.config, number)
            or type(value.get("id")) is not int or value["id"] != TARGETS[number]
            or value.get("repository_url") != f"{sa.API}/repos/{sa.REPO}"
            or type(value.get("locked")) is not bool):
        raise sa.LedgerUnavailable("Operational Issue identity mismatch")
    return value


def _targets(github, *, locked=()):
    # Re-read every fixed identity before each write, including Issues that have
    # not yet been probed. No configurable Issue numbers or alternate writers.
    targets = {number: _issue(github, number) for number in TARGETS}
    if any(targets[number]["locked"] is not True for number in locked):
        raise sa.LedgerUnavailable("Operational Issue no longer locked")
    return targets


def _comment(github, value, number, *, body=None, comment_id=None):
    if (not sa._trusted_comment(value, github.config, number)
            or (comment_id is not None and value["id"] != comment_id)
            or (body is not None and value.get("body") != body)):
        raise sa.LedgerUnavailable("Diagnostic comment identity or content mismatch")
    return value


def _body(run_id, number, phase, *, verified):
    marker = f"<!-- qd-writer-probe:{run_id}:{number}:{phase} -->"
    state = "Verified create/update diagnostic." if verified else "Pending create/update diagnostic."
    return "\n".join((marker, "Operational Issue writer diagnostic / NONTRADE", "", state,
                      f"Phase: {phase}; Issue: #{number}.",
                      f"Run: https://github.com/{sa.REPO}/actions/runs/{run_id}",
                      "Inert audit comment only. No market signal, delivery replay, or trading action."))


def _audit(github, number):
    comments = github.list_issue_comments(number)
    markers = set()
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str):
            raise sa.LedgerUnavailable("Incomplete operational Issue comments")
        operational = any(marker in body for marker in OPERATIONAL_MARKERS)
        if not sa._trusted_comment(comment, github.config, number):
            # Ordinary public discussion is harmless. Operational-looking text
            # is never authority and blocks this one-time lock/poison assessment.
            if operational:
                raise sa.LedgerUnavailable("Untrusted operational marker requires review")
            continue
        if "qd-writer-probe:" not in body:
            continue
        first = body.split("\n", 1)[0]
        match = PROBE_PATTERN.fullmatch(first)
        if (not match or int(match[2]) != number or first in markers
                or body not in {_body(match[1], number, match[3], verified=False),
                                _body(match[1], number, match[3], verified=True)}):
            raise sa.LedgerUnavailable("Conflicting or malformed diagnostic marker")
        markers.add(first)
    return comments


def _snapshot(github):
    # LedgerStore.load normally repairs a single orphan shard. Here even that
    # recovery must fail closed: the transport rejects every non-GET request.
    readonly = _ReadOnlyGitHub(github.config, github._token, github.http, github.budget)
    store = sa.LedgerStore(readonly, github.config)
    ledger = store.load()
    return _Snapshot(ledger, hashlib.sha256(store._manifest_raw.encode()).hexdigest(), tuple(
        (number, store._shards[number][0], hashlib.sha256(body.encode()).hexdigest())
        for number, body in sorted(store._shard_bodies.items())))


def _unchanged(github, baseline):
    for number in TARGETS:
        _audit(github, number)
    if _snapshot(github) != baseline:
        raise sa.LedgerUnavailable("Ledger changed during diagnostic")


def _find(github, number, run_id, phase):
    marker = f"<!-- qd-writer-probe:{run_id}:{number}:{phase} -->"
    matches = [comment for comment in _audit(github, number)
               if sa._trusted_comment(comment, github.config, number)
               and comment["body"].split("\n", 1)[0] == marker]
    if len(matches) > 1:
        raise sa.LedgerUnavailable("Ambiguous diagnostic comments")
    return matches[0] if matches else None


def _probe(github, number, run_id, phase, result, *, allow_create):
    locked = (number,) if phase == "post-lock" else ()
    _targets(github, locked=locked)
    current = _find(github, number, run_id, phase)
    pending = _body(run_id, number, phase, verified=False)
    verified = _body(run_id, number, phase, verified=True)
    if current is None:
        if not allow_create:
            raise sa.LedgerUncertain("Rerun may only reconcile existing diagnostic comments")
        response_id = None
        contradictory = False
        try:
            response, _ = github._request("POST", f"{ROOT}/{number}/comments", {"body": pending})
        except sa.LedgerUnavailable:
            # A timeout or unsuccessful HTTP status is never proof of no commit.
            # Rediscover once, never send a second POST with this marker.
            pass
        else:
            try:
                response_id = _comment(github, response, number, body=pending)["id"]
            except sa.LedgerUnavailable:
                contradictory = True
        current = _find(github, number, run_id, phase)
        if (contradictory or current is None or current.get("body") != pending
                or (response_id is not None and current["id"] != response_id)):
            raise sa.LedgerUncertain("Diagnostic creation could not be verified")
        _comment(github, github.get_comment(current["id"]), number,
                 body=pending, comment_id=current["id"])
        result["created_comments"] += 1
    else:
        _comment(github, github.get_comment(current["id"]), number,
                 body=current["body"], comment_id=current["id"])
        result["reused_comments"] += 1
    # A repeated PATCH of the old verified body would not prove current write
    # permission: a denied request could still read that same historical body.
    # Reset a reused verified record first, proving an actual content transition.
    if current["body"] == verified:
        _patch(github, number, current["id"], pending, locked=locked)
        result["updated_comments"] += 1
    checked = _patch(github, number, current["id"], verified, locked=locked)
    result["updated_comments"] += 1
    result["verified_probes"] += 1
    result["comment_urls"].append(checked["html_url"])


def _patch(github, number, comment_id, body, *, locked=()):
    _targets(github, locked=locked)
    contradictory = False
    try:
        response, _ = github._request("PATCH", f"{ROOT}/comments/{comment_id}", {"body": body})
    except sa.LedgerUnavailable:
        pass
    else:
        try:
            _comment(github, response, number, body=body, comment_id=comment_id)
        except sa.LedgerUnavailable:
            contradictory = True
    checked = _comment(github, github.get_comment(comment_id), number,
                       body=body, comment_id=comment_id)
    if contradictory:
        raise sa.LedgerUncertain("Diagnostic update response contradicted readback")
    if locked and _issue(github, number)["locked"] is not True:
        raise sa.LedgerUnavailable("Post-lock write was not verified while locked")
    return checked


def _lock(github, number):
    target = _targets(github)[number]
    if not target["locked"]:
        try:
            github._request("PUT", f"{ROOT}/{number}/lock", {"lock_reason": "resolved"})
        except sa.LedgerUnavailable:
            # GitHub returns an empty 204 body, which the shared JSON client
            # rejects. Either that case or a lost response needs exact GET proof.
            pass
    if _issue(github, number)["locked"] is not True:
        raise sa.LedgerUncertain("Operational Issue lock could not be verified")


def run(github, *, operation, run_id, allow_create=True):
    """Exercise only fixed diagnostic writes, keeping the ledger byte-identical."""
    if (operation not in {"probe", "lock-and-probe"}
            or not isinstance(run_id, str) or not re.fullmatch(r"[1-9][0-9]{0,31}", run_id)
            or type(allow_create) is not bool
            or github.config != sa.Config(sa.REPO, 43, frozenset({BOT_ID}), sa.CHANNEL_ID)):
        raise sa.LedgerUnavailable("Untrusted operational writer configuration")
    _targets(github)
    for number in TARGETS:
        _audit(github, number)
    baseline = _snapshot(github)
    if operation == "lock-and-probe" and not allow_create:
        # A rerun cannot create any absent diagnostic marker. Check all phases
        # before allowing a new lock that could not then be tested completely.
        for number in TARGETS:
            for phase in ("pre-lock", "post-lock"):
                if _find(github, number, run_id, phase) is None:
                    raise sa.LedgerUncertain("Rerun cannot complete missing diagnostic phases")
    result = {"ok": False, "operation": operation, "verified_probes": 0,
              "created_comments": 0, "reused_comments": 0, "updated_comments": 0,
              "verified_locks": 0, "ledger_unchanged": False, "comment_urls": [],
              "run_url": f"https://github.com/{sa.REPO}/actions/runs/{run_id}",
              "ledger": {"shards": len(baseline.shards), "entries": len(baseline.ledger.entries),
                         "delivered": sum(e.status == "delivered" for e in baseline.ledger.entries.values()),
                         "unknown": sum(e.status == "unknown" for e in baseline.ledger.entries.values())}}
    try:
        for number in TARGETS:
            _probe(github, number, run_id, "pre-lock", result, allow_create=allow_create)
        _unchanged(github, baseline)
        if operation == "lock-and-probe":
            for number in TARGETS:
                _lock(github, number)
                result["verified_locks"] += 1
                _probe(github, number, run_id, "post-lock", result, allow_create=allow_create)
                _unchanged(github, baseline)
    finally:
        # Attempt the strict after-check even on failure. Budget exhaustion still
        # aborts immediately; it never authorizes further writes or an unlock.
        _unchanged(github, baseline)
    if operation == "lock-and-probe":
        _targets(github, locked=TARGETS)
    result.update(ok=True, ledger_unchanged=True)
    return result


def main():
    try:
        if (os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("GITHUB_REPOSITORY") != sa.REPO
                or os.environ.get("GITHUB_REPOSITORY_ID") != REPOSITORY_ID
                or os.environ.get("GITHUB_REF") != "refs/heads/main"
                or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
                or os.environ.get("GITHUB_WORKFLOW_REF") != WORKFLOW):
            raise ValueError
        token = os.environ.get("GITHUB_TOKEN")
        operation = os.environ.get("QD_ISSUE_WRITER_OPERATION")
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if (not token or operation not in {"probe", "lock-and-probe"}
                or not re.fullmatch(r"[1-9][0-9]{0,31}", run_id)
                or not re.fullmatch(r"[1-9][0-9]{0,5}", attempt)):
            raise ValueError
        config = sa.Config(sa.REPO, 43, frozenset({BOT_ID}), sa.CHANNEL_ID)
        budget = ProbeBudget()
        github = sa.GitHubClient(config, token, sa.StdlibHttpClient(), budget)
        with sa._hard_deadline(budget):
            result = run(github, operation=operation, run_id=run_id, allow_create=attempt == "1")
    except sa.BudgetExpired:
        result = {"ok": False, "error": "deadline_exceeded_reconcile_existing_audit_comments_and_locks"}
    except sa.LedgerUncertain:
        result = {"ok": False, "error": "write_unverified_reconcile_existing_audit_comments_and_locks"}
    except (sa.LedgerUnavailable, ValueError):
        result = {"ok": False, "error": "blocked_verify_dispatch_identity_markers_and_ledger"}
    except Exception:
        result = {"ok": False, "error": "diagnostic_failed_reconcile_existing_audit_comments_and_locks"}
    # Never include remote content, market snapshots, tokens or exception text.
    output = json.dumps(result, sort_keys=True, separators=(",", ":"))
    print(output)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as file:
                file.write(output + "\n")
        except OSError:
            return 1
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
