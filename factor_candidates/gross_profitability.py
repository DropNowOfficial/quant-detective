"""Gross profitability (GP/A), Novy-Marx (2013) JFE 108(1) 1-28.

GP/A = gross profit / total assets, where gross profit = revenue - COGS.
(It is NOT gross margin, which would divide by revenue.) The Novy-Marx full
text has not been read in this repo; the definition is the standard one and the
revenue-COGS construction is a reference choice (contract status: partial).

Facts carry period_type, currency, unit_scale, statement_ref and revision.
Only records with known_at <= as_of are visible. Among visible records for the
latest period_end, select_version() chooses the governing version and raises
AmbiguousVersionError when it cannot disambiguate. Missing inputs => None.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from .common import (
    as_of_utc, finite_or_none, scaled, select_version, validate_filing_basis,
)


@dataclass(frozen=True)
class FundamentalFact:
    security: str
    period_end: date
    known_at: datetime       # timezone-aware public availability (e.g. SEC acceptance time)
    source: str
    period_type: str         # "annual" | "quarterly" | "ttm"
    currency: str            # ISO-4217 style, e.g. "USD"
    unit_scale: float        # multiply reported numbers by this to get currency units
    statement_ref: str       # accession number / form / original statement reference
    revision: int            # 0 = original, 1.. = restatements/amendments
    revenue: Optional[float] = None
    cogs: Optional[float] = None
    total_assets: Optional[float] = None


def gross_profitability(
    facts: Iterable[FundamentalFact],
    security: str,
    as_of: datetime,
    max_age_days: int = 548,
    period_type: str = "annual",
) -> Optional[float]:
    as_of = as_of_utc(as_of)
    facts = list(facts)
    for f in facts:
        validate_filing_basis(f)
    visible = [f for f in facts
               if f.security == security and f.period_type == period_type
               and f.known_at <= as_of]
    if not visible:
        return None
    latest_pe = max(f.period_end for f in visible)
    if (as_of.date() - latest_pe).days > max_age_days:
        return None
    best = select_version([f for f in visible if f.period_end == latest_pe],
                          f"{security}@{latest_pe} GP/A")
    rev, cogs, assets = (scaled(best.revenue, best.unit_scale),
                         scaled(best.cogs, best.unit_scale),
                         scaled(best.total_assets, best.unit_scale))
    if rev is None or cogs is None or assets is None or assets <= 0:
        return None
    return (rev - cogs) / assets
