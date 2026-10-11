"""Deterministic reference implementations of candidate factors (NOT_VALIDATED).

Research only. No trading actions. Nothing here is evidence of alpha.
"""
from .common import (
    AmbiguousVersionError, PITError, TradingCalendarError, cross_sectional_rank,
)
from .gross_profitability import FundamentalFact, gross_profitability
from .accruals import BalanceSheetFact, sloan_accruals
from .max_lottery import DailyReturn, max_lottery

__all__ = [
    "PITError", "AmbiguousVersionError", "TradingCalendarError", "cross_sectional_rank", "FundamentalFact", "gross_profitability",
    "BalanceSheetFact", "sloan_accruals", "DailyReturn", "max_lottery",
]
