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


def test_asof_uses_latest_published_eligible_fact_independently_of_input_order():
    old = fact()
    later = fact(value=110.0, accession='synthetic-002', evidence=evidence(
        vintage_id='v2', event_time=stamp() + timedelta(hours=1), available_at=stamp() + timedelta(hours=1)))
    for rows in permutations([old, later]):
        assert api().asof_fact(iter(rows), key(), stamp()) == old
        assert api().asof_fact(iter(rows), key(), stamp() + timedelta(hours=1)) == later
    assert api().asof_fact([old], key(), stamp() - timedelta(seconds=1)) is None


def test_asof_latest_missing_value_does_not_fall_back_to_old_number():
    missing = fact(value=None, accession='synthetic-002', evidence=evidence(
        vintage_id='v2', event_time=stamp() + timedelta(hours=1), available_at=stamp() + timedelta(hours=1)))
    result = api().asof_fact([fact(), missing], key(), missing.evidence.available_at)
    assert result == missing and result.value is None


def test_late_receipt_of_old_filing_cannot_undo_newer_published_revision():
    old = fact(value=100., evidence=evidence(event_time=stamp()-timedelta(days=4),
               first_seen_at=stamp()+timedelta(days=1), available_at=stamp()+timedelta(days=1)))
    revised = fact(value=120., accession='restatement', evidence=evidence(vintage_id='v2'))
    for records in permutations([old, revised]):
        assert api().asof_fact(records, key(), stamp()).value == 120.
        assert api().asof_fact(records, key(), stamp()+timedelta(days=2)).value == 120.


def test_financial_facts_do_not_certify_pit_consensus_or_unlock_hypotheses():
    for name in ('analyst_surprise', 'forecast_revision'):
        result = blocked_hypothesis(name, entity='ACME', decision_time=stamp())
        assert result.status == 'BLOCKED'
        assert result.reason == 'BLOCKED_MISSING_PIT_CONSENSUS'
