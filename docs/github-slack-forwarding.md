# GitHub → Slack observation forwarding

## Scope and default state

The hosted fallback workflow can forward already-saved GitHub observations from
`DropNowOfficial/quant-detective` to the fixed Slack channel `C0C7HLTGQ0N`.
It is **off unless the repository variable `QD_SLACK_ENABLED` is exactly `true`**.
Implementation or design approval does not authorize production setup, enabling,
deployment, or a real message. None of those external actions is established by
the offline tests described here.

The four accepted states are `ENTRY_ARMED`, `ENTRY_CONFIRMED`, `LEADER_WATCH`,
and `LEADER_HOT_NO_CHASE`. Heartbeats and ordinary comments are not forwarded.
The original inline `watch --once --github-alerts` scan command, daily Issue and
heartbeat publishing, cron schedules, eight-minute job limit, permissions, and
serialized concurrency group are preserved. The integrated source publisher also
applies the [quote/session-validity gates](quote-validity.md): missing or expired
source evidence cannot create a new material event. Those gates preserve the
existing strategy thresholds and stock universe. The exchange calendar is a
runtime dependency; no factor changes or VPS deployment are included.

Slack acceptance proves only that this saved GitHub observation was forwarded.
It does **not** prove the underlying quote belongs to the current session, is
fresh at delivery, still satisfies a condition, is actionable, or triggered a
mobile notification. No orders are created.
Missing source quote timestamps are not reconstructed from a heartbeat, Slack
delivery time, or a newer scan.

## Production gates: separate authorization required

An operator must obtain authorization for the relevant external actions and
record the actual verified values before enabling production. There are no
placeholder production Issue numbers, producer IDs, or delivery receipts in this
document.

1. **Review and deploy the code through the approved repository process.** Local
   validation is not a push, PR, merge, deployment, or hosted-runner validation.
   Keep forwarding disabled during setup.
2. **Create/install the channel-limited Slack Incoming Webhook.** In Slack's
   installation/configuration interface, independently verify the selected
   destination's channel ID is `C0C7HLTGQ0N`. An Incoming Webhook is bound to its
   installed channel: the POST payload cannot override or correct the channel.
   The program's fixed channel constant cannot detect a wrongly installed
   webhook. A real delivery and destination check remain separate gates.
3. **Verify production GitHub author identities.** Read the numeric `user.id`
   from actual API-returned source comments produced by the approved workflow,
   and independently confirm the producer account. A display name or copied
   event marker is insufficient. Verify which identity will author the ledger
   Issue and shard comments as well.
4. **Provision one dedicated ledger Issue, separately from daily market Issues.**
   Its creator must be an independently verified permitted producer identity.
   The initial body must be exactly `<!-- qd-slack-ledger:uninitialized:v1 -->`,
   with no additional text or newline. Confirm the API-returned body and author.
   If the authorized tooling cannot create the Issue as that identity, stop and
   resolve provisioning. Do not add a human creator to `QD_SLACK_PRODUCER_IDS`
   merely to bypass validation: that would also authorize their event comments.
   The forwarding program never creates a missing ledger Issue.

   After this provisioning workflow is reviewed and deployed, dispatch
   `.github/workflows/provision-slack-ledger.yml` on this repository's `main`
   with `confirm_provision=true`. Its default is false. The job uses only the
   existing `GITHUB_TOKEN` with `contents: read` / `issues: write`, the same
   `hosted-core-watch-fallback` serialization group, and fixed author
   `github-actions[bot]` (`41898282`); it has no human-author fallback. That
   numeric identity was checked against the API author of
   [a hosted workflow comment](https://github.com/DropNowOfficial/quant-detective/issues/40#issuecomment-6045865536).
   It checks every open/closed Issue page for the fixed title
   `Quant Detective Slack delivery ledger`, label `qd-slack-ledger`, or ledger
   body marker, and cross-checks `QD_SLACK_LEDGER_ISSUE` if already configured.
   PRs are excluded. Duplicate, corrupt, foreign, or wrong-author candidates
   block creation. It creates no labels or signal comments.

   A unique verified existing Issue is reused, including a closed Issue or one
   whose initialized manifest is valid. Existing bodies, shards, and baselines
   are never changed. This manifest identity check does not validate all shards
   or establish forwarding readiness. Otherwise one POST creates the exact
   uninitialized body, followed by complete rediscovery and authoritative
   readback. API requests have five-second timeouts within a 30-second activity
   deadline. A failed/ambiguous write is never automatically repeated; workflow
   reruns can only rediscover. If verification fails or the job is interrupted,
   inspect all candidate Issues and the run before considering a new dispatch.
   GitHub Issue creation has no idempotency key: do not blindly dispatch again
   after an uncertain outcome or create independent/out-of-band ledger writers.

   The only successful outputs are `ledger_issue` and `ledger_url`, also shown
   in the run summary. Use the verified number for the separately authorized
   `QD_SLACK_LEDGER_ISSUE` variable update. Provisioning neither changes
   `QD_SLACK_ENABLED` nor receives a webhook secret; it never initializes the
   ledger, runs a market scan, or calls Slack. Keep forwarding disabled until
   the remaining production gates are satisfied.
5. **Configure authorized repository variables and save the secret securely.**
   Use the repository's secure secret interface for the webhook URL; do not
   paste it into logs, Issue bodies, workflow inputs, artifacts, or source code.
   Credential installation/saving requires its own authorization.
6. **Explicitly authorize first initialization and enablement.** After setup,
   set `QD_SLACK_ENABLED=true` and dispatch this workflow on trusted `main`
   with `slack_operation=initialize`. Confirm `Ledger ready: True` in the run
   summary before regarding the baseline as established. This dispatch also
   runs the ordinary scan and may send eligible new observations, so authorization
   must cover that effect. Runs before a valid baseline continue scanning but
   cannot forward. The initialization cut, rather than changing the variable,
   determines which results are eligible.
7. **Authorize and verify a real end-to-end check.** Verify an eligible source
   comment, its durable delivery state, and the actual message in the installed
   target channel. Incoming Webhook acceptance does not supply a message `ts`.
   Do not claim an actual channel binding or receipt without observed evidence.

Repository configuration:

| Name | Location | Required value |
| --- | --- | --- |
| `QD_SLACK_ENABLED` | Repository variable | Exact `true` enables; unset or any other value disables |
| `QD_SLACK_LEDGER_ISSUE` | Repository variable | Actual positive integer number of the provisioned ledger Issue |
| `QD_SLACK_PRODUCER_IDS` | Repository variable | Independently verified positive numeric producer IDs, comma-separated without spaces |
| `QD_SLACK_WEBHOOK_URL` | Actions secret | Securely stored webhook whose installed channel has been verified |
| `GITHUB_TOKEN` | Existing Actions token | Existing repository read/Issue-write access, injected only into prepare, scan, and forward |

There is no runtime repository or channel override. The webhook secret is
injected only into the guarded forwarding step; prepare and scan do not receive
it. Forwarding is restricted to this repository's `refs/heads/main` and `push`,
`schedule`, or `workflow_dispatch`. PRs, forks, other refs, cancellations, a
skipped/cancelled scan, and an unready ledger cannot forward. Missing credentials
pause forwarding without changing the scan's result.

## Initialization, normal runs, and shutdown

The dispatch choice defaults to `run`. Only an explicit trusted-main
`workflow_dispatch` selecting `initialize` adds `--initialize` to prepare.
Initialization takes a GitHub-server-time cut before the scan and durably records
IDs already present in that second. Older sources and those baseline IDs are
excluded; subsequent eligible comments, including new IDs in that same second,
can be forwarded. Repeated initialization of a valid ledger retains its existing
baseline. Normal recovery never initializes an empty, missing, or corrupt ledger.

The sequence is prepare → original scan → independent forward → original scan
summary → full report artifact upload. The report upload uses `always()` and
retains `market-watch.json`, including publication rejection diagnostics, for
30 days when a report exists. If publication saves one event and a later POST fails, forwarding can
still discover the saved event from GitHub. It cannot send the unsaved local
event. An `INCOMPLETE` scan or publisher failure still fails the scan and job;
prepare/forward failures are isolated with `continue-on-error` and cannot mask
that failure. The forward guard includes `!cancelled()` so it can run after a
scan failure without running after cancellation.

To pause, change only `QD_SLACK_ENABLED` to `false` (or remove it). Preserve the
ledger, source Issues, and scan workflow. To resume, use the original ledger and
normal `run` mode. Pending work remains eligible subject to source revalidation,
retry rules, and the budget. Do not reset the baseline or delete ledger history.

## Durable delivery and operator interpretation

The ledger Issue body holds the manifest; marked comments hold shards. Each
shard is limited to 64 records and 48 KiB of full serialized UTF-8 text, including
its marker. Full shards roll over. Eligible source snapshots remain complete;
oversized sources are rejected before sending, with identity/link/hash/reason
retained and no misleading truncated message.

- `pending`: source persisted and waiting for an attempt.
- `attempting`: attempt ID/count/time and the channel reservation were durably
  verified before one POST. An interrupted attempt becomes `unknown` on recovery.
- `delivered`: HTTP 200 with a complete, unambiguous `ok` response and an
  authoritatively verified durable receipt. The run's delivered count means
  **newly confirmed in this run**, not total historical deliveries.
- `retry_wait`: explicit 429 or a failure proven to occur before request bytes
  were written. There are at most three total attempts across all runs.
  Non-429 delays are one minute then five minutes. 429 respects `Retry-After`
  through a durable channel-wide hold; an invalid/missing value uses five
  minutes. The next existing workflow run performs a due retry, with no new
  schedule or long rate-limit sleep.
- `unknown`: a timeout, ambiguous response, interrupted write, or unconfirmed
  receipt could have followed acceptance. **No automatic replay is supported.**
  Inspect the source, ledger, and actual Slack history. Even after manual
  reconciliation, replay requires separate authorization and implementation;
  there is no hidden replay/reset switch or instruction to edit the state back
  to pending.
- `needs_attention`: permanent rejection, exhausted retries, oversized content,
  or a changed/deleted source needs review. Other eligible records can proceed.
- `duplicate_source`: another source comment has the same event identity; only
  the canonical earliest valid source is eligible for delivery.

Before every attempt the source's author, Issue identity, metadata, exact body,
and hash are rechecked. Cross-day work and closed daily Issues are retained.
Unknown scan times are explicitly labeled. Retries, scans older than ten minutes,
and earlier-ET-date observations start with
`历史结果／延迟补发，仅供回顾`. This is a display rule, not a strategy or quote-validity
rule. Text blocks preserve the source safely without enabling mention injection.

The summary's queue counts describe the last verified durable snapshot.
`Rejected` is a subset of `needs_attention`. A failed initial load cannot provide
a complete queue count; zero counters in that paused summary do not establish
an empty ledger. Raw credentials and remote error bodies are never diagnostic
output.

## Budget, scale, and diagnosis

Prepare and forward share a **30-second active-work allowance**. Prepare exports
`remaining_budget`; forward consumes that exact allowance. The intervening scan
does not consume Slack activity time, but the whole job still has its unchanged
eight-minute limit. Per-request timeouts are bounded by the allowance, and the
Linux wall timer also limits blocking work. At least one second separates actual
POST writes; an uncertain attempt retains its conservative durable channel hold.
Discovery is attempted before selecting due work. Its source changes are batched
per touched shard, in two durable phases (retire old canonical roots, then admit
replacements); the cursor advances only after the complete traversal is durable.
If retained due work exists, the read-only traversal reserves one safe send
transaction. A traversal that cannot fit yields `discovery_deferred` before any
source mutation, allowing already-verified work to progress. This is explicit
capacity degradation, not evidence that there are no new sources. All non-deferral
discovery failures refresh authoritative state before any old queue is reused.

When discovery completes, first-attempt, same-ET-date sources with trusted scan
age 0–600 seconds in the observation window are fresh. At most two fresh attempts
precede a due historical attempt; each class remains in source timestamp/ID order.
The recent durable attempt history carries this fairness across fresh processes.
Selection and POST-time history labels both use current time. Unknown scan times,
retries, older results and out-of-window results remain historical. Rate-limit
holds always precede sending. Fresh-before-undiscovered-work cannot be guaranteed
when traversal is explicitly deferred; known work still progresses safely.

The workflow now runs `market_data.slack_drain`: the original prepare remainder
funds the first forward, then fresh sequential prepare/forward pairs may continue
inside the SAME job/concurrency group. Pairs default to 30 seconds. A proved
pre-attempt capacity stop may reallocate a pair to at most 60 seconds, as described
below. There are at most eight slices and 240 total allocated active seconds, with a separate
270-second wrapper wall limit. An earliest-job monotonic timestamp also stops
new slices before the eight-minute job deadline, reserving 45 seconds for shutdown
and artifacts; a full next slice plus process margin must fit. The market scan is not rerun. No second writer is
created. Holds, new uncertainty and read/write errors stop continuation. A
zero-delivery slice normally stops as `no_verified_progress`. Once per drain,
complete discovery OR explicit read-only `discovery_deferred=true`, plus
`budget_stop_stage=pre_attempt_reserve` and an explicit
`safe_pending_continuation=true` may grant a new slice: the selected durable
pending entry has no attempt evidence, and this slice has not begun any attempt
write. This exception never applies after attempting/POST/receipt work, to
unclassified/failed incomplete discovery, or to a retry/channel hold. A read-only
deferral preserves the durable cursor and existing verified queue; the next
slice revalidates both. If the sanitized admission requirement is strictly above
30 and below 60 seconds and exceeds remaining time, that one safe exception may
choose a 60-second allocation. Prepare retains its 30-second ceiling; forward
receives prepare's unspent remainder plus the borrowed 30 seconds. The first
slice debits 30 seconds including its earlier prepare; each subsequent pair
debits its entire allocation before starting. After confirmed progress, subsequent
pairs may retain the 60-second allocation, still debiting the same 240-second pool.
`allocated_activity_seconds` reports this conservative allocation, not actual
elapsed time. `activity_limit` stops before a pair would exceed the pool. This
never grants a second zero-delivery exception. Persistent capacity requirements
that do not fit, or sudden latency increases, can still stop progress. A second zero-delivery budget
pause stops; the existing eight-slice and wall/job limits still apply.
`budget_stops` retains the fixed stage and slice number in the final drain report,
including when continuation succeeds. Each stop may include a sanitized numeric
`diagnostic`: remaining seconds, required admission reserve where known, observed
GitHub request peak, GitHub request count/time, and fixed-phase count/time. Counts
include failed GitHub calls; they exclude Slack calls, sleeps and process overhead.
The reserve is the existing conservative admission calculation, not a prediction
of future latency. Non-finite, negative, boolean or oversized numbers and unknown
keys/phases are omitted. No URLs, payloads or credentials are logged.
Existing quarantined unknown records are retained, never replayed,
and do not prevent healthy work. `discovery_complete=false` cannot be reported
as an empty, fully drained channel. Remaining work stays durable for a later run.
The eight-minute whole-job cap remains unchanged; its hard cancellation can still
interrupt an unusually slow setup/scan, and must be treated conservatively.

The implementation requires **one serialized writer** using the existing
`hosted-core-watch-fallback` concurrency group with `cancel-in-progress: false`,
including manual reruns. Do not run independent local or second-workflow writers
against the same ledger. A run starts with complete authoritative validation of
all shards. Mutations reread the manifest and touched shard and verify the exact
written content. GitHub Issue storage has no atomic compare-and-swap guarantee.
Unrelated out-of-band edits to untouched shards during a run can be detected only
at the next full load; they are outside the serialized-writer prerequisite.

Deterministic offline latency evidence, not production throughput promises:

- 25 retained completed shards plus 25 new burst sources and one existing fixture
  discovery source at 300 ms per HTTP request: all 26 unique messages confirmed in
  eight prepare/forward slices, 162.3 simulated active seconds, no unknowns or
  duplicates. Every slice stays within the shared 30-second allowance.
- 2,400 completed underfilled shards at 300 ms/request still make confirmed old
  queue progress within prepare+forward's shared allowance by deferring the
  oversized read-only discovery traversal. This is not a freshness guarantee.
- At 1.2 s/request, a full read of that retained ledger cannot fit 30 seconds.
  Even for a small ledger, an estimated transaction that cannot fit remains
  pending with zero attempts; drain stops on no verified delivery progress.
  Capacity failure is reported; no unlimited extension or reserve reduction occurs.
- A small queue with one 1.5-second GET peak in every slice previously stopped
  twice with a 35-second admission requirement. Bounded reallocation confirms
  nine safe pending events while retaining one historical unknown in the fixture.
  When base latency is 2.5 seconds plus the repeated peak (83-second requirement),
  both slices stop before attempting, with the complete durable ledger unchanged.
- Repeated same-slice `store.load()` calls already reuse `verified_run`'s validated
  snapshot and issue no extra HTTP requests. `refresh()` and write guards remain
  authoritative reads. This change does not remove any write-before-check read.

Before persisting attempting, admission reserves eight observed GitHub request
latencies for preparation, up to five seconds for Slack POST, and twelve observed
GitHub request latencies (minimum four seconds) for closeout/429/reconciliation.
The POST receives a reserved-budget view and a nested Linux hard deadline; the
outer absolute alarm is restored without extension. A timed-out POST is persisted
as unknown while closeout time remains. These are mitigations, not a guarantee
against process death, sudden slow GitHub reads or an unbounded reconciliation
refresh. Never retry an unknown delivery automatically. Request latency metrics
are run-local and contain no credentials, URLs or response bodies.

Use the run's fixed diagnostic and durable state to choose the next action:

| Diagnostic/state | Operator action |
| --- | --- |
| `disabled` | Check whether forwarding is intentionally off; enable only with authorization |
| `untrusted_context` | Use this repository's trusted-main supported trigger; do not weaken the guard |
| `invalid_configuration` | Verify the configured ledger number, independently verified ID list, existing token permission, and secret presence/installed destination through secure interfaces; never echo the secret |
| `ledger_unavailable` | Read the Issue and all shards; check permissions, producer authors, schema/markers, missing shards, and unexpected edits. Preserve evidence and repair only through an authorized plan; normal recovery is not initialization |
| `discovery_unavailable` | Check GitHub availability, permissions and complete pagination; retain the cursor and ledger, then let the next normal run retry |
| `budget_exhausted` | Compare prepare's remaining allowance and confirmed-delivery counts over runs. Inspect ledger shard/page counts and observed API latency. If the initial load repeatedly consumes the allowance without progress, pause forwarding and obtain a separately reviewed capacity/storage plan |
| `internal_error` | Retain the run ID and sanitized summary; investigate offline before resuming |
| `retry_wait` with no new delivery | Inspect the persisted next-attempt/channel-hold time and wait for a due existing run |
| `unknown` or `needs_attention` | Reconcile the specific source and delivery evidence; do not reset attempts or replay automatically |

Do not increase the reviewed 60-second extended-slice ceiling, 240-second allocation
pool, eight-slice drain limit, or eight-minute job timeout,
clear the ledger, discard history, weaken validation, or change storage to hide
a scale problem. A migration, compaction design, or replay tool is a separate
reviewed and authorized change.

## Verification boundaries

The offline tests exercise actual publisher/scan/prepare/forward code with mocked
network I/O, durable state shared across fresh transports, and deterministic
clocks. Workflow tests pin the exact guards, cancellation/failure truth table,
secret scope, remaining-budget wiring, and unchanged scheduler/scan contracts.
They include a successful first source POST followed by a failed second POST;
only the saved source is forwarded while the `INCOMPLETE` scan remains failed.
The combined regression also rejects stale and missing source provenance before
any material POST, preserves the exact source metadata/body through independent
Slack discovery, and retains the rejection report when a later POST fails.
Delayed forwarding still transmits a valid saved observation as history; it does
not recertify the market input as fresh.

The canonical full local verification command is `make verify`. A successful
run ending in `VERIFY_OK` establishes local reproducibility only. The local
tests do not establish GitHub-hosted-runner execution, actual installed Slack
channel binding, real delivery, production latency/clock behavior, or remote
reproducibility. Real end-to-end testing has not been run as part of this change;
the production gates above remain outstanding.

## Runtime diagnostics and trusted operational state

Scanner diagnostics are explicitly nontrade `SCAN_DEGRADED`, `SCAN_UNAVAILABLE`,
`SCAN_FAILED`, `SCAN_RECOVERED`, and `SCAN_DELIVERY_TEST` sources. They are written
to the existing configured ledger issue and forwarded through the same durable
queue, conservative retry policy, and webhook. Raw provider errors, credentials,
and account data are not included. A same-state incident generates no new
notification; the immutable accepted snapshot takes precedence over a later
source edit. A 30-minute notification cooldown limits recovery/downgrade
flapping; this is not a claim of 30 continuous healthy minutes.

The health and Slack CLIs use only Python's standard library, so they can report
a dependency-sync failure. They run even after scan exit 2. Health persistence
failure remains a failed workflow; forwarding still gets its independent chance
to run. If GitHub/ledger access itself fails, a job never starts, checkout fails,
or the runner is cancelled, an in-job notification cannot be guaranteed.

A manual `hosted-core-watch-fallback` dispatch on main with
`slack_operation=health-test` skips market scanning and emits one clearly labeled
transport diagnostic per run ID. It does not alter the current incident state.
Verify the actual source comment, durable delivery status, and Slack receipt;
green CI alone is not production delivery proof. Market observations forwarded
outside 04:00–20:00 ET retain their source timestamps and are explicitly marked
historical even when under ten minutes old. Current health diagnostics use the
normal age/retry history policy, without pretending to be market observations.

### Identity checks

Daily observation issues and comments require the exact repository and canonical
URLs, positive numeric identities, and the designated GitHub Actions bot ID
41898282 with type `Bot`. Titles, display names, and workflow IDs are not author
identity. Untrusted comments are ignored before parsing ledger-like text, event
markers, or heartbeat state. Corrupted or ambiguous trusted state still fails
closed; it is not repaired by resetting the ledger. Accepted immutable source
snapshots remain authoritative when a live comment later changes.

These checks do not make public code private or prove that another repository
writer cannot edit bot-authored content. Locking conversations still allows
repository writers. Preserve all existing delivery entries, especially unknown
attempts: unknown means unconfirmed, never permission to resend. There is no
phase-two private outbox or new persistence architecture in this change.

### Manual writer/lock acceptance

`operational-issue-writer-test` is manual-only on the fixed main branch. Its
`probe` operation verifies inert diagnostic comment creation, modification, and
exact readback on the three fixed operational Issues. `lock-and-probe` first
finishes every pre-lock writer check, then locks and verifies each target and
repeats the writer test while locked. The shared workflow concurrency group
prevents normal ledger writes during the diagnostic snapshot comparison.

The probe verifies exact Issue and bot identities, checks existing state for
poison markers, and uses a read-only ledger loader so a diagnostic cannot repair
an orphan or mutate authoritative state. Existing delivery records, hashes, and
unknown attempts must survive unchanged relative to the fresh run baseline.
Retry reconciliation cannot blindly repeat an uncertain POST or claim write
access from an unchanged old comment. On failure it stops additional locks and
reports the verified result; it never deletes comments, resets the ledger,
replays unknown deliveries, or automatically unlocks a target. Current runner
results, not this document, establish locked-writer compatibility.

## Regression-test interpretation (2026-10-14 repair)

The first three new latency acceptance cases implement the newly approved capacity
and scheduling policy; they were not violations of the previous old-queue-first
contract. Short-budget admission and POST-time history classification correct
reliability/display edge cases. Existing crash, canonicalization, cooldown and
unknown-quarantine protections remain mandatory. The source fixture's HTTP Date
must be set on the fresh forward client, not a discarded predecessor instance.

Observability scope: this repair adds `discovery_complete` and aggregate drain
counts/stop reasons. Per-phase elapsed, cursor lag, oldest pending age, and exact
POST-start/response/receipt-readback timing are not implemented yet. Do not infer
those timings from attempted_at/delivered_at or claim the full observability
proposal is complete.
