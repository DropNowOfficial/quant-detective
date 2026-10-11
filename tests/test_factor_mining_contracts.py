from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path
import pytest


def api():
    assert (Path(__file__).parents[1] / 'research_lab/factor_mining/contracts.py').exists(), 'factor evidence contract missing'
    return importlib.import_module('research_lab.factor_mining.contracts')


def stamp():
    return datetime(2026, 10, 9, 20, tzinfo=timezone.utc)


def evidence(**kw):
    t = stamp()
    d = dict(event_time=t, available_at=t + timedelta(minutes=1), first_seen_at=t + timedelta(minutes=1),
             source_uri='synthetic://test', vintage_id='fixture-v1', license_id='synthetic-generated',
             availability_basis='SYNTHETIC_FIXTURE')
    d.update(kw)
    return api().Evidence(**d)


def observation(**kw):
    d = dict(factor_id='fixture.factor', entity='ACME', unit='USD^-1', value=1.0, status='VALID',
             reason=None, decision_time=stamp() + timedelta(minutes=2), evidence=(evidence(),))
    d.update(kw)
    return api().FactorObservation(**d)


def test_contract_normalizes_utc_and_preserves_known_time():
    e = evidence(event_time=stamp().astimezone(timezone(timedelta(hours=-4))))
    assert e.event_time.utcoffset() == timedelta(0)
    o = observation(evidence=(e,))
    assert o.available_at == e.available_at and o.usable
    assert o.research_state == 'NEEDS BACKTEST'


@pytest.mark.parametrize('field', ['event_time', 'available_at', 'first_seen_at'])
def test_naive_evidence_time_is_rejected(field):
    with pytest.raises(ValueError): evidence(**{field: stamp().replace(tzinfo=None)})


@pytest.mark.parametrize('kw', [
    {'available_at': stamp() - timedelta(seconds=1)},
    {'first_seen_at': stamp() + timedelta(hours=1)},
    {'source_uri': ''}, {'vintage_id': ''}, {'license_id': ''}, {'availability_basis': 'ASSUME_OLD'},
])
def test_invalid_evidence_does_not_become_pit(kw):
    with pytest.raises(ValueError): evidence(**kw)


@pytest.mark.parametrize('value', [None, True, float('nan'), float('inf')])
def test_valid_observation_requires_real_finite_number(value):
    with pytest.raises(ValueError): observation(value=value)


def test_missing_is_not_zero_and_keeps_a_reason():
    o = observation(value=None, status='MISSING', reason='NO_ROWS', evidence=())
    assert o.value is None and not o.usable and o.available_at is None
    with pytest.raises(ValueError): observation(value=0.0, status='MISSING', reason='NO_ROWS')
    with pytest.raises(ValueError): observation(value=None, status='MISSING', reason=None)


def test_valid_future_evidence_is_rejected():
    future = evidence(available_at=stamp()+timedelta(days=1), first_seen_at=stamp()+timedelta(days=1))
    with pytest.raises(ValueError, match='decision'): observation(evidence=(future,))


def test_conflicting_duplicate_vintage_is_rejected():
    e = evidence()
    with pytest.raises(ValueError, match='duplicate'): observation(evidence=(e,e))


def test_research_state_is_not_constructor_overridable():
    with pytest.raises(TypeError): observation(research_state='VALIDATED')


def test_missing_data_hypotheses_stay_blocked():
    for name, reason in [('analyst_surprise', 'BLOCKED_MISSING_PIT_CONSENSUS'),
                         ('forecast_revision', 'BLOCKED_MISSING_PIT_CONSENSUS'),
                         ('capital_intent', 'BLOCKED_MISSING_PUBLIC_INTENT_EVIDENCE'),
                         ('exposure_connectivity', 'BLOCKED_MISSING_PIT_EDGES')]:
        o = api().blocked_hypothesis(name, entity='ACME', decision_time=stamp())
        assert o.status == 'BLOCKED' and o.reason == reason and o.value is None
    with pytest.raises(ValueError): api().blocked_hypothesis('made_up', entity='ACME', decision_time=stamp())


def test_extreme_integer_is_invalid_not_an_overflow_exception():
    assert api().finite(10**1000) is False
    with pytest.raises(ValueError): observation(value=10**1000)


def test_diagnostic_pairs_must_be_deeply_immutable():
    with pytest.raises(ValueError): observation(diagnostics=(['coverage', .8],))
