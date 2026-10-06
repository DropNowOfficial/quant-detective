# Non-promoting local governance ledger v1 (C1)

Research and opportunity evidence only. No trading, provider calls, paid calls,
scheduler, automatic quarantine, recovery, retirement, production cutover or
web governance writes are supplied. Original legacy alert behavior stays on its
existing A-quality-gated path. Source quality, PIT grade, the three locked axes,
and lifecycle are separate. No efficacy or investment probability is certified.

## Lifecycle and controlled local approval

`factors.lifecycle.LifecycleEvent(event_id, factor_ref, from_state, to_state, at,
actor, reason, evidence_ids, expected_revision)` and `TransitionResult(accepted,
state, event_id, error_code, revision)` are strict frozen models; nested evidence
lists are defensively revalidated/copied at the store boundary. `at` is UTC.
States are the nine lowercase names `candidate`, `data_ready`,
`research_validated`, `forward_shadow`, `approved_active`, `watch`, `quarantine`,
`retired`, `archive`. `LEGACY_UNVALIDATED` is an additional static legacy label,
not a tenth supported transition state, validation approval or active permit.

The only supported transitions are:

- candidate → data_ready, quarantine, retired
- data_ready → quarantine, retired
- quarantine → candidate, data_ready
- retired → archive

Every accepted transition requires an explicit local approval and nonblank
reason. No operation converts the nine reserved legacy calculator identities
into governed production. The seven reserved components retain their definitions
and are unimplemented. Definition registration is not lifecycle validation.
Requests targeting research_validated, forward_shadow, approved_active or watch
always return `EVIDENCE_VALIDATOR_NOT_CONFIGURED`, including high-score,
self-reported report/approval declarations and otherwise properly approved local
operations. A future independent validator requires a separately reviewed change.

The local-only approval contract is:

```python
with local_operation(store, actor_label="local-reviewer", event=event,
                     now=current_process_utc_time,
                     approval_id="manual-approval-id",
                     approval_reason="Explicitly approved retirement") as context:
    result = record_transition(event, store=store, approvals={"context": context})
```

`current_process_utc_time` must come from the trusted operation boundary's actual
current UTC decision clock. `event.at` must equal that clock. Checks rerun inside
the transaction at exactly this bound clock; no hidden library wall clock is
used, and a backdated event cannot be paired with a current operation to revive
expired evidence. This API is for controlled Python/local administration code,
not JSON, a browser request, or untrusted code in the same Python process.

`local_operation` stages approval without writing or increasing a revision. It
yields a non-serializable opaque object valid only during that context, bound to
the exact event fingerprint and exact store instance. An actor string, boolean,
self-reported check result, serialized object, expired context, context for
another event/store, or extra approval keys cannot create authority. The event's
actor must match the context's label; the stored actor comes from the context.
This is explicitly a local audit label, not external identity authentication.
Approval IDs cannot authorize a second event.

`record_transition(event, *, store, approvals)` serializes writers with SQLite
BEGIN IMMEDIATE, checks the original global expected_revision, replays state,
and atomically appends the accepted approval, local check artifact and event
while advancing the revision once. Failure rolls everything back. Rejections
return accepted=False, event_id=None and the unchanged current state/revision;
this round does not persist rejected attempts. An exact accepted event_id replay
returns the original result without new writes or requiring new authority;
changed content under that event_id fails. Event decision times cannot precede
an already accepted event for the same factor/version.

## Honest first-round checks

Candidate restoration reruns strict versioned definition/reserved-source
hypothesis integrity, allowing an incomplete but explicitly labeled candidate.
Quarantine/retirement/archive approvals do not require current market inputs.
Data-ready transitions/restoration additionally require:

- nonempty min_history, window and lag maps; positive min_history/window
- only supported bars-count requirements; session counts are rejected rather
  than fabricated from bars or inferred from formula prose
- current latest source-publication-selected observations per retained instrument
- a nonmissing value, actual retained QualityResult with observation_ok, quality
  evaluated no later than the decision time, and unexpired exclusive quality TTL
- retained sample_count_bars covering max(min_history, window) + lag
- original dataset manifests with an explicitly recorded permission_basis;
  missing/unknown/none/unspecified/not-provided bases cannot establish readiness

Only already stored evidence is checked. This does not fetch a dataset, rerun a
calculator or establish full universe coverage. Reconstructed exploratory inputs
may be data-ready, but remain ineligible for live/strict PIT and for production.
Imported unavailable quality cannot pass merely because an approval says so.
Source-revision ambiguity fails closed. Ordinary source expiry rejects the
specific input/use or restoration; it does not automatically quarantine lifecycle.

Accepted check artifacts retain check_id, event_id, exact decision clock, checked
store revision, definition fingerprint, scope, original observation audit/input
hashes, PIT grade, original quality evaluation/expiry, sample_count_bars, original
dataset identities and permission bases. Limitations explicitly include
LOCAL_CHECKS_ONLY, NO_PIT_VERIFICATION, NO_EFFECTIVENESS_VALIDATION and
NO_LICENSE_VERIFICATION; candidate incompleteness is retained as a limitation.
Checks do not certify permission legality, raw source quality or effectiveness.
The approval record retains approval ID/reason, event fingerprint, bound actor
and decision time, with EXPLICIT_LOCAL_AUDIT_NOT_EXTERNAL_AUTHENTICATION scope.

## Trials and budgets

`factors.trials.TrialRecord(run_id, factor_refs, report_kind, status, code_commit,
input_hashes, universe_version, calendar_version, split,
preprocessing_artifact_id, parameters, seed, dependency_lock_hash, artifact_refs,
error_reason)` records declarations only. `report_kind` is factor/strategy/forward;
status is PLANNED/RUNNING/SUCCEEDED/FAILED/CANCELLED. FAILED/CANCELLED require a
reason. A nonempty distinct factor_refs list must resolve to exact retained or
reserved definitions. Input/lock hashes are lowercase SHA-256. Missing
code/universe/calendar/preprocessing/lock/seed declarations may remain None.
Split and parameters are inert, finite strict JSON containers, never executed.
Artifact strings are retained as labels, never opened as paths or fetched URLs.

`record_trial(record, *, store) -> run_id` appends once and advances the store
revision; an exact repeat replays without advancing it. Changing an existing
run_id fails, including rewriting PLANNED/RUNNING or erasing FAILED/CANCELLED.
Use a new run_id for a new immutable declared record. There is no run-state
machine or executor. `store.trial(run_id)` and `store.trials(ref)` return fresh
snapshots; ordering is recorded store revision, not invented execution times.

`trial_evidence(run_id, *, store, required_kind)` is an honest read-only summary.
It always reports promotable=False and EVIDENCE_VALIDATOR_NOT_CONFIGURED. It adds
REPORT_KIND_MISMATCH, TRIAL_NOT_SUCCEEDED and missing declarations for train/test
windows (train_start/train_end/test_start/test_end), preprocessing/report
artifacts, code commit, input hashes, universe/calendar/lock as appropriate.
Split strings and artifact IDs are declarations, not verified training/PIT,
preprocessor-fitting or strategy/forward-validation proof. A factor report
cannot satisfy strategy evidence. No report kind can unlock a promotion.

`ResearchBudget(batch_id, candidate_limit, trial_limit, compute_seconds_limit,
paid_spend_limit, enabled=False)` is a strict inert proposed constraint record.
Count limits are nonnegative integers; paid_spend_limit must be numeric zero and
enabled must be actual boolean False. Positive proposed candidate/trial/compute
limits are allowed. Enabled/paid configurations are rejected this round, so a
future runner cannot inherit an apparently active/paid permission. Activation
requires a separate reviewed API. `record_budget(budget, *, store) -> batch_id`
appends/replays like trials; `store.budget(batch_id)` reads a fresh snapshot.
Neither this API nor recording trials starts jobs or buys/calls datasets.

## Production manifest and read history

`production_manifest(store) -> dict` reads a stable governed scope: governed_active
is empty, nine immutable legacy baselines have LEGACY_UNVALIDATED labels and no
governed production eligibility, legacy_behavior is
UNCHANGED_A_QUALITY_GATED_PATH, legacy_migration_completed is False, and the
independent evidence validator is NOT_CONFIGURED. Its deterministic fingerprint
is independent of new candidates/trials/budgets/transitions. This is not an
inventory or validation of every existing production alert and does not claim a
completed migration. It does not register the virtual catalog or write a permit.

`store.lifecycle_history(ref, as_of=None)` returns independent accepted event
models in decision-time/store-revision order. `lifecycle_state(ref, store=...,
as_of=None)` replays that history. `store.lifecycle_check_artifacts(ref,
as_of=None)` reads accepted local checks; `store.lifecycle_approval(event_id)`
reads the associated approval. The original expected_revision remains in the
event; check artifacts retain the revision checked. Global revision now also
advances for new accepted lifecycle events/trials/budgets, so B2 previews become
stale if these writes occur. Approval staging does not advance it.

- GET /api/factors/lifecycle?factor_id=...&version=... [&as_of=timezone-aware ISO]
  returns state, accepted events, local check artifacts, initialized flag, and
  eligible_for_production=False. as_of is normalized to UTC
- GET /api/factors/trials?factor_id=...&version=... returns immutable trial records
  and their unverified evidence limitations; no records means 未运行 / No recorded
  trials. It has no time filter because TrialRecord has no execution timestamp
- Duplicate/unknown/blank query keys and fragments fail; ref is always required.
  No path, SQL, source file, mode, universe expansion or job can be selected
- GET uses only an explicitly configured, already existing factor_store_path via
  FactorStore(path, read_only=True); it never creates an absent DB or initializes
  governance tables in an older schema. history_initialized=False distinguishes
  absent/uninitialized history. Writable local store opening initializes schema
- Existing session/preview/commit are still the only write routes. Governance
  POST/PUT/PATCH/DELETE remain unsupported. History UI uses textContent/DOM nodes,
  including arbitrary reasons, report labels, artifacts and errors

## Stable errors for C2

Semantic transition refusals are TransitionResult.error_code strings:
EVIDENCE_VALIDATOR_NOT_CONFIGURED, LEGACY_TRANSITION_NOT_CONFIGURED,
LIFECYCLE_STATE_CONFLICT, INVALID_LIFECYCLE_TRANSITION, LOCAL_APPROVAL_REQUIRED,
REVISION_CONFLICT, EVENT_ID_CONFLICT, EVENT_TIME_ORDER, APPROVAL_ID_CONFLICT,
DEFINITION_CHECK_FAILED, UNSUPPORTED_READINESS_COUNT, CURRENT_DATA_CHECK_FAILED,
PERMISSION_BASIS_REQUIRED, UNKNOWN_DEFINITION, SOURCE_REVISION_AMBIGUOUS.

Context creation uses ValueError codes OPERATION_TIME_MISMATCH,
INVALID_LOCAL_APPROVAL, LOCAL_ACTOR_MISMATCH and UTC_REQUIRED. Existing model
validation errors remain Pydantic ValidationError with field locations, including
INVALID_EVIDENCE_IDS. Store reads/writes retain STORE_CLOSED and add
STORE_READ_ONLY / INVALID_READ_ONLY_MODE / INVALID_RUN_ID / INVALID_BATCH_ID.
Trial/budget boundary failures include INVALID_TRIAL_RECORD,
INVALID_RESEARCH_BUDGET, TRIAL_ID_CONFLICT, BUDGET_ID_CONFLICT and UNKNOWN_DEFINITION.
Models report INVALID_FACTOR_REFS, INVALID_ARTIFACT_REFS, INVALID_TRIAL_JSON,
TRIAL_ERROR_REASON_REQUIRED, PAID_CALLS_DISABLED and RESEARCH_RUNNER_DISABLED.
Read evidence summaries use INVALID_REPORT_KIND and UNKNOWN_TRIAL.
HTTP query errors remain safe fixed B3 codes; no malformed text or secrets are
reflected. Unexpected SQLite failures roll back and remain SQLite exceptions in
library writes; HTTP reports FACTOR_STORE_UNAVAILABLE with status 503.
