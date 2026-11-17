"""PIT-safe fundamental quality (GP/A) and microstructure (MAX, CLV) primitives.

All outputs return FactorObservation with research_state='NEEDS BACKTEST'.
Missing inputs stay None (never coerced to zero).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Iterable, Sequence

from .contracts import Evidence, FactorObservation, finite, nonempty, utc
from .inputs import FactKey, TimedFact, asof_fact


def _unique_evidence(items: Iterable[Evidence]) -> tuple[Evidence, ...]:
    seen = set()
    ordered = []
    for ev in items:
        ident = (ev.source_uri, ev.vintage_id, ev.event_time)
        if ident not in seen:
            seen.add(ident)
            ordered.append(ev)
    return tuple(ordered)


def _validate_session_lattice(sessions: Sequence[datetime], window: int, decision: datetime) -> tuple[datetime, ...]:
    lattice = tuple(utc(t) for t in sessions)
    if (len(lattice) < window or tuple(sorted(set(lattice))) != lattice
            or len({t.date() for t in lattice}) != len(lattice)):
        raise ValueError('complete ordered unique session lattice required')
    if lattice[-1] > decision:
        raise ValueError('unfinished session at decision')
    return lattice


def gross_profitability_pit(
    facts: Iterable[TimedFact],
    *,
    entity: str,
    period_start: date,
    period_end: date,
    fiscal_period: str,
    decision_time: datetime,
    unit: str = 'million',
    currency: str = 'USD',
    basis: str = 'GAAP',
    context_id: str = 'consolidated',
    max_staleness_days: int = 548,
) -> FactorObservation:
    """Compute (Revenue - COGS) / TotalAssets from exact-key PIT TimedFacts."""
    nonempty(entity, 'entity')
    decision = utc(decision_time)
    if type(max_staleness_days) is not int or max_staleness_days <= 0:
        raise ValueError('max_staleness_days must be a positive integer')
    fact_list = tuple(facts)
    keys = {
        metric: FactKey(
            entity=entity,
            metric=metric,
            period_start=period_start,
            period_end=period_end,
            fiscal_period=fiscal_period,
            unit=unit,
            currency=currency,
            basis=basis,
            context_id=context_id,
        )
        for metric in ('revenue', 'cogs', 'total_assets')
    }
    rev_fact = asof_fact(fact_list, keys['revenue'], decision)
    cogs_fact = asof_fact(fact_list, keys['cogs'], decision)
    assets_fact = asof_fact(fact_list, keys['total_assets'], decision)

    evidence = _unique_evidence(
        item.evidence for item in (rev_fact, cogs_fact, assets_fact) if item is not None
    )
    factor_id = 'custom.gross_profitability_gpa'
    base_diag = (
        ('period_end', period_end.isoformat()),
        ('fiscal_period', fiscal_period),
        ('max_staleness_days', max_staleness_days),
    )

    if any(item is None or item.value is None for item in (rev_fact, cogs_fact, assets_fact)):
        return FactorObservation(
            factor_id, entity, 'dimensionless', None, 'MISSING',
            'MISSING_FUNDAMENTAL_COMPONENT', decision, evidence, (), base_diag,
        )

    age_days = (decision.date() - period_end).days
    diag = base_diag + (('age_days', age_days),)
    if age_days > max_staleness_days:
        return FactorObservation(
            factor_id, entity, 'dimensionless', None, 'MISSING',
            'STALE_FUNDAMENTAL_FILING', decision, evidence, ('STALE_FILING',), diag,
        )

    if assets_fact.value <= 0:
        return FactorObservation(
            factor_id, entity, 'dimensionless', None, 'INVALID',
            'NONPOSITIVE_TOTAL_ASSETS', decision, evidence, (), diag,
        )

    gpa = (rev_fact.value - cogs_fact.value) / assets_fact.value
    if not math.isfinite(gpa):
        return FactorObservation(
            factor_id, entity, 'dimensionless', None, 'INVALID',
            'NONFINITE_RATIO', decision, evidence, (), diag,
        )

    return FactorObservation(
        factor_id, entity, 'dimensionless', float(gpa), 'VALID',
        None, decision, evidence, (), diag,
    )


@dataclass(frozen=True)
class DailyReturnObs:
    symbol: str
    session_close: datetime
    total_return: float | None
    evidence: Evidence
    actions_verified: bool = True
    return_basis: str = 'TOTAL_RETURN_DECIMAL'


def max_lottery_pit(
    rows: Sequence[DailyReturnObs],
    *,
    entity: str,
    sessions: Sequence[datetime],
    decision_time: datetime,
    window: int = 21,
    min_valid_sessions: int = 15,
) -> FactorObservation:
    """Maximum single-day return over trailing declared sessions (Bali et al. 2011 adaptation)."""
    nonempty(entity, 'entity')
    if type(window) is not int or window < 5:
        raise ValueError('window must be an integer >= 5')
    if type(min_valid_sessions) is not int or not 1 <= min_valid_sessions <= window:
        raise ValueError('min_valid_sessions must be an integer in 1..window')
    decision = utc(decision_time)
    lattice = _validate_session_lattice(sessions, window, decision)
    target = lattice[-window:]

    indexed = {}
    for row in rows:
        if not isinstance(row, DailyReturnObs) or not isinstance(row.evidence, Evidence):
            raise ValueError('typed DailyReturnObs and Evidence required')
        stamp = utc(row.session_close)
        if stamp in indexed:
            raise ValueError('duplicate session or ambiguous revision')
        if row.symbol != entity or row.evidence.event_time != stamp:
            raise ValueError('entity or completed-event identity mismatch')
        if stamp not in lattice:
            raise ValueError('row outside declared session lattice')
        indexed[stamp] = row

    selected = tuple(indexed[t] for t in target if t in indexed)
    evidence = _unique_evidence(r.evidence for r in selected)
    factor_id = f'custom.max_lottery_{window}d'
    if any(e.available_at > decision for e in evidence):
        return FactorObservation(
            factor_id, entity, 'decimal_return', None, 'BLOCKED',
            'NOT_YET_AVAILABLE', decision, evidence,
        )

    vals, flags = [], set()
    for t in target:
        row = indexed.get(t)
        if row is None:
            flags.add('MISSING_SESSION'); continue
        if row.actions_verified is not True or row.return_basis != 'TOTAL_RETURN_DECIMAL':
            flags.add('UNVERIFIED_RETURN_BASIS'); continue
        if not finite(row.total_return) or row.total_return < -1.0:
            flags.add('INVALID_OR_MISSING_RETURN'); continue
        vals.append(float(row.total_return))

    diagnostics = (
        ('valid_sessions', len(vals)),
        ('requested_sessions', window),
        ('min_valid_sessions', min_valid_sessions),
    )
    if len(vals) < min_valid_sessions:
        return FactorObservation(
            factor_id, entity, 'decimal_return', None, 'MISSING',
            'INSUFFICIENT_VALID_SESSIONS', decision, evidence,
            tuple(sorted(flags)), diagnostics,
        )

    return FactorObservation(
        factor_id, entity, 'decimal_return', float(max(vals)), 'VALID',
        None, decision, evidence, tuple(sorted(flags)), diagnostics,
    )


@dataclass(frozen=True)
class OHLCVDay:
    symbol: str
    session_close: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    evidence: Evidence


def clv_accumulation_pit(
    rows: Sequence[OHLCVDay],
    *,
    entity: str,
    sessions: Sequence[datetime],
    decision_time: datetime,
    window: int = 20,
) -> FactorObservation:
    """Volume-weighted Close Location Value in [-1, 1] over trailing declared sessions."""
    nonempty(entity, 'entity')
    if type(window) is not int or window not in (10, 20, 60):
        raise ValueError('window must be one of the predeclared 10, 20, or 60 sessions')
    decision = utc(decision_time)
    lattice = _validate_session_lattice(sessions, window, decision)
    target = lattice[-window:]

    indexed = {}
    for row in rows:
        if not isinstance(row, OHLCVDay) or not isinstance(row.evidence, Evidence):
            raise ValueError('typed OHLCVDay and Evidence required')
        stamp = utc(row.session_close)
        if stamp in indexed:
            raise ValueError('duplicate session or ambiguous revision')
        if row.symbol != entity or row.evidence.event_time != stamp:
            raise ValueError('entity or completed-event identity mismatch')
        if stamp not in lattice:
            raise ValueError('row outside declared session lattice')
        indexed[stamp] = row

    selected = tuple(indexed[t] for t in target if t in indexed)
    evidence = _unique_evidence(r.evidence for r in selected)
    factor_id = f'custom.clv_accumulation_{window}d'
    if any(e.available_at > decision for e in evidence):
        return FactorObservation(
            factor_id, entity, 'dimensionless', None, 'BLOCKED',
            'NOT_YET_AVAILABLE', decision, evidence,
        )

    weighted_clv, volumes, flags = [], [], set()
    for t in target:
        row = indexed.get(t)
        if row is None:
            flags.add('MISSING_SESSION'); continue
        vals = (row.open, row.high, row.low, row.close, row.volume)
        if not all(finite(x) for x in vals):
            flags.add('INVALID_OR_MISSING_OHLCV'); continue
        o, h, l, c, v = (float(x) for x in vals)
        if min(o, h, l, c) <= 0 or v <= 0 or not (l <= min(o, c) <= max(o, c) <= h):
            flags.add('INVALID_OHLCV_OR_NONPOSITIVE_VOLUME'); continue
        span = h - l
        clv = ((c - l) - (h - c)) / span if span > 0 else 0.0
        if not math.isfinite(clv * v):
            flags.add('NONFINITE_WEIGHTED_CLV'); continue
        weighted_clv.append(clv * v)
        volumes.append(v)

    min_req = math.ceil(0.8 * window)
    diagnostics = (
        ('valid_sessions', len(volumes)),
        ('requested_sessions', window),
        ('coverage', len(volumes) / window),
        ('min_coverage', 0.8),
    )
    total_vol = math.fsum(volumes) if volumes else 0.0
    if len(volumes) < min_req or not math.isfinite(total_vol) or total_vol <= 0:
        return FactorObservation(
            factor_id, entity, 'dimensionless', None, 'MISSING',
            'INSUFFICIENT_VALID_SESSIONS', decision, evidence,
            tuple(sorted(flags)), diagnostics,
        )

    raw_val = math.fsum(weighted_clv) / total_vol
    if not math.isfinite(raw_val):
        return FactorObservation(
            factor_id, entity, 'dimensionless', None, 'INVALID',
            'NONFINITE_CLV_RATIO', decision, evidence,
            tuple(sorted(flags)), diagnostics,
        )
    clamped = min(max(float(raw_val), -1.0), 1.0)
    return FactorObservation(
        factor_id, entity, 'dimensionless', clamped, 'VALID',
        None, decision, evidence, tuple(sorted(flags)), diagnostics,
    )
