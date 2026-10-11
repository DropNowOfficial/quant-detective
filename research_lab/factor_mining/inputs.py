"""Minimal offline inputs, not factor formulas or economic certification.

A financial fact is not analyst consensus. Surprise/revision research separately
requires pre-event consensus snapshots, their PIT availability and provenance,
and matching periods, units, currencies, accounting bases and coverage contexts.
Nothing here extracts records, unlocks gated hypotheses, or connects live orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Literal

from .contracts import Evidence, finite, nonempty, utc


@dataclass(frozen=True)
class FactKey:
    """All accounting dimensions must match; no implicit unit/basis conversion."""

    entity: str
    metric: str
    period_start: date
    period_end: date
    fiscal_period: str
    unit: str
    currency: str
    basis: str
    context_id: str

    def __post_init__(self):
        for name in ('entity', 'metric', 'fiscal_period', 'unit', 'currency', 'basis', 'context_id'):
            nonempty(getattr(self, name), name)
        for name in ('period_start', 'period_end'):
            if type(getattr(self, name)) is not date:
                raise ValueError(f'{name} must be a date, not a datetime')
        if self.period_start > self.period_end:
            raise ValueError('fact period starts after its end')


@dataclass(frozen=True)
class TimedFact:
    """One sourced actual-fact vintage; missing values remain missing.

    evidence.event_time is this source version's publication/revision timestamp,
    not the fiscal period end (which belongs to FactKey). Forecast consensus
    needs a separate type and must not be inserted here as completed actuals."""

    key: FactKey
    value: float | None
    evidence: Evidence
    accession: str

    def __post_init__(self):
        if not isinstance(self.key, FactKey) or not isinstance(self.evidence, Evidence):
            raise ValueError('typed fact key and evidence required')
        nonempty(self.accession, 'accession')
        if self.key.period_end > self.evidence.event_time.date():
            raise ValueError('actual fact period cannot end after its reported event')
        if self.value is not None and not finite(self.value):
            raise ValueError('fact value must be finite and nonboolean, or None')


def asof_fact(rows: Iterable[TimedFact], key: FactKey, decision_time: datetime) -> TimedFact | None:
    """Select the latest published exact-key version known by decision time.

    Validate identities only among eligible exact-key rows, so future records do
    not change a historical selection. Within a key, (source_uri, vintage_id)
    cannot carry different values. Receipt determines eligibility, not version
    precedence: a late old filing must not undo a newer publication. Publication-event ties must be identical whole records;
    choosing arbitrarily between distinct provenance would be order-dependent.
    """
    decision = utc(decision_time)
    if not isinstance(key, FactKey):
        raise ValueError('typed fact key required')
    eligible = []
    identities = {}
    for row in rows:
        if not isinstance(row, TimedFact):
            raise ValueError('typed timed facts required')
        if row.key != key or row.evidence.available_at > decision:
            continue
        identity = (row.evidence.source_uri, row.evidence.vintage_id)
        if identity in identities and identities[identity] != row.value:
            raise ValueError('conflicting values for reused source/vintage identity')
        identities[identity] = row.value
        eligible.append(row)
    if not eligible:
        return None
    latest_time = max(row.evidence.event_time for row in eligible)
    latest = [row for row in eligible if row.evidence.event_time == latest_time]
    if any(row != latest[0] for row in latest[1:]):
        raise ValueError('ambiguous latest fact: conflicting values or provenance')
    return latest[0]


@dataclass(frozen=True)
class ExposureEdge:
    """A directed, sourced exposure valid on [effective_from, effective_to).

    The source depends on the target. Zero weights are valid recorded edges with
    no exposure contribution; active_at describes timing, not positive weight.
    A graph first observed today cannot become known at an earlier decision.
    """

    source_id: str
    target_id: str
    edge_type: str
    direction: Literal['source_depends_on_target']
    weight: float
    unit: str
    effective_from: datetime
    evidence: Evidence
    effective_to: datetime | None = None

    def __post_init__(self):
        for name in ('source_id', 'target_id', 'edge_type', 'unit'):
            nonempty(getattr(self, name), name)
        if self.direction != 'source_depends_on_target':
            raise ValueError('direction must be source_depends_on_target')
        if not finite(self.weight) or self.weight < 0:
            raise ValueError('edge weight must be finite, nonboolean and nonnegative')
        if not isinstance(self.evidence, Evidence):
            raise ValueError('typed edge evidence required')
        object.__setattr__(self, 'effective_from', utc(self.effective_from))
        if self.effective_to is not None:
            object.__setattr__(self, 'effective_to', utc(self.effective_to))
            if self.effective_to <= self.effective_from:
                raise ValueError('edge effective interval must be nonempty')

    def active_at(self, decision: datetime) -> bool:
        decision = utc(decision)
        return (self.evidence.available_at <= decision
                and self.effective_from <= decision
                and (self.effective_to is None or decision < self.effective_to))


@dataclass(frozen=True)
class PublicIntent:
    """A public statement, never an inference of private managerial intent.

    Preserve the stated rationale and supporting public evidence span. Use the
    explicit labels 'unknown' or 'mixed' when appropriate instead of inventing a
    motive. An authorization does not establish execution; guidance is distinct.
    """

    entity: str
    event_kind: Literal['AUTHORIZATION', 'EXECUTION', 'CANCELLATION', 'CAPEX_GUIDANCE']
    stated_rationale: str
    evidence_span: str
    source: Evidence

    def __post_init__(self):
        for name in ('entity', 'stated_rationale', 'evidence_span'):
            nonempty(getattr(self, name), name)
        if self.event_kind not in ('AUTHORIZATION', 'EXECUTION', 'CANCELLATION', 'CAPEX_GUIDANCE'):
            raise ValueError('unsupported public event kind')
        if not isinstance(self.source, Evidence):
            raise ValueError('typed public evidence required')
