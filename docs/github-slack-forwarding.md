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
Due old queue work is considered before new discovery. Budget exhaustion leaves
unfinished work for a later existing workflow run.

The implementation requires **one serialized writer** using the existing
`hosted-core-watch-fallback` concurrency group with `cancel-in-progress: false`,
including manual reruns. Do not run independent local or second-workflow writers
against the same ledger. A run starts with complete authoritative validation of
all shards. Mutations reread the manifest and touched shard and verify the exact
written content. GitHub Issue storage has no atomic compare-and-swap guarantee.
Unrelated out-of-band edits to untouched shards during a run can be detected only
at the next full load; they are outside the serialized-writer prerequisite.

Deterministic offline latency evidence, not production throughput promises:

- A fixture with **2,400 underfilled completed shards**, four old queued sources,
  220 unrelated historical comments, and one new source progresses at 300 ms per
  HTTP request. Prepare uses **7.8 s**, leaving **22.2 s** for forward, which
  confirms **two old entries** within the combined 30-second allowance. Fresh
  runs continue the durable queue. Separate full-allowance forward runs confirm
  four old entries in 30 s and the new-history entry in the next 22.5 s.
- At **1.2 s per request**, the same fixture's initial full load requires
  **26 requests / 31.2 s**. It safely pauses at 30 s with `budget_exhausted` and
  no POST. If complete validation cannot fit, repeated runs cannot solve that
  scale/latency boundary. Real parsing/serialization overhead can reduce capacity.

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

Do not automatically increase the 30-second budget or eight-minute job timeout,
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
