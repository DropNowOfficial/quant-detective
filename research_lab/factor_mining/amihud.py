"""Custom 20/60-session Amihud adaptations on an explicit session lattice."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Sequence
from .contracts import Evidence, FactorObservation, finite, utc


@dataclass(frozen=True)
class LiquidityDay:
    symbol: str
    session_close: datetime
    total_return: float | None
    raw_close: float | None
    raw_share_volume: float | None
    currency: str
    actions_verified: bool
    evidence: Evidence
    close_basis: str = 'AS_TRADED'
    volume_basis: str = 'AS_TRADED_SHARES'
    return_basis: str = 'TOTAL_RETURN_DECIMAL'


def amihud(rows: Sequence[LiquidityDay], *, entity: str, sessions: Sequence[datetime],
           decision_time: datetime, window: int = 20) -> FactorObservation:
    """Never infer a calendar or corporate-action convention from the numbers.

    Caller supplies completed trading-session closes. Missing sessions remain in
    the denominator of coverage. 80% is a fixed research acceptance choice.
    """
    if type(window) is not int or window not in (20, 60):
        raise ValueError('window must be the predeclared 20 or 60 sessions')
    decision = utc(decision_time)
    lattice = tuple(utc(t) for t in sessions)
    if (len(lattice) < window or tuple(sorted(set(lattice))) != lattice
            or len({t.date() for t in lattice}) != len(lattice)):
        raise ValueError('complete ordered unique session lattice required')
    if lattice[-1] > decision:
        raise ValueError('unfinished session at decision')
    target = lattice[-window:]
    indexed = {}
    for row in rows:
        if not isinstance(row, LiquidityDay) or not isinstance(row.evidence, Evidence):
            raise ValueError('typed liquidity day/evidence required')
        stamp = utc(row.session_close)
        if stamp in indexed:
            raise ValueError('duplicate session or ambiguous revision')
        if row.symbol != entity or row.evidence.event_time != stamp:
            raise ValueError('entity or completed-event identity mismatch')
        if stamp not in lattice:
            raise ValueError('row outside declared session lattice')
        indexed[stamp] = row
    selected = tuple(indexed[t] for t in target if t in indexed)
    evidence = tuple(row.evidence for row in selected)
    factor_id = f'custom.amihud_{window}d'
    if any(e.available_at > decision for e in evidence):
        return FactorObservation(factor_id, entity, 'USD^-1', None, 'BLOCKED', 'NOT_YET_AVAILABLE', decision, evidence)
    ratios, flags = [], set()
    for t in target:
        row = indexed.get(t)
        if row is None:
            flags.add('MISSING_SESSION'); continue
        if (row.currency != 'USD' or row.close_basis != 'AS_TRADED'
                or row.volume_basis != 'AS_TRADED_SHARES' or row.return_basis != 'TOTAL_RETURN_DECIMAL'
                or row.actions_verified is not True):
            flags.add('UNVERIFIED_UNITS_OR_ACTIONS'); continue
        if (not all(finite(x) for x in (row.total_return, row.raw_close, row.raw_share_volume))
                or row.raw_close <= 0 or row.raw_share_volume <= 0 or row.total_return < -1):
            flags.add('INVALID_OR_MISSING_DAILY_INPUT'); continue
        dollars = row.raw_close * row.raw_share_volume
        if not finite(dollars) or dollars <= 0:
            flags.add('INVALID_DOLLAR_VOLUME'); continue
        value = abs(row.total_return) / dollars
        if not math.isfinite(value):
            flags.add('NONFINITE_DAILY_RATIO'); continue
        ratios.append(value)
    diagnostics = (('valid_sessions', len(ratios)), ('requested_sessions', window),
                   ('coverage', len(ratios) / window), ('min_coverage', .8))
    if len(ratios) < math.ceil(.8 * window):
        return FactorObservation(factor_id, entity, 'USD^-1', None, 'MISSING',
                                 'INSUFFICIENT_VALID_SESSIONS', decision, evidence,
                                 tuple(sorted(flags)), diagnostics)
    # Scale by the largest ratio: avoid both overflowing the sum and
    # underflowing every summand when the representable mean is subnormal.
    scale = max(ratios)
    value = scale * (math.fsum(x / scale for x in ratios) / len(ratios)) if scale else 0.0
    return FactorObservation(factor_id, entity, 'USD^-1', value, 'VALID', None,
                             decision, evidence, tuple(sorted(flags)), diagnostics)
