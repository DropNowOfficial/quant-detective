"""Non-promoting governance on synthetic immutable evidence, never trading."""
from contextlib import closing
from datetime import timedelta
import sqlite3

import pytest

from factors.store import FactorStore
from factors.models import FactorRef
from test_factor_models import T, definition, manifest, observation, quality


@pytest.fixture
def store(tmp_path):
    with FactorStore(tmp_path / 'governance.sqlite') as result:
        yield result


def setup_candidate(store, **changes):
    store.commit_dataset(manifest(), [observation()], expected_revision=0,
                         request_id='initial', definitions=[definition(**changes)])


def event(store, target, *, source=None, event_id='event-1', at=T, **changes):
    from factors.lifecycle import LifecycleEvent, lifecycle_state
    return LifecycleEvent(event_id=event_id, factor_ref=definition().ref,
        from_state=source or lifecycle_state(definition().ref, store=store), to_state=target,
        at=at, actor='local-reviewer', reason='Explicit local review', evidence_ids=[],
        expected_revision=store.revision(), **changes)


def approved(store, item):
    from factors.lifecycle import local_operation, record_transition
    with local_operation(store, actor_label='local-reviewer', event=item, now=item.at,
                         approval_id='approval-'+item.event_id, approval_reason='Explicit manual approval') as context:
        return record_transition(item, store=store, approvals={'context': context})


def test_candidate_cannot_jump_to_active_even_with_high_score(store):
    from factors.lifecycle import record_transition
    setup_candidate(store)
    item = event(store, 'approved_active')
    result = record_transition(item, store=store, approvals={'approval': True, 'score': 100, 'actor': 'admin'})
    assert result.accepted is False
    assert result.error_code == 'EVIDENCE_VALIDATOR_NOT_CONFIGURED'
    assert store.revision() == 1


@pytest.mark.parametrize('target', ['research_validated', 'forward_shadow', 'approved_active', 'watch'])
def test_unconfigured_evidence_validator_blocks_promotion(store, target):
    setup_candidate(store)
    result = approved(store, event(store, target))
    assert result.accepted is False
    assert result.error_code == 'EVIDENCE_VALIDATOR_NOT_CONFIGURED'
    assert result.state == 'candidate' and result.revision == 1
    assert store.lifecycle_history(definition().ref) == []


def test_new_challenger_leaves_production_fingerprint_unchanged(store):
    from factors.lifecycle import production_manifest
    before = production_manifest(store)
    setup_candidate(store)
    assert approved(store, event(store, 'data_ready')).accepted
    after = production_manifest(store)
    assert after == before
    assert before['governed_active'] == []
    assert before['legacy_behavior'] == 'UNCHANGED_A_QUALITY_GATED_PATH'
    assert before['legacy_migration_completed'] is False


def test_transient_stale_does_not_quarantine_lifecycle(store):
    from factors.lifecycle import lifecycle_state
    from factors.bindings import evidence_card
    setup_candidate(store)
    assert approved(store, event(store, 'data_ready')).accepted
    old = store.observations(definition().ref, as_of=T+timedelta(hours=1), mode='exploratory')[0]
    assert 'EXPIRED_QUALITY' in evidence_card(old, now=T+timedelta(hours=1))['block_reasons']
    assert lifecycle_state(definition().ref, store=store) == 'data_ready'
    assert len(store.lifecycle_history(definition().ref)) == 1


def test_retired_version_stays_immutable(store):
    setup_candidate(store)
    assert approved(store, event(store, 'retired')).accepted
    original = store.definitions()
    with pytest.raises(ValueError, match='DEFINITION_VERSION_CONFLICT'):
        store.register_definition(definition(formula_text='overwrite retired'),
                                   expected_revision=store.revision(), request_id='overwrite')
    assert approved(store, event(store, 'archive', event_id='archive')).accepted
    assert approved(store, event(store, 'candidate', event_id='resurrect')).error_code == 'INVALID_LIFECYCLE_TRANSITION'
    assert store.definitions() == original
    assert [x.to_state for x in store.lifecycle_history(definition().ref)] == ['retired', 'archive']
    with closing(sqlite3.connect(store.path)) as connection:
        for table in ('lifecycle_events', 'lifecycle_approvals', 'lifecycle_checks'):
            with pytest.raises(sqlite3.IntegrityError, match='IMMUTABLE_RECORD'):
                connection.execute('DELETE FROM '+table)


def test_missing_legacy_transition_approval_does_not_cut_over(store):
    from factors.lifecycle import LifecycleEvent, record_transition, production_manifest, lifecycle_state
    ref = FactorRef(factor_id='minute.vwap60', version='1.0.0')
    before = production_manifest(store)
    item = LifecycleEvent(event_id='legacy', factor_ref=ref, from_state='LEGACY_UNVALIDATED',
        to_state='data_ready', at=T, actor='admin', reason='Client claims migration',
        evidence_ids=[], expected_revision=store.revision())
    assert record_transition(item, store=store, approvals={'approval': True}).accepted is False
    assert lifecycle_state(ref, store=store) == 'LEGACY_UNVALIDATED'
    assert production_manifest(store) == before


def test_quarantine_restore_requires_approval_and_fresh_checks(store):
    from factors.lifecycle import record_transition, lifecycle_state
    setup_candidate(store)
    assert approved(store, event(store, 'quarantine')).accepted
    restore = event(store, 'data_ready', event_id='restore')
    result = record_transition(restore, store=store, approvals={'approval': True, 'checks_passed': True})
    assert not result.accepted and result.error_code == 'LOCAL_APPROVAL_REQUIRED'
    stale = approved(store, event(store, 'data_ready', event_id='stale', at=T+timedelta(minutes=5)))
    assert not stale.accepted and stale.error_code == 'CURRENT_DATA_CHECK_FAILED'
    assert lifecycle_state(definition().ref, store=store) == 'quarantine'
    assert approved(store, restore).accepted
    assert approved(store, event(store, 'quarantine', event_id='again')).accepted
    assert approved(store, event(store, 'candidate', event_id='candidate')).accepted
    assert lifecycle_state(definition().ref, store=store) == 'candidate'


@pytest.mark.parametrize('field', ['min_history', 'window', 'lag'])
def test_incomplete_candidate_definition_is_not_data_ready(store, field):
    setup_candidate(store, **{field: {}})
    result = approved(store, event(store, 'data_ready'))
    assert not result.accepted and result.error_code == 'DEFINITION_CHECK_FAILED'
    assert store.revision() == 1


def test_expected_revision_actor_and_context_are_bound(store):
    from factors.lifecycle import local_operation, record_transition
    setup_candidate(store)
    item = event(store, 'retired')
    with local_operation(store, actor_label='local-reviewer', event=item, now=item.at,
                         approval_id='approve', approval_reason='Explicit approval') as context:
        changed = item.model_copy(update={'actor': 'impersonation'})
        assert record_transition(changed, store=store, approvals={'context': context}).error_code == 'LOCAL_APPROVAL_REQUIRED'
        other = definition(ref=FactorRef(factor_id='external.other', version='1'))
        store.register_definition(other, expected_revision=1, request_id='concurrent')
        assert record_transition(item, store=store, approvals={'context': context}).error_code == 'REVISION_CONFLICT'
    assert record_transition(item, store=store, approvals={'context': context}).error_code == 'LOCAL_APPROVAL_REQUIRED'
    assert store.lifecycle_history(item.factor_ref) == []


def test_transition_replay_and_event_id_conflict(store):
    from factors.lifecycle import record_transition
    setup_candidate(store)
    item = event(store, 'data_ready')
    first = approved(store, item)
    assert first.accepted and first.revision == 2
    # A persisted success is inspectable/idempotent; replay creates no authority.
    assert record_transition(item, store=store, approvals={}) == first
    changed = item.model_copy(update={'to_state': 'retired'})
    assert record_transition(changed, store=store, approvals={}).error_code == 'EVENT_ID_CONFLICT'
    assert store.revision() == 2 and len(store.lifecycle_history(item.factor_ref)) == 1
    history = store.lifecycle_history(item.factor_ref)
    history[0].evidence_ids.append('changed-by-reader')
    assert store.lifecycle_history(item.factor_ref)[0].evidence_ids == []


def test_future_history_does_not_change_past_state(store):
    from factors.lifecycle import lifecycle_state
    setup_candidate(store)
    assert approved(store, event(store, 'retired', at=T+timedelta(days=1))).accepted
    assert lifecycle_state(definition().ref, store=store, as_of=T) == 'candidate'
    assert lifecycle_state(definition().ref, store=store) == 'retired'
    assert store.lifecycle_history(definition().ref, as_of=T) == []


def test_backdated_event_cannot_make_stale_source_fresh(store):
    from factors.lifecycle import local_operation
    setup_candidate(store)
    item = event(store, 'data_ready', at=T)
    with pytest.raises(ValueError, match='OPERATION_TIME_MISMATCH'):
        with local_operation(store, actor_label='local-reviewer', event=item, now=T+timedelta(minutes=6),
                             approval_id='backdated', approval_reason='Must use current checks'):
            pass
    assert store.revision() == 1 and store.lifecycle_history(item.factor_ref) == []


def test_current_source_revision_is_rechecked_and_no_half_approval(store):
    from factors.lifecycle import lifecycle_state
    setup_candidate(store)
    assert approved(store, event(store, 'quarantine')).accepted
    later = T+timedelta(minutes=1)
    row = observation(value=12, source_published_at=later, provider_available_at=later,
        ingested_at=later, computed_at=later, effective_available_at=later, input_hash='e'*64,
        quality=quality(state='UNAVAILABLE', observation_ok=False, confirmation_ok=False,
                        reason_codes=('SOURCE_REJECTED',), valid_until_ms=None,
                        evaluated_at_ms=int(later.timestamp()*1000)))
    store.commit_dataset(manifest(version=2, imported_at=later), [row],
        expected_revision=store.revision(), request_id='new-source')
    restore = event(store, 'data_ready', event_id='restore-new-source', at=later)
    result = approved(store, restore)
    assert not result.accepted and result.error_code == 'CURRENT_DATA_CHECK_FAILED'
    assert lifecycle_state(restore.factor_ref, store=store) == 'quarantine'
    assert store.lifecycle_approval(restore.event_id) is None
    assert len(store.lifecycle_check_artifacts(restore.factor_ref)) == 1


def test_candidate_restore_keeps_incomplete_hypothesis_as_candidate(store):
    setup_candidate(store, min_history={}, window={}, lag={})
    assert approved(store, event(store, 'quarantine')).accepted
    assert approved(store, event(store, 'candidate', event_id='candidate-restore')).accepted
    check = store.lifecycle_check_artifacts(definition().ref)[-1]
    assert check['scope'] == 'CANDIDATE_INTEGRITY'
    assert 'INCOMPLETE_CANDIDATE_DEFINITION' in check['limitations']
    assert check['input_evidence'] == []


@pytest.mark.parametrize('changes,code', [
    ({'min_history':{'completed_sessions':1}}, 'UNSUPPORTED_READINESS_COUNT'),
    ({'lag':{'regular_sessions':1}}, 'UNSUPPORTED_READINESS_COUNT'),
])
def test_session_counts_are_never_fabricated_from_bars(store, changes, code):
    setup_candidate(store, **changes)
    result = approved(store, event(store, 'data_ready'))
    assert not result.accepted and result.error_code == code


def test_permission_basis_and_current_quality_are_separate_from_pit(store):
    store.commit_dataset(manifest(permission_basis='unknown'), [observation(pit_grade='RECONSTRUCTED')],
                         expected_revision=0, request_id='unknown-license', definitions=[definition()])
    result = approved(store, event(store, 'data_ready'))
    assert not result.accepted and result.error_code == 'PERMISSION_BASIS_REQUIRED'


def test_reconstructed_data_ready_is_honestly_non_promoting(store):
    store.commit_dataset(manifest(), [observation(pit_grade='RECONSTRUCTED')],
                         expected_revision=0, request_id='exploratory', definitions=[definition()])
    assert approved(store, event(store, 'data_ready')).accepted
    check = store.lifecycle_check_artifacts(definition().ref)[0]
    assert check['input_evidence'][0]['pit_grade'] == 'RECONSTRUCTED'
    assert check['input_evidence'][0]['quality_state'] == 'VALID'
    assert check['input_evidence'][0]['quality_reason_codes'] == []
    assert 'NO_PIT_VERIFICATION' in check['limitations']
    assert approved(store, event(store, 'research_validated', event_id='try-promote')).error_code == 'EVIDENCE_VALIDATOR_NOT_CONFIGURED'


def test_approval_check_and_transition_roll_back_together(store):
    setup_candidate(store)
    store._db.execute("CREATE TRIGGER force_check_failure BEFORE INSERT ON lifecycle_checks BEGIN SELECT RAISE(ABORT, 'SYNTHETIC_FAILURE'); END")
    with pytest.raises(sqlite3.IntegrityError, match='SYNTHETIC_FAILURE'):
        approved(store, event(store, 'data_ready'))
    assert store.revision() == 1 and store.lifecycle_history(definition().ref) == []
    assert store.lifecycle_approval('event-1') is None
    assert store.lifecycle_check_artifacts(definition().ref) == []


def test_context_cannot_be_serialized_forged_or_used_for_another_store(store, tmp_path):
    from factors.lifecycle import local_operation, record_transition
    setup_candidate(store)
    item = event(store, 'retired')
    for claimed in ({'context':{'approval':True}}, {'context':'local-reviewer'}, {'actor':'local-reviewer','approval':True}):
        assert record_transition(item, store=store, approvals=claimed).error_code == 'LOCAL_APPROVAL_REQUIRED'
    with FactorStore(tmp_path/'other.sqlite') as other:
        setup_candidate(other)
        with local_operation(store, actor_label=item.actor, event=item, now=T,
                             approval_id='store-bound', approval_reason='Explicit local approval') as context:
            assert record_transition(item, store=other, approvals={'context':context}).error_code == 'LOCAL_APPROVAL_REQUIRED'
    assert store.revision() == 1
