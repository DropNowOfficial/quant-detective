"""Manual fresh-only recovery, run under the existing workflow writer lock."""
import json
import os
from pathlib import Path

from market_data import slack_alerts as sa


def check_context(env):
    expected = {"GITHUB_REPOSITORY": sa.REPO, "GITHUB_REPOSITORY_ID": str(sa.REPO_ID),
                "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
                "QD_SLACK_ENABLED": "false"}
    if any(env.get(key) != value for key, value in expected.items()):
        raise ValueError("Recovery requires manual main execution with delivery disabled")


def preview(store):
    issue, response = store.github.get_issue(store.config.ledger_issue)
    cutoff = sa._server_date(response)
    comments = store.github.list_issue_comments(store.config.ledger_issue)
    result = sa.recover_fresh(store, cutoff=cutoff, now=cutoff)
    backup = {"issue": issue, "comments": comments, "cutoff": cutoff.isoformat()}
    return backup, result


def apply_backup(store, backup):
    issue, response = store.github.get_issue(store.config.ledger_issue)
    comments = store.github.list_issue_comments(store.config.ledger_issue)
    if issue != backup["issue"] or comments != backup["comments"]:
        raise sa.LedgerUnavailable("Ledger changed after backup; preview again")
    cutoff = sa._time(backup["cutoff"])
    before = store.load(repair=False)
    result = sa.recover_fresh(store, cutoff=cutoff, now=sa._server_date(response), apply=True)
    after = store.load(repair=False)
    if (after.manifest.fresh_after != cutoff or after.manifest.cursor != cutoff
            or set(before.entries) != set(after.entries)
            or any(after.entries[key].source != entry.source for key, entry in before.entries.items())
            or any(after.entries[key] != entry for key, entry in before.entries.items()
                   if entry.status in {"delivered", "unknown"})):
        raise sa.LedgerUnavailable("Recovery readback verification failed")
    return result | {"verified": True}


def main(argv=None):
    parser = sa._SafeParser(description=__doc__)
    parser.add_argument("operation", choices=("preview", "apply"))
    parser.add_argument("--backup", default="slack-recovery-backup.json")
    args = parser.parse_args(argv)
    try:
        check_context(os.environ)
        config = sa.Config(sa.REPO, int(os.environ["QD_SLACK_LEDGER_ISSUE"]),
                           frozenset({sa.PRODUCER_ID}), sa.CHANNEL_ID)
        if os.environ.get("QD_SLACK_PRODUCER_IDS") != str(sa.PRODUCER_ID):
            raise ValueError("Unexpected producer")
        clock = sa.SystemClock()
        budget = sa.MonotonicBudget(clock, sa.EXTENDED_SLICE_SECONDS, maximum=sa.EXTENDED_SLICE_SECONDS)
        github = sa.GitHubClient(config, os.environ["GITHUB_TOKEN"], sa.StdlibHttpClient(), budget)
        store = sa.LedgerStore(github, config)
        with sa._hard_deadline(budget):
            if args.operation == "preview":
                backup, result = preview(store)
                Path(args.backup).write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8")
            else:
                result = apply_backup(store, json.loads(Path(args.backup).read_text(encoding="utf-8")))
        print(json.dumps(result))
        return 0
    except (sa.LedgerUnavailable, sa.BudgetExpired, ValueError, KeyError, OSError):
        print(json.dumps({"verified": False, "reason": "recovery_not_verified", "action": "keep_delivery_disabled_and_inspect"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
