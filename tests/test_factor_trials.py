"""Trial and budget recording retains limitations and never launches work."""
from contextlib import closing
import sqlite3

import pytest
from pydantic import ValidationError

from factors.store import FactorStore
from test_factor_models import definition


@pytest.fixture
def store(tmp_path):
    with FactorStore(tmp_path / 'trials.sqlite') as result:
        result.register_definition(definition(), expected_revision=0, request_id='definition')
        yield result


def trial(**changes):
    from factors.trials import TrialRecord
    return TrialRecord(**dict(run_id='run-1', factor_refs=[definition().ref], report_kind='factor',
        status='SUCCEEDED', code_commit='a'*40, input_hashes={'input':'b'*64},
        universe_version='synthetic-v1', calendar_version='XNYS-test',
        split={'train_start':'2025-01-01', 'train_end':'2025-12-31', 'test_start':'2026-01-01', 'test_end':'2026-06-30'},
        preprocessing_artifact_id='fitted-on-training-only', parameters={'window':20}, seed=7,
        dependency_lock_hash='c'*64, artifact_refs=['report:factor-1'], error_reason=None) | changes)


def test_factor_report_cannot_satisfy_strategy_evidence(store):
    from factors.trials import record_trial, trial_evidence
    assert record_trial(trial(), store=store) == 'run-1'
    evidence = trial_evidence('run-1', store=store, required_kind='strategy')
    assert evidence['promotable'] is False
    assert 'REPORT_KIND_MISMATCH' in evidence['limitations']
    assert store.trial('run-1').report_kind == 'factor'


@pytest.mark.parametrize('changes, limitation', [
    ({'split':{}}, 'TRAINING_WINDOW_MISSING'),
    ({'preprocessing_artifact_id':None}, 'PREPROCESSING_ARTIFACT_MISSING'),
    ({'artifact_refs':[]}, 'REPORT_ARTIFACT_MISSING'),
])
def test_trial_missing_training_window_or_artifact_is_not_promotable(store, changes, limitation):
    from factors.trials import record_trial, trial_evidence
    record_trial(trial(**changes), store=store)
    evidence = trial_evidence('run-1', store=store, required_kind='factor')
    assert evidence['promotable'] is False
    assert limitation in evidence['limitations']
    assert 'EVIDENCE_VALIDATOR_NOT_CONFIGURED' in evidence['limitations']


@pytest.mark.parametrize('status', ['FAILED', 'CANCELLED', 'PLANNED', 'RUNNING'])
def test_failed_trials_are_retained(store, status):
    from factors.trials import record_trial, trial_evidence
    item = trial(status=status, error_reason='Original failure or cancellation' if status in {'FAILED','CANCELLED'} else None)
    record_trial(item, store=store)
    assert store.trial(item.run_id).status == status
    assert store.trials(definition().ref)[0] == item
    assert trial_evidence(item.run_id, store=store, required_kind='factor')['promotable'] is False


def test_trial_records_are_immutable_replayed_and_independent(store):
    from factors.trials import record_trial
    item = trial()
    assert record_trial(item, store=store) == 'run-1'
    revision = store.revision()
    assert record_trial(item, store=store) == 'run-1' and store.revision() == revision
    with pytest.raises(ValueError, match='TRIAL_ID_CONFLICT'):
        record_trial(item.model_copy(update={'status':'FAILED', 'error_reason':'cannot rewrite'}), store=store)
    read = store.trial('run-1'); read.parameters['window'] = 999; read.factor_refs.clear()
    assert store.trial('run-1') == item
    with closing(sqlite3.connect(store.path)) as connection:
        for action in ('UPDATE trials SET payload=\'{}\'', 'DELETE FROM trials'):
            with pytest.raises(sqlite3.IntegrityError, match='IMMUTABLE_RECORD'):
                connection.execute(action)


def test_disabled_budget_starts_no_job(store):
    from factors.trials import ResearchBudget, record_budget
    jobs_started = []
    budget = ResearchBudget(batch_id='batch-1', candidate_limit=4, trial_limit=8,
        compute_seconds_limit=600, paid_spend_limit=0)
    assert budget.enabled is False
    assert record_budget(budget, store=store) == 'batch-1'
    assert store.budget('batch-1') == budget
    assert jobs_started == []
    with pytest.raises(ValidationError):
        ResearchBudget(batch_id='paid', candidate_limit=1, trial_limit=1, compute_seconds_limit=1, paid_spend_limit=1)
    with pytest.raises(ValidationError):
        ResearchBudget(batch_id='enabled', candidate_limit=1, trial_limit=1, compute_seconds_limit=1, paid_spend_limit=0, enabled=True)


@pytest.mark.parametrize('field,value', [('candidate_limit', True), ('trial_limit',-1), ('compute_seconds_limit',float('inf'))])
def test_budget_limits_are_strict_and_finite(field, value):
    from factors.trials import ResearchBudget
    values=dict(batch_id='batch',candidate_limit=2,trial_limit=2,compute_seconds_limit=2,paid_spend_limit=0)
    values[field] = value
    with pytest.raises(ValidationError):
        ResearchBudget(**values)


def test_budget_id_conflicts_and_append_only(store):
    from factors.trials import ResearchBudget, record_budget
    item = ResearchBudget(batch_id='batch',candidate_limit=2,trial_limit=2,compute_seconds_limit=2,paid_spend_limit=0)
    record_budget(item, store=store)
    revision = store.revision()
    assert record_budget(item, store=store) == 'batch' and store.revision() == revision
    with pytest.raises(ValueError, match='BUDGET_ID_CONFLICT'):
        record_budget(item.model_copy(update={'trial_limit':3}), store=store)
    with closing(sqlite3.connect(store.path)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match='IMMUTABLE_RECORD'):
            connection.execute('DELETE FROM research_budgets')


def test_trial_kinds_statuses_and_unknown_refs_fail_closed(store):
    from factors.trials import record_trial
    with pytest.raises(ValidationError):
        trial(report_kind='backtest')
    with pytest.raises(ValidationError):
        trial(status='PASSED')
    with pytest.raises(ValidationError):
        trial(factor_refs=[])
    unknown = definition().ref.model_copy(update={'factor_id':'unknown'})
    with pytest.raises(ValueError, match='UNKNOWN_DEFINITION'):
        record_trial(trial(factor_refs=[unknown]), store=store)


@pytest.mark.parametrize('value', [{'nested':(1,2)}, {'nested':{1:'bad-key'}}, {'nested':[float('nan')]}])
def test_trial_parameters_are_strict_inert_json(value):
    with pytest.raises(ValidationError, match='INVALID_TRIAL_JSON'):
        trial(parameters=value)


@pytest.mark.parametrize('value', [0, 'false', None])
def test_disabled_budget_requires_actual_boolean_false(value):
    from factors.trials import ResearchBudget
    with pytest.raises(ValidationError):
        ResearchBudget(batch_id='b',candidate_limit=1,trial_limit=1,compute_seconds_limit=1,
                       paid_spend_limit=0,enabled=value)
