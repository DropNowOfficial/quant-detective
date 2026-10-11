"""Balance-sheet accruals, Sloan (1996) The Accounting Review 71(3) 289-315.

accruals = ((dCA - dCash) - (dCL - dSTD - dTaxPayable) - Depreciation) / avg(TA_t, TA_{t-1})
Raw ratio returned; higher accruals are hypothesised to predict LOWER returns
(sign handled by the ranking layer, not here). Any missing component => None;
a missing tax-payable or short-term-debt line is NOT treated as zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from .common import finite_or_none, require_provenance


@dataclass(frozen=True)
class BalanceSheetFact:
    security: str
    period_end: date
    known_at: date
    source: str
    current_assets: Optional[float] = None
    cash: Optional[float] = None
    current_liabilities: Optional[float] = None
    short_term_debt: Optional[float] = None
    tax_payable: Optional[float] = None
    depreciation: Optional[float] = None
    total_assets: Optional[float] = None


def _latest_visible(facts: list[BalanceSheetFact], security: str, as_of: date):
    vis = [f for f in facts if f.security == security and f.known_at <= as_of]
    return sorted(vis, key=lambda f: (f.period_end, f.known_at))


def sloan_accruals(
    facts: Iterable[BalanceSheetFact],
    security: str,
    as_of: date,
    max_age_days: int = 548,
    prior_gap_days: tuple[int, int] = (300, 430),
) -> Optional[float]:
    facts = list(facts)
    for f in facts:
        require_provenance(f.source, f.known_at, f"{f.security}@{f.period_end}")
    vis = _latest_visible(facts, security, as_of)
    if not vis:
        return None
    cur_pe = max(f.period_end for f in vis)
    if (as_of - cur_pe).days > max_age_days:
        return None
    cur = max((f for f in vis if f.period_end == cur_pe), key=lambda f: f.known_at)
    priors = [f for f in vis
              if prior_gap_days[0] <= (cur_pe - f.period_end).days <= prior_gap_days[1]]
    if not priors:
        return None
    prev_pe = max(f.period_end for f in priors)
    prev = max((f for f in priors if f.period_end == prev_pe), key=lambda f: f.known_at)

    names = ("current_assets", "cash", "current_liabilities", "short_term_debt", "tax_payable")
    d = {}
    for n in names:
        a, b = finite_or_none(getattr(cur, n)), finite_or_none(getattr(prev, n))
        if a is None or b is None:
            return None
        d[n] = a - b
    dep = finite_or_none(cur.depreciation)
    ta_c, ta_p = finite_or_none(cur.total_assets), finite_or_none(prev.total_assets)
    if dep is None or ta_c is None or ta_p is None:
        return None
    avg_ta = (ta_c + ta_p) / 2.0
    if avg_ta <= 0:
        return None
    acc = ((d["current_assets"] - d["cash"])
           - (d["current_liabilities"] - d["short_term_debt"] - d["tax_payable"])
           - dep)
    return acc / avg_ta
