# Versioned factor store contract (B1)

This store serves research and evidence, not trading. It does not execute formula
text, certify factor efficacy, alter `known_at`, or change the locked axes/seven
components. Raw market files belong in separately licensed storage, never Git.

## Models and boundary parsing

All models are frozen Pydantic models with `extra='forbid'`, strict types and
instance revalidation. Nested dictionaries/lists retain their specified public
types; storage revalidates and copies them before committing, and reads return
independent objects. Mutating a returned list cannot mutate stored evidence.

- `FactorRef(factor_id, version)`: stable lowercase ID, allowing dot/underscore/hyphen
  segments, and a separate explicit nonblank version
- `FactorDefinition(ref, name, purpose, market, frequency, unit, formula_text,
  calculator_key, input_fields, min_history, missing_policy, window, lag, direction)`
  - `input_fields`: nonempty distinct string list
  - `min_history`, `window`, `lag`: string-to-nonnegative-integer count maps;
    booleans and numeric strings are rejected
  - Count keys can be `bars`, `completed_sessions`, or `regular_sessions`; session
    labels belong in calendar/source provenance rather than a numeric count map
  - Empty maps may describe incomplete candidates. They do not establish data
    readiness; the lifecycle/data-readiness layer must check completeness
  - `calculator_key=None` for external or unimplemented definitions; only the
    immutable `BUILTIN_CALCULATOR_KEYS` set is permitted otherwise
  - `formula_text` is display-only, regardless of whether it resembles code
- `FactorObservation(ref, instrument_id, observed_at, source_published_at,
  provider_available_at, ingested_at, computed_at, effective_available_at, value,
  missing_reason, pit_grade, source_ref, input_hash, quality, availability_basis,
  input_refs, dependency_available_ats=[])`
  - `quality` is the original frozen `market_data.quality.QualityResult` dataclass,
    not a second factor-quality representation; it is strictly revalidated
  - `input_hash` is lowercase SHA-256; `input_refs` is nonempty and distinct
  - `value=None` requires a nonblank missing reason and cannot carry
    `quality.confirmation_ok=True`. Real numeric zero is retained; bool, numeric
    strings, NaN and infinities are rejected. A value cannot also claim missing
  - `pit_grade` defaults to `RECONSTRUCTED`; the only other grade is
    `FORWARD_OBSERVED`. `VINTAGE_VERIFIED` is deliberately unsupported while no
    controlled historical-vintage verifier exists
  - Quality states are `VALID`, `OBSERVATION_ONLY`, `UNAVAILABLE`, with internally
    consistent flags, reason/timestamp types and exclusive expiry. `VALID` is
    availability, never lifecycle `VALIDATED` or PIT verification
- `DatasetManifest(dataset_id, version, source_ref, file_sha256, imported_at,
  universe_version, adjustment, availability_basis, row_count, provider,
  permission_basis, source_timezone, corporate_action_basis, history_version,
  coverage_gaps)`
  - Dataset versions are positive integers; row counts are nonnegative integers
  - Hashes are lowercase SHA-256; textual metadata must be nonblank
  - `source_timezone` is a real IANA timezone; `coverage_gaps` is a string list
  - A license/permission description is provenance, not an access grant
- `CommitResult(dataset_id, version, inserted_rows, replayed, store_revision)`

Strict Python constructors require actual timezone-aware UTC `datetime` objects,
`FactorRef` objects, and the original `QualityResult` object. They do not coerce
CSV strings. `model_validate_json(...)` is the JSON boundary and parses ISO UTC
datetimes, nested references, and the same quality dataclass. Offset timestamps
must be normalized explicitly to UTC before construction; naive timestamps are
rejected. A Python `model_dump()` turns dataclasses into dictionaries, so use the
JSON roundtrip for exported JSON or retain actual dataclass attributes in Python.

All observation times obey observed <= published <= provider <= ingested <=
computed. Live effective availability is at least every source/provider,
ingestion, computation and explicitly listed dependency availability time.
Reconstructed history can retain a historical effective availability at least
its historical source/provider/dependency times while recording present-day
receipt/calculation separately. This never changes `known_at`.

B2 must supply ingestion, computation and provenance grade server-side. It must
not accept CSV grade/quality claims as trust. The store is a controlled application
boundary, not an independent verifier of an arbitrary caller's asserted clock.

## Store API

Use `with FactorStore(Path(...)) as store:` or call `close()` explicitly. Closed
stores reject operations with `STORE_CLOSED`; closing twice is harmless. This
synchronous SQLite interface does not share a connection across threads.

- `revision() -> int`: monotonically increases once for each new definition
  registration or atomic dataset commit; content/request replays do not increase it
- `definitions() -> list[FactorDefinition]`: independent snapshots, ordered by
  factor ID then version
- `dataset_manifest(dataset_id, version) -> DatasetManifest | None`: read the
  original retained manifest using the returned commit/replay identity, including
  the first original raw file SHA and permission/universe/adjustment provenance.
  Returns an independent snapshot or None for an unknown dataset/version. Dataset
  ID must be a nonblank string; version a positive integer, never bool or string
- `register_definition(definition, *, expected_revision, request_id) -> int`:
  append-only catalog registration. Same ref/version with different content fails.
  Same request replays its original integer result. A new request for an identical
  existing definition returns the current unchanged revision
- `commit_dataset(manifest, observations, *, expected_revision, request_id,
  definitions=()) -> CommitResult`: original signature remains usable; the optional
  controlled definitions iterable atomically introduces catalog definitions with
  the manifest, all rows, revision and request result
  - B2 preview reads `revision()` without writes, then passes that original revision
    and the proposed definitions in this one commit. Do not call
    `register_definition()` between preview and commit
  - All refs must resolve to existing or proposed definitions. Source references
    must match the manifest; row count must match; import time cannot precede
    first ingestion. Two rows for the same ref/instrument/observed time in one
    dataset are rejected, even if their values happen to match
  - SQLite `BEGIN IMMEDIATE` and optimistic revision checking serialize writers;
    any validation/storage failure rolls back definitions, manifest, rows,
    revision and request state together
  - Request replay and exact source-content replay are checked before revision
    conflicts, so retries may safely use the original stale preview revision
  - A repeated source-content import may have a different operational dataset ID,
    dataset version, request ID, receipt/compute clock and import clock. It returns
    the original dataset/version/revision with `replayed=True`, preserves the first
    rows and availability, and never creates duplicate observations. Parsed
    content-equivalent files with different whitespace/line endings replay too;
    the first original manifest/file SHA is retained. B2 preview may still report
    the newly submitted file fingerprint without adding a duplicate dataset
  - `inserted_rows` is the original successful result's count even on replay
  - Reusing a request ID for different content or a different operation fails
  - Reusing an existing dataset/ref version for changed content fails; use an
    explicit new version. There is no overwrite, update or delete product API;
    database triggers also reject updates/deletes of evidence and request records
- `observations(ref, *, as_of, mode) -> list[FactorObservation]`:
  - UTC `as_of`; mode is `live`, `strict_replay`, or `exploratory`
  - Only effective availability <= `as_of` is eligible, including all actual
    forward dependencies; future revisions never change past results
  - Latest source/provider revision wins per factor ref/instrument/observed event;
    delayed computation of older source evidence cannot override newer source
    evidence. Ties use effective availability then atomic store revision, with
    deterministic dataset ordering
  - `live` excludes reconstructed rows
  - `strict_replay` rejects any selected reconstructed row with
    `ValueError('PIT_EVIDENCE_REQUIRED')`. A later actual forward row may supersede
    a historical claim only at its actual availability, never retroactively
  - `exploratory` permits explicitly labeled reconstructed availability claims
  - Output is ordered by observed time then instrument. An unknown ref returns []
  - This is historical evidence selection, not a current freshness evaluation;
    B3/C1 must evaluate quality expiry and lifecycle eligibility separately

## Fingerprints

Three identities have deliberately different purposes:

1. `definition_fingerprint(definition)` binds the entire versioned definition,
   including formula text, calculator identity, metadata, window and inputs
2. `mathematical_fingerprint(definition, observation)` binds the complete
   definition, ref, instrument/event identity, canonical numeric value or missing
   reason, input hash and stable input references. It excludes operational
   UUIDs, receipt/calculation/effective time and quality evaluation clocks;
   definition/input changes still change it. A ref mismatch is rejected
3. Dataset source-content fingerprint binds source/provider claims, availability
   basis, grade, original input identity, canonical row contents, definitions,
   and manifest provenance. Row ordering and object-key ordering are normalized.
   Raw file SHA is retained only in the original immutable audit payload; it is
   excluded from canonical duplicates so CSV formatting cannot create duplicates.
   Input hashes must likewise describe canonical dependencies rather than raw
   CSV formatting. Dataset ID/version, import/receipt/computation clocks, quality
   evaluation/expiry clocks, and a forward effective time equal to the maximum
   actual dependency/receipt/computation time are operational and excluded.
   Independent delayed or reconstructed effective-availability claims remain
   content. Changed source/provider availability claims are conflicting content

Stored payload audit hashes retain every timestamp and all immutable row/manifest
content. Duplicate imports retain the original audit hashes. None of these hashes
is a PIT certificate, quality upgrade, or proof of investment effectiveness.

## Stable error codes

Store semantic errors are exact `ValueError(code)` strings:

- `INVALID_EXPECTED_REVISION`, `INVALID_REQUEST_ID`, `REVISION_CONFLICT`
- `REQUEST_ID_CONFLICT`, `DEFINITION_VERSION_CONFLICT`, `DATASET_VERSION_CONFLICT`
- `INVALID_DEFINITION`, `INVALID_MANIFEST`, `INVALID_OBSERVATION`, `INVALID_FACTOR_REF`
- `INVALID_DATASET_ID`, `INVALID_DATASET_VERSION`
- `ROW_COUNT_MISMATCH`, `DUPLICATE_OBSERVATION`, `UNKNOWN_DEFINITION`
- `SOURCE_MISMATCH`, `IMPORT_BEFORE_INGESTION`
- `INVALID_QUERY_MODE`, `UTC_REQUIRED`, `PIT_EVIDENCE_REQUIRED`, `STORE_CLOSED`

Strict model failures are Pydantic `ValidationError` with field locations/types;
explicit validator codes include `UNKNOWN_CALCULATOR`, `INVALID_INPUT_FIELDS`,
`INVALID_INPUT_REFS`, `INVALID_FACTOR_VALUE`, `INVALID_QUALITY_RESULT`,
`MISSING_REASON_REQUIRED`, `MISSING_VALUE_CONFIRMATION`, `VALUE_WITH_MISSING_REASON`,
`IMPOSSIBLE_TIME_ORDER`, `AVAILABILITY_BEFORE_DEPENDENCY`, `UTC_REQUIRED`, and
`INVALID_SOURCE_TIMEZONE`. Extra fields and unsupported grades are Pydantic
schema errors. The mathematical fingerprint helper raises
`DEFINITION_REF_MISMATCH`. Unexpected SQLite failures remain SQLite exceptions,
with their transaction fully rolled back.
