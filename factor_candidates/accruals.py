"""Balance-sheet accruals, Sloan (1996) The Accounting Review 71(3) 289-315.

Definition VERIFIED against the original full text (JSTOR scan, pp. 289-315;
see docs/research/evidence/pr48_sources.md):

  Accruals = (dCA - dCash) - (dCL - dSTD - dTP) - Dep
  dCA   change in current assets            (Compustat item 4)
  dCash change in cash / cash equivalents   (item 1; see CASH SCOPE below)
  dCL   change in current liabilities       (item 5)
  dSTD  change in debt in current liabilities (item 34)
  dTP   change in income taxes payable      (item 71)
  Dep   depreciation AND amortization expense (item 14), SUBTRACTED
  scaled by AVERAGE total assets (beginning and end of year book value, item 6).

CASH SCOPE. The 1996 text defines dCash as "change in cash/cash equivalents
(Compustat item 1)"; the Compustat item-1 data definition is cash AND
short-term investments (the latter is Compustat documentation, NOT verified
in this repo). This implementation therefore removes cash + short_term_investments
from current assets. Missing is not zero: short_term_investments must be
supplied for BOTH statements, or the statement must carry
no_short_term_investments=True (an explicit assertion that the company reports
none); a missing value without the flag raises PITError. A value together with
the flag is a contradiction and raises. A reported 0.0 is a real value.

D&A SCOPE. The Sloan equation defines Dep as "depreciation and amortization
expense (Compustat item 14)", while the prose says "depreciation expense". The
field here is total D&A for the period; callers must document which XBRL tags
(and any accretion/other lines) were summed. Compustat-item -> XBRL-tag mapping
is UNVERIFIED, so this factor is NOT usable for scoring stocks.

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
    PITError, as_of_utc, calendar_date, scaled, select_version, validate_filing_basis,
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
    cash: Optional[float] = None                         # cash and cash equivalents
    current_liabilities: Optional[float] = None
    short_term_debt: Optional[float] = None
    tax_payable: Optional[float] = None
    depreciation_amortization: Optional[float] = None   # D&A expense for the period
    total_assets: Optional[float] = None
    short_term_investments: Optional[float] = None       # marketable securities / ST investments
    no_short_term_investments: bool = False              # explicit "company reports none"


def _cash_and_sti(f: BalanceSheetFact) -> Optional[float]:
    """Cash + short-term investments in currency units; None only if CASH is missing."""
    label = f"{f.security}@{f.period_end} cash/short-term investments"
    if not isinstance(f.no_short_term_investments, bool):
        raise PITError(f"{label}: no_short_term_investments must be a bool")
    sti = scaled(f.short_term_investments, f.unit_scale)
    if f.no_short_term_investments:
        if f.short_term_investments is not None:
            raise PITError(f"{label}: short_term_investments given together with "
                           "no_short_term_investments=True (contradiction)")
        sti = 0.0
    elif sti is None:
        raise PITError(f"{label}: short_term_investments missing; supply a value or set "
                       "no_short_term_investments=True explicitly (missing is not zero)")
    cash = scaled(f.cash, f.unit_scale)
    return None if cash is None else cash + sti


_BALANCE = ("current_assets", "current_liabilities", "short_term_debt",
            "tax_payable", "total_assets")


def sloan_accruals(
    facts: Iterable[BalanceSheetFact],
    security: str,
    as_of: datetime,
    max_age_days: int = 548,
    prior_gap_days: tuple[int, int] = (300, 430),
    period_type: str = "annual",
    calendar_tz: str = "America/New_York",
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
    if (calendar_date(as_of, calendar_tz) - cur_pe).days > max_age_days:
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
    cash_c, cash_p = _cash_and_sti(cur), _cash_and_sti(prev)
    if cash_c is None or cash_p is None:
        return None
    d["cash"] = cash_c - cash_p
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
