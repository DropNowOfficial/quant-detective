"""Offline PIT evidence; VALID means calculation validity, never economic validation."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Literal


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone-aware datetime required')
    return value.astimezone(timezone.utc)


def nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be nonempty')


def finite(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Evidence:
    event_time: datetime
    available_at: datetime
    first_seen_at: datetime
    source_uri: str
    vintage_id: str
    license_id: str
    availability_basis: Literal['OBSERVED', 'RETROSPECTIVE_CURRENT', 'SYNTHETIC_FIXTURE']

    def __post_init__(self):
        for name in ('event_time', 'available_at', 'first_seen_at'):
            object.__setattr__(self, name, utc(getattr(self, name)))
        for name in ('source_uri', 'vintage_id', 'license_id'):
            nonempty(getattr(self, name), name)
        if self.available_at < max(self.event_time, self.first_seen_at):
            raise ValueError('availability precedes completion or first receipt')
        if self.availability_basis not in {'OBSERVED', 'RETROSPECTIVE_CURRENT', 'SYNTHETIC_FIXTURE'}:
            raise ValueError('unknown availability basis')


@dataclass(frozen=True)
class FactorObservation:
    factor_id: str
    entity: str
    unit: str
    value: float | None
    status: Literal['VALID', 'MISSING', 'INVALID', 'BLOCKED']
    reason: str | None
    decision_time: datetime
    evidence: tuple[Evidence, ...]
    quality_flags: tuple[str, ...] = ()
    diagnostics: tuple[tuple[str, float | int | str], ...] = ()
    definition_version: str = '1'
    research_state: str = field(default='NEEDS BACKTEST', init=False)

    def __post_init__(self):
        for name in ('factor_id', 'entity', 'unit', 'definition_version'):
            nonempty(getattr(self, name), name)
        object.__setattr__(self, 'decision_time', utc(self.decision_time))
        if self.status not in {'VALID', 'MISSING', 'INVALID', 'BLOCKED'}:
            raise ValueError('unknown calculation status')
        if not isinstance(self.evidence, tuple) or any(not isinstance(e, Evidence) for e in self.evidence):
            raise ValueError('immutable typed evidence required')
        identities = [(e.source_uri, e.vintage_id, e.event_time) for e in self.evidence]
        if len(set(identities)) != len(identities):
            raise ValueError('duplicate evidence identity')
        if self.status == 'VALID':
            if not finite(self.value) or self.reason is not None or not self.evidence:
                raise ValueError('valid calculation needs finite value and evidence')
            if any(e.available_at > self.decision_time for e in self.evidence):
                raise ValueError('evidence unavailable at decision')
        elif self.value is not None or not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError('unavailable calculation requires null value and reason')
        if not isinstance(self.quality_flags, tuple) or len(set(self.quality_flags)) != len(self.quality_flags):
            raise ValueError('unique immutable quality flags required')
        for f in self.quality_flags:
            nonempty(f, 'quality flag')
        if not isinstance(self.diagnostics, tuple):
            raise ValueError('immutable diagnostics required')
        names = []
        for pair in self.diagnostics:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError('immutable diagnostic pairs required')
            name, value = pair
            nonempty(name, 'diagnostic key'); names.append(name)
            if not isinstance(value, str) and not finite(value):
                raise ValueError('finite scalar diagnostic required')
        if len(set(names)) != len(names):
            raise ValueError('duplicate diagnostic keys')

    @property
    def available_at(self):
        return max((e.available_at for e in self.evidence), default=None)

    @property
    def usable(self):
        return self.status == 'VALID'


def blocked_hypothesis(name: str, *, entity: str, decision_time: datetime) -> FactorObservation:
    reasons = {'analyst_surprise': 'BLOCKED_MISSING_PIT_CONSENSUS',
               'forecast_revision': 'BLOCKED_MISSING_PIT_CONSENSUS',
               'capital_intent': 'BLOCKED_MISSING_PUBLIC_INTENT_EVIDENCE',
               'exposure_connectivity': 'BLOCKED_MISSING_PIT_EDGES'}
    if name not in reasons:
        raise ValueError('unknown gated hypothesis')
    return FactorObservation(name, entity, 'unavailable', None, 'BLOCKED', reasons[name], decision_time, ())
