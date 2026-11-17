"""Versioned JSON-ready boundary for future read-only live shadow adapters.

No I/O, signals, orders or claim of operational freshness is performed here.
"""
from __future__ import annotations
from datetime import datetime
from typing import Sequence
from .contracts import FactorObservation, utc


def shadow_snapshot(observations: Sequence[FactorObservation], *, generated_at: datetime) -> dict:
    generated = utc(generated_at)
    rows, identities = [], set()
    for o in observations:
        if not isinstance(o, FactorObservation):
            raise ValueError('typed factor observation required')
        if o.decision_time > generated:
            raise ValueError('generation cannot precede decision')
        identity = (o.factor_id, o.definition_version, o.entity, o.decision_time)
        if identity in identities:
            raise ValueError('duplicate observation identity')
        identities.add(identity)
        evidence = [{
            'event_time': e.event_time.isoformat(), 'available_at': e.available_at.isoformat(),
            'first_seen_at': e.first_seen_at.isoformat(), 'source_uri': e.source_uri,
            'vintage_id': e.vintage_id, 'license_id': e.license_id,
            'availability_basis': e.availability_basis,
        } for e in o.evidence]
        rows.append({'factor_id': o.factor_id, 'definition_version': o.definition_version,
                     'entity': o.entity, 'unit': o.unit, 'value': o.value,
                     'calculation_status': o.status, 'reason': o.reason,
                     'decision_time': o.decision_time.isoformat(), 'evidence': evidence,
                     'quality_flags': list(o.quality_flags), 'diagnostics': dict(o.diagnostics),
                     'validation_status': o.research_state})
    return {'schema_version': 1, 'mode': 'research_shadow', 'generated_at': generated.isoformat(),
            'official_signal_eligible': False, 'operational_freshness_verified': False,
            'observations': rows}
