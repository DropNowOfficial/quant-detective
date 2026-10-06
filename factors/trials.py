"""Immutable research declarations, with no validation engine, scheduler or spend."""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import field_validator, model_validator

from .models import Count, FactorRef, Nonempty, Sha256, StrictModel
from .store import FactorStore, _snapshot

ReportKind = Literal['factor', 'strategy', 'forward']
TrialStatus = Literal['PLANNED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED']


class TrialRecord(StrictModel):
    run_id: Nonempty
    factor_refs: list[FactorRef]
    report_kind: ReportKind
    status: TrialStatus
    code_commit: Nonempty | None
    input_hashes: dict[Nonempty, Sha256]
    universe_version: Nonempty | None
    calendar_version: Nonempty | None
    split: dict[str, Any]
    preprocessing_artifact_id: Nonempty | None
    parameters: dict[str, Any]
    seed: int | None
    dependency_lock_hash: Sha256 | None
    artifact_refs: list[Nonempty]
    error_reason: Nonempty | None

    @field_validator('factor_refs')
    @classmethod
    def distinct_refs(cls, refs):
        if not refs or len({(ref.factor_id, ref.version) for ref in refs}) != len(refs):
            raise ValueError('INVALID_FACTOR_REFS')
        return refs

    @field_validator('artifact_refs')
    @classmethod
    def distinct_artifacts(cls, refs):
        if len(set(refs)) != len(refs):
            raise ValueError('INVALID_ARTIFACT_REFS')
        return refs

    @field_validator('split', 'parameters')
    @classmethod
    def inert_json(cls, value):
        # Reject non-JSON values/nonfinite nested numbers rather than executing or
        # retaining arbitrary Python objects in a declared artifact/parameter.
        def valid(item):
            if item is None or type(item) in {str, bool, int}:
                return True
            if type(item) is float:
                return math.isfinite(item)
            if type(item) is list:
                return all(valid(child) for child in item)
            if type(item) is dict:
                return all(type(key) is str and valid(child) for key, child in item.items())
            return False
        try:
            acceptable = valid(value)
        except RecursionError:
            acceptable = False
        if not acceptable:
            raise ValueError('INVALID_TRIAL_JSON')
        return value

    @model_validator(mode='after')
    def failure_reason(self):
        if self.status in {'FAILED', 'CANCELLED'} and self.error_reason is None:
            raise ValueError('TRIAL_ERROR_REASON_REQUIRED')
        return self


class ResearchBudget(StrictModel):
    batch_id: Nonempty
    candidate_limit: Count
    trial_limit: Count
    compute_seconds_limit: Count
    paid_spend_limit: Literal[0]
    enabled: Literal[False] = False

    @field_validator('enabled', mode='before')
    @classmethod
    def disabled_runner(cls, value):
        if value is not False:
            raise ValueError('RESEARCH_RUNNER_DISABLED')
        return value

    @field_validator('paid_spend_limit', mode='before')
    @classmethod
    def disabled_paid_calls(cls, value):
        if type(value) not in {int, float} or value != 0:
            raise ValueError('PAID_CALLS_DISABLED')
        return 0


def record_trial(record: TrialRecord, *, store: FactorStore) -> str:
    record = _snapshot(record, TrialRecord, 'INVALID_TRIAL_RECORD')
    return store._record_trial(record)


def record_budget(budget: ResearchBudget, *, store: FactorStore) -> str:
    budget = _snapshot(budget, ResearchBudget, 'INVALID_RESEARCH_BUDGET')
    return store._record_budget(budget)


def trial_evidence(run_id: str, *, store: FactorStore, required_kind: ReportKind) -> dict:
    """Display declared missing evidence, never certify the report or its claims."""
    if required_kind not in {'factor', 'strategy', 'forward'}:
        raise ValueError('INVALID_REPORT_KIND')
    record = store.trial(run_id)
    if record is None:
        raise ValueError('UNKNOWN_TRIAL')
    limitations = ['EVIDENCE_VALIDATOR_NOT_CONFIGURED']
    if record.report_kind != required_kind:
        limitations.append('REPORT_KIND_MISMATCH')
    if record.status != 'SUCCEEDED':
        limitations.append('TRIAL_NOT_SUCCEEDED')
    if not all(isinstance(record.split.get(key), str) and record.split[key].strip()
               for key in ('train_start', 'train_end')):
        limitations.append('TRAINING_WINDOW_MISSING')
    if not all(isinstance(record.split.get(key), str) and record.split[key].strip()
               for key in ('test_start', 'test_end')):
        limitations.append('TEST_WINDOW_MISSING')
    if record.preprocessing_artifact_id is None:
        limitations.append('PREPROCESSING_ARTIFACT_MISSING')
    if not record.artifact_refs:
        limitations.append('REPORT_ARTIFACT_MISSING')
    if record.code_commit is None:
        limitations.append('CODE_COMMIT_MISSING')
    if not record.input_hashes:
        limitations.append('INPUT_HASHES_MISSING')
    if record.universe_version is None:
        limitations.append('UNIVERSE_VERSION_MISSING')
    if record.calendar_version is None:
        limitations.append('CALENDAR_VERSION_MISSING')
    if record.dependency_lock_hash is None:
        limitations.append('DEPENDENCY_LOCK_MISSING')
    return {'run_id': record.run_id, 'report_kind': record.report_kind,
            'required_kind': required_kind, 'promotable': False,
            'limitations': limitations, 'verification': 'DECLARATIONS_ONLY_UNVERIFIED'}
