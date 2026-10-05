"""Canonical market-watch universe and discovery configuration.

The fixed symbol list is a fallback/core watchlist only. It must never be
reported as whole-market coverage.
"""
from __future__ import annotations

CORE_FALLBACK_SYMBOLS = (
    "NVDA","TSM","MSFT","AMD","AVGO","MU","INTC","SNDK","MRVL","GLW",
    "GOOG","META","ARM","ASML","AMAT","LRCX","KLAC","ANET","VRT","ORCL",
    "CRCL","MSTR","AMZN","DELL","AAPL","SMCI","CRDO","ALAB","COHR","LITE",
    "AAOI","QCOM","MPWR","MCHP","NXPI","ETN","VST","CEG","HPE","WDC","STX",
)

# Broad context is intentionally not a tech-only QQQ view.
MARKET_CONTEXT_SYMBOLS = (
    "SPY",   # broad large-cap U.S. equity
    "QQQ",   # Nasdaq-100 / growth context
    "IWM",   # small-cap context
    "SOXX",  # semiconductor context
    "NQ=F",  # Nasdaq-100 futures
    "ES=F",  # S&P 500 futures
    "RTY=F", # Russell 2000 futures
)

# Resolve these by display_name from /iserver/scanner/params at runtime.
# Do not guess undocumented scan codes.
IBKR_DISCOVERY_SCAN_NAMES = (
    "Top % Gainers",
    "Top % Losers",
    "Hot Contracts by Volume",
    "Top Trade Count",
    "Top Volume Rate",
)

IBKR_DISCOVERY_INSTRUMENT = "STK"
IBKR_DISCOVERY_LOCATION = "STK.US.MAJOR"

COVERAGE_CORE_FALLBACK = "CORE_FALLBACK"
COVERAGE_IBKR_DYNAMIC = "IBKR_US_MAJOR_DYNAMIC"


LIVE_CAPABILITY_GAPS = (
    "liquidity_gate_policy_not_integrated",
    "market_breadth_not_integrated",
    "sector_industry_relative_strength_not_integrated",
    "news_first_market_discovery_not_integrated",
    "halt_resume_event_feed_not_integrated",
    "block_trade_options_event_feed_not_integrated",
    "reclaim_retest_higher_low_confirmation_not_integrated",
    "momentum_continuation_reentry_not_integrated",
    "historical_3y_confidence_not_integrated",
    "value_price_return_pendulum_not_integrated",
    "corporate_action_normalization_not_audited",
    "exchange_calendar_early_close_not_integrated",
    "external_push_notification_not_configured",
    "stale_state_watchdog_not_integrated",
)
