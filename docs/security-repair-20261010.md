# Local scan and operational repair

Base: `b18e62b46f9adf97d94426ebf2328b4f2c044b35`. These changes require review
and separate authorization before push or production installation.

## Boundaries repaired

- Coverage evidence requires exactly one well-formed row per requested symbol.
  Partial valid coverage is degraded success; zero coverage and execution failures
  remain failures. Existing bar/receipt and strategy gates remain unchanged.
- Raw provider, curl and publication exceptions no longer enter saved scan rows
  or watch CLI stderr. Fixed failure codes and trusted workflow links remain.
- The GitHub publisher limits response size, page count and API path scope and
  refuses redirects. Provider news is escaped as single-line prose, including
  HTML, Markdown, mentions and bidirectional controls. Event envelopes are bounded.
- Existing incoming Slack sources continue to require exact numeric producer
  identities, repository/issue/comment URLs and a matching source envelope.
  Source prose is delivered as Slack plain text. Existing durable unknown-send
  blocking and confirmed readback remain in force.
- Notification verdict includes actual durable delivery, separately from market
  coverage. Failed preparation, health persistence and forwarding cannot produce
  a healthy notification verdict through `continue-on-error`.
- Health fingerprints include coverage and fixed rejection reasons. Unchanged
  incidents deduplicate across restarts; changed coverage is bounded by the
  existing cooldown, while severity escalation and full recovery are immediate.
  Failed processes cannot be relabeled degraded merely because an earlier
  partial artifact survived. Contradictory status/coverage evidence fails closed.
- The hosted scan job and publisher both require the fixed repository, numeric
  repository ID, main ref and allowed trigger. Topic-branch dispatches cannot
  manufacture trusted market observation comments.
- A return between published material states (A -> B -> A) is a new event.
  Optional predecessor-comment identity preserves retry deduplication; old
  three-part markers remain readable. Slack and boundary smoke validators both
  validate the new format and reject self/future predecessor IDs.
- Malformed provider envelopes, nested containers, timestamps and excessive
  numeric values become acquisition failures without discarding other usable
  symbols. Actual programming exceptions remain execution failures. The
  classification, daily/intraday math, RVOL calculations and complete
  quote-validity module are unchanged from the base.
- The fallback dispatcher checks the exact workflow run attempt's scan-step
  evidence before calling a workflow successful as a scan. Delivery-only tests,
  skipped scan steps, holidays and closed sessions do not count. Market coverage
  remains explicitly unverified by the dispatcher. The endpoint contract is
  documented by [GitHub](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt).
- Fallback dispatch writes and fsyncs an attempt record before POST. Lost or
  unconfirmed responses remain quarantined across restarts; cooldown expiry
  cannot replay them. Reconciliation requires a subsequent verified same-target
  scan outside the native and fallback baseline run IDs. Unreadable state and
  pre-POST persistence failures fail closed.
- The pytest constraint and lockfile now exclude releases affected by the advisory
  returned by pip-audit. Auditing does not certify local project code.
- Runtime liveness rejects future/naive timestamps, invalid modes and missing
  run IDs. It explicitly does not certify quote coverage or brokerage health.
- The updater uses a new, root-protected Git object store with isolated Git
  configuration to resolve the exact main commit. It materializes raw blobs,
  ignoring archive attributes and checkout filters, and rejects symlinks,
  submodules and reserved runtime paths. Existing release Git config, index,
  replacement refs and hooks are never executed or trusted by root.
- Every candidate has a new unique directory. Abandoned directories and their
  ignored/runtime files are never reused. Source files remain root-owned and
  non-writable by the service account throughout the frozen dependency build
  and tests; only the new virtualenv is writable during that build. Verification
  compares exact source bytes and executable bits against the separate trusted
  tree, rejects extra files (including ignored Python files), symlinks,
  hardlinks and unsafe ownership/permissions. The virtualenv is the explicit
  generated-artifact exception; no virtualenv program runs as root.
- Activation and rollback replace the current symlink by a same-filesystem
  atomic rename. Failures restore the previous link and saved operational
  unit/updater files. Successful rollback requires an active service plus a
  healthy fresh run ID distinct from both the original and failed candidate
  runs, checked with the newly verified health code. A failed rollback exits 7
  and retains recovery evidence instead of reporting success.
- The root-side state identity reader refuses symlinks, non-regular files and
  oversized state, and reads at most 8 MiB. The repair-current entry point now
  delegates to the reviewed updater's --repair transaction, including a fresh
  same-commit build, rather than modifying the live checkout or ignoring health
  failure. The repair shim also requires the installed updater hash to match
  its reviewed sibling update-production.sh; root ownership alone is insufficient. A legacy/current tree that cannot be verified fails closed and can
  be rebuilt through this explicit repair route.
- Manual self-hosted deployment is restricted to this repository's main branch,
  uses the canonical verification entrypoint and the same reviewed updater.
  Both affected checkout steps disable persisted credentials. An expected commit
  argument prevents deploying a different main commit from the reviewed run.

## Production prerequisites and limits

No VPS connection, production restart, credential change or security setting
change was made. Deployment requires root-owned, non-group/other-writable
`/opt/quant-detective` and `releases` directories, system Python 3.11 or newer,
Git, the existing service account, and a reviewed root-owned non-writable
`/usr/local/bin/uv` regular executable. The updater does not install these
prerequisites. Manual deployment requires the installed updater SHA-256 to
match the reviewed checkout and passes the workflow's expected commit SHA.
`deploy/validate_release.py` is fetched from that exact commit into the new
root-only source workspace; no separately installed or mutable release helper
is used. Provisioning the updater or prerequisites is a separately authorized
production action. Missing prerequisites or incompatible source fail closed.

Offline tests use real local Git repositories and filesystem data while faking
root identity, account switching and service commands; they never operate real
systemd. They cover Git fsmonitor/index/replacement-ref attacks, ignored files,
file permissions/link types, no-op validation, same-SHA repair, activation and
rollback failures, and stale/unhealthy restored state. A real `uv sync --frozen
--offline` also passed against a temporary source tree with 0555 directories,
0444 regular files and only its precreated virtualenv writable; all six updater
acceptance test files passed there (639 tests). This verifies the editable build
layout on Linux but does not substitute for an authorized production dry run or
prove real root/service-user isolation under adversarial concurrent processes.

The bootstrap script remains an existing one-time provisioning path with
unpinned pip installation and weaker readiness behavior; do not use it for
this update without a separate provisioning review. Git main remains the
release authority; this patch adds no commit-signature or branch-protection
policy. The virtualenv is generated runtime data, not part of the committed
source comparison; dependency compromise or malicious same-account build
processes require separate isolation/provenance measures. Existing abandoned
release directories are intentionally left untouched for operator review.

Do not merge this repair and rely on the old auto-updater to upgrade itself
safely. A controlled, separately authorized installation of the reviewed updater
and its prerequisites must precede the production rollout; otherwise the first
update still uses the old updater's trust boundary.

Dispatch records in PENDING, UNKNOWN or ACCEPTED_UNCONFIRMED do not expire into
retry. If a subsequent verified same-target scan cannot reconcile an attempt,
an operator must review the target and attempted interval in GitHub before
deliberately repairing only dispatch_attempt while preserving other history.
Do not delete or silently reset unreadable state to resume dispatch. No reset
command or automatic override is introduced.

The service user can access its existing runtime credentials. Reducing build-time
credential access further needs a separate build identity or externally prepared
release artifacts and is outside the authorized machine-security changes.
Future re-audits are needed for new advisories. Provider silence and sparse POST
trades cannot be distinguished conclusively from the captured bar timestamps.
