"""Non-promoting local audit ledger; no external identity or production authority.

Only trusted local application code may create an operation context. JSON fields,
actor labels and report declarations are not authority. Decision time is supplied
by that controlled boundary, never taken from a hidden library wall clock.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import field_validator

from .models import Count, FactorRef, Nonempty, StrictModel, require_utc
from .registry import definition_fingerprint, definitions, fingerprint
from .store import FactorStore, _snapshot

LifecycleState = Literal['candidate', 'data_ready', 'research_validated', 'forward_shadow',
                         'approved_active', 'watch', 'quarantine', 'retired', 'archive',
                         'LEGACY_UNVALIDATED']
PROMOTIONS = frozenset({'research_validated', 'forward_shadow', 'approved_active', 'watch'})
ALLOWED = frozenset({('candidate', 'data_ready'), ('candidate', 'quarantine'),
                     ('candidate', 'retired'), ('data_ready', 'quarantine'),
                     ('data_ready', 'retired'), ('quarantine', 'candidate'),
                     ('quarantine', 'data_ready'), ('retired', 'archive')})


class LifecycleEvent(StrictModel):
    event_id: Nonempty
    factor_ref: FactorRef
    from_state: LifecycleState
    to_state: LifecycleState
    at: datetime
    actor: Nonempty
    reason: Nonempty
    evidence_ids: list[Nonempty]
    expected_revision: Count

    @field_validator('at')
    @classmethod
    def utc_at(cls, value):
        return require_utc(value)

    @field_validator('evidence_ids')
    @classmethod
    def distinct_evidence(cls, values):
        if len(values) != len(set(values)):
            raise ValueError('INVALID_EVIDENCE_IDS')
        return values


class TransitionResult(StrictModel):
    accepted: bool
    state: LifecycleState
    event_id: Nonempty | None
    error_code: Nonempty | None
    revision: Count


@dataclass(frozen=True)
class _Operation:
    store: FactorStore
    event_hash: str
    now: datetime
    actor: str
    approval_id: str
    approval_reason: str


# Opaque identity tokens are valid only within this process and this live context.
# This prevents serialized context/actor/approval shortcuts; it is intentionally
# not a security sandbox for arbitrary Python code in the trusted process.
_OPERATIONS: dict[object, _Operation] = {}


@contextmanager
def local_operation(store: FactorStore, *, actor_label: str, event: LifecycleEvent,
                    now: datetime, approval_id: str, approval_reason: str):
    """Stage explicit local approval bound to one exact event and store instance.

    The controlled caller supplies the actual current UTC decision clock. No
    write occurs on entry; accepted checks/approval/event commit atomically.
    This audit label does not authenticate a person or permit legacy migration.
    """
    store._ensure_open()
    now = require_utc(now)
    event = _snapshot(event, LifecycleEvent, 'INVALID_LIFECYCLE_EVENT')
    if event.at != now:
        raise ValueError('OPERATION_TIME_MISMATCH')
    if any(not isinstance(value, str) or not value.strip()
           for value in (actor_label, approval_id, approval_reason)):
        raise ValueError('INVALID_LOCAL_APPROVAL')
    if actor_label != event.actor:
        raise ValueError('LOCAL_ACTOR_MISMATCH')
    token = object()
    _OPERATIONS[token] = _Operation(store, fingerprint(event.model_dump(mode='json')),
                                   now, actor_label, approval_id, approval_reason)
    try:
        yield token
    finally:
        _OPERATIONS.pop(token, None)


def lifecycle_state(ref: FactorRef, *, store: FactorStore, as_of: datetime | None = None) -> str:
    definition = store.resolve_definition(ref)
    history = store.lifecycle_history(ref, as_of=as_of)
    if history:
        return history[-1].to_state
    return 'LEGACY_UNVALIDATED' if definition.calculator_key is not None else 'candidate'


def _checks(event, store, now):
    definition = store.resolve_definition(event.factor_ref)
    ready = event.to_state == 'data_ready'
    check = {'check_id': 'local-check:'+event.event_id, 'event_id': event.event_id,
             'at': now.isoformat(), 'store_revision_checked': store.revision(),
             'scope': 'DATA_READY' if ready else 'CANDIDATE_INTEGRITY' if event.to_state == 'candidate' else 'TRANSITION_INTEGRITY',
             'definition_fingerprint': definition_fingerprint(definition),
             'input_evidence': [], 'limitations': ['LOCAL_CHECKS_ONLY', 'NO_PIT_VERIFICATION',
                                                  'NO_EFFECTIVENESS_VALIDATION', 'NO_LICENSE_VERIFICATION'],
             'passed': True}
    if not ready:
        # Strict versioned model and reserved catalog resolution establish only
        # candidate-level definition/source-hypothesis integrity. Incomplete
        # candidate count maps remain explicitly incomplete and unvalidated.
        if not all((definition.min_history, definition.window, definition.lag)):
            check['limitations'].append('INCOMPLETE_CANDIDATE_DEFINITION')
        return check, None
    if (not definition.min_history or not definition.window or not definition.lag
            or not any(definition.min_history.values()) or not any(definition.window.values())):
        return check, 'DEFINITION_CHECK_FAILED'
    if any(set(counts) - {'bars'} for counts in (definition.min_history, definition.window, definition.lag)):
        return check, 'UNSUPPORTED_READINESS_COUNT'
    latest = {}
    for row in store.observations(event.factor_ref, as_of=now, mode='exploratory'):
        latest[row.instrument_id] = row
    if not latest:
        return check, 'CURRENT_DATA_CHECK_FAILED'
    now_ms = int(now.timestamp()*1000)
    required = max(definition.min_history['bars'], definition.window['bars'])
    for row in latest.values():
        quality = row.quality
        if (row.value is None or not quality.observation_ok or quality.evaluated_at_ms > now_ms
                or quality.valid_until_ms is None or now_ms >= quality.valid_until_ms
                or quality.sample_count < required + definition.lag['bars']):
            return check, 'CURRENT_DATA_CHECK_FAILED'
        manifests = store.observation_manifests(row)
        if not manifests or any(manifest.permission_basis.strip().lower() in {
                'unknown', 'none', 'unspecified', 'not provided'} for manifest in manifests):
            return check, 'PERMISSION_BASIS_REQUIRED'
        check['input_evidence'].append({'instrument_id': row.instrument_id,
            'observation_audit_hash': fingerprint(row.model_dump(mode='json')),
            'input_hash': row.input_hash, 'input_refs': row.input_refs, 'pit_grade': row.pit_grade,
            'availability_basis': row.availability_basis, 'quality_state': quality.state,
            'quality_reason_codes': list(quality.reason_codes), 'confirmation_ok': quality.confirmation_ok,
            'quality_evaluated_at_ms': quality.evaluated_at_ms,
            'quality_valid_until_ms': quality.valid_until_ms, 'sample_count_bars': quality.sample_count,
            'permission_bases': [manifest.permission_basis for manifest in manifests],
            'datasets': [{'dataset_id': manifest.dataset_id, 'version': manifest.version} for manifest in manifests]})
    return check, None


def record_transition(event: LifecycleEvent, *, store: FactorStore, approvals: dict) -> TransitionResult:
    """Fail closed; allowed changes are append-only local records, not promotion."""
    event = _snapshot(event, LifecycleEvent, 'INVALID_LIFECYCLE_EVENT')
    state = event.from_state

    def rejected(code):
        return TransitionResult(accepted=False, state=state, event_id=None,
                                error_code=code, revision=store.revision())

    try:
        with store._transaction():
            state = lifecycle_state(event.factor_ref, store=store)
            replay = store._lifecycle_replay(event)
            if replay is not None:
                return replay
            if event.to_state in PROMOTIONS:
                return rejected('EVIDENCE_VALIDATOR_NOT_CONFIGURED')
            if state == 'LEGACY_UNVALIDATED':
                return rejected('LEGACY_TRANSITION_NOT_CONFIGURED')
            if event.from_state != state:
                return rejected('LIFECYCLE_STATE_CONFLICT')
            if (state, event.to_state) not in ALLOWED:
                return rejected('INVALID_LIFECYCLE_TRANSITION')
            operation = None
            if isinstance(approvals, dict) and set(approvals) == {'context'}:
                # Unhashable JSON context values are invalid, never exception
                # shortcuts out of a failed-closed authority decision.
                try:
                    operation = _OPERATIONS.get(approvals['context'])
                except TypeError:
                    pass
            if (operation is None or operation.store is not store or operation.actor != event.actor
                    or operation.now != event.at
                    or operation.event_hash != fingerprint(event.model_dump(mode='json'))):
                return rejected('LOCAL_APPROVAL_REQUIRED')
            store._check_revision(event.expected_revision)
            history = store.lifecycle_history(event.factor_ref)
            if history and event.at < history[-1].at:
                return rejected('EVENT_TIME_ORDER')
            if store._db.execute('SELECT 1 FROM lifecycle_approvals WHERE approval_id=?',
                                 (operation.approval_id,)).fetchone():
                return rejected('APPROVAL_ID_CONFLICT')
            check, error = _checks(event, store, operation.now)
            if error:
                return rejected(error)
            approval = {'approval_id': operation.approval_id, 'event_id': event.event_id,
                        'event_fingerprint': operation.event_hash, 'actor': operation.actor,
                        'at': operation.now.isoformat(), 'reason': operation.approval_reason,
                        'authority_scope': 'EXPLICIT_LOCAL_AUDIT_NOT_EXTERNAL_AUTHENTICATION'}
            result = TransitionResult(accepted=True, state=event.to_state, event_id=event.event_id,
                                      error_code=None, revision=store._next_revision())
            store._append_lifecycle(event, approval, check, result)
            return result
    except ValueError as exc:
        return rejected(str(exc))


def production_manifest(store: FactorStore) -> dict:
    """Truthful governed scope; never imply that legacy production was migrated."""
    store._ensure_open()
    # Validate existing reserved content without creating/registering anything.
    for definition in store.definitions():
        from .registry import check_reserved_definition
        check_reserved_definition(definition)
    legacy = [{'factor_ref': definition.ref.model_dump(mode='json'),
               'definition_fingerprint': definition_fingerprint(definition),
               'lifecycle_label': 'LEGACY_UNVALIDATED', 'governed_production_eligible': False}
              for definition in definitions() if definition.calculator_key is not None]
    manifest = {'scope': 'NON_PROMOTING_FACTOR_FOUNDATION', 'governed_active': [],
                'legacy_baselines': legacy, 'legacy_behavior': 'UNCHANGED_A_QUALITY_GATED_PATH',
                'legacy_migration_completed': False, 'evidence_validator': 'NOT_CONFIGURED'}
    return {**manifest, 'fingerprint': fingerprint(manifest)}
