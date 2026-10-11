"""Balance-sheet accruals, Sloan (1996) The Accounting Review 71(3) 289-315.

Definition VERIFIED against the original full text (JSTOR scan, pp. 289-315;
see docs/research/evidence/pr48_sources.md):

  Accruals = (dCA - dCash) - (dCL - dSTD - dTP) - Dep
  dCA   change in current assets            (Compustat item 4)
  dCash change in cash / cash equivalents   (item 1)
  dCL   change in current liabilities       (item 5)
  dSTD  change in debt in current liabilities (item 34)
  dTP   change in income taxes payable      (item 71)
  Dep   depreciation AND amortization expense (item 14), SUBTRACTED
  scaled by AVERAGE total assets (beginning and end of year book value, item 6).

Annual data only by default. Raw ratio returned; the sign convention (higher
accruals hypothesised to predict LOWER returns) belongs to the ranking layer.
Any missing component => None (never 0). Current and prior statements are
version-selected independently under the PIT rule; they must share a currency.
XBRL-tag mapping of items 34/71 is NOT verified (see docs).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from .common import (
    PITError, as_of_utc, scaled, select_version, validate_filing_basis,
)


@dataclass(frozen=True)
class BalanceSheetFact:
    security: str
    period_end: date
    known_at: datetime
    source: str
    period_type: str
    currency: str
    unit_scale: float
    statement_ref: str
    revision: int
    current_assets: Optional[float] = None
    cash: Optional[float] = None
    current_liabilities: Optional[float] = None
    short_term_debt: Optional[float] = None
    tax_payable: Optional[float] = None
    depreciation_amortization: Optional[float] = None   # D&A expense for the period
    total_assets: Optional[float] = None


_BALANCE = ("current_assets", "cash", "current_liabilities", "short_term_debt",
            "tax_payable", "total_assets")


def sloan_accruals(
    facts: Iterable[BalanceSheetFact],
    security: str,
    as_of: datetime,
    max_age_days: int = 548,
    prior_gap_days: tuple[int, int] = (300, 430),
    period_type: str = "annual",
) -> Optional[float]:
    as_of = as_of_utc(as_of)
    facts = list(facts)
    for f in facts:
        validate_filing_basis(f)
    vis = [f for f in facts if f.security == security and f.period_type == period_type
           and f.known_at <= as_of]
    if not vis:
        return None
    cur_pe = max(f.period_end for f in vis)
    if (as_of.date() - cur_pe).days > max_age_days:
        return None
    priors_pe = [f.period_end for f in vis
                 if prior_gap_days[0] <= (cur_pe - f.period_end).days <= prior_gap_days[1]]
    if not priors_pe:
        return None
    prev_pe = max(priors_pe)
    cur = select_version([f for f in vis if f.period_end == cur_pe],
                         f"{security}@{cur_pe} accruals(current)")
    prev = select_version([f for f in vis if f.period_end == prev_pe],
                          f"{security}@{prev_pe} accruals(prior)")
    if cur.currency != prev.currency:
        raise PITError(f"{security}: currency mismatch {prev.currency} vs {cur.currency}")

    d = {}
    for n in _BALANCE[:-1]:
        a, b = scaled(getattr(cur, n), cur.unit_scale), scaled(getattr(prev, n), prev.unit_scale)
        if a is None or b is None:
            return None
        d[n] = a - b
    dep = scaled(cur.depreciation_amortization, cur.unit_scale)
    ta_c = scaled(cur.total_assets, cur.unit_scale)
    ta_p = scaled(prev.total_assets, prev.unit_scale)
    if dep is None or ta_c is None or ta_p is None:
        return None
    avg_ta = (ta_c + ta_p) / 2.0
    if avg_ta <= 0:
        return None
    acc = ((d["current_assets"] - d["cash"])
           - (d["current_liabilities"] - d["short_term_debt"] - d["tax_payable"])
           - dep)
    return acc / avg_ta
