"""Synthetic fixtures only; these input contracts do not validate a trading signal."""
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
import importlib
from itertools import permutations
from pathlib import Path

import pytest

from research_lab.factor_mining.contracts import Evidence, blocked_hypothesis


def api():
    path = Path(__file__).parents[1] / 'research_lab/factor_mining/inputs.py'
    assert path.exists(), 'PIT fact, exposure, and public-intent contracts missing'
    return importlib.import_module('research_lab.factor_mining.inputs')


def stamp():
    return datetime(2026, 10, 9, 20, tzinfo=timezone.utc)


def evidence(**kw):
    fields = dict(event_time=stamp(), available_at=stamp(), first_seen_at=stamp(),
                  source_uri='synthetic://filing', vintage_id='v1',
                  license_id='synthetic-generated', availability_basis='SYNTHETIC_FIXTURE')
    fields.update(kw)
    return Evidence(**fields)


def key(**kw):
    fields = dict(entity='ACME', metric='revenue', period_start=date(2026, 7, 1),
                  period_end=date(2026, 9, 30), fiscal_period='Q3', unit='million',
                  currency='USD', basis='GAAP', context_id='consolidated')
    fields.update(kw)
    return api().FactKey(**fields)


def fact(**kw):
    fields = dict(key=key(), value=100.0, evidence=evidence(), accession='synthetic-001')
    fields.update(kw)
    return api().TimedFact(**fields)


def edge(**kw):
    fields = dict(source_id='ACME', target_id='SUPPLIER', edge_type='supplier',
                  direction='source_depends_on_target', weight=0.2, unit='fraction_of_cost',
                  effective_from=stamp(), effective_to=None, evidence=evidence())
    fields.update(kw)
    return api().ExposureEdge(**fields)


def intent(**kw):
    fields = dict(entity='ACME', event_kind='AUTHORIZATION', stated_rationale='unknown',
                  evidence_span='Synthetic public statement: board authorized a repurchase.',
                  source=evidence())
    fields.update(kw)
    return api().PublicIntent(**fields)


@pytest.mark.parametrize('factory,field,value', [
    (key, 'metric', 'profit'), (fact, 'value', 2.0),
    (edge, 'weight', 0.4), (intent, 'event_kind', 'EXECUTION'),
])
def test_input_contracts_are_immutable(factory, field, value):
    obj = factory()
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field, value)


@pytest.mark.parametrize('field', [
    'entity', 'metric', 'fiscal_period', 'unit', 'currency', 'basis', 'context_id',
])
@pytest.mark.parametrize('value', ['', '  ', None, 42])
def test_fact_key_requires_explicit_semantics(field, value):
    with pytest.raises(ValueError):
        key(**{field: value})


@pytest.mark.parametrize('field', ['period_start', 'period_end'])
@pytest.mark.parametrize('value', [stamp(), '2026-09-30', None])
def test_fact_periods_require_dates_not_datetimes(field, value):
    with pytest.raises(ValueError):
        key(**{field: value})


def test_fact_period_cannot_run_backwards_but_may_be_instantaneous():
    with pytest.raises(ValueError):
        key(period_start=date(2026, 10, 1))
    assert key(period_start=date(2026, 9, 30)).period_start == date(2026, 9, 30)


@pytest.mark.parametrize('value', [True, False, float('nan'), float('inf'), -float('inf'), '100'])
def test_fact_values_require_finite_nonboolean_numbers_or_missing(value):
    with pytest.raises(ValueError):
        fact(value=value)


def test_zero_negative_and_missing_facts_remain_distinct():
    assert fact(value=0.0).value == 0.0
    assert fact(value=-1.0).value == -1.0
    assert fact(value=None).value is None


@pytest.mark.parametrize('kw', [{'key': {}}, {'evidence': {}}, {'accession': ''}])
def test_fact_requires_typed_provenance_and_accession(kw):
    with pytest.raises(ValueError):
        fact(**kw)


def test_asof_uses_latest_published_eligible_fact_independently_of_input_order():
    old = fact()
    later = fact(value=110.0, accession='synthetic-002', evidence=evidence(
        vintage_id='v2', event_time=stamp() + timedelta(hours=1), available_at=stamp() + timedelta(hours=1)))
    for rows in permutations([old, later]):
        assert api().asof_fact(iter(rows), key(), stamp()) == old
        assert api().asof_fact(iter(rows), key(), stamp() + timedelta(hours=1)) == later
    assert api().asof_fact([old], key(), stamp() - timedelta(seconds=1)) is None
    assert api().asof_fact([], key(), stamp()) is None


@pytest.mark.parametrize('kw', [
    {'entity': 'OTHER'}, {'metric': 'profit'}, {'period_start': date(2026, 1, 1)},
    {'period_end': date(2026, 10, 1)}, {'fiscal_period': 'FY'}, {'unit': 'unit'},
    {'currency': 'CNY'}, {'basis': 'NON_GAAP'}, {'context_id': 'segment-1'},
])
def test_asof_never_mixes_any_fact_key_dimension(kw):
    assert api().asof_fact([fact(key=key(**kw))], key(), stamp()) is None


def test_asof_latest_missing_value_does_not_fall_back_to_old_number():
    missing = fact(value=None, accession='synthetic-002', evidence=evidence(
        vintage_id='v2', event_time=stamp() + timedelta(hours=1), available_at=stamp() + timedelta(hours=1)))
    result = api().asof_fact([fact(), missing], key(), missing.evidence.available_at)
    assert result == missing and result.value is None


def test_asof_identical_duplicates_are_harmless():
    row = fact()
    assert api().asof_fact([row, replace(row)], key(), stamp()) == row


@pytest.mark.parametrize('kw', [
    {'value': 101.0}, {'value': None}, {'accession': 'different'},
    {'evidence': evidence(vintage_id='v2')},
    {'evidence': evidence(source_uri='synthetic://other')},
])
def test_asof_conflicting_latest_ties_raise_in_all_orders(kw):
    for rows in permutations([fact(), fact(**kw)]):
        with pytest.raises(ValueError, match='conflict|ambiguous'):
            api().asof_fact(rows, key(), stamp())


def test_asof_changed_value_cannot_reuse_a_source_vintage_identity():
    changed = fact(value=101.0, accession='synthetic-002', evidence=evidence(
        event_time=stamp() + timedelta(minutes=1),
        available_at=stamp() + timedelta(hours=1)))
    for rows in permutations([fact(), changed]):
        with pytest.raises(ValueError, match='identity|conflict'):
            api().asof_fact(rows, key(), changed.evidence.available_at)
    # Later conflicting data cannot affect an earlier point-in-time selection.
    assert api().asof_fact([fact(), changed], key(), stamp()) == fact()


def test_asof_rejects_naive_decision_and_untyped_inputs():
    with pytest.raises(ValueError):
        api().asof_fact([], key(), stamp().replace(tzinfo=None))
    with pytest.raises(ValueError):
        api().asof_fact([], {}, stamp())
    with pytest.raises(ValueError):
        api().asof_fact([{}], key(), stamp())


def test_asof_compares_aware_decisions_in_utc():
    decision = stamp().astimezone(timezone(timedelta(hours=8)))
    assert api().asof_fact([fact()], key(), decision) == fact()


@pytest.mark.parametrize('field', ['source_id', 'target_id', 'edge_type', 'unit'])
def test_edges_require_explicit_semantics(field):
    with pytest.raises(ValueError):
        edge(**{field: '  '})


@pytest.mark.parametrize('direction', ['', 'undirected', 'target_depends_on_source'])
def test_edge_direction_has_one_unambiguous_orientation(direction):
    with pytest.raises(ValueError):
        edge(direction=direction)


@pytest.mark.parametrize('weight', [-1.0, True, float('nan'), float('inf'), None, '0.2'])
def test_edges_require_finite_nonnegative_weights(weight):
    with pytest.raises(ValueError):
        edge(weight=weight)


def test_zero_weight_edge_is_valid_but_has_zero_exposure_contribution():
    row = edge(weight=0.0)
    assert row.weight == 0.0 and row.active_at(stamp())


def test_edge_end_is_optional_for_an_open_effective_interval():
    row = api().ExposureEdge(source_id='ACME', target_id='SUPPLIER', edge_type='supplier',
                             direction='source_depends_on_target', weight=0.2,
                             unit='fraction_of_cost', effective_from=stamp(), evidence=evidence())
    assert row.effective_to is None and row.active_at(stamp() + timedelta(days=1))


def test_edge_uses_half_open_effective_interval():
    row = edge(effective_to=stamp() + timedelta(hours=1))
    assert not row.active_at(stamp() - timedelta(seconds=1))
    assert row.active_at(stamp())
    assert row.active_at(stamp() + timedelta(minutes=59))
    assert not row.active_at(stamp() + timedelta(hours=1))
    assert edge().active_at(stamp() + timedelta(days=1))


def test_present_graph_evidence_cannot_be_backdated():
    row = edge(effective_from=stamp() - timedelta(days=30), evidence=evidence(
        available_at=stamp() + timedelta(days=1), first_seen_at=stamp() + timedelta(days=1),
        availability_basis='RETROSPECTIVE_CURRENT'))
    assert not row.active_at(stamp())
    assert row.active_at(stamp() + timedelta(days=1))


def test_edge_dates_normalize_to_utc():
    offset = timezone(timedelta(hours=8))
    row = edge(effective_from=stamp().astimezone(offset),
               effective_to=(stamp() + timedelta(hours=1)).astimezone(offset))
    assert row.effective_from.tzinfo == timezone.utc
    assert row.effective_to.tzinfo == timezone.utc
    assert row.active_at(stamp().astimezone(offset))


@pytest.mark.parametrize('kw', [
    {'effective_from': stamp().replace(tzinfo=None)},
    {'effective_to': stamp().replace(tzinfo=None)},
    {'effective_to': stamp()}, {'effective_to': stamp() - timedelta(hours=1)},
    {'evidence': {}},
])
def test_edge_requires_typed_evidence_and_nonempty_aware_interval(kw):
    with pytest.raises(ValueError):
        edge(**kw)


def test_edge_rejects_naive_decision():
    with pytest.raises(ValueError):
        edge().active_at(stamp().replace(tzinfo=None))


@pytest.mark.parametrize('kind', ['AUTHORIZATION', 'EXECUTION', 'CANCELLATION', 'CAPEX_GUIDANCE'])
def test_public_intent_keeps_public_event_kinds_distinct(kind):
    assert intent(event_kind=kind).event_kind == kind


@pytest.mark.parametrize('rationale', ['unknown', 'mixed', 'Company states that capacity is constrained.'])
def test_public_intent_preserves_explicit_rationale_without_inference(rationale):
    assert intent(stated_rationale=rationale).stated_rationale == rationale


@pytest.mark.parametrize('kw', [
    {'entity': ''}, {'event_kind': 'PRIVATE_INTENT'}, {'stated_rationale': ''},
    {'evidence_span': ' '}, {'source': {}},
])
def test_public_intent_requires_public_evidence_and_supported_kind(kw):
    with pytest.raises(ValueError):
        intent(**kw)


def test_financial_facts_do_not_certify_pit_consensus_or_unlock_hypotheses():
    assert fact().key.metric == 'revenue'
    for name in ('analyst_surprise', 'forecast_revision'):
        result = blocked_hypothesis(name, entity='ACME', decision_time=stamp())
        assert result.status == 'BLOCKED'
        assert result.reason == 'BLOCKED_MISSING_PIT_CONSENSUS'


def test_actual_fact_cannot_be_reported_before_fiscal_period_completes():
    from datetime import date, datetime, timezone
    from research_lab.factor_mining.contracts import Evidence
    from research_lab.factor_mining.inputs import FactKey, TimedFact
    key=FactKey('ACME','EPS',date(2027,1,1),date(2027,3,31),'2027Q1','USD/share','USD','GAAP','consolidated')
    t=datetime(2026,10,9,20,tzinfo=timezone.utc)
    proof=Evidence(t,t,t,'synthetic://future-fact','v1','synthetic-generated','SYNTHETIC_FIXTURE')
    with pytest.raises(ValueError,match='period'): TimedFact(key,100.,proof,'fixture-accession')


def test_late_receipt_of_old_filing_cannot_undo_newer_published_revision():
    old=fact(value=100.,evidence=evidence(event_time=stamp()-timedelta(days=4),
             first_seen_at=stamp()+timedelta(days=1),available_at=stamp()+timedelta(days=1)))
    revised=fact(value=120.,accession='restatement',evidence=evidence(vintage_id='v2'))
    for records in permutations([old,revised]):
        assert api().asof_fact(records,key(),stamp()).value==120.
        assert api().asof_fact(records,key(),stamp()+timedelta(days=2)).value==120.


def test_same_publication_time_different_receipt_does_not_resolve_conflict():
    later_receipt=fact(value=120.,accession='v2',evidence=evidence(vintage_id='v2',
        available_at=stamp()+timedelta(hours=1),first_seen_at=stamp()+timedelta(hours=1)))
    with pytest.raises(ValueError,match='ambiguous'):
        api().asof_fact([fact(),later_receipt],key(),stamp()+timedelta(hours=2))
