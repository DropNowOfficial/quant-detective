# Factor foundations validation record — 2026-10-06

## Scope and outcome

The local source gate and synthetic integration checks passed. This is evidence
for the first research/evidence delivery, not factor effectiveness, certified
PIT, real-provider reproduction, a production migration or a trading permission.
No provider purchases, real notifications, trades, deployment, merge, push or
remote CI run were performed. All source/network fixtures are synthetic or mocked;
the HTTP integration uses only a local loopback server.

The delivery adds three end-to-end evidence tests and one installed-wheel test in
`tests/test_factor_roundtrip.py`. No product-code integration repair was needed:
the existing A/B/C APIs and canonical definition packaging passed once the new
test harness correctly encoded offset query strings and removed the editable
source root from its isolated subprocess. The initial run had 2 passes and 2
harness failures; it was not evidence of a product defect or a production-code
RED/GREEN repair. Subsequent checks below use the corrected assertions.

## Commands and observed results

Verification runtime: Linux, Python 3.13.5, uv 0.12.19, Node v24.19.0. Commands using
uv below set `UV_CACHE_DIR=/tmp/qd-factor-uv-cache UV_LINK_MODE=copy` so the cache is
writable and links do not depend on the workspace filesystem.

| Command | Observed result | Exit |
|---|---|---:|
| `make verify` | `SCHEMA_GATE_OK`; validation kernel passes; 738 pytest tests; 84 legacy unittest tests; Time Machine synthetic fixture passes; `VERIFY_OK` | 0 |
| `uv run --extra research pytest -q tests/test_factor_roundtrip.py` | 4 passed | 0 |
| `uv run --extra research pytest -q -W error::ResourceWarning tests/test_factor_roundtrip.py tests/test_us_watch.py tests/test_live_features.py tests/test_factor_bindings.py` | 121 passed; no resource warnings | 0 |
| `uv run --extra research pytest -q -s tests/test_factor_roundtrip.py -k installed_wheel` | 1 passed, 3 deselected; `INSTALLED_WHEEL_OK` | 0 |
| `node tests/test_factor_import_state.cjs` | actual-page async import-state suite: PASS | 0 |
| `node tests/test_factor_history_state.cjs` | actual-page history-state suite: PASS | 0 |
| `node --check tests/test_factor_import_state.cjs` | syntax check passes | 0 |
| `node --check tests/test_factor_history_state.cjs` | syntax check passes | 0 |
| `node --check tests/browser_market_data.cjs` | supported browser entry syntax passes; browser acceptance not executed | 0 |
| `node --check` on the extracted `market_data/factors.html` inline script | actual-page JavaScript syntax passes | 0 |

`make verify` invokes the full root pytest suite, the preserved legacy suite,
schema/kernel checks and the Time Machine fixture through the existing Makefile.
The two Node entries are suites with assertion summaries, not test-count-reporting
runners. No individual browser assertions or screenshots passed this delivery.

The full legacy unittest command emits baseline `ResourceWarning: unclosed
database` messages from the legacy SQLite store and later garbage collection.
They are not hidden or treated as pristine output. New focused factor resources
close their DBs/server/connections; the separate focused resource-warning gate is
reported below. Repairing legacy connection lifetime is outside this delivery.

## What the roundtrip establishes

- Real temporary CSV/definition files pass through the reviewed B2 preview and
  explicit commit boundary into B1; original source/provenance/raw-file SHA remain
  inspectable through the manifest, catalog and B3 cards
- Future effective claims are absent from current as-of selection; zero remains
  numeric zero; reconstructed external values remain unavailable for confirmation,
  excluded from live mode and refused by strict replay
- A failed factor trial is retained with its original error; it cannot satisfy
  strategy evidence or promote a candidate. Imported unavailable quality cannot
  pass data readiness. Blocked events write no accepted history
- Explicit local approval stages without a revision write; an accepted quarantine
  atomically retains an event, check and approval. Restart reopens the same DB and
  preserves selected observations, manifests, failed trials, lifecycle history,
  checks, approval and the unchanged production manifest
- A same-source/definition re-preview has the same canonical source-content and
  mathematical output identities despite formatting and operational clock changes;
  commit replays the first original dataset and bytes/hashes
- A formula-only change changes mathematical identity at the same reference. A
  new explicit definition version and a changed publication/input snapshot have
  different fingerprints, even when the supplied value is unchanged. New evidence
  cannot rewrite the old dataset, observations, failed trial or accepted decision:
  original SQLite payload bytes and audit/content hashes remain identical

## Installed artifact, not checkout masking

The wheel test runs `uv build --wheel --out-dir <temporary-directory>` and
`uv pip install --no-deps --target <temporary-directory> <built-wheel>`, both with
exit 0. It launches the verification Python with `-I`, removes any editable `.pth`
source-root entry before importing project modules and checks their paths belong
to the installed target. It then checks all 16 `registry.definitions/get` entries,
9 calculator bindings, the canonical packaged JSON hash and real HTTP 200
responses for `/factors` HTML and `/api/factors` from that installation. A provider
stub raises if called. The source catalog is not duplicated or redesigned.

This verifies runtime resources from an installed wheel using the verification
environment's dependencies. It does not claim a fresh isolated dependency
resolution, universal platform support, a browser-rendered page or publication of
the artifact. The build/install check is part of the canonical root pytest suite.

## Preserved locked contracts

The original files remain byte-identical to the pre-implementation baseline:

- `contracts/factors/FACTOR_CONTRACT_v1.md`: SHA-256
  `1072fb942a04059402282a6193f44c3fdc0c4e59a5b05ca0bf265badb1cd2549`
- `contracts/protocol/TIME_MACHINE_PROTOCOL_v1.md`: SHA-256
  `f03f4bf00487e4e6308ff1e36f7bd76a92909871ec7573c223496f626238719a`

The canonical 16-definition resource has SHA-256
`0f981d3c9a86d7e95b2382ad623e33c36d32eb3fb21ca5806fe232efcad1c2e6`.
The original entry geometry, 0.8 same-time RVOL gate, leader checks and minute
formulas are covered by existing fixed synthetic US-watch/live-feature tests.
The input-quality tests refuse bad/stale/gapped consumed data. This delivery
changes no production formulas, thresholds, deployment scripts, schedules or
permissions.

## Browser acceptance: BLOCKED / UNVERIFIED

The verified runtime limitation is a Chromium OS socket denial on fresh startup;
the supported existing cloud CDP browser also rejects local access with
`ERR_BLOCKED_BY_CLIENT`. Those restrictions were not bypassed or retried in this
delivery. The supported browser test entry remains available for a permitted
runtime. No screenshots were produced or substituted.

Not established: rendered DOM interaction, native file/download behavior,
responsive 320px layout, visual/accessibility acceptance and absence of real
browser JavaScript errors. Node state/history/syntax and installed HTTP resource
checks are useful evidence but do not establish those browser results.

## Limits, preservation and next plans

No independent evidence validator, training/preprocessing verifier, executable
trial runner, lifecycle active transition, paid budget activation, license
verifier, production cutover or additional notification category exists. First
readiness checks support explicit bars counts only; session readiness is refused.
Source freshness is distinct from PIT/lifecycle. The nine legacy baselines remain
unvalidated and the seven locked components remain unimplemented.

A clean-clone rerun and remote/public reproducibility were not newly established
for this delivery. Retain original licensed input storage and immutable factor
SQLite evidence outside Git. Before a code rollback, stop the writer and take a
consistent DB backup; disable imports by restarting without `--factor-import`.
Do not rewrite old decisions/trials or attempt an evidence-schema downgrade.
Preserve a separate DB if the older build cannot read the new schema.

The [usage guide](factor-foundations.md) gives the complete A/B/C checklist,
rollback steps and next separately scoped plans: permitted real-browser
acceptance; controlled Amihud and market-adjusted relative-strength shadow work;
independent evidence validation; and separately approved legacy migration,
production/budget activation, procurement, scheduling or new notifications.
