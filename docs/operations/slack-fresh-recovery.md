# Fresh-only recovery after the October 2026 ledger outage

The manual `manual-slack-fresh-recovery` workflow is the recovery entry point.
It defaults to preview and requires `QD_SLACK_ENABLED` to be exactly `false`.
It uploads the complete backup before its optional apply step; backup upload failure
prevents mutation. No Slack webhook is passed and it never re-enables delivery.

This change does not itself resume production delivery. Do not merge pagination
alone, clear the ledger, replay unknown deliveries, or call initialization.

## Policy

The explicitly chosen UTC cutoff is stored as `manifest.fresh_after`. Each source
is judged by its own `generated_at_et`, not the GitHub comment creation/update
time. Missing, future, pre-cutoff, and more-than-600-second-old scan timestamps
are not sent. The test runs both before preparing a send and immediately before
POST, including time consumed by durable writes.

Existing immutable source snapshots, delivered entries, event-key deduplication,
original enabled_at, and baseline_ids remain intact. Recovery moves old unsent
pending/retry_wait entries to needs_attention with a cutoff reason. Unknown entries
remain unknown; an interrupted attempting entry becomes unknown, never a retry.

## Deployment and recovery order

1. Disable outbound delivery, verify it is disabled, and ensure no sender remains
   running. Deployment must include pagination, recovery hold, and fresh-only
   logic atomically, not PR #79 by itself.
2. Run a trusted manual recovery operation in **the same GitHub Actions concurrency
   group `hosted-core-watch-fallback`**. `LedgerStore.verified_run()` verifies writes
   but is not a lock. Do not run recovery separately alongside any writer.
3. Save a read-only backup of issue 43 and all pages of comments in private storage.
   Pick the current GitHub server UTC time as the cutoff. Use that exact value for
   preview and apply; do not move the cutoff between retries.
4. Call `recover_fresh(store, cutoff=cutoff, now=server_now)` to preview. No writes
   occur, and an orphan shard causes an error rather than implicit repair. Review
   the returned counts and any remaining non-unknown sendable entries.
5. With explicit recovery authority, call the same function with `apply=True`.
   It never sends Slack. It retires old queue entries first, verifies those writes,
   then writes the cursor and fresh_after together. A partial failure leaves the
   old cursor/hold until the retirement batch finishes. Re-read all records and
   verify the cutoff, statuses, unchanged source hashes and unknown entries.
6. Re-enable delivery only with the cutoff verified. Observe a new eligible event
   and its actual receipt; overnight skipped jobs do not prove recovery. Do not
   synthesize a trade signal as a test. Live test notifications require approval.

## Failure and rollback

An apply uses only the backup from the same serialized run and rejects any
intervening ledger change. After partial failure, keep delivery disabled and inspect
the saved backup and live state. Run a new preview before explicitly approving
another apply; do not force the old backup past the mismatch check. Never delete
or rewrite original source records or rewind the cursor.

New code reads old manifests without fresh_after. Old code cannot read the new
field, and deliberately fails closed. **Do not roll back to old code or delete the
field to make it parse.** Roll back only to a reviewed version that retains this
cutoff and the fresh-only send gate, with outbound delivery disabled first.

The stored cutoff cannot be removed or moved backwards through update_manifest.
This is a one-time repair entry point, not a weekly credential-renewal procedure.
