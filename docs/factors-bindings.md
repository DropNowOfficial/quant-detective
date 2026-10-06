# Source-bound factor catalog and evidence (B3)

This page and catalog wrap existing output values. They do not calculate a new
factor, rank stocks, promote a lifecycle state, certify PIT, or execute trades.
The exact three locked axes remain `CONTRACT_STATUS`, `DATA_STATUS`, and
`VALIDATION_STATUS`; `lifecycle_state` is separate and additive. The nine reviewed
existing calculator identities are version `1.0.0` and `LEGACY_UNVALIDATED`, with
`NEEDS BACKTEST` and no production eligibility. External definitions and the
market-adjusted relative-strength hypothesis remain `CANDIDATE`. The hypothesis
has no calculator or observations and is not Firm-Specific Alpha. The seven
locked components retain their original names and remain unimplemented.

## Definition catalog

`contracts/factors/definitions_v1.json` contains the nine existing definitions
and seven unconnected component definitions. `factors.registry.definitions()`
returns independently parsed snapshots. `get(FactorRef)` resolves exactly the
reviewed ID/version and raises `UNKNOWN_DEFINITION` otherwise. Reads do not
register these virtual definitions in SQLite or increase a store revision.

The reviewed IDs are reserved. Store registration/atomic definition commit
rejects changed content or a new unreviewed version with
`DEFINITION_VERSION_CONFLICT`; an external ID cannot borrow a built-in calculator
identity. The external-value importer additionally refuses all reserved IDs,
including an exact copy of the reviewed definition. An already present conflicting
record makes catalog/evidence reads fail closed with HTTP 409, without overwriting
or displaying it as an authentic built-in.

## Existing-report boundary

`observations_from_report(report, *, recorded_at)` returns only bound fields
actually supplied by a report. It checks the explicit UTC call clock but never
uses `recorded_at` to create a source, availability, ingestion, or computation
clock. `binding_diagnostics(report)` identifies unavailable source provenance,
unknown source clocks, or invalid evidence. No observation is generated for any
unconnected component or omitted field/report.

Accepted shapes are a native `{"rows": [...]}` report (or a single native symbol
row) and an explicit combination `{"us_report": ..., "minute_report": ...,
"research_report": ...}`. The same report can be passed to
`make_server(..., factor_reports=report)` as an application-owned defensive
snapshot. No URL/query can choose a source file, fetch a report, expand a universe,
or start a scan. If the application does not supply reports, reads inspect only
already captured minute analyses in existing in-memory live sessions. They do
not call `live.snapshot`, create a session, or request provider data. Native US
headless reports are not automatically discovered from an arbitrary file.

Field binding is exact:

- US daily `daily.ma5_slope_1d` → `us.daily.ma5_slope_1`
- US `intraday.d5_atr` → `us.intraday.distance_ma5_atr5`
- Existing `relative_change_vs_qqq_pp` → `us.intraday.relative_qqq_change`
- US `intraday.same_time_rvol` → `us.intraday.rvol_same_time20`
- US `intraday.rth_vwap_approx` → `us.intraday.vwap_rth_hlc3`
- Minute `metrics.rvol20` → `minute.rvol_prior20`
- Minute `metrics.vwap60` → `minute.vwap60`; original `vwap_basis` remains visible
- Supplied research row `features.volume_ratio` → `research.daily.volume_ratio20`
- Supplied research row `features.adv20` → `research.daily.adv20`

The three RVOL definitions have distinct IDs, formulas, and windows. Original
same-time RVOL comparable-session count remains separately visible in the
availability basis; `QualityResult.sample_count` is explicitly labeled as bars. Missing
values remain `None` with `REPORT_VALUE_MISSING`; zero remains numeric zero.
Boolean/nonfinite values and invalid clocks produce diagnostics, never values.

### Explicit original provenance

A controlled report row can include `factor_evidence` with original
`observed_at`, `source_published_at`, `provider_available_at`, `ingested_at`,
`computed_at`, `effective_available_at`, `source_ref`, `input_hash`, `input_refs`,
and optional `dependency_available_ats`/`original_receipt_at`. It may instead map
each exact factor ID to that factor's distinct original provenance. The caller
must attach actual source evidence, not invent it from a run clock. ISO timestamps
need offsets and are normalized to UTC. All B1 consistency checks still apply.
This is an application boundary, not an upload route or PIT-verification service.

### Native report limitations and conservative bounds

Native US/minute reports currently retain source bar/event watermarks, original
receipts and dependency evidence, but do not retain exact publication/provider
availability clocks. These values are represented only as `RECONSTRUCTED`
exploratory evidence, using the original source receipt as a conservative upper
bound in B1's mandatory time fields. `availability_basis` includes both
`SOURCE_PUBLICATION_TIME_UNKNOWN` and `PROVIDER_AVAILABILITY_TIME_UNKNOWN`.
Cards expose the actual publication/provider clock as null/unknown and explicitly
label the receipt bound; it is never presented as an exact historical source
clock. Contradictory partially specified clocks fail B1's order checks rather
than being repaired.

Original US daily receipt and history receipt are preserved as applicable.
The daily event uses the official XNYS close of the report's original completed
session; calendar failure creates no observation. US intraday observed times use
original current-bar time (distance/relative change) or completed watermark
(RVOL/VWAP). Minute observed time is the original completed signal watermark.
Original receipt remains the receipt even if a later dependency was ingested;
all consumed dependency captures remain in `dependency_available_ats`.

Native calculation time is also not recorded exactly. The original report's
`generated_at_utc`, or original quality evaluation clock when no report generation
clock exists, is explicitly labeled `COMPUTATION_TIME_UPPER_BOUND`; the card's
`actual_computed_at` is null. Receipt/dependency/computation bounds gate effective
availability. The native input hash identifies the original report output and
captured dependency evidence, explicitly not a raw-source-file digest. These
bounds are not forwarded into the locked `known_at` protocol as exact times.

Bare research feature maps have no original source-event/receipt provenance and
thus produce no observation. An explicit source-bound research report is required
for the two research bindings. A summary's `snapshot_known_at` or current run time
alone is not sufficient per-feature source evidence.

The relative-QQQ binding also hashes the complete original benchmark dependency
(input value, source identity, capture/source timestamps and stable evidence),
and retains a QQQ dependency input reference with original source/receipt and
evidence fingerprint. Missing benchmark source identity is labeled unknown.
Operational wrapper/computation/freshness-evaluation clocks are excluded from
that mathematical input identity; changed original QQQ inputs/provenance still
change the hash even when the already computed relative value is unchanged.

Original `QualityResult` source freshness is retained separately from validation
and eligibility; benchmark capture/expiry is bounded on the relative dependency,
and minute report quality already includes its actual populated benchmark expiry.
Every B3-wrapped observation remains reconstructed. `live` excludes it;
`strict_replay` rejects it with `PIT_EVIDENCE_REQUIRED`. No wrapped/imported value
gets production eligibility. Old records remain inspectable after exclusive
quality expiry with their value, age, original clocks and `EXPIRED_QUALITY`.

## HTTP and guarded browser flow

- GET `/factors` serves the local page, no query/fragment permitted
- GET `/api/factors` returns virtual + external definitions, fingerprints,
  axes, lifecycle labels, hypothesis, import enable state and diagnostics; no query
- GET `/api/factors/observations` requires exactly one nonblank `factor_id` and
  `version`; optional `as_of` (timezone-aware ISO), `mode` (`exploratory` default,
  `live` or `strict_replay`). Unknown/duplicate keys fail; no arbitrary SQL/path
- GET `/api/factors/lifecycle` requires exactly one factor_id/version; optional
  timezone-aware as_of reads accepted local events/checks only
- GET `/api/factors/trials` requires exactly one factor_id/version and reads
  immutable factor/strategy/forward records with honest missing-evidence limits
- History query keys are strict; duplicate/unknown/blank keys fail. Reads use an
  explicitly configured existing DB in SQLite read-only mode and never initialize
  a missing/old schema. No trials are shown as 未运行 / No recorded trials
- Existing B2 session/preview/commit remain the only writable routes, opt-in

The page reads CSV and strict definition+manifest JSON into page memory, exposes
canonical-field→CSV-header selects, sends the complete B2 preview, and displays
row errors, warnings, ten-row sample, fingerprint, original revision and proposed
manifest. It never saves a preview automatically. A separate explicit button
commits only the frozen preview handle and a stable retry request ID. Editing
files/mapping invalidates the prior preview; exclusive expiry disables save.
Selecting or clearing a replacement immediately clears that parsed source and
blocks preview/save while either file is being read. Read completion invalidates
the prior generation again. Exact source/mapping generation and file-read
identity follow preview and commit; late superseded reads (including failures)
and old preview responses are discarded. A commit uses its frozen preview's
definition reference rather than a subsequently selected file.
Saved/replayed results display B2's retained original manifest.

Session token exists only in process/page memory and the CSRF request header.
The metadata JSON download includes preview/evidence fields and optional commit
result, but excludes the token, session, preview handle, CSV and original upload.
All user-supplied names, formulas, source citations and file/header text are
inserted via `textContent`/DOM nodes, with no HTML insertion or source URL links.
The only generated download URL is an internal Blob URL, revoked afterward.

`CHROMIUM_EXECUTABLE=/usr/bin/chromium node tests/browser_market_data.cjs` retains
the existing mocked market-page checks and adds a real loopback synthetic
factor-server import workflow. It never fetches a real provider or sends a
notification. Screenshots go only into the ignored task workspace. Actual
browser acceptance is separate from `make verify`; see the task report for the
verified runtime and any browser startup/access blocker.

`node tests/test_factor_import_state.cjs` executes the actual page script in a
minimal DOM/fetch fixture to test asynchronous replacement/read/preview/save
state. It is a focused unit test, not browser rendering or visual acceptance.
The supported browser entry additionally holds real `File.text()` completions
to cover CSV/JSON replacement and out-of-order completion.


C1 adds read-only local lifecycle/trial history under the
[non-promoting governance contract](../contracts/factors/governance_v1.md).
Untouched catalog labels retain the B3 display casing; accepted local records
show their lowercase governed state separately from the unchanged three axes.
Local audit actor labels are not authenticated external identities. Reconstructed
B3 observations remain ineligible for production, and LEGACY_UNVALIDATED never
authorizes a migration. Existing alerts still follow the original A-quality gate.
`node tests/test_factor_history_state.cjs` checks the actual script's literal-text
history, failed trial/kind display and late-selection isolation. It is unit
evidence, not browser/visual acceptance.
