"""Gross profitability (GP/A), Novy-Marx (2013) JFE 108(1) 1-28.

factor = (Revenue - COGS) / Total assets, latest filing *known* at as_of.
Missing revenue/COGS/assets => None (never 0). Stale filings => None.
Restatements: each version is a separate fact with its own known_at; only
versions with known_at <= as_of are visible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from .common import finite_or_none, require_provenance


@dataclass(frozen=True)
class FundamentalFact:
    security: str
    period_end: date
    known_at: date          # public availability (filing acceptance / announcement date)
    source: str
    revenue: Optional[float] = None
    cogs: Optional[float] = None
    total_assets: Optional[float] = None


def gross_profitability(
    facts: Iterable[FundamentalFact],
    security: str,
    as_of: date,
    max_age_days: int = 548,
) -> Optional[float]:
    visible = []
    for f in facts:
        require_provenance(f.source, f.known_at, f"{f.security}@{f.period_end}")
        if f.security == security and f.known_at <= as_of:
            visible.append(f)
    if not visible:
        return None
    best = max(visible, key=lambda f: (f.period_end, f.known_at))
    if (as_of - best.period_end).days > max_age_days:
        return None
    rev, cogs, assets = (finite_or_none(best.revenue), finite_or_none(best.cogs),
                         finite_or_none(best.total_assets))
    if rev is None or cogs is None or assets is None or assets <= 0:
        return None
    return (rev - cogs) / assets
