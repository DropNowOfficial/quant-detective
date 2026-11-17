"""Experimental multi-factor cross-sectional scoring, 3-engine setup matrix, and asymmetric portfolio lab.

Research hypothesis layer only (VALIDATION_STATUS='NEEDS_BACKTEST').
Does not alter locked Factor Contract v1 or live market_data watchers.
"""
from .scoring import (
    FACTOR_ORIENTATION,
    RESIDUAL_MOMENTUM_KERNEL_DECLARATION,
    SCORE_PROFILES,
    ScoreCard,
    compute_panel_factors,
    cross_sectional_percentile,
    score_cross_section,
    score_panel_by_date,
)
from .engines import (
    ENGINES,
    RegimeSnapshot,
    build_v2_signals,
    compute_regime_timeline,
    compute_three_engine_features,
)
from .portfolio_v2 import (
    DEFAULT_SUBINDUSTRY_MAP,
    PortfolioV2Config,
    simulate_v2,
)
from .ablation import run_ablation_matrix

__all__ = [
    "DEFAULT_SUBINDUSTRY_MAP",
    "ENGINES",
    "FACTOR_ORIENTATION",
    "PortfolioV2Config",
    "RESIDUAL_MOMENTUM_KERNEL_DECLARATION",
    "RegimeSnapshot",
    "SCORE_PROFILES",
    "ScoreCard",
    "build_v2_signals",
    "compute_panel_factors",
    "compute_regime_timeline",
    "compute_three_engine_features",
    "cross_sectional_percentile",
    "run_ablation_matrix",
    "score_cross_section",
    "score_panel_by_date",
    "simulate_v2",
]
