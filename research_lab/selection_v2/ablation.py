"""Controlled 4-stage ablation harness isolating each Quant Detective 2.0 component.

Stages compared on identical dates, cash rules, and transaction-cost grid:
- S0_legacy_ma5_3d: Original lagged-MA5 pullback + raw RS20 rank + fixed 3d close exit.
- S1_ma5_multifactor_3d: Same MA5 setup, ranked & gated by Multi-Factor Cross-Sectional Score.
- S2_three_engine_multifactor_3d: 3-Engine Matrix (Pullback v2 + Episodic Pivot + VCP) + Regime Gate + Multi-Factor Score + fixed 3d close exit.
- S3_three_engine_multifactor_asymmetric: Full 2.0 stack adding ATR volatility-parity sizing, sub-industry concentration cap, and causal Chandelier/ATR/MA10 exits.

All outputs remain DIAGNOSTIC_ONLY (validated_metrics=None; Vector never self-certifies).
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import math
from typing import Mapping, Sequence

from research_lab.data import Panel
from research_lab.experiment import buy_hold, equal_weight, historical_members
from research_lab.features import compute_features
from research_lab.metrics import paired_interval, summarize
from research_lab.portfolio import Config as LegacyConfig, simulate as legacy_simulate
from validation_kernel.kernel_v0 import gate_residual_momentum_intercept

from .engines import build_v2_signals, compute_regime_timeline, compute_three_engine_features
from .portfolio_v2 import DEFAULT_SUBINDUSTRY_MAP, PortfolioV2Config, simulate_v2
from .scoring import (
    RESIDUAL_MOMENTUM_KERNEL_DECLARATION,
    ScoreCard,
    compute_panel_factors,
    score_panel_by_date,
)


def _canonical_json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _content_hash(payload: Mapping[str, object]) -> str:
    body = {k: v for k, v in payload.items() if k != "ablation_run_id"}
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()[:24]


def _clean_metrics(metrics: Mapping[str, object]) -> dict:
    return {k: v for k, v in metrics.items() if k not in {"drawdown", "daily_returns"}}


def _build_s1_ma5_multifactor_signals(
    ma5_features: Mapping[str, Mapping[str, Mapping]],
    scores_by_date: Mapping[str, Mapping[str, ScoreCard]],
    dates: Sequence[str],
    min_score: float = 55.0,
) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {d: [] for d in dates}
    for sym in sorted(ma5_features):
        prev = False
        for d in dates:
            f = ma5_features[sym][d]
            card = scores_by_date.get(d, {}).get(sym)
            score = card.composite_score if card is not None else None
            qualifies = bool(f["eligible"] and score is not None and math.isfinite(score) and score >= min_score)
            if qualifies and not prev:
                out[d].append({
                    "symbol": sym,
                    "engine": "LEGACY_MA5_MULTIFACTOR",
                    "composite_score": score,
                    "adv20": f["adv20"],
                    "atr14": f["atr5"],
                })
            prev = qualifies
    for d in dates:
        out[d].sort(key=lambda x: (-x["composite_score"], -x["adv20"], x["symbol"]))
    return out


def _engine_breakdown(trades: Sequence[Mapping]) -> dict[str, dict]:
    grouped: dict[str, list[Mapping]] = {}
    for t in trades:
        eng = str(t.get("engine", "LEGACY_MA5"))
        grouped.setdefault(eng, []).append(t)
    summary: dict[str, dict] = {}
    for eng in sorted(grouped):
        rows = grouped[eng]
        wins = [r["pnl"] for r in rows if r["pnl"] > 0]
        summary[eng] = {
            "trades": len(rows),
            "total_pnl": math.fsum(r["pnl"] for r in rows),
            "win_rate": len(wins) / len(rows) if rows else None,
        }
    return summary


def run_ablation_matrix(
    panel: Panel,
    *,
    start: str | None = None,
    end: str | None = None,
    fit_window: int = 60,
    signal_window: int = 20,
    profile: str = "balanced_v2",
    cost_bps_grid: Sequence[float] = (10.0, 25.0, 50.0),
    sector_map: Mapping[str, str] | None = DEFAULT_SUBINDUSTRY_MAP,
    fundamental_facts=None,
    fundamental_periods: Mapping[str, Sequence[tuple[date, date, str]]] | None = None,
) -> dict:
    """Run controlled S0 -> S1 -> S2 -> S3 ablation matrix and statistical comparisons."""
    # Gate check: verify residual momentum declaration satisfies Validation Kernel v0 FM-003
    kernel_err = gate_residual_momentum_intercept(**RESIDUAL_MOMENTUM_KERNEL_DECLARATION)
    if kernel_err is not None:
        raise ValueError(f"Validation Kernel blocked residual momentum specification: {kernel_err}")

    dates = [d for d in panel.dates if (start is None or d >= start) and (end is None or d <= end)]
    if len(dates) < 25:
        raise ValueError("ablation interval requires at least 25 sessions for block-interval diagnostics")

    # 1. Compute legacy MA5 features & signals (S0)
    legacy_feats = compute_features(panel, "primary")
    from research_lab.features import build_signals as build_legacy_signals
    s0_signals = build_legacy_signals(legacy_feats, panel.dates)

    # 2. Compute PIT/causal multi-factor scores across panel
    panel_factors = compute_panel_factors(
        panel,
        fit_window=fit_window,
        signal_window=signal_window,
        fundamental_facts=fundamental_facts,
        fundamental_periods=fundamental_periods,
    )
    scores_by_date = score_panel_by_date(panel_factors, panel.dates, profile=profile)

    # 3. Build S1 signals (Legacy MA5 setup + Multi-Factor Score)
    s1_signals = _build_s1_ma5_multifactor_signals(legacy_feats, scores_by_date, panel.dates)

    # 4. Build S2/S3 signals (Three-Engine Matrix + Regime Gate + Multi-Factor Score)
    regimes = compute_regime_timeline(panel)
    engine_feats = compute_three_engine_features(panel)
    v2_signals = build_v2_signals(engine_feats, scores_by_date, regimes, panel.dates)

    stages_by_cost: dict[str, dict[str, dict]] = {}
    daily_returns_10bps: dict[str, list[float]] = {}

    for cost in cost_bps_grid:
        c_key = f"{int(cost) if float(cost).is_integer() else cost}bps"
        stages_by_cost[c_key] = {}

        # S0: Legacy MA5 + RS20 + 3d close exit
        r0 = legacy_simulate(panel, s0_signals, LegacyConfig(slots=5, hold=3, cost_bps=cost), start=dates[0], end=dates[-1])
        m0 = summarize(r0["nav"], trades=r0["trades"])

        # S1: Legacy MA5 + Multi-Factor Score + 3d close exit
        r1 = simulate_v2(
            panel, s1_signals,
            PortfolioV2Config(
                slots=5, max_per_sector=5, cost_bps=cost,
                sizing_mode="equal_slot", exit_mode="fixed_hold_close",
                hold=3, dynamic_regime_slots=False,
            ),
            sector_map=sector_map, start=dates[0], end=dates[-1],
        )
        m1 = summarize(r1["nav"], trades=r1["trades"])

        # S2: Three-Engine + Regime + Multi-Factor Score + 3d close exit
        r2 = simulate_v2(
            panel, v2_signals,
            PortfolioV2Config(
                slots=5, max_per_sector=5, cost_bps=cost,
                sizing_mode="equal_slot", exit_mode="fixed_hold_close",
                hold=3, dynamic_regime_slots=True,
            ),
            sector_map=sector_map, regimes=regimes, start=dates[0], end=dates[-1],
        )
        m2 = summarize(r2["nav"], trades=r2["trades"])

        # S3: Full Quant Detective 2.0 (3-Engine + Multi-Factor + Sector Cap + ATR Vol-Parity + Asymmetric Exits)
        r3 = simulate_v2(
            panel, v2_signals,
            PortfolioV2Config(
                slots=5, max_per_sector=2, cost_bps=cost,
                sizing_mode="atr_volatility_parity", exit_mode="asymmetric_next_open",
                max_hold=20, min_trend_hold=3, stop_atr_mult=1.5, trail_atr_mult=2.5,
                dynamic_regime_slots=True,
            ),
            sector_map=sector_map, regimes=regimes, start=dates[0], end=dates[-1],
        )
        m3 = summarize(r3["nav"], trades=r3["trades"])

        for stage_id, run_res, met in [
            ("S0_legacy_ma5_3d", r0, m0),
            ("S1_ma5_multifactor_3d", r1, m1),
            ("S2_three_engine_multifactor_3d", r2, m2),
            ("S3_three_engine_multifactor_asymmetric", r3, m3),
        ]:
            stages_by_cost[c_key][stage_id] = {
                "status": run_res["status"],
                "validated_metrics": None,
                "metrics": _clean_metrics(met),
                "fees": run_res["fees"],
                "entry_count": run_res["entry_count"],
                "open_positions": run_res["open_positions"],
                "engine_attribution": _engine_breakdown(run_res["trades"]),
            }
            if cost == cost_bps_grid[0]:
                daily_returns_10bps[stage_id] = met["daily_returns"]

    qqq_bh = buy_hold(panel, "QQQ", dates, cost_bps=cost_bps_grid[0])
    ew_bh = equal_weight(panel, historical_members(panel), dates, cost_bps=cost_bps_grid[0])

    comparisons = {
        "S3_vs_S0_legacy": paired_interval(
            daily_returns_10bps["S3_three_engine_multifactor_asymmetric"],
            daily_returns_10bps["S0_legacy_ma5_3d"],
        ),
        "S2_vs_S1_engine_gain": paired_interval(
            daily_returns_10bps["S2_three_engine_multifactor_3d"],
            daily_returns_10bps["S1_ma5_multifactor_3d"],
        ),
        "S3_vs_S2_asymmetric_exit_gain": paired_interval(
            daily_returns_10bps["S3_three_engine_multifactor_asymmetric"],
            daily_returns_10bps["S2_three_engine_multifactor_3d"],
        ),
    }
    if qqq_bh["metrics"] is not None:
        comparisons["S3_vs_QQQ"] = paired_interval(
            daily_returns_10bps["S3_three_engine_multifactor_asymmetric"],
            qqq_bh["metrics"]["daily_returns"],
        )

    payload: dict[str, object] = {
        "schema_version": 1,
        "mode": "ABLATION_RESEARCH_DIAGNOSTIC",
        "validation_status": "DIAGNOSTIC_ONLY_NEEDS_UNTOUCHED_OOS",
        "official_signal_eligible": False,
        "kernel_fm003_residual_momentum_gate": "PASS",
        "window": {"start": dates[0], "end": dates[-1], "sessions": len(dates)},
        "score_profile": profile,
        "fit_window": fit_window,
        "signal_window": signal_window,
        "stages_by_cost": stages_by_cost,
        "baselines_primary_cost": {
            "QQQ": _clean_metrics(qqq_bh["metrics"]) if qqq_bh["metrics"] else None,
            "EW": _clean_metrics(ew_bh["metrics"]) if ew_bh["metrics"] else None,
        },
        "paired_block_comparisons": comparisons,
    }
    payload["ablation_run_id"] = _content_hash(payload)
    return payload
