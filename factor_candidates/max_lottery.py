"""MAX lottery factor, Bali, Cakici & Whitelaw (2011) JFE 99(2) 427-446.

MAX = maximum daily return over the trailing `window` trading days ending at
the last trading day <= as_of. Days with no row, a missing return, or
known_at > as_of are MISSING (skipped, never 0). Needs >= min_obs valid days.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence

from .common import finite_or_none, require_provenance


@dataclass(frozen=True)
class DailyReturn:
    security: str
    day: date
    known_at: date
    source: str
    ret: Optional[float]


def max_lottery(
    rows: Iterable[DailyReturn],
    security: str,
    as_of: date,
    trading_days: Sequence[date],
    window: int = 21,
    min_obs: int = 15,
) -> Optional[float]:
    days = sorted(d for d in trading_days if d <= as_of)[-window:]
    if len(days) < window:
        return None
    wanted = set(days)
    by_day: dict[date, DailyReturn] = {}
    for r in rows:
        require_provenance(r.source, r.known_at, f"{r.security}@{r.day}")
        if r.security != security or r.day not in wanted or r.known_at > as_of:
            continue
        prev = by_day.get(r.day)
        if prev is None or r.known_at >= prev.known_at:
            by_day[r.day] = r
    vals = [v for v in (finite_or_none(r.ret) for r in by_day.values()) if v is not None]
    if len(vals) < min_obs:
        return None
    return max(vals)
