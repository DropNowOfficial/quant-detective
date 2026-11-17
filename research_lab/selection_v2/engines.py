"""Three-engine orthogonal setup matrix + market regime & effective-dimension gate.

All calculations use completed daily bars <= signal date t for earliest t+1 open entry.
Engines:
1. PULLBACK_V2: Trend-aligned (Close > MA20 > MA50) pullback to lagged MA5 with
   volume dry-up or constructive accumulation close.
2. EPISODIC_PIVOT: Catalyst gap (>= 0.75 ATR14 or >= 3.0%) on >= 1.8x volume,
   breaking 20-day high and closing in top 35% of range (CLV >= 0.30).
3. VCP_BREAKOUT: Volatility contraction (ATR5/ATR20 <= 0.75, 10d range <= 12%)
   breaking 20-day high on >= 1.4x volume with CLV >= 0.25.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
import math
from typing import Mapping, Sequence

import numpy as np

from research_lab.data import Panel
from research_lab.factor_mining.dimension import effective_dimension
from .scoring import ScoreCard

ENGINES: dict[str, str] = {
    "EPISODIC_PIVOT": "催化剂跳空突破 (Episodic Pivot)",
    "VCP_BREAKOUT": "波动率收敛突破 (VCP Breakout)",
    "PULLBACK_V2": "中期趋势缩量回踩 (Trend Pullback v2)",
}

ENGINE_PRIORITY = ("EPISODIC_PIVOT", "VCP_BREAKOUT", "PULLBACK_V2")


@dataclass(frozen=True)
class RegimeSnapshot:
    date: str
    regime: str  # 'RISK_ON' | 'NEUTRAL' | 'RISK_OFF' | 'BLOCKED_BENCHMARK'
    qqq_above_ma20: bool | None
    qqq_above_ma50: bool | None
    qqq_ret_5d: float | None
    breadth_above_ma20: float | None
    normalized_participation_dim: float | None


def _sample_participation_dim(
    ret_matrix: np.ndarray,
    end_idx: int,
    window: int = 60,
) -> float | None:
    if end_idx < window or ret_matrix.shape[1] < 2:
        return None
    block = ret_matrix[end_idx - window + 1: end_idx + 1]
    valid_cols = [
        j for j in range(block.shape[1])
        if np.isfinite(block[:, j]).all() and not np.all(block[:, j] == block[0, j])
    ]
    if len(valid_cols) < 2:
        return None
    sub = block[:, valid_cols]
    try:
        with np.errstate(all="raise"):
            centered = sub - sub.mean(axis=0)
            stds = sub.std(axis=0, ddof=1)
            if np.any(stds <= 0):
                return None
            z = centered / stds
            corr = (z.T @ z) / (window - 1)
            corr = (corr + corr.T) / 2.0
            np.fill_diagonal(corr, 1.0)
        stats = effective_dimension(corr)
        return stats.normalized_participation
    except (FloatingPointError, ValueError):
        return None


def compute_regime_timeline(
    panel: Panel,
    *,
    market_symbol: str = "QQQ",
    dim_window: int = 60,
) -> dict[str, RegimeSnapshot]:
    """Classify each session close into RISK_ON, NEUTRAL, RISK_OFF, or BLOCKED_BENCHMARK."""
    dates = panel.dates
    if market_symbol not in panel.bars:
        raise ValueError(f"market benchmark {market_symbol!r} missing from panel")

    q_bars = panel.bars[market_symbol]
    q_seq = [q_bars.get(d) for d in dates]
    stock_symbols = [s for s in sorted(panel.bars) if s not in {market_symbol, "QQQ", "SOXX"}]

    # Precompute daily returns for dimension diagnostic
    ret_mat = np.full((len(dates), len(stock_symbols)), np.nan, dtype=float)
    stock_seqs = {s: [panel.bars[s].get(d) for d in dates] for s in stock_symbols}
    for col_idx, s in enumerate(stock_symbols):
        seq = stock_seqs[s]
        for i in range(1, len(dates)):
            a, b = seq[i - 1], seq[i]
            if a is not None and b is not None and a.valid and b.valid and a.close > 0 and b.close > 0:
                ret_mat[i, col_idx] = b.close / a.close - 1.0

    timeline: dict[str, RegimeSnapshot] = {}
    for i, d in enumerate(dates):
        above_count, valid_count = 0, 0
        if i >= 19:
            for s in stock_symbols:
                w = stock_seqs[s][i - 19: i + 1]
                if all(b is not None and b.valid for b in w):
                    valid_count += 1
                    if w[-1].close > fmean(b.close for b in w):
                        above_count += 1
        breadth = above_count / valid_count if valid_count > 0 else None
        norm_dim = _sample_participation_dim(ret_mat, i, window=dim_window)

        if i < 49 or any(b is None or not b.valid for b in q_seq[i - 49: i + 1]):
            timeline[d] = RegimeSnapshot(d, "BLOCKED_BENCHMARK", None, None, None, breadth, norm_dim)
            continue

        q_cur = q_seq[i].close
        q_ma20 = fmean(q_seq[j].close for j in range(i - 19, i + 1))
        q_ma50 = fmean(q_seq[j].close for j in range(i - 49, i + 1))
        q_ret5 = q_cur / q_seq[i - 5].close - 1.0
        above20 = q_cur > q_ma20
        above50 = q_cur > q_ma50

        if above20 and (breadth is None or breadth >= 0.40) and q_ret5 > -0.035:
            state = "RISK_ON"
        elif (not above50) and (q_ret5 <= -0.025 or (breadth is not None and breadth < 0.25)):
            state = "RISK_OFF"
        else:
            state = "NEUTRAL"

        timeline[d] = RegimeSnapshot(d, state, above20, above50, q_ret5, breadth, norm_dim)
    return timeline


def compute_three_engine_features(
    panel: Panel,
    *,
    min_price: float = 5.0,
    min_adv20: float = 2e7,
    max_atr_pct: float = 0.12,
    enabled_engines: Sequence[str] = ENGINE_PRIORITY,
) -> dict[str, dict[str, dict]]:
    """Evaluate causal setup geometry at each session close for all non-benchmark symbols."""
    unknown = set(enabled_engines) - set(ENGINES)
    if not enabled_engines or unknown:
        raise ValueError(f"invalid enabled_engines: {sorted(unknown)}")
    active_set = set(enabled_engines)

    dates = panel.dates
    out: dict[str, dict[str, dict]] = {}

    for sym in sorted(panel.bars):
        if sym in {"QQQ", "SOXX"}:
            continue
        by_date = panel.bars[sym]
        seq = [by_date.get(d) for d in dates]
        records: dict[str, dict] = {}

        for i, d in enumerate(dates):
            row = {
                "setup_ok": False,
                "primary_engine": None,
                "matched_engines": (),
                "ma5_lag": None,
                "ma10": None,
                "ma20": None,
                "ma50": None,
                "atr14": None,
                "atr14_lag": None,
                "d5_atr14": None,
                "gap_atr": None,
                "rvol20": None,
                "clv": None,
                "adv20": None,
                "close": None,
                "reason": "INSUFFICIENT_OR_INVALID_HISTORY",
            }
            records[d] = row
            if i < 50:
                continue
            window = seq[i - 50: i + 1]
            if len(window) != 51 or any(b is None or not b.valid for b in window):
                continue

            cur = seq[i]
            prev_b = seq[i - 1]
            ma5_lag = fmean(seq[j].close for j in range(i - 5, i))
            ma5_lag_prev = fmean(seq[j].close for j in range(i - 6, i - 1))
            ma5_lag_prev2 = fmean(seq[j].close for j in range(i - 7, i - 2))

            tr_lag = [
                max(
                    seq[j].high - seq[j].low,
                    abs(seq[j].high - seq[j - 1].close),
                    abs(seq[j].low - seq[j - 1].close),
                )
                for j in range(i - 20, i)
            ]
            atr5_lag = fmean(tr_lag[-5:])
            atr14_lag = fmean(tr_lag[-14:])
            atr20_lag = fmean(tr_lag)
            if atr14_lag <= 0 or atr20_lag <= 0:
                row["reason"] = "ZERO_ATR"
                continue

            vol20_lag = fmean(seq[j].volume for j in range(i - 20, i))
            adv20_lag = fmean(seq[j].close * seq[j].volume for j in range(i - 20, i))
            if vol20_lag <= 0 or adv20_lag <= 0:
                row["reason"] = "ZERO_VOLUME"
                continue

            high20_lag = max(seq[j].high for j in range(i - 20, i))
            high10_lag = max(seq[j].high for j in range(i - 10, i))
            low10_lag = min(seq[j].low for j in range(i - 10, i))
            ma50_lag = fmean(seq[j].close for j in range(i - 50, i))

            ma10_cur = fmean(seq[j].close for j in range(i - 9, i + 1))
            ma20_cur = fmean(seq[j].close for j in range(i - 19, i + 1))
            ma20_prev5 = fmean(seq[j].close for j in range(i - 24, i - 4))
            ma50_cur = fmean(seq[j].close for j in range(i - 49, i + 1))

            tr_cur = max(
                cur.high - cur.low,
                abs(cur.high - prev_b.close),
                abs(cur.low - prev_b.close),
            )
            atr14_cur = fmean(tr_lag[-13:] + [tr_cur])
            span = cur.high - cur.low
            clv_cur = ((cur.close - cur.low) - (cur.high - cur.close)) / span if span > 0 else 0.0
            gap_atr = (cur.open - prev_b.close) / atr14_lag
            gap_pct = cur.open / prev_b.close - 1.0
            d5_atr14 = (cur.close - ma5_lag) / atr14_lag
            rvol = cur.volume / vol20_lag
            vol3_ratio = fmean(seq[j].volume for j in range(i - 3, i)) / vol20_lag

            base_ok = (
                cur.close >= min_price
                and adv20_lag >= min_adv20
                and atr14_cur / cur.close <= max_atr_pct
            )

            matched: list[str] = []
            if base_ok:
                # Engine B: Catalyst Gap & Go (Episodic Pivot)
                if "EPISODIC_PIVOT" in active_set:
                    ep_ok = (
                        (gap_atr >= 0.75 or gap_pct >= 0.03)
                        and rvol >= 1.8
                        and cur.close > high20_lag
                        and cur.close >= cur.open
                        and clv_cur >= 0.30
                        and cur.close > ma50_cur
                    )
                    if ep_ok:
                        matched.append("EPISODIC_PIVOT")

                # Engine C: Volatility Contraction Breakout (VCP)
                if "VCP_BREAKOUT" in active_set:
                    vcp_ok = (
                        atr5_lag / atr20_lag <= 0.75
                        and (high10_lag - low10_lag) / prev_b.close <= 0.12
                        and prev_b.close > ma50_lag
                        and ma20_cur >= ma50_cur
                        and cur.close > high20_lag
                        and rvol >= 1.4
                        and cur.close >= cur.open
                        and clv_cur >= 0.25
                    )
                    if vcp_ok:
                        matched.append("VCP_BREAKOUT")

                # Engine A: Trend-Aligned Volume-Dryup Pullback
                if "PULLBACK_V2" in active_set:
                    trend_ok = (
                        cur.close > ma20_cur > ma50_cur
                        and ma20_cur > ma20_prev5
                        and (ma5_lag - ma5_lag_prev) > 0
                        and (ma5_lag - ma5_lag_prev2) >= 0
                    )
                    band_ok = (-0.35 <= d5_atr14 <= 0.25) and (abs(gap_atr) <= 0.60)
                    vol_action_ok = (
                        (vol3_ratio <= 0.92 and clv_cur >= -0.15)
                        or (cur.close >= cur.open and clv_cur >= 0.15 and rvol >= 0.85)
                    )
                    if trend_ok and band_ok and vol_action_ok:
                        matched.append("PULLBACK_V2")

            row.update(
                setup_ok=bool(matched),
                primary_engine=matched[0] if matched else None,
                matched_engines=tuple(matched),
                ma5_lag=ma5_lag,
                ma10=ma10_cur,
                ma20=ma20_cur,
                ma50=ma50_cur,
                atr14=atr14_cur,
                atr14_lag=atr14_lag,
                d5_atr14=d5_atr14,
                gap_atr=gap_atr,
                rvol20=rvol,
                clv=clv_cur,
                adv20=adv20_lag,
                close=cur.close,
                reason="MATCHED:" + ",".join(matched) if matched else "SETUP_NOT_MET",
            )
        out[sym] = records
    return out


def build_v2_signals(
    engine_features: Mapping[str, Mapping[str, Mapping]],
    scores_by_date: Mapping[str, Mapping[str, ScoreCard]],
    regimes_by_date: Mapping[str, RegimeSnapshot],
    dates: Sequence[str],
    *,
    min_score_risk_on: float = 55.0,
    min_score_neutral: float = 65.0,
    min_score_risk_off: float = 80.0,
) -> dict[str, list[dict]]:
    """Combine setups, multi-factor cross-sectional scores, and regime gates into queues."""
    queues: dict[str, list[dict]] = {d: [] for d in dates}
    symbols = sorted(engine_features)
    prev_active: dict[tuple[str, str], bool] = {}

    for d in dates:
        reg = regimes_by_date[d]
        date_scores = scores_by_date.get(d, {})

        for sym in symbols:
            feat = engine_features[sym][d]
            matched = feat.get("matched_engines") or ()
            card = date_scores.get(sym)
            score = card.composite_score if card is not None else None
            raw_rm = card.raw_dict().get("residual_momentum") if card is not None else None

            chosen_engine: str | None = None
            for eng in ENGINE_PRIORITY:
                is_match = eng in matched
                qualifies = False
                if is_match and score is not None and math.isfinite(score):
                    if reg.regime == "RISK_ON":
                        qualifies = score >= min_score_risk_on
                    elif reg.regime == "NEUTRAL":
                        qualifies = score >= min_score_neutral
                    elif reg.regime == "RISK_OFF":
                        # In crash regimes, knife-catching pullbacks are blocked; only
                        # ultra-high-volume episodic pivots in top cross-sectional names pass.
                        qualifies = (
                            eng == "EPISODIC_PIVOT"
                            and score >= min_score_risk_off
                            and (feat.get("rvol20") or 0.0) >= 2.2
                        )
                    else:
                        qualifies = False

                was_active = prev_active.get((sym, eng), False)
                prev_active[(sym, eng)] = qualifies
                if qualifies and not was_active and chosen_engine is None:
                    chosen_engine = eng

            if chosen_engine is not None and score is not None:
                queues[d].append({
                    "symbol": sym,
                    "engine": chosen_engine,
                    "matched_engines": matched,
                    "composite_score": score,
                    "residual_momentum": raw_rm,
                    "adv20": feat["adv20"],
                    "atr14": feat["atr14"],
                    "ma10": feat["ma10"],
                    "regime": reg.regime,
                })

        queues[d].sort(
            key=lambda x: (
                -x["composite_score"],
                -(x["residual_momentum"] if isinstance(x["residual_momentum"], (int, float)) and math.isfinite(x["residual_momentum"]) else -1e9),
                -x["adv20"],
                x["symbol"],
            )
        )
    return queues
