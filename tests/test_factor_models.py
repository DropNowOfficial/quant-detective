"""Synthetic contract tests: no market data and no trust from imported claims."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from factors.models import DatasetManifest, FactorDefinition, FactorObservation, FactorRef
from factors.registry import mathematical_fingerprint
from market_data.quality import QualityResult

T = datetime(2026, 10, 6, 12, tzinfo=UTC)


def definition(**changes):
    return FactorDefinition(**{
        "ref": FactorRef(factor_id="external_value", version="1"),
        "name": "External value", "purpose": "exploratory research", "market": "US",
        "frequency": "daily", "unit": "ratio", "formula_text": "external value",
        "calculator_key": None, "input_fields": ["value"], "min_history": {"bars": 1},
        "missing_policy": "preserve with reason", "window": {"bars": 1}, "lag": {"bars": 0},
        "direction": "descriptive", **changes,
    })


def quality(**changes):
    return QualityResult(**{
        "state": "VALID", "reason_codes": (), "observation_ok": True,
        "confirmation_ok": True, "evaluated_at_ms": int(T.timestamp() * 1000),
        "valid_until_ms": int((T + timedelta(minutes=5)).timestamp() * 1000),
        "expected_bar_end_ms": None, "last_bar_end_ms": None, "sample_count": 1, **changes,
    })


def observation(**changes):
    return FactorObservation(**{
        "ref": FactorRef(factor_id="external_value", version="1"), "instrument_id": "US:SYNTH",
        "observed_at": T, "source_published_at": T, "provider_available_at": T,
        "ingested_at": T, "computed_at": T, "effective_available_at": T,
        "value": 10.0, "missing_reason": None, "pit_grade": "FORWARD_OBSERVED",
        "source_ref": "synthetic-source", "input_hash": "a" * 64, "quality": quality(),
        "availability_basis": "actual forward receipt", "dependency_available_ats": [],
        "input_refs": ["synthetic-input:1"], **changes,
    })


def manifest(**changes):
    return DatasetManifest(**{
        "dataset_id": "synthetic-dataset", "version": 1, "source_ref": "synthetic-source",
        "file_sha256": "b" * 64, "imported_at": T, "universe_version": "synthetic-universe-1",
        "adjustment": "unadjusted", "availability_basis": "actual forward receipt", "row_count": 1,
        "provider": "synthetic", "permission_basis": "synthetic test fixture",
        "source_timezone": "UTC", "corporate_action_basis": "not applicable",
        "history_version": "synthetic-history-1", "coverage_gaps": [], **changes,
    })


def test_missing_is_not_zero():
    missing = observation(value=None, missing_reason="NO_SOURCE_VALUE", quality=quality(
        state="UNAVAILABLE", reason_codes=("NO_SOURCE_VALUE",), observation_ok=False,
        confirmation_ok=False, valid_until_ms=None))
    assert missing.value is None
    assert observation(value=0.0).value == 0.0
    with pytest.raises(ValidationError, match="MISSING_REASON_REQUIRED"):
        observation(value=None)
    with pytest.raises(ValidationError, match="VALUE_WITH_MISSING_REASON"):
        observation(missing_reason="NO_SOURCE_VALUE")
    with pytest.raises(ValidationError, match="MISSING_VALUE_CONFIRMATION"):
        observation(value=None, missing_reason="NO_SOURCE_VALUE")


@pytest.mark.parametrize("value", [True, False, float("nan"), float("inf"), -float("inf"), "10"])
def test_nonfinite_boolean_and_naive_time_rejected(value):
    with pytest.raises(ValidationError):
        observation(value=value)


@pytest.mark.parametrize("field", ["observed_at", "source_published_at", "provider_available_at",
                                     "ingested_at", "computed_at", "effective_available_at"])
def test_all_observation_times_require_utc(field):
    with pytest.raises(ValidationError, match="UTC_REQUIRED"):
        observation(**{field: T.replace(tzinfo=None)})
    with pytest.raises(ValidationError, match="UTC_REQUIRED"):
        observation(**{field: T.astimezone(timezone(timedelta(hours=8)))})


def test_live_availability_includes_actual_dependencies_and_computation():
    later = T + timedelta(seconds=30)
    with pytest.raises(ValidationError, match="AVAILABILITY_BEFORE_DEPENDENCY"):
        observation(computed_at=later)
    with pytest.raises(ValidationError, match="AVAILABILITY_BEFORE_DEPENDENCY"):
        observation(dependency_available_ats=[later])
    assert observation(computed_at=later, effective_available_at=later).effective_available_at == later


def test_impossible_time_order_is_rejected():
    with pytest.raises(ValidationError, match="IMPOSSIBLE_TIME_ORDER"):
        observation(provider_available_at=T - timedelta(seconds=1))


def test_reconstructed_preserves_historical_availability_without_relabeling():
    later = T + timedelta(days=10)
    item = observation(pit_grade="RECONSTRUCTED", ingested_at=later, computed_at=later,
                       effective_available_at=T, availability_basis="unverified historical claim")
    assert item.effective_available_at == T
    assert item.computed_at == later


@pytest.mark.parametrize("claim", ["VINTAGE_VERIFIED", "VALIDATED"])
def test_external_csv_cannot_claim_validated_or_pit_verified(claim):
    with pytest.raises(ValidationError):
        observation(pit_grade=claim)
    with pytest.raises(ValidationError):
        observation(quality=quality(state=claim))
    with pytest.raises(ValidationError):
        definition(**{"status": claim})


def test_existing_quality_result_is_preserved_and_strictly_checked():
    q = quality()
    item = observation(quality=q)
    assert item.quality == q
    assert isinstance(item.quality, QualityResult)
    with pytest.raises(ValidationError):
        observation(quality=replace(q, confirmation_ok="true"))
    with pytest.raises(ValidationError):
        observation(quality=replace(q, sample_count=True))
    with pytest.raises(ValidationError):
        observation(quality=replace(q, state="UNAVAILABLE"))


@pytest.mark.parametrize("factory", [definition, observation, manifest])
def test_models_forbid_extra_fields(factory):
    with pytest.raises(ValidationError, match="extra_forbidden"):
        factory(unknown=True)


def test_ids_versions_numeric_counts_and_metadata_are_strict():
    for factor_id in ["UPPER", "a b", "", "__import__('os')"]:
        with pytest.raises(ValidationError):
            FactorRef(factor_id=factor_id, version="1")
    for bad in [True, 0, -1, "1"]:
        with pytest.raises(ValidationError):
            manifest(version=bad)
    with pytest.raises(ValidationError):
        definition(min_history={"bars": True})
    with pytest.raises(ValidationError):
        manifest(row_count=True)
    with pytest.raises(ValidationError):
        manifest(source_timezone="not/a-zone")
    with pytest.raises(ValidationError):
        manifest(permission_basis="")
    with pytest.raises(ValidationError):
        observation(input_hash="not-a-sha")


def test_calculator_is_allowlisted_and_formula_is_never_executed():
    with pytest.raises(ValidationError, match="UNKNOWN_CALCULATOR"):
        definition(calculator_key="__import__('os').system")
    item = definition(formula_text="__import__('os').system('never execute')")
    assert item.calculator_key is None


def test_mathematical_fingerprint_ignores_operational_time_but_binds_inputs_and_definition():
    original = observation()
    later = T + timedelta(days=1)
    rerun = observation(ingested_at=later, computed_at=later, effective_available_at=later,
                        quality=quality(evaluated_at_ms=int(later.timestamp() * 1000),
                                        valid_until_ms=int((later + timedelta(minutes=5)).timestamp() * 1000)))
    assert mathematical_fingerprint(definition(), original) == mathematical_fingerprint(definition(), rerun)
    assert mathematical_fingerprint(definition(), original) != mathematical_fingerprint(
        definition(formula_text="new formula"), original)
    assert mathematical_fingerprint(definition(), original) != mathematical_fingerprint(
        definition(), observation(input_hash="c" * 64))
    assert mathematical_fingerprint(definition(), original) != mathematical_fingerprint(
        definition(), observation(value=12.0))


def test_full_json_roundtrip_reuses_shared_quality_contract():
    item = observation()
    restored = FactorObservation.model_validate_json(item.model_dump_json())
    assert restored == item
    assert type(restored.quality) is QualityResult
    assert FactorDefinition.model_validate_json(definition().model_dump_json()) == definition()
    assert DatasetManifest.model_validate_json(manifest().model_dump_json()) == manifest()


def test_definition_ref_is_part_of_mathematical_identity():
    with pytest.raises(ValueError, match="^DEFINITION_REF_MISMATCH$"):
        mathematical_fingerprint(definition(ref=FactorRef(factor_id="other", version="1")), observation())


def test_unknown_quality_fields_and_nested_coercion_are_rejected():
    import json
    payload = json.loads(observation().model_dump_json())
    payload["quality"]["validated"] = True
    with pytest.raises(ValidationError, match="unexpected_keyword_argument"):
        FactorObservation.model_validate_json(json.dumps(payload))
    payload["quality"].pop("validated")
    payload["quality"]["sample_count"] = True
    with pytest.raises(ValidationError):
        FactorObservation.model_validate_json(json.dumps(payload))


def test_default_provenance_is_reconstructed_and_zero_is_canonical():
    item = observation()
    payload = {name: getattr(item, name) for name in FactorObservation.model_fields}
    payload.pop("pit_grade")
    assert FactorObservation(**payload).pit_grade == "RECONSTRUCTED"
    assert mathematical_fingerprint(definition(), observation(value=0.0)) == mathematical_fingerprint(
        definition(), observation(value=-0.0))
