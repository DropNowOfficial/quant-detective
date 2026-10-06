# Factor foundations: local research and evidence

This first delivery adds trustworthy input boundaries, versioned evidence and a
non-promoting local audit ledger. It does not add alpha, a stock score, a learning
service, a job runner, trading, provider purchases or a production migration.
Source quality, point-in-time (PIT) grade, lifecycle and the three locked axes
remain separate; passing a rule is not an investment probability.

## Start and inspect

Install the repository dependencies before starting the Python server:

```bash
uv sync --frozen --extra research
uv run qd-market serve
```

Open `http://127.0.0.1:8767/factors`. The default catalog has nine source-bound
existing calculator definitions and seven unimplemented locked components.
Catalog reads do not register them in SQLite. A missing observation is shown as
missing, including for unimplemented components. The market-adjusted relative
strength hypothesis is an unvalidated candidate with no calculator.

Import is disabled by default. To explicitly enable the local research importer:

```bash
uv run qd-market serve --factor-import --factor-store-path runtime/factors.sqlite
```

The browser and server must use the same loopback origin and port. Use the page's
CSV and JSON file selectors, map each canonical field to a CSV header, inspect
Preview and then explicitly Save. Preview never saves automatically. Replacing a
file or mapping invalidates the preview. Revision conflicts require a new
preview; unchanged committed source content can replay its original dataset.
The page's downloaded metadata is an evidence summary, not the raw upload or a
PIT certificate. Keep original data in separately licensed storage, outside Git.

The exact definition/manifest JSON and canonical CSV mapping are documented in
[factors-import](factors-import.md). The page's session response provides the
current instrument-universe version; imports cannot expand membership or choose
their own trust, quality, receipt or calculation clocks. Source citations are
retained as text and never fetched. Formula text is never executed.

Imported external values always remain `RECONSTRUCTED` with `UNAVAILABLE`
confirmation quality. Exploratory as-of reads expose only evidence whose effective
availability is no later than the requested time. `live` excludes these rows;
`strict_replay` refuses them with `PIT_EVIDENCE_REQUIRED`. A future effective claim
does not become current evidence. A real numeric zero stays zero; a missing value
requires an explicit reason.

## A / B / C first-delivery scope

- **A: input availability.** The existing minute and US-watch paths check actual
  source/session clocks, input windows, receipt and exclusive expiry. Missing,
  malformed, gapped or stale consumed inputs veto confirmation. Ordinary expiry
  is a use-time failure, not an automatic lifecycle quarantine. Fresh synthetic
  fixtures retain the existing entry geometry, 0.8 same-time RVOL threshold,
  leader rules and minute formulas
- **B: retained evidence.** Strict frozen models, atomic append-only SQLite
  datasets/definitions, source-publication as-of selection, immutable replay,
  guarded opt-in preview/commit, source-bound cards and token-free evidence
  metadata are available. The three volume-ratio meanings have distinct IDs.
  Original report clocks and unknown-clock bounds remain explicit
- **C: local audit.** Exact-version lifecycle events, immutable failed/cancelled
  trial declarations, read-only history and disabled zero-money budgets are
  available. Accepted local events atomically retain approval, checks and event
  under one revision. Local actor labels are audit labels, not authenticated
  external identities

The nine baselines remain `LEGACY_UNVALIDATED`; their existing alerts stay on the
unchanged A-quality-gated path. None has received a governed active permit. The
seven original components and the Time Machine protocol remain unchanged.

## Local governance and trials

Use only the controlled Python API in
[governance_v1](../contracts/factors/governance_v1.md). There is no web governance
write endpoint. Each accepted event requires an opaque live `local_operation`
context bound to the exact store, event and actual UTC decision clock. Staging
approval does not write. Backdated events cannot use a current operation to revive
expired evidence. User-supplied actor/approval/report fields do not confer authority.

First-round data readiness supports explicit **bars counts only**, including lag
and retained original quality sample counts. Session-count requirements are
refused rather than inferred. Imported unavailable quality cannot pass readiness.
Permission descriptions are retained provenance, not verified legal licenses.

Only candidate/data-ready/quarantine/retired/archive transitions specified in the
contract are supported. Requests for research validation, forward shadow,
approved active or watch fail with `EVIDENCE_VALIDATOR_NOT_CONFIGURED`.
Trial factor/strategy/forward kinds remain distinct; even a declared successful
report cannot promote a factor. A trial record is a declaration, not an execution
or verified train/test/preprocessing artifact. Use a new run ID for new evidence;
never rewrite a failed record. Budgets must be disabled and have zero paid spend;
recording one starts no work and grants no future spending authority.

## Verification and packaged runtime

```bash
UV_CACHE_DIR=/tmp/qd-factor-uv-cache UV_LINK_MODE=copy make verify
UV_CACHE_DIR=/tmp/qd-factor-uv-cache UV_LINK_MODE=copy \
  uv run --extra research pytest -q tests/test_factor_roundtrip.py
node tests/test_factor_import_state.cjs
node tests/test_factor_history_state.cjs
```

`make verify` remains the single canonical source verification entrypoint. The
roundtrip suite also builds a real wheel, installs it into a temporary target and
loads it without the editable source root. It checks all sixteen registry
`definitions/get` entries, the packaged canonical JSON hash, `/factors` HTML and
`/api/factors` over a synthetic loopback server. Dependencies come from the
verification environment; this does not assert an independent fresh dependency
installation or a rendered browser. The build needs `uv` and access to its
cached/official build backend. There is one canonical definition file in source;
the wheel includes that file as a package resource.

The Node suites execute the actual page script with minimal fixtures. They check
asynchronous file/read/preview/save and history selection, not DOM rendering,
visuals, responsive layout, native downloads or real-browser JavaScript errors.
The supported browser entry remains:

```bash
CHROMIUM_EXECUTABLE=/usr/bin/chromium node tests/browser_market_data.cjs
```

It additionally needs Playwright via `CODEX_PRIMARY_RUNTIME_NODE_MODULES` and a
permitted Chromium/loopback runtime. The first delivery's browser run was blocked;
see the [dated validation record](factor-foundations-validation-20261006.md) for
exactly what was and was not established.

## Disable, roll back and preserve evidence

1. Stop the server before backing up the factor SQLite file. Keep the DB, any
   SQLite sidecars and original licensed sources outside Git. Prefer a consistent
   SQLite backup if another connection may still be open
2. Restart without `--factor-import` to disable writes. An explicitly configured
   existing store may still be inspected read-only; a read never creates or
   migrates an absent store
3. To roll back code, restore the previously reviewed source/build after preserving
   the DB. Do not delete, edit, downgrade or rewrite dataset/trial/event rows.
   Keep this evidence DB separate if an older build cannot read its schema
4. Check the original quality-gated alert path and locked-contract hashes with the
   canonical verification command before any separately approved production use

## Next separately scoped plans

- Run real-browser DOM/download/responsive/visual/JS-error acceptance in a
  permitted runtime, retaining screenshots outside Git
- Implement controlled Amihud and market-adjusted relative-strength calculations
  and shadow research under explicit source/calendar/permission constraints
- Design and independently review train/test, preprocessing, strategy and forward
  evidence validation before any promotion API
- Review any legacy-baseline migration, production cutover, budget activation,
  paid data procurement, scheduling or new notification category separately

API references: [store](factors-store.md), [import](factors-import.md),
[bindings/cards](factors-bindings.md),
[governance](../contracts/factors/governance_v1.md).
