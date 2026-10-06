"""Closed calculator identities and deterministic, non-executable fingerprints.

A key is permission to bind a known implementation, never permission to evaluate
``formula_text``. Adding a calculator requires a reviewed source-code change.
"""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import FactorDefinition, FactorObservation

BUILTIN_CALCULATOR_KEYS = frozenset({
    "us.daily.ma5_slope_1",
    "us.intraday.distance_ma5_atr5",
    "us.intraday.relative_qqq_change",
    "us.intraday.rvol_same_time20",
    "minute.rvol_prior20",
    "research.daily.volume_ratio20",
    "research.daily.adv20",
    "us.intraday.vwap_rth_hlc3",
    "minute.vwap60",
})


def canonical_json(value: object) -> str:
    """Stable object-key ordering; reject nonfinite JSON numbers."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def definition_fingerprint(definition: FactorDefinition) -> str:
    """Bind the complete versioned definition, including formula and inputs."""
    return fingerprint(definition.model_dump(mode="json"))


def mathematical_fingerprint(definition: FactorDefinition, observation: FactorObservation) -> str:
    """Output identity, independent of run UUIDs, receipt and quality clocks.

    Input revisions, source observation identity and complete definition changes
    remain part of this fingerprint. This is not an availability/PIT certificate.
    """
    if definition.ref != observation.ref:
        raise ValueError("DEFINITION_REF_MISMATCH")
    return fingerprint({
        "definition": definition.model_dump(mode="json"),
        "ref": observation.ref.model_dump(mode="json"),
        "instrument_id": observation.instrument_id,
        "observed_at": observation.observed_at.isoformat(),
        "input_hash": observation.input_hash,
        "input_refs": observation.input_refs,
        "value": observation.value,
        "missing_reason": observation.missing_reason,
    })


def source_content(observation: FactorObservation) -> dict:
    """Canonical import content with operational times removed.

    Actual forward availability derived as the maximum dependency/receipt/compute
    time is operational; independent historical or delayed availability claims
    remain content. Original records and their audit hash are never rewritten.
    """
    content = observation.model_dump(mode="json")
    content.pop("ingested_at")
    content.pop("computed_at")
    if (observation.pit_grade == "FORWARD_OBSERVED"
            and observation.effective_available_at == observation.actual_available_at):
        content.pop("effective_available_at")
    for key in ("evaluated_at_ms", "valid_until_ms"):
        content["quality"].pop(key)
    return content


def definitions() -> list[FactorDefinition]:
    """Immutable built-ins and locked unconnected components, independent copies."""
    from pathlib import Path
    from .models import FactorDefinition
    packaged = Path(__file__).with_name('definitions_v1.json')
    path = packaged if packaged.exists() else Path(__file__).resolve().parent.parent / 'contracts/factors/definitions_v1.json'
    return [FactorDefinition.model_validate_json(canonical_json(item))
            for item in json.loads(path.read_text(encoding='utf-8'))]


def get(ref: FactorRef) -> FactorDefinition:
    """Resolve only the reviewed virtual catalog, never external shadow entries."""
    from .models import FactorRef
    if not isinstance(ref, FactorRef):
        raise ValueError('INVALID_FACTOR_REF')
    for item in definitions():
        if item.ref == ref:
            return item
    raise ValueError('UNKNOWN_DEFINITION')


def check_reserved_definition(definition: FactorDefinition) -> None:
    """A built-in ID cannot be redefined by an external version or calculator."""
    for original in definitions():
        if original.ref.factor_id == definition.ref.factor_id:
            if definition_fingerprint(original) != definition_fingerprint(definition):
                raise ValueError('DEFINITION_VERSION_CONFLICT')
            return
    if definition.calculator_key is not None:
        raise ValueError('DEFINITION_VERSION_CONFLICT')


def hypotheses() -> list[dict]:
    return [{'name': 'Market-adjusted relative strength', 'binding_status': 'UNIMPLEMENTED',
             'lifecycle_state': 'CANDIDATE', 'VALIDATION_STATUS': 'NEEDS BACKTEST',
             'description': 'Unvalidated research hypothesis; no calculator or observation. '
                            'Not Firm-Specific Alpha or an efficacy claim.'}]
