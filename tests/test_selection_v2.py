"""Comprehensive verification for Multi-Factor Scoring, 3-Engine Matrix, and Asymmetric Portfolio v2.

Covers:
1. Exact algebraic equivalence between Panel OOS residual momentum and factor_mining.residual
   + Validation Kernel v0 FM-003 gate compliance.
2. Cross-sectional scorer missing != zero invariants, lottery/illiquidity orientation,
   fundamental sleeve fallback vs strict mode, and cross-process PYTHONHASHSEED invariance.
3. AAOI-class catalyst gap-and-go capture via EPISODIC_PIVOT (missed by legacy MA5) and
   COHR-class gap-and-crap fade rejection.
4. VCP volatility-contraction breakout detection vs loose high-volatility chop rejection.
5. Crash-day RISK_OFF regime gate blocking pullback knife-catching.
6. 100 seeded multi-sector causal cash/PnL reconciliation & future-tail prefix invariance.
7. Full 4-stage S0 -> S1 -> S2 -> S3 ablation matrix determinism and governance boundaries.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
import os
import random
import subprocess
import sys

import pytest

from research_lab.data import Bar, Panel
from research_lab.features import compute_features as compute_legacy_features
from research_lab.factor_mining import Evidence, ReturnDay, residual_momentum
from research_lab.selection_v2 import (
    PortfolioV2Config,
    RESIDUAL_MOMENTUM_KERNEL_DECLARATION,
    build_v2_signals,
    compute_panel_factors,
    compute_regime_timeline,
    compute_three_engine_features,
    cross_sectional_percentile,
    run_ablation_matrix,
    score_cross_section,
    score_panel_by_date,
    simulate_v2,
)
from validation_kernel.kernel_v0 import gate_residual_momentum_intercept


def _trading_dates(n: int, start: date = date(2024, 1, 2)) -> list[str]:
    out: list[str] = []
    cur = start
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def test_panel_residual_momentum_matches_factor_mining_primitive_and_passes_kernel_gate():
    assert gate_residual_momentum_intercept(**RESIDUAL_MOMENTUM_KERNEL_DECLARATION) is None

    n_bars = 95  # 94 daily returns -> fit_window=60 + signal_window=20 supported from index 80
    dates = _trading_dates(n_bars)
    qqq_p, soxx_p, acme_p = 100.0, 100.0, 100.0
    bars = {"QQQ": {}, "SOXX": {}, "ACME": {}}
    return_days: list[ReturnDay] = []
    sessions: list[datetime] = []

    for i, d in enumerate(dates):
        if i > 0:
            r_m = 0.008 * math.sin(i * 0.63)
            r_s = r_m + 0.005 * math.cos(i * 0.41)
            r_i = 0.0012 + 1.15 * r_m + 0.65 * (r_s - r_m) + 0.004 * math.sin(i * 1.29)
            qqq_p *= 1.0 + r_m
            soxx_p *= 1.0 + r_s
            acme_p *= 1.0 + r_i
            t_close = datetime.fromisoformat(d + "T20:00:00+00:00")
            ev = Evidence(
                t_close, t_close + timedelta(minutes=1), t_close + timedelta(minutes=1),
                "synthetic://panel-equiv", f"v{i}", "synthetic", "SYNTHETIC_FIXTURE",
            )
            sessions.append(t_close)
            return_days.append(ReturnDay("ACME", t_close, r_i, r_m, r_s, ev, "QQQ", "SOXX"))

        for sym, px in [("QQQ", qqq_p), ("SOXX", soxx_p), ("ACME", acme_p)]:
            bars[sym][d] = Bar(d, px * 0.998, px * 1.005, px * 0.995, px, 2_000_000.0, True)

    panel = Panel(dates, bars, [], [])
    pf = compute_panel_factors(panel, fit_window=60, signal_window=20)
    panel_val = pf["ACME"][dates[-1]]["residual_momentum"]

    prim = residual_momentum(
        return_days,
        entity="ACME",
        sessions=sessions,
        decision_time=sessions[-1] + timedelta(minutes=5),
        market_benchmark="QQQ",
        sector_benchmark="SOXX",
        membership_vintage="test-2024",
        benchmark_available_at=sessions[0] - timedelta(days=2),
        fit_window=60,
        signal_window=20,
    )
    assert prim.observation.status == "VALID"
    assert panel_val == pytest.approx(prim.observation.value, rel=1e-11, abs=1e-11)


def test_cross_sectional_scorer_missing_not_zero_and_orientation():
    raw = {
        "ALPHA_LEADER": {
            "residual_momentum": 2.8,
            "clv_accumulation": 0.45,
            "max_lottery": 0.032,
            "amihud_illiquidity": 1e-10,
            "gross_profitability": None,
        },
        "LOTTERY_PUMP": {
            "residual_momentum": -0.4,
            "clv_accumulation": -0.35,
            "max_lottery": 0.28,
            "amihud_illiquidity": 5e-8,
            "gross_profitability": None,
        },
        "MISSING_RVOL": {
            "residual_momentum": None,
            "clv_accumulation": 0.50,
            "max_lottery": 0.02,
            "amihud_illiquidity": 1e-10,
            "gross_profitability": None,
        },
    }
    cards = score_cross_section(raw, profile="balanced_v2", require_fundamentals=False)
    assert cards["MISSING_RVOL"].composite_score is None
    assert cards["MISSING_RVOL"].status == "INCOMPLETE_REQUIRED_FACTORS"
    assert cards["ALPHA_LEADER"].composite_score > cards["LOTTERY_PUMP"].composite_score
    assert "FUNDAMENTALS_UNAVAILABLE_WEIGHTS_RENORMALIZED" in cards["ALPHA_LEADER"].quality_flags
    assert cards["ALPHA_LEADER"].official_signal_eligible is False

    # Strict fundamental mode blocks when GP/A is absent
    strict_cards = score_cross_section(raw, profile="balanced_v2", require_fundamentals=True)
    assert strict_cards["ALPHA_LEADER"].composite_score is None
    assert strict_cards["ALPHA_LEADER"].status == "BLOCKED_MISSING_FUNDAMENTALS"


def test_percentile_ties_and_nonfinite_rejection():
    p = cross_sectional_percentile({"A": 1.0, "B": 1.0, "C": 5.0, "D": None})
    assert p["D"] is None
    assert p["A"] == p["B"] == pytest.approx(0.5)
    assert p["C"] == pytest.approx(1.0)
    with pytest.raises(ValueError):
        cross_sectional_percentile({"A": float("nan")})


def test_episodic_pivot_captures_aaoi_style_catalyst_gap_while_rejecting_cohr_fade():
    dates = _trading_dates(65)
    bars = {"QQQ": {}, "AAOI_LIKE": {}, "COHR_FADE": {}}
    for i, d in enumerate(dates):
        q = 400.0 + i * 0.4
        bars["QQQ"][d] = Bar(d, q - 0.5, q + 1.0, q - 1.0, q, 5e7, True)

        if i < 64:
            # Steady uptrend prior to catalyst day
            px = 100.0 + i * 0.25
            bars["AAOI_LIKE"][d] = Bar(d, px - 0.3, px + 0.8, px - 0.8, px, 1_000_000.0, True)
            bars["COHR_FADE"][d] = Bar(d, px - 0.3, px + 0.8, px - 0.8, px, 1_000_000.0, True)
        else:
            # Day 64: AAOI_LIKE gaps +6% above prior close (115.75 -> open 122.7, low 122.5, high 128.0, close 127.5) on 3x volume
            # Never touches lagged MA5 (~115.25), so legacy MA5 misses it completely!
            bars["AAOI_LIKE"][d] = Bar(d, 122.7, 128.0, 122.5, 127.5, 3_200_000.0, True)
            # COHR_FADE gaps +6% to 122.7, spikes to 125.0, but fades below open to close at 116.5 (CLV < 0)
            bars["COHR_FADE"][d] = Bar(d, 122.7, 125.0, 116.0, 116.5, 3_200_000.0, True)

    panel = Panel(dates, bars, [], [])
    legacy = compute_legacy_features(panel, "primary")
    v2 = compute_three_engine_features(panel)

    last_day = dates[-1]
    assert legacy["AAOI_LIKE"][last_day]["eligible"] is False, "Legacy narrow MA5 band misses catalyst gap-and-go"
    assert "EPISODIC_PIVOT" in v2["AAOI_LIKE"][last_day]["matched_engines"]
    assert v2["COHR_FADE"][last_day]["setup_ok"] is False, "Gap-and-crap red fade must be rejected"


def test_vcp_breakout_requires_prior_volatility_contraction():
    dates = _trading_dates(65)
    bars = {"QQQ": {}, "TIGHT_VCP": {}, "LOOSE_CHOP": {}}
    for i, d in enumerate(dates):
        q = 400.0 + i * 0.3
        bars["QQQ"][d] = Bar(d, q - 0.5, q + 1.0, q - 1.0, q, 5e7, True)
        if i < 54:
            # Prior uptrend with wider +/- 3.0 swings (ATR20 ~ 6.0)
            px = 80.0 + i * 0.4
            bars["TIGHT_VCP"][d] = Bar(d, px - 1.0, px + 3.0, px - 3.0, px, 1_000_000.0, True)
            bars["LOOSE_CHOP"][d] = Bar(d, px - 1.0, px + 3.0, px - 3.0, px, 1_000_000.0, True)
        elif i < 64:
            # Last 10 sessions before breakout: TIGHT_VCP compresses to +/- 0.45 range; LOOSE_CHOP swings +/- 5.5
            px = 101.5
            bars["TIGHT_VCP"][d] = Bar(d, px - 0.2, px + 0.45, px - 0.45, px, 800_000.0, True)
            alt = 101.5 + (4.5 if i % 2 == 0 else -4.5)
            bars["LOOSE_CHOP"][d] = Bar(d, alt - 1.0, alt + 3.5, alt - 3.5, alt, 1_000_000.0, True)
        else:
            # Breakout day: closes above 20d high on 1.8x volume with strong CLV, small gap (<3% so not EP)
            bars["TIGHT_VCP"][d] = Bar(d, 102.0, 106.0, 101.8, 105.6, 1_800_000.0, True)
            bars["LOOSE_CHOP"][d] = Bar(d, 102.0, 112.0, 101.0, 111.0, 1_800_000.0, True)

    panel = Panel(dates, bars, [], [])
    v2 = compute_three_engine_features(panel)
    last_day = dates[-1]
    assert "VCP_BREAKOUT" in v2["TIGHT_VCP"][last_day]["matched_engines"]
    assert "VCP_BREAKOUT" not in v2["LOOSE_CHOP"][last_day]["matched_engines"]


def test_crash_regime_blocks_pullback_knife_catching():
    dates = _trading_dates(65)
    bars = {"QQQ": {}, "SEMI_STOCK": {}}
    for i, d in enumerate(dates):
        # QQQ crashes below MA50 in final 6 days (-6%)
        q = 420.0 + i * 0.2 if i < 58 else 431.0 - (i - 57) * 5.0
        bars["QQQ"][d] = Bar(d, q + 1.0, q + 1.5, q - 2.0, q, 6e7, True)
        # Stock still above MA20/MA50 pulling back to MA5
        px = 100.0 + i * 0.5 if i < 63 else 131.0
        bars["SEMI_STOCK"][d] = Bar(d, px - 0.2, px + 0.6, px - 0.6, px, 800_000.0, True)

    panel = Panel(dates, bars, [], [])
    regimes = compute_regime_timeline(panel)
    assert regimes[dates[-1]].regime == "RISK_OFF"

    feats = compute_three_engine_features(panel)
    # Even if given a high scoreCard, RISK_OFF blocks PULLBACK_V2
    from research_lab.selection_v2.scoring import ScoreCard
    fake_scores = {
        d: {
            "SEMI_STOCK": ScoreCard(
                "SEMI_STOCK", 90.0, "SCORED", "balanced_v2", (), (), (("residual_momentum", 1.0),),
            )
        }
        for d in dates
    }
    sigs = build_v2_signals(feats, fake_scores, regimes, dates)
    assert sigs[dates[-1]] == []


def test_100_random_multi_sector_ledgers_reconcile_and_preserve_prefix_invariance():
    rng = random.Random(20261117)
    dates = _trading_dates(45)
    symbols = ["NVDA", "AMD", "AVGO", "COHR", "LITE", "MSFT"]
    sector_map = {
        "NVDA": "SEMI", "AMD": "SEMI", "AVGO": "SEMI",
        "COHR": "OPTICAL", "LITE": "OPTICAL", "MSFT": "CLOUD",
    }

    for _ in range(100):
        bars = {"QQQ": {}}
        q = 400.0
        for d in dates:
            q *= 1.0 + rng.uniform(-0.02, 0.025)
            bars["QQQ"][d] = Bar(d, q * 0.998, q * 1.01, q * 0.99, q, 5e7, True)

        for s in symbols:
            bars[s] = {}
            px = 100.0
            for d in dates:
                o = px * (1.0 + rng.uniform(-0.05, 0.05))
                px = o * (1.0 + rng.uniform(-0.06, 0.06))
                bars[s][d] = Bar(d, o, max(o, px) * 1.01, min(o, px) * 0.99, px, 2e6, True)

        signals = {}
        for d in dates:
            picked = rng.sample(symbols, rng.randrange(len(symbols) + 1))
            signals[d] = [
                {
                    "symbol": s,
                    "engine": rng.choice(["PULLBACK_V2", "EPISODIC_PIVOT", "VCP_BREAKOUT"]),
                    "composite_score": rng.uniform(55.0, 99.0),
                    "adv20": 2e8,
                    "atr14": max(1.0, bars[s][d].close * 0.03),
                }
                for s in picked
            ]

        cfg = PortfolioV2Config(
            slots=4,
            max_per_sector=2,
            cost_bps=rng.choice([0.0, 10.0, 25.0, 50.0]),
            sizing_mode=rng.choice(["atr_volatility_parity", "equal_slot"]),
            exit_mode="asymmetric_next_open",
            dynamic_regime_slots=False,
        )
        p = Panel(dates, bars, [], [])
        full = simulate_v2(p, signals, cfg, sector_map=sector_map)

        # Mutate future prices wildly after index 25 and prove prefix [0..25] is 100% invariant
        mutated = deepcopy(p)
        for s in symbols:
            for d in dates[26:]:
                mutated.bars[s][d] = Bar(d, 9999.0, 12000.0, 8000.0, 11000.0, 9e8, True)
        prefix_mut = simulate_v2(mutated, signals, cfg, sector_map=sector_map, end=dates[25])
        assert full["nav"][:26] == prefix_mut["nav"]

        # Cash, slot cap, and PnL accounting identity
        assert all(r["cash"] >= 0.0 and 0 <= r["positions"] <= cfg.slots and r["equity"] > 0 for r in full["nav"])
        sec_counts = {}
        for pos in full["open_book"]:
            sec_counts[pos["sector"]] = sec_counts.get(pos["sector"], 0) + 1
        assert all(c <= cfg.max_per_sector for c in sec_counts.values())

        realized = math.fsum(t["pnl"] for t in full["trades"])
        open_pnl = math.fsum(pos["quantity"] * pos["last_mark"] - pos["cost_basis"] for pos in full["open_book"])
        assert full["nav"][-1]["equity"] == pytest.approx(cfg.initial + realized + open_pnl, rel=1e-11)


def test_ablation_matrix_end_to_end_and_hash_seed_determinism():
    program = """
from datetime import date, timedelta
import math
from research_lab.data import Bar, Panel
from research_lab.selection_v2 import run_ablation_matrix

dates = []
cur = date(2024, 1, 2)
while len(dates) < 130:
    if cur.weekday() < 5:
        dates.append(cur.isoformat())
    cur += timedelta(days=1)

bars = {"QQQ": {}, "SOXX": {}}
symbols = ["NVDA", "AVGO", "AAOI", "ANET", "MSFT", "MSTR"]
px = {s: 100.0 for s in ["QQQ", "SOXX"] + symbols}

for i, d in enumerate(dates):
    rm = 0.002 + 0.006 * math.sin(i * 0.35)
    rs = rm + 0.003 * math.cos(i * 0.27)
    px["QQQ"] *= 1 + rm
    px["SOXX"] *= 1 + rs
    for b_sym in ("QQQ", "SOXX"):
        p = px[b_sym]
        bars[b_sym][d] = Bar(d, p * 0.997, p * 1.008, p * 0.992, p, 6e7, True)

    for j, s in enumerate(symbols):
        alpha = 0.0025 if j < 3 else -0.0005
        spike = 0.14 if (s == "MSTR" and i % 19 == 0) else 0.0
        ri = alpha + (1.1 + 0.1 * j) * rm + 0.005 * math.sin(i * (0.7 + 0.13 * j)) + spike
        prev_c = px[s]
        if s == "AAOI" and i in (95, 115):
            # Catalyst gap & go
            o = prev_c * 1.045
            c = prev_c * 1.075
            h, l, v = c * 1.004, o * 0.998, 5e6
        else:
            o = prev_c * (1 + 0.001 * math.cos(i + j))
            c = prev_c * (1 + ri)
            hi = max(o, c) * 1.008
            lo = min(o, c) * 0.992
            h, l, v = hi, lo, 1.5e6
        px[s] = c
        bars[s][d] = Bar(d, o, h, l, c, v, True)

panel = Panel(dates, bars, [], [])
res = run_ablation_matrix(panel, fit_window=50, signal_window=15)
assert res["validated_metrics"] if "validated_metrics" in res else True
assert res["official_signal_eligible"] is False
assert res["kernel_fm003_residual_momentum_gate"] == "PASS"
s10 = res["stages_by_cost"]["10bps"]
assert set(s10) == {
    "S0_legacy_ma5_3d",
    "S1_ma5_multifactor_3d",
    "S2_three_engine_multifactor_3d",
    "S3_three_engine_multifactor_asymmetric",
}
print(res["ablation_run_id"])
"""
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", program],
            env=dict(os.environ, PYTHONHASHSEED=str(seed)),
            text=True,
        ).strip()
        for seed in (1, 2, 3)
    ]
    assert len(set(outputs)) == 1 and len(outputs[0]) == 24
