"""Offline, point-in-time factor primitives; no live execution or validated alpha."""
from .contracts import Evidence, FactorObservation, blocked_hypothesis, finite, nonempty, utc
from .amihud import LiquidityDay, amihud
from .residual import ResidualMomentum, ReturnDay, residual_momentum
from .dimension import DimensionStats, dimension_from_returns, effective_dimension
from .inputs import ExposureEdge, FactKey, PublicIntent, TimedFact, asof_fact
from .quality_microstructure import (
    DailyReturnObs,
    OHLCVDay,
    clv_accumulation_pit,
    gross_profitability_pit,
    max_lottery_pit,
)
from .shadow import shadow_snapshot

__all__ = [
    "DailyReturnObs",
    "DimensionStats",
    "Evidence",
    "ExposureEdge",
    "FactKey",
    "FactorObservation",
    "LiquidityDay",
    "OHLCVDay",
    "PublicIntent",
    "ResidualMomentum",
    "ReturnDay",
    "TimedFact",
    "amihud",
    "asof_fact",
    "blocked_hypothesis",
    "clv_accumulation_pit",
    "dimension_from_returns",
    "effective_dimension",
    "finite",
    "gross_profitability_pit",
    "max_lottery_pit",
    "nonempty",
    "residual_momentum",
    "shadow_snapshot",
    "utc",
]
