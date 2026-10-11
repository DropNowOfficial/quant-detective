"""Custom daily one-step-ahead forecast-error momentum, not paper replication."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence
import numpy as np
from .contracts import Evidence, FactorObservation, finite, nonempty, utc


@dataclass(frozen=True)
class ReturnDay:
    symbol: str
    session_close: datetime
    stock_return: float | None
    market_return: float | None
    sector_return: float | None
    evidence: Evidence
    market_benchmark: str
    sector_benchmark: str
    return_basis: str = 'TOTAL_RETURN_DECIMAL'


@dataclass(frozen=True)
class ResidualMomentum:
    observation: FactorObservation
    forecast_errors: tuple[float, ...]
    fit_end_times: tuple[datetime, ...]
    market_benchmark: str
    sector_benchmark: str
    membership_vintage: str


def residual_momentum(rows: Sequence[ReturnDay], *, entity: str, sessions: Sequence[datetime],
                      decision_time: datetime, market_benchmark: str, sector_benchmark: str,
                      membership_vintage: str, benchmark_available_at: datetime,
                      fit_window: int = 126, signal_window: int = 20) -> ResidualMomentum:
    for name, value in [('fit_window', fit_window), ('signal_window', signal_window)]:
        if type(value) is not int or value < (4 if name == 'fit_window' else 2):
            raise ValueError('positive windows and regression degrees of freedom required')
    for name, value in [('market_benchmark', market_benchmark), ('sector_benchmark', sector_benchmark),
                        ('membership_vintage', membership_vintage)]:
        nonempty(value, name)
    if market_benchmark == sector_benchmark:
        raise ValueError('market and sector benchmark identities must differ')
    decision = utc(decision_time); benchmark_known = utc(benchmark_available_at)
    lattice = tuple(utc(t) for t in sessions)
    if (not lattice or tuple(sorted(set(lattice))) != lattice
            or len({t.date() for t in lattice}) != len(lattice) or lattice[-1] > decision):
        raise ValueError('ordered unique completed daily session lattice required')
    indexed = {}
    for row in rows:
        if not isinstance(row, ReturnDay) or not isinstance(row.evidence, Evidence):
            raise ValueError('typed return day and evidence required')
        stamp = utc(row.session_close)
        if stamp in indexed or stamp not in lattice:
            raise ValueError('duplicate or undeclared daily session')
        if (row.symbol != entity or row.evidence.event_time != stamp
                or row.market_benchmark != market_benchmark or row.sector_benchmark != sector_benchmark):
            raise ValueError('entity or event identity mismatch')
        indexed[stamp] = row
    required = fit_window + signal_window
    target = lattice[-required:]
    selected = tuple(indexed[t] for t in target if t in indexed)
    evidence = tuple(r.evidence for r in selected)
    diagnostics = (('fit_window', fit_window), ('signal_window', signal_window),
                   ('available_sessions', len(selected)), ('market_benchmark', market_benchmark),
                   ('sector_benchmark', sector_benchmark), ('membership_vintage', membership_vintage))
    factor_id = f'custom.residual_momentum_{signal_window}d_market_sector_fit{fit_window}'
    def result(reason=None, status='MISSING', value=None, errors=(), ends=()):
        observation = FactorObservation(factor_id, entity, 'dimensionless', value,
                                        'VALID' if reason is None else status, reason, decision,
                                        evidence, (), diagnostics)
        return ResidualMomentum(observation, tuple(errors), tuple(ends), market_benchmark,
                                sector_benchmark, membership_vintage)
    if len(lattice) < required:
        return result('INSUFFICIENT_HISTORY')
    if len(selected) != required:
        return result('MISSING_SOURCE_SESSION')
    if any(e.available_at > decision for e in evidence):
        return result('NOT_YET_AVAILABLE', 'BLOCKED')
    first_formation = utc(selected[fit_window].session_close).replace(hour=0, minute=0, second=0, microsecond=0)
    if benchmark_known > first_formation:
        return result('BENCHMARK_NOT_AVAILABLE_AT_FORMATION', 'BLOCKED')
    if any(r.return_basis != 'TOTAL_RETURN_DECIMAL' for r in selected):
        return result('UNVERIFIED_RETURN_BASIS')
    if any(not finite(x) or x < -1 for r in selected for x in (r.stock_return, r.market_return, r.sector_return)):
        return result('INVALID_OR_MISSING_RETURN')
    values = np.array([[r.stock_return, r.market_return, r.sector_return] for r in selected], dtype=float)
    errors, ends = [], []
    try:
        with np.errstate(all='raise'):
            for i in range(fit_window, required):
                history = selected[i-fit_window:i]
                formation = utc(selected[i].session_close).replace(hour=0, minute=0, second=0, microsecond=0)
                if any(r.evidence.available_at > formation for r in history):
                    return result('FIT_VINTAGE_NOT_AVAILABLE', 'BLOCKED')
                train = values[i-fit_window:i]
                x = np.column_stack((np.ones(fit_window), train[:, 1], train[:, 2]-train[:, 1]))
                coefficients, _, rank, _ = np.linalg.lstsq(x, train[:, 0], rcond=None)
                if rank != 3:
                    return result('RANK_DEFICIENT_FIT')
                if np.linalg.cond(x) > 1e12:
                    return result('ILL_CONDITIONED_FIT')
                current = np.array([1., values[i, 1], values[i, 2]-values[i, 1]])
                error = float(values[i, 0]-current@coefficients)
                if not np.isfinite(error):
                    return result('NONFINITE_COMPUTATION')
                errors.append(error); ends.append(history[-1].evidence.event_time)
            sd = float(np.std(errors, ddof=1))
            tolerance = 100*np.finfo(float).eps*max(1., float(np.max(np.abs(values))))
            if sd <= tolerance:
                return result('ZERO_RESIDUAL_VARIANCE')
            score = float(np.sum(errors)/sd)
            if not np.isfinite(score):
                return result('NONFINITE_COMPUTATION')
    except (FloatingPointError, np.linalg.LinAlgError, OverflowError):
        return result('NONFINITE_COMPUTATION')
    return result(value=score, errors=errors, ends=ends)
