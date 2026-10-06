"""Frozen initial operational policies; no changes to signal mathematics."""
from __future__ import annotations

import math

from .quality import QualityPolicy


US_PUBLIC_5M_POLICY = QualityPolicy(
    policy_id="us_public_5m_v1", interval_ms=300_000,
    response_max_age_ms=120_000, publication_grace_ms=60_000,
    future_clock_tolerance_ms=5_000, min_completed_bars=2,
    min_rvol_sessions=20, bar_age_limit_ms=None,
)


def minute_policy(interval_ms: int) -> QualityPolicy:
    """Keep the existing 1m/5m/15m entrypoint's 61-bar and age contract."""
    if type(interval_ms) is not int or interval_ms not in {60_000, 300_000, 900_000}:
        raise ValueError("minute interval must be 60000, 300000, or 900000 ms")
    return QualityPolicy(
        policy_id=f"minute_ma5_v1_{interval_ms}ms", interval_ms=interval_ms,
        response_max_age_ms=36_000, publication_grace_ms=15_000,
        future_clock_tolerance_ms=5_000, min_completed_bars=61,
        min_rvol_sessions=0, bar_age_limit_ms=interval_ms + 15_000,
    )


def ibkr_quote_policy(snapshot_seconds: float = 2.0) -> QualityPolicy:
    """Quote callers consume response_max_age_ms, not the bar evaluator."""
    error = "snapshot period must be finite positive whole milliseconds"
    if not isinstance(snapshot_seconds, (int, float)) or isinstance(snapshot_seconds, bool):
        raise ValueError(error)
    try:
        milliseconds = snapshot_seconds * 1000
        if not math.isfinite(milliseconds) or milliseconds <= 0 or milliseconds != int(milliseconds):
            raise ValueError(error)
        interval = int(milliseconds)
    except OverflowError:
        raise ValueError(error) from None
    return QualityPolicy(
        policy_id=f"ibkr_quote_v1_{interval}ms", interval_ms=interval,
        response_max_age_ms=3 * interval, publication_grace_ms=0,
        future_clock_tolerance_ms=5_000, min_completed_bars=0,
        min_rvol_sessions=0, bar_age_limit_ms=None,
    )


MINUTE_POLICY = minute_policy(60_000)
IBKR_QUOTE_POLICY = ibkr_quote_policy()
