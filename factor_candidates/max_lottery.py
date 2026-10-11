"""MAX lottery factor, Bali, Cakici & Whitelaw (2011) JFE 99(2) 427-446.

PRIMARY definition (read in the Aug-2008 NYU working-paper version of the
paper; the published JFE text was not available): MAX is the maximum daily
return within the PREVIOUS CALENDAR MONTH. Used for as_of in month t, it is the
max over all trading days of month t-1 (the formation month) -> variant
"prior_calendar_month".

ALTERNATIVE (not the paper's definition): trailing fixed window of the last
`window` (default 21) trading days ending at as_of -> variant
"fixed_21_trading_days". Always label results with the variant used.

Rows are versioned (known_at, revision); only rows with known_at <= as_of are
visible. Same-day conflicting rows raise AmbiguousVersionError. All rows for a
security/day must share one adjustment_basis. Missing/NaN returns are skipped,
never 0; fewer than min_obs valid days => None. min_obs is a project choice,
not from the paper. trading_days must be unique dates (duplicates raise).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from .common import (
    PITError, as_of_utc, clean_trading_days, finite_or_none, require_date,
    require_provenance, require_revision, require_text, select_version,
)

VARIANT_PRIOR_MONTH = "prior_calendar_month"
VARIANT_FIXED = "fixed_21_trading_days"
VARIANTS = (VARIANT_PRIOR_MONTH, VARIANT_FIXED)


@dataclass(frozen=True)
class DailyReturn:
    security: str
    day: date
    known_at: datetime
    source: str
    adjustment_basis: str     # e.g. "split_dividend_adjusted_close_to_close"
    revision: int
    ret: Optional[float]


def _prior_month(as_of_date: date) -> tuple[int, int]:
    return (as_of_date.year - 1, 12) if as_of_date.month == 1 else (as_of_date.year, as_of_date.month - 1)


def max_lottery(
    rows: Iterable[DailyReturn],
    security: str,
    as_of: datetime,
    trading_days: Sequence[date],
    variant: str = VARIANT_PRIOR_MONTH,
    window: int = 21,
    min_obs: int = 15,
) -> Optional[float]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown MAX variant {variant!r}; use one of {VARIANTS}")
    as_of = as_of_utc(as_of)
    cal = clean_trading_days(trading_days)
    as_of_date = as_of.date()
    upto = [d for d in cal if d <= as_of_date]
    if variant == VARIANT_FIXED:
        days = upto[-window:]
        if len(days) < window:
            return None
    else:
        ym = _prior_month(as_of_date)
        days = [d for d in upto if (d.year, d.month) == ym]
        if not days:
            return None
    wanted = set(days)

    rows = list(rows)
    for r in rows:
        label = f"{r.security}@{r.day}"
        require_provenance(r.source, r.known_at, label)
        require_text(r.security, f"{label}: security")
        require_date(r.day, f"{label}: day")
        require_text(r.adjustment_basis, f"{label}: adjustment_basis")
        require_revision(r.revision, label)

    per_day: dict[date, list[DailyReturn]] = {}
    for r in rows:
        if r.security == security and r.day in wanted and r.known_at <= as_of:
            per_day.setdefault(r.day, []).append(r)
    vals = []
    for day in sorted(per_day):
        recs = per_day[day]
        if len({r.adjustment_basis for r in recs}) > 1:
            raise PITError(f"{security}@{day}: mixed adjustment_basis across versions")
        best = select_version(recs, f"{security}@{day} return")
        v = finite_or_none(best.ret)
        if v is not None:
            vals.append(v)
    if len(vals) < min_obs:
        return None
    return max(vals)
