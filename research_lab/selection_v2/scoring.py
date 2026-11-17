"""Cross-sectional multi-factor ranker on PIT/causal daily features.

Scope: EXPERIMENTAL_RESEARCH_HYPOTHESIS (outside locked Factor Contract v1).
- Missing factor values stay None and are NEVER imputed with 0.0 or 0.5.
- Residual momentum uses 1-step-ahead OOS forecast errors with intercept
  estimated and subtracted OOS (passes Validation Kernel v0 FM-003 gate).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from research_lab.data import Panel, finite
from research_lab.factor_mining.inputs import TimedFact
from research_lab.factor_mining.quality_microstructure import gross_profitability_pit

ET = ZoneInfo("America/New_York")

RESIDUAL_MOMENTUM_KERNEL_DECLARATION = {
    "est_has_intercept": True,
    "oos_subtracts_alpha": True,
    "label": "custom.residual_momentum_oos_forecast_error",
}

# True => higher raw value ranks higher; False => lower raw value ranks higher.
FACTOR_ORIENTATION: dict[str, bool] = {
    "residual_momentum": True,
    "clv_accumulation": True,
    "max_lottery": False,
    "amihud_illiquidity": False,
    "gross_profitability": True,
}

SCORE_PROFILES: dict[str, dict[str, float]] = {
    "balanced_v2": {
        "residual_momentum": 0.35,
        "clv_accumulation": 0.20,
        "max_lottery": 0.20,
        "amihud_illiquidity": 0.10,
        "gross_profitability": 0.15,
    },
    "equal_weight": {
        "residual_momentum": 0.20,
        "clv_accumulation": 0.20,
        "max_lottery": 0.20,
        "amihud_illiquidity": 0.20,
        "gross_profitability": 0.20,
    },
    "price_microstructure_v2": {
        "residual_momentum": 0.40,
        "clv_accumulation": 0.25,
        "max_lottery": 0.20,
        "amihud_illiquidity": 0.15,
    },
    "only_residual_momentum": {"residual_momentum": 1.0},
    "only_clv_accumulation": {"clv_accumulation": 1.0},
    "only_low_lottery": {"max_lottery": 1.0},
    "only_liquidity": {"amihud_illiquidity": 1.0},
}


@dataclass(frozen=True)
class ScoreCard:
    symbol: str
    composite_score: float | None
    status: str
    profile: str
    raw_factors: tuple[tuple[str, float | None], ...]
    factor_percentiles: tuple[tuple[str, float | None], ...]
    active_weights: tuple[tuple[str, float], ...]
    quality_flags: tuple[str, ...] = ()
    validation_status: str = "NEEDS_BACKTEST"
    official_signal_eligible: bool = False

    def percentile_dict(self) -> dict[str, float | None]:
        return dict(self.factor_percentiles)

    def raw_dict(self) -> dict[str, float | None]:
        return dict(self.raw_factors)


def cross_sectional_percentile(
    values: Mapping[str, float | None],
    *,
    higher_is_better: bool = True,
) -> dict[str, float | None]:
    """Deterministic mid-rank percentile in (0, 1]; None remains None."""
    if type(higher_is_better) is not bool:
        raise ValueError("higher_is_better must be boolean")
    out: dict[str, float | None] = {}
    present: list[tuple[float, str]] = []
    for sym in sorted(values):
        val = values[sym]
        if val is None:
            out[sym] = None
            continue
        if not finite(val):
            raise ValueError(f"nonfinite value for {sym}; missing must be None")
        oriented = float(val) if higher_is_better else -float(val)
        present.append((oriented, sym))

    present.sort(key=lambda item: (item[0], item[1]))
    n = len(present)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        mid_rank = (i + 1 + j + 1) / 2.0
        pct = mid_rank / n
        for k in range(i, j + 1):
            out[present[k][1]] = pct
        i = j + 1
    return out


def _resolve_weights(profile: str, custom_weights: Mapping[str, float] | None) -> dict[str, float]:
    if custom_weights is not None:
        if not custom_weights:
            raise ValueError("custom_weights must be nonempty")
        resolved: dict[str, float] = {}
        for k in sorted(custom_weights):
            if k not in FACTOR_ORIENTATION:
                raise ValueError(f"unknown factor key {k!r}")
            w = custom_weights[k]
            if not finite(w) or w <= 0:
                raise ValueError(f"weight for {k!r} must be positive and finite")
            resolved[k] = float(w)
        total = math.fsum(resolved.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError("custom_weights must sum to 1.0")
        return resolved
    if profile not in SCORE_PROFILES:
        raise ValueError(f"unknown score profile {profile!r}")
    return dict(SCORE_PROFILES[profile])


def score_cross_section(
    raw_by_symbol: Mapping[str, Mapping[str, float | None]],
    *,
    profile: str = "balanced_v2",
    custom_weights: Mapping[str, float] | None = None,
    require_fundamentals: bool = False,
) -> dict[str, ScoreCard]:
    """Score a single date's cross-section without zero-imputing missing inputs."""
    if type(require_fundamentals) is not bool:
        raise ValueError("require_fundamentals must be boolean")
    weights = _resolve_weights("custom" if custom_weights is not None else profile, custom_weights)
    profile_label = "custom" if custom_weights is not None else profile
    symbols = sorted(raw_by_symbol)

    pct_by_factor: dict[str, dict[str, float | None]] = {}
    for factor in sorted(FACTOR_ORIENTATION):
        col = {sym: raw_by_symbol[sym].get(factor) for sym in symbols}
        pct_by_factor[factor] = cross_sectional_percentile(
            col, higher_is_better=FACTOR_ORIENTATION[factor]
        )

    required_price_sleeves = [f for f in sorted(weights) if f != "gross_profitability"]
    cards: dict[str, ScoreCard] = {}

    for sym in symbols:
        raw_tuple = tuple((f, raw_by_symbol[sym].get(f)) for f in sorted(FACTOR_ORIENTATION))
        pct_tuple = tuple((f, pct_by_factor[f][sym]) for f in sorted(FACTOR_ORIENTATION))

        if any(pct_by_factor[f][sym] is None for f in required_price_sleeves):
            cards[sym] = ScoreCard(
                symbol=sym,
                composite_score=None,
                status="INCOMPLETE_REQUIRED_FACTORS",
                profile=profile_label,
                raw_factors=raw_tuple,
                factor_percentiles=pct_tuple,
                active_weights=(),
            )
            continue

        active = dict(weights)
        flags: list[str] = []
        if "gross_profitability" in active and pct_by_factor["gross_profitability"][sym] is None:
            if require_fundamentals:
                cards[sym] = ScoreCard(
                    symbol=sym,
                    composite_score=None,
                    status="BLOCKED_MISSING_FUNDAMENTALS",
                    profile=profile_label,
                    raw_factors=raw_tuple,
                    factor_percentiles=pct_tuple,
                    active_weights=(),
                )
                continue
            del active["gross_profitability"]
            if not active:
                cards[sym] = ScoreCard(
                    symbol=sym,
                    composite_score=None,
                    status="NO_ACTIVE_SLEEVES",
                    profile=profile_label,
                    raw_factors=raw_tuple,
                    factor_percentiles=pct_tuple,
                    active_weights=(),
                )
                continue
            denom = math.fsum(active.values())
            active = {f: w / denom for f, w in sorted(active.items())}
            flags.append("FUNDAMENTALS_UNAVAILABLE_WEIGHTS_RENORMALIZED")

        total = 100.0 * math.fsum(active[f] * float(pct_by_factor[f][sym]) for f in sorted(active))
        cards[sym] = ScoreCard(
            symbol=sym,
            composite_score=round(total, 8),
            status="SCORED",
            profile=profile_label,
            raw_factors=raw_tuple,
            factor_percentiles=pct_tuple,
            active_weights=tuple((f, active[f]) for f in sorted(active)),
            quality_flags=tuple(flags),
        )
    return cards


def _return_series(bars_by_date: Mapping[str, object], dates: Sequence[str]) -> np.ndarray:
    n = len(dates)
    out = np.full(n, np.nan, dtype=float)
    for i in range(1, n):
        prev_b = bars_by_date.get(dates[i - 1])
        cur_b = bars_by_date.get(dates[i])
        if (prev_b is not None and cur_b is not None
                and getattr(prev_b, "valid", False) and getattr(cur_b, "valid", False)
                and prev_b.close > 0 and cur_b.close > 0):
            out[i] = cur_b.close / prev_b.close - 1.0
    return out


def _rolling_oos_residual_momentum(
    stock_rets: np.ndarray,
    mkt_rets: np.ndarray,
    sec_rets: np.ndarray | None,
    *,
    fit_window: int,
    signal_window: int,
) -> list[float | None]:
    """Exact 1-step-ahead OOS forecast-error momentum in O(T) regressions."""
    n = len(stock_rets)
    oos_err = np.full(n, np.nan, dtype=float)
    two_factor = sec_rets is not None
    expected_rank = 3 if two_factor else 2
    ones = np.ones(fit_window, dtype=float)

    for m in range(fit_window + 1, n):
        s_slice = stock_rets[m - fit_window: m]
        m_slice = mkt_rets[m - fit_window: m]
        if not (np.isfinite(s_slice).all() and np.isfinite(m_slice).all()):
            continue
        if not (math.isfinite(stock_rets[m]) and math.isfinite(mkt_rets[m])):
            continue
        if two_factor:
            sec_slice = sec_rets[m - fit_window: m]
            if not (np.isfinite(sec_slice).all() and math.isfinite(sec_rets[m])):
                continue
            x = np.column_stack((ones, m_slice, sec_slice - m_slice))
            cur_x = np.array([1.0, mkt_rets[m], sec_rets[m] - mkt_rets[m]], dtype=float)
        else:
            x = np.column_stack((ones, m_slice))
            cur_x = np.array([1.0, mkt_rets[m]], dtype=float)

        try:
            with np.errstate(all="raise"):
                coeffs, _, rank, _ = np.linalg.lstsq(x, s_slice, rcond=None)
                if rank != expected_rank or np.linalg.cond(x) > 1e12:
                    continue
                err = float(stock_rets[m] - cur_x @ coeffs)
                if math.isfinite(err):
                    oos_err[m] = err
        except (FloatingPointError, np.linalg.LinAlgError, OverflowError):
            continue

    scores: list[float | None] = [None] * n
    required_index = fit_window + signal_window
    for i in range(required_index, n):
        window_errs = oos_err[i - signal_window + 1: i + 1]
        if not np.isfinite(window_errs).all():
            continue
        sd = float(np.std(window_errs, ddof=1))
        ref_scale = float(np.max(np.abs(stock_rets[i - required_index + 1: i + 1])))
        tol = 100.0 * float(np.finfo(float).eps) * max(1.0, ref_scale)
        if sd <= tol:
            continue
        val = float(np.sum(window_errs) / sd)
        if math.isfinite(val):
            scores[i] = val
    return scores


def compute_panel_factors(
    panel: Panel,
    *,
    fit_window: int = 60,
    signal_window: int = 20,
    amihud_window: int = 20,
    max_window: int = 21,
    clv_window: int = 20,
    market_symbol: str = "QQQ",
    sector_symbol: str | None = "SOXX",
    fundamental_facts: Sequence[TimedFact] | None = None,
    fundamental_periods: Mapping[str, Sequence[tuple[date, date, str]]] | None = None,
) -> dict[str, dict[str, dict[str, float | str | None]]]:
    """Compute causal daily factor inputs per stock and session close on a Panel."""
    for name, val, lo in [
        ("fit_window", fit_window, 10),
        ("signal_window", signal_window, 5),
        ("amihud_window", amihud_window, 10),
        ("max_window", max_window, 10),
        ("clv_window", clv_window, 10),
    ]:
        if type(val) is not int or val < lo:
            raise ValueError(f"{name} must be an integer >= {lo}")

    dates = panel.dates
    if market_symbol not in panel.bars:
        raise ValueError(f"market benchmark {market_symbol!r} missing from panel")

    use_sector = (
        sector_symbol is not None
        and sector_symbol in panel.bars
        and sector_symbol != market_symbol
    )
    excluded = {market_symbol, "QQQ", "SOXX"}
    if sector_symbol:
        excluded.add(sector_symbol)

    mkt_rets = _return_series(panel.bars[market_symbol], dates)
    sec_rets = _return_series(panel.bars[sector_symbol], dates) if use_sector else None
    model_basis = "MARKET_AND_SECTOR_OOS" if use_sector else "MARKET_ONLY_OOS"

    min_amihud = math.ceil(0.8 * amihud_window)
    min_max_obs = max(10, math.ceil(0.7 * max_window))
    min_clv = math.ceil(0.8 * clv_window)
    fact_tuple = tuple(fundamental_facts) if fundamental_facts else ()

    out: dict[str, dict[str, dict[str, float | str | None]]] = {}
    for sym in sorted(panel.bars):
        if sym in excluded:
            continue
        by_date = panel.bars[sym]
        seq = [by_date.get(d) for d in dates]
        s_rets = _return_series(by_date, dates)
        rm_series = _rolling_oos_residual_momentum(
            s_rets, mkt_rets, sec_rets, fit_window=fit_window, signal_window=signal_window
        )

        sym_records: dict[str, dict[str, float | str | None]] = {}
        periods = list(fundamental_periods.get(sym, ())) if fundamental_periods else []

        for i, d in enumerate(dates):
            amihud_val: float | None = None
            if i >= amihud_window:
                ratios = []
                for m in range(i - amihud_window + 1, i + 1):
                    b = seq[m]
                    r = s_rets[m]
                    if b is not None and b.valid and b.volume > 0 and math.isfinite(r):
                        dv = b.close * b.volume
                        if dv > 0 and math.isfinite(dv):
                            rv = abs(float(r)) / dv
                            if math.isfinite(rv):
                                ratios.append(rv)
                if len(ratios) >= min_amihud:
                    scale = max(ratios)
                    amihud_val = scale * (math.fsum(x / scale for x in ratios) / len(ratios)) if scale else 0.0

            max_val: float | None = None
            if i >= max_window:
                r_vals = [float(s_rets[m]) for m in range(i - max_window + 1, i + 1) if math.isfinite(s_rets[m])]
                if len(r_vals) >= min_max_obs:
                    max_val = max(r_vals)

            clv_val: float | None = None
            if i >= clv_window - 1:
                w_clv, vols = [], []
                for m in range(i - clv_window + 1, i + 1):
                    b = seq[m]
                    if b is not None and b.valid and b.volume > 0:
                        span = b.high - b.low
                        c_pos = ((b.close - b.low) - (b.high - b.close)) / span if span > 0 else 0.0
                        w_clv.append(c_pos * b.volume)
                        vols.append(b.volume)
                tot_v = math.fsum(vols) if vols else 0.0
                if len(vols) >= min_clv and tot_v > 0:
                    clv_val = min(max(math.fsum(w_clv) / tot_v, -1.0), 1.0)

            gpa_val: float | None = None
            if fact_tuple and periods:
                decision_dt = datetime.fromisoformat(d + "T16:30:00").replace(tzinfo=ET)
                eligible_periods = [p for p in periods if p[1].isoformat() <= d]
                if eligible_periods:
                    p_start, p_end, f_label = max(eligible_periods, key=lambda item: item[1])
                    obs = gross_profitability_pit(
                        fact_tuple,
                        entity=sym,
                        period_start=p_start,
                        period_end=p_end,
                        fiscal_period=f_label,
                        decision_time=decision_dt,
                    )
                    if obs.usable:
                        gpa_val = obs.value

            sym_records[d] = {
                "residual_momentum": rm_series[i],
                "clv_accumulation": clv_val,
                "max_lottery": max_val,
                "amihud_illiquidity": amihud_val,
                "gross_profitability": gpa_val,
                "residual_model_basis": model_basis,
            }
        out[sym] = sym_records
    return out


def score_panel_by_date(
    panel_factors: Mapping[str, Mapping[str, Mapping[str, float | str | None]]],
    dates: Sequence[str],
    *,
    profile: str = "balanced_v2",
    custom_weights: Mapping[str, float] | None = None,
    require_fundamentals: bool = False,
) -> dict[str, dict[str, ScoreCard]]:
    """Produce cross-sectional ScoreCards for every date in ``dates``."""
    symbols = sorted(panel_factors)
    by_date: dict[str, dict[str, ScoreCard]] = {}
    for d in dates:
        raw_cross: dict[str, dict[str, float | None]] = {}
        for sym in symbols:
            rec = panel_factors[sym].get(d, {})
            raw_cross[sym] = {
                f: (rec.get(f) if isinstance(rec.get(f), (int, float)) else None)  # type: ignore[misc]
                for f in FACTOR_ORIENTATION
            }
        by_date[d] = score_cross_section(
            raw_cross,
            profile=profile,
            custom_weights=custom_weights,
            require_fundamentals=require_fundamentals,
        )
    return by_date
