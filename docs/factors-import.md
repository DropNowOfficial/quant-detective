# Guarded external factor import (B2)

This is research-only. No trades, network data procurement, script execution,
PIT certification, factor validation or notification is performed. External
values are always `RECONSTRUCTED`, with `UNAVAILABLE` confirmation quality and
clear `PIT_EVIDENCE_REQUIRED` / `EXTERNAL_VALUES_UNVERIFIED` warnings. They may be
inspected through `exploratory`; `live` excludes them and `strict_replay` rejects
them. Catalog registration alone does not establish lifecycle eligibility.

## Enable and controlled instrument identity

`qd-market serve --factor-import [--factor-store-path runtime/factors.sqlite]`
opts into the two write routes. Without the flag, their POSTs remain 405, the
session endpoint is 404, and the factor DB is not opened. Old `make_server(port,
fetcher)` calls still work. Optional application-only keywords are
`factor_store_path`, `enable_factor_import`, and `factor_universe`.

The default current identity catalog is exactly the repository's configured
`market_data.us_watch.DEFAULT_SYMBOLS` plus `QQQ`, with market `us_equity` and
instrument IDs matching existing records' uppercase `symbol` values, e.g.
`AAPL`, `QQQ`. There is no inferred ticker syntax, case conversion, alias lookup,
external catalog request or historical membership claim. Its version is
`us-watch-v1-<12-character SHA256 prefix of canonical membership>`; changing
membership changes the version. Unsupported markets and unknown symbols fail
closed. A reviewed application can inject `InstrumentUniverse(version,
{instrument_id: market})`; the immutable snapshot is copied defensively. Uploads
cannot choose or expand catalog membership. This is a configured research
universe, not a security-master service or exhaustive US directory.

## Session and request security

`GET /api/factors/session` returns the process-memory `csrf_token`, read-only
`universe: {version, instruments: [{instrument_id, market}]}`, `csv_fields`,
`limits` and `pit_grade: "RECONSTRUCTED"`. The same-origin page should request it
using a relative URL and retain the token only in memory. Session responses are
`Cache-Control: no-store`. Never persist or download the token.

Only `POST /api/factors/import/preview` and
`POST /api/factors/import/commit` are writable. Each requires:

- One loopback `Host` header, exactly `127.0.0.1:<server-port>` or
  `localhost:<server-port>`
- One `Origin` header exactly `http://` plus that same Host, including port;
  another loopback hostname, missing/null Origin, HTTPS or trailing slash fails
- One `X-Factor-CSRF` header matching this running server's token
- `Content-Type: application/json` (optional UTF-8 charset), one decimal
  Content-Length, no Transfer-Encoding, and no query string
- UTF-8 JSON object, no duplicate keys and no NaN/Infinity constants

The whole JSON body is at most 5 MiB and is rejected before reading when its
length exceeds the limit. Body reading has a five-second timeout. Definition
wrapper JSON is at most 64 KiB, CSV at most 20,000 logical data rows, and preview
validity is 15 minutes with exclusive expiry. Oversize data is rejected, never
truncated or partially saved. In-memory preview retention is bounded to 64
entries and 64 MiB; the same byte budget covers retained entries plus the one
serialized in-flight preview builder. Normalized rows are counted incrementally
before retaining/hashing the full preview, so legal repeated provenance cannot
expand past that budget. Oversize normalized content returns `PREVIEW_CAPACITY`
without partial store/cache data. Canonical hashing streams rows without a full
all-row JSON string. A full live cache also returns `PREVIEW_CAPACITY`. Expired entries
are reclaimed when a new preview arrives. Shutdown releases this DB's previews.

Other POSTs, including `/api/orders`, and all PUT/PATCH/DELETE retain 405.
Only data text is accepted; requests cannot reference a file path, fetch URL,
executable, script, macro, grade, quality or actual ingestion/computation clock.
Source/provenance text may cite a URL, but is never fetched. Formula text remains
display-only; controlled calculator keys are checked by B1 and never executed by
this external-value importer. Diagnostics do not log request paths, queries,
headers or bodies, and HTTP parser errors do not echo user input.

## Preview JSON and controlled CSV mapping

POST JSON has exactly three fields:

```
{
  "csv_text": "<UTF-8 CSV file decoded by the page>",
  "definition_json": {
    "definition": {
      "ref": {"factor_id": "external.research_value", "version": "1"},
      "name": "External research value",
      "purpose": "Exploratory source evidence",
      "market": "us_equity",
      "frequency": "daily",
      "unit": "ratio",
      "formula_text": "Externally supplied value; no calculation executed",
      "calculator_key": null,
      "input_fields": ["value"],
      "min_history": {"bars": 1},
      "missing_policy": "Preserve missing values with explicit reason",
      "window": {"bars": 1},
      "lag": {"bars": 0},
      "direction": "descriptive"
    },
    "manifest": {
      "dataset_id": "external-research-example",
      "version": 1,
      "source_ref": "source-citation",
      "universe_version": "<session.universe.version>",
      "adjustment": "unadjusted",
      "provider": "provider-name",
      "permission_basis": "permission/provenance description",
      "source_timezone": "America/New_York",
      "corporate_action_basis": "source corporate-action description",
      "history_version": "source-history-version",
      "coverage_gaps": []
    }
  },
  "mapping": {
    "instrument_id": "Instrument",
    "observed_at": "Observed",
    "source_published_at": "Published",
    "provider_available_at": "ProviderAvailable",
    "value": "Value",
    "unit": "Unit",
    "factor_id": "FactorId",
    "factor_version": "FactorVersion",
    "missing_reason": "MissingReason",
    "effective_available_at": "HistoricalAvailable"
  }
}
```

`mapping` is canonical field → CSV header, not the reverse. Its first eight
fields above are required; `missing_reason` and `effective_available_at` are
optional. Header names must be nonblank/distinct and every CSV header must be
mapped exactly once. Unmapped/executable/trust columns fail. Required row
instrument membership/market, unit, factor ID and version must match the server
catalog and single definition. There is no arbitrary expression mapping.

Each timestamp must have an explicit offset or `Z` and is normalized to UTC;
`source_timezone` retains original IANA provenance. Required ordering is
observed ≤ published ≤ provider ≤ actual server receipt. Source/provider future
time is rejected. Optional historical effective availability must be at least
the source/provider times; when blank it defaults to their maximum. An explicit
future effective claim is retained, labeled reconstructed, and remains excluded
from as-of queries before that time. This never upgrades strict PIT or enables
live use. The server assigns actual receipt/computation time at preview and
actual import time at commit, and always assigns reconstructed grade and its own
quality/availability basis. The file cannot assert them.

Values are finite numbers. Numeric zero is preserved; blank value requires a
nonblank `missing_reason`, and a numeric value cannot also claim missing. Every
duplicate instrument/observed event in this single-factor CSV is rejected,
including identical duplicate rows. No partial successful rows are saved.

## Preview response and downloadable evidence

A successful preview request returns 200 with:

- `ok`: false if there are validation errors
- `preview_id`: opaque server-memory handle
- `content_hash`: canonical source-content SHA256, or null for invalid content
- `expected_revision`: original read-only store revision
- `expires_at`: exclusive UTC expiry
- `errors`: `{row, code, field}` entries, with physical CSV line numbers including
  the header (row 2 is the first ordinary data row); document errors have null row
- `warnings`: always identifies the PIT/external-verification limitations
- `sample_rows`: at most ten normalized B1 observation objects
- `proposed_manifest`: B1 manifest including newly uploaded raw `file_sha256`,
  provenance and validated row count, or null when the definition is invalid

The preview changes no catalog, observation, dataset or store revision. Errors
include `UNKNOWN_INSTRUMENT`, `INSTRUMENT_MARKET_MISMATCH`, `UNIT_MISMATCH`,
`FACTOR_ID_MISMATCH`, `FACTOR_VERSION_MISMATCH`, `TIMEZONE_REQUIRED`,
`INVALID_TIMESTAMP`, `IMPOSSIBLE_TIME_ORDER`, `FUTURE_SOURCE_TIME`,
`AVAILABILITY_BEFORE_DEPENDENCY`, `INVALID_FACTOR_VALUE`, `MISSING_REASON_REQUIRED`,
`VALUE_WITH_MISSING_REASON`, `DUPLICATE_OBSERVATION`, `INVALID_UTF8`,
`INVALID_CSV_HEADER`, `INVALID_CSV_ROW`, `INVALID_CSV`, `EMPTY_CSV`,
`INVALID_MAPPING`, `INVALID_DEFINITION_DOCUMENT`, `INVALID_MANIFEST_FIELDS`,
`UNIVERSE_VERSION_MISMATCH`, `DEFINITION_VERSION_CONFLICT`, `DEFINITION_TOO_LARGE`,
`CSV_TOO_LARGE` and `TOO_MANY_ROWS`. Messages never include raw uploaded cells.

B3 can offer this response as downloadable JSON evidence, excluding session/token
information. The download is an audit/validation summary, not a PIT certificate.
Do not put preview ID or CSRF token into a URL; only the header carries CSRF.

## Confirm and immutable replay

Confirm POST has exactly `{ "preview_id": "<handle>", "request_id": "<id>" }`.
Request IDs are 1–128 ASCII letters/digits/dot/underscore/colon/hyphen. No CSV,
definition, hash, clock or changed sample is accepted at commit. The importer
holds an independent serialized private snapshot; edits to returned samples or
request objects cannot alter saved content. It checks store identity, explicit
clock and expiry, then calls one
`FactorStore.commit_dataset(..., definitions=[proposed_definition])` against the
original preview revision. It never registers a definition separately.

Response 200 contains `{ok:true, result:<B1 CommitResult>, manifest:<retained
original B1 DatasetManifest>}`. Concurrent duplicate confirms produce one dataset
and one revision. A reused request with different content fails; a changed store
revision returns 409 `REVISION_CONFLICT` and requires a new preview. Invalid
preview returns 400 `PREVIEW_INVALID`; unavailable preview returns 400
`PREVIEW_NOT_FOUND`; expired preview returns 410 `PREVIEW_EXPIRED`. Semantic
version/request conflicts are 409; body oversize is 413; wrong media type 415;
security failures 403; cache/storage unavailability 503. There is no blind
revision retry, replacement, overwrite or partial success.

Canonical input/content hashes retain original source/provider/effective
availability claims, source adjustment/history/corporate-action basis, normalized
values, definition and provenance. They
exclude raw CSV formatting SHA, receipt/computation/import clocks and operational
dataset identity. Formatting-only repeats return the original immutable
manifest/rows, while their new preview may show a different raw file SHA. A
highest-eligible-publication conflict without independent source-revision proof
still fails `SOURCE_REVISION_AMBIGUOUS` on evidence selection; a later import,
provider delivery or server clock cannot manufacture source revision order.
