"""Manual bot-owned ledger provisioning; never enables or initializes Slack.

The workflow supplies its own GITHUB_TOKEN and serializes with the only ledger
writer. There is one POST at most. A lost response is followed by full discovery,
never a second POST; an unverified outcome requires operator reconciliation.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from market_data import slack_alerts as sa

TITLE = "Quant Detective Slack delivery ledger"
LABEL = "qd-slack-ledger"
BOT_ID = 41898282
REPOSITORY_ID = 1369484548
ROOT = f"/repos/{sa.REPO}/issues"
WORKFLOW = f"{sa.REPO}/.github/workflows/provision-slack-ledger.yml@refs/heads/main"
QUERY = {"state": "all", "sort": "created", "direction": "asc", "per_page": 100}


def _next_page(headers, page):
    """Validate, never follow, remote pagination URLs (including short pages)."""
    link = sa._header(headers, "Link")
    if link is None:
        return False, page
    if not isinstance(link, str) or not link:
        raise sa.LedgerUnavailable("Invalid Issue pagination")
    relations = {}
    for part in link.split(","):
        match = re.fullmatch(r'\s*<([^>]+)>;\s*rel="(next|prev|first|last)"\s*', part)
        if not match or match[2] in relations:
            raise sa.LedgerUnavailable("Invalid Issue pagination")
        target = urlsplit(match[1])
        query = parse_qs(target.query, keep_blank_values=True)
        target_page = query.pop("page", [])
        if (target.scheme != "https" or target.netloc != "api.github.com"
                or target.path not in {ROOT, f"/repositories/{REPOSITORY_ID}/issues"}
                or target.fragment
                or query != {key: [str(value)] for key, value in QUERY.items()}
                or len(target_page) != 1 or not re.fullmatch(r"[1-9][0-9]*", target_page[0])
                or (match[2] == "next" and int(target_page[0]) != page + 1)):
            raise sa.LedgerUnavailable("Invalid Issue pagination")
        relations[match[2]] = int(target_page[0])
    if (("prev" in relations and relations["prev"] != page - 1)
            or ("first" in relations and relations["first"] != 1)
            or ("last" in relations and (relations["last"] < page
                or (relations["last"] > page) != ("next" in relations)))):
        raise sa.LedgerUnavailable("Contradictory Issue pagination")
    # Canonical numeric paths are metadata only; every actual request is built
    # from the fixed owner/repo ROOT, never from a Link URL.
    return "next" in relations, max(page, *relations.values())


def _identity(item):
    if not isinstance(item, dict):
        raise sa.LedgerUnavailable("Invalid ledger Issue response")
    number = item.get("number")
    if (type(number) is not int or number <= 0 or type(item.get("id")) is not int
            or item["id"] <= 0 or "pull_request" in item
            or item.get("url") != f"{sa.API}{ROOT}/{number}"
            or item.get("repository_url") != f"{sa.API}/repos/{sa.REPO}"
            or item.get("html_url") != f"https://github.com/{sa.REPO}/issues/{number}"):
        raise sa.LedgerUnavailable("Invalid ledger Issue identity")
    return number


def _bot_author(item):
    author = item.get("user")
    if (not isinstance(author, dict) or type(author.get("id")) is not int
            or author["id"] != BOT_ID or author.get("login") != "github-actions[bot]"
            or author.get("type") != "Bot"):
        raise sa.LedgerUnavailable("Ledger was not authored by the fixed workflow bot")


def _candidate(item):
    if ("body" not in item or not isinstance(item.get("title"), str) or not isinstance(item.get("labels"), list)
            or (item.get("body") is not None and not isinstance(item["body"], str))):
        raise sa.LedgerUnavailable("Incomplete Issue lookup")
    labels = item["labels"]
    if any(not isinstance(label, dict) or not isinstance(label.get("name"), str) for label in labels):
        raise sa.LedgerUnavailable("Incomplete Issue labels")
    return (item["title"] == TITLE or any(label["name"].casefold() == LABEL for label in labels)
            or "qd-slack-ledger:" in (item.get("body") or ""))


def _discover(github, configured_issue):
    """Exhaust open and closed Issues before deciding whether creation is safe."""
    candidates, seen_ids, seen_numbers = {}, set(), set()
    page, advertised, last_announced = 1, False, 1
    while True:
        values, response = github._request("GET", ROOT + "?" + urlencode(QUERY | {"page": page}))
        if (not isinstance(values, list) or len(values) > 100
                or (not values and (advertised or 1 < page <= last_announced))):
            raise sa.LedgerUnavailable("Incomplete Issue pagination")
        for item in values:
            if (not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0
                    or item["id"] in seen_ids):
                raise sa.LedgerUnavailable("Duplicate or invalid Issue page record")
            seen_ids.add(item["id"])
            if "pull_request" in item:
                continue
            number = _identity(item)
            if number in seen_numbers:
                raise sa.LedgerUnavailable("Duplicate Issue number")
            seen_numbers.add(number)
            if _candidate(item) or number == configured_issue:
                candidates[number] = item
        advertised, last_page = _next_page(response.headers, page)
        last_announced = max(last_announced, last_page)
        if not advertised and len(values) < 100:
            if page < last_announced:
                raise sa.LedgerUnavailable("Previously advertised Issue pages are missing")
            break
        if advertised and not values:
            raise sa.LedgerUnavailable("Empty advertised Issue page")
        page += 1
    if len(candidates) > 1 or (configured_issue and set(candidates) != {configured_issue}):
        raise sa.LedgerUnavailable("Ambiguous or missing configured ledger")
    if not candidates:
        return None
    number, listed = next(iter(candidates.items()))
    current, _ = github.get_issue(number)
    if _identity(current) != number or current["id"] != listed["id"]:
        raise sa.LedgerUnavailable("Ledger identity changed during readback")
    _bot_author(current)
    if current.get("body") != sa.UNINITIALIZED_MARKER:
        # Parse the existing manifest without LedgerStore.load(): orphan-shard
        # recovery there can write. Provisioning must never reset or repair it.
        document = sa._document(current.get("body"), sa.MANIFEST_MARKER)
        sa._envelope(document, {"operation_id", "revision", "manifest"})
        config = sa.Config(sa.REPO, number, frozenset({BOT_ID}), sa.CHANNEL_ID)
        sa._manifest(document["manifest"], config)
    return current


def provision(github, *, configured_issue=None, allow_create=True):
    """Return a verified Issue number/link, not operational readiness."""
    if (github.config.repo != sa.REPO or github.config.producer_ids != frozenset({BOT_ID})
            or (configured_issue is not None
                and (type(configured_issue) is not int or configured_issue <= 0))):
        raise sa.LedgerUnavailable("Invalid provisioning configuration")
    existing = _discover(github, configured_issue)
    if existing:
        return existing["number"], existing["html_url"]
    if not allow_create:
        raise sa.LedgerUnavailable("Workflow reruns may only rediscover an existing ledger")
    response_identity = None
    try:
        result, _ = github._request("POST", ROOT, {"title": TITLE, "body": sa.UNINITIALIZED_MARKER})
    except sa.LedgerUnavailable:
        # Even an HTTP error may follow commit. Never infer that POST was absent.
        pass
    else:
        try:
            number = _identity(result)
            _bot_author(result)
            if result.get("body") != sa.UNINITIALIZED_MARKER:
                raise sa.LedgerUnavailable("Unexpected initial ledger body")
            response_identity = (number, result["id"])
        except sa.LedgerUnavailable:
            response_identity = False  # a received but contradictory response
    try:
        created = _discover(github, None)
        if (created is not None and created.get("body") == sa.UNINITIALIZED_MARKER
                and (response_identity is None
                     or response_identity == (created["number"], created["id"]))):
            return created["number"], created["html_url"]
    except sa.LedgerUnavailable:
        pass
    raise sa.LedgerUncertain("Creation unverified; reconcile before another dispatch")


def main():
    try:
        if (os.environ.get("GITHUB_ACTIONS") != "true"
                or os.environ.get("GITHUB_REPOSITORY") != sa.REPO
                or os.environ.get("GITHUB_REF") != "refs/heads/main"
                or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
                or os.environ.get("GITHUB_WORKFLOW_REF") != WORKFLOW
                or os.environ.get("QD_SLACK_PROVISION_CONFIRMED") != "true"):
            raise ValueError
        token = os.environ.get("GITHUB_TOKEN")
        configured = os.environ.get("QD_SLACK_LEDGER_ISSUE", "")
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
        if (not token or not re.fullmatch(r"[1-9][0-9]*", attempt)
                or (configured and not re.fullmatch(r"[1-9][0-9]*", configured))):
            raise ValueError
        config = sa.Config(sa.REPO, int(configured or "1"), frozenset({BOT_ID}), sa.CHANNEL_ID)
        budget = sa.MonotonicBudget(sa.SystemClock())
        github = sa.GitHubClient(config, token, sa.StdlibHttpClient(), budget)
        with sa._hard_deadline(budget):
            number, url = provision(github, configured_issue=int(configured) if configured else None,
                                    allow_create=attempt == "1")
        # Only verified integers/fixed-origin links reach outputs. No remote
        # response bodies, titles, exception text, tokens, or mutable settings.
        output = f"ledger_issue={number}\nledger_url={url}\n"
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
                file.write(output)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
                file.write(f"Verified ledger Issue: [#{number}]({url})\n")
        print(output, end="")
        return 0
    except sa.LedgerUncertain:
        print("Ledger creation unverified. Reconcile Issues before another dispatch; do not blindly retry.")
    except sa.BudgetExpired:
        print("Provisioning deadline exceeded. Reconcile Issues before another dispatch; creation may have committed.")
    except (sa.LedgerUnavailable, ValueError):
        print("Provisioning blocked: verify dispatch context, configuration, and existing ledger Issues.")
    except Exception:
        print("Provisioning failed. Reconcile Issues before another dispatch; creation may have committed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
