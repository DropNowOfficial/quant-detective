"""Causal portfolio engine with ATR volatility-parity sizing, sub-industry caps, and asymmetric exits.

Causality & Ledger Invariants:
- Morning entries and conditional exits execute at session open using ONLY information
  locked at prior completed closes (never peeking at same-day high, low, close, or validity).
- Cash >= 0, zero leverage, positions <= slots, per-sector positions <= max_per_sector.
- Equity reconciles exactly: initial + sum(realized_pnl) + sum(unrealized_open_pnl).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Mapping

from research_lab.data import Panel, finite
from .engines import RegimeSnapshot

DEFAULT_SUBINDUSTRY_MAP: dict[str, str] = {
    # AI Compute & Accelerators
    "NVDA": "AI_COMPUTE", "AMD": "AI_COMPUTE", "ARM": "AI_COMPUTE", "SMCI": "AI_COMPUTE",
    # Foundry & Wafer Fab Equipment
    "TSM": "FOUNDRY_WFE", "ASML": "FOUNDRY_WFE", "AMAT": "FOUNDRY_WFE",
    "LRCX": "FOUNDRY_WFE", "KLAC": "FOUNDRY_WFE",
    # Optical & High-Speed Networking
    "ANET": "OPTICAL_NET", "CRDO": "OPTICAL_NET", "ALAB": "OPTICAL_NET",
    "COHR": "OPTICAL_NET", "LITE": "OPTICAL_NET", "AAOI": "OPTICAL_NET", "GLW": "OPTICAL_NET",
    # Memory & Storage
    "MU": "MEMORY_STORAGE", "SNDK": "MEMORY_STORAGE", "WDC": "MEMORY_STORAGE",
    "STX": "MEMORY_STORAGE", "P": "MEMORY_STORAGE",
    # Broadline / Custom / Analog Semiconductors
    "AVGO": "BROAD_SEMI", "MRVL": "BROAD_SEMI", "QCOM": "BROAD_SEMI",
    "MPWR": "BROAD_SEMI", "MCHP": "BROAD_SEMI", "NXPI": "BROAD_SEMI", "INTC": "BROAD_SEMI",
    # Cloud & Enterprise Platforms
    "MSFT": "CLOUD_PLATFORM", "GOOG": "CLOUD_PLATFORM", "META": "CLOUD_PLATFORM",
    "AMZN": "CLOUD_PLATFORM", "AAPL": "CLOUD_PLATFORM", "ORCL": "CLOUD_PLATFORM",
    "DELL": "CLOUD_PLATFORM", "HPE": "CLOUD_PLATFORM",
    # Power & Thermal Infrastructure
    "ETN": "POWER_INFRA", "VST": "POWER_INFRA", "CEG": "POWER_INFRA", "VRT": "POWER_INFRA",
    # Crypto / High-Beta Digital
    "MSTR": "DIGITAL_BETA", "CRCL": "DIGITAL_BETA",
    # Space / Defense
    "SPCX": "AEROSPACE",
}


@dataclass(frozen=True)
class PortfolioV2Config:
    slots: int = 5
    max_per_sector: int = 2
    cost_bps: float = 10.0
    initial: float = 10000.0
    max_participation: float = 0.01
    sizing_mode: str = "atr_volatility_parity"  # 'atr_volatility_parity' | 'equal_slot'
    risk_per_trade: float = 0.012               # 1.2% NAV risk at stop distance
    exit_mode: str = "asymmetric_next_open"     # 'asymmetric_next_open' | 'fixed_hold_close'
    hold: int = 3                               # used when exit_mode == 'fixed_hold_close'
    max_hold: int = 20                          # used when exit_mode == 'asymmetric_next_open'
    min_trend_hold: int = 3
    stop_atr_mult: float = 1.5
    trail_atr_mult: float = 2.5
    breakeven_buffer: float = 0.02
    dynamic_regime_slots: bool = True

    def __post_init__(self):
        for name in ("slots", "max_per_sector", "hold", "max_hold", "min_trend_hold"):
            v = getattr(self, name)
            if type(v) is not int or not 1 <= v <= 250:
                raise ValueError(f"{name} must be an integer in 1..250")
        for name in (
            "cost_bps", "initial", "max_participation", "risk_per_trade",
            "stop_atr_mult", "trail_atr_mult", "breakeven_buffer",
        ):
            v = getattr(self, name)
            if not finite(v):
                raise ValueError(f"{name} must be finite")
        if not 0.0 <= self.cost_bps <= 1000.0 or self.initial <= 0.0:
            raise ValueError("cost_bps or initial out of bounds")
        if not 0.0 < self.max_participation <= 1.0 or not 0.0 < self.risk_per_trade <= 0.20:
            raise ValueError("participation or risk_per_trade out of bounds")
        if self.stop_atr_mult <= 0.0 or self.trail_atr_mult <= 0.0 or self.breakeven_buffer < 0.0:
            raise ValueError("ATR multipliers must be positive")
        if self.sizing_mode not in {"atr_volatility_parity", "equal_slot"}:
            raise ValueError(f"unsupported sizing_mode {self.sizing_mode!r}")
        if self.exit_mode not in {"asymmetric_next_open", "fixed_hold_close"}:
            raise ValueError(f"unsupported exit_mode {self.exit_mode!r}")
        if type(self.dynamic_regime_slots) is not bool:
            raise ValueError("dynamic_regime_slots must be boolean")


def _effective_slot_cap(
    config: PortfolioV2Config,
    prior_regime: RegimeSnapshot | None,
) -> int:
    if not config.dynamic_regime_slots or prior_regime is None:
        return config.slots
    if prior_regime.regime == "BLOCKED_BENCHMARK":
        return 0
    if prior_regime.regime == "RISK_OFF":
        return min(1, config.slots)
    cap = config.slots
    if prior_regime.regime == "NEUTRAL":
        cap = max(1, (config.slots + 1) // 2)
    # Tighten slot count by 1 when market correlation collapses effective dimension (< 25% of N)
    if prior_regime.normalized_participation_dim is not None and prior_regime.normalized_participation_dim < 0.25:
        cap = max(1, cap - 1)
    return cap


def _completed_ma10(panel: Panel, symbol: str, dates: list[str], idx: int) -> float | None:
    if idx < 9:
        return None
    by_date = panel.bars.get(symbol, {})
    closes = []
    for j in range(idx - 9, idx + 1):
        b = by_date.get(dates[j])
        if b is None or not b.valid:
            return None
        closes.append(b.close)
    return sum(closes) / 10.0


def simulate_v2(
    panel: Panel,
    signals: Mapping[str, list[dict]],
    config: PortfolioV2Config = PortfolioV2Config(),
    *,
    sector_map: Mapping[str, str] | None = None,
    regimes: Mapping[str, RegimeSnapshot] | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Run causal daily portfolio ledger with volatility-parity sizing and asymmetric exits."""
    dates = [d for d in panel.dates if (start is None or d >= start) and (end is None or d <= end)]
    if not dates:
        raise ValueError("empty test interval")

    cash = float(config.initial)
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    nav: list[dict] = []
    warnings: list[dict] = []
    previous_nav = float(config.initial)
    fee = config.cost_bps / 20000.0
    entry_count = 0
    stale_days = 0
    fees = 0.0
    rejected = 0

    def sector_of(sym: str) -> str:
        if sector_map is not None and sym in sector_map:
            return sector_map[sym]
        return f"UNMAPPED:{sym}"

    for n, day in enumerate(dates):
        turnover = 0.0
        stale = False

        # Snapshot pre-open cash so morning entry budgets never depend on same-open exit fills
        entry_cash_budget = cash

        # 1. Execute pending conditional exits scheduled at prior completed close (next-open fill)
        if config.exit_mode == "asymmetric_next_open":
            for s in sorted(list(positions)):
                pos = positions[s]
                if pos["pending_exit_reason"] is None:
                    continue
                b = panel.bars.get(s, {}).get(day)
                if b is None or not finite(b.open) or b.open <= 0:
                    pos["delayed_exit"] = True
                    warnings.append({"date": day, "symbol": s, "kind": "EXIT_OPEN_UNAVAILABLE"})
                    rejected += 1
                    continue
                gross = pos["quantity"] * b.open
                cost = gross * fee
                net = gross - cost
                cash += net
                fees += cost
                turnover += gross
                trades.append({
                    "symbol": s,
                    "engine": pos["engine"],
                    "sector": pos["sector"],
                    "signal_date": pos["signal_date"],
                    "entry_date": pos["entry_date"],
                    "entry_price": pos["entry_price"],
                    "exit_signal_date": pos["exit_signal_date"],
                    "exit_date": day,
                    "exit_price": b.open,
                    "exit_timing": "NEXT_OPEN",
                    "exit_reason": pos["pending_exit_reason"],
                    "quantity": pos["quantity"],
                    "cost_basis": pos["cost_basis"],
                    "entry_fee": pos["entry_fee"],
                    "exit_fee": cost,
                    "pnl": net - pos["cost_basis"],
                    "net_return": net / pos["cost_basis"] - 1.0,
                    "holding_sessions": n - pos["entry_index"] + 1,
                    "stale": pos["stale"],
                    "delayed_exit": pos["delayed_exit"],
                })
                del positions[s]

        # 2. Execute morning open entries from prior session close queue
        prior_day = dates[n - 1] if n > 0 else None
        queue = signals.get(prior_day, []) if prior_day is not None else []
        prior_regime = regimes.get(prior_day) if (regimes is not None and prior_day is not None) else None
        slot_cap = _effective_slot_cap(config, prior_regime)

        if nav and nav[-1]["stale"]:
            warnings.append({"date": day, "symbol": None, "kind": "NEW_ENTRIES_PAUSED_AFTER_STALE_MARK"})
            queue = []

        for sig in queue:
            s = sig["symbol"]
            if s in positions or len(positions) >= slot_cap:
                continue
            sec = sector_of(s)
            sec_count = sum(1 for p in positions.values() if p["sector"] == sec)
            if sec_count >= config.max_per_sector:
                continue

            b = panel.bars.get(s, {}).get(day)
            # Strict causality: only b.open is observable at the morning open
            if b is None or not finite(b.open) or b.open <= 0:
                warnings.append({"date": day, "symbol": s, "kind": "ENTRY_OPEN_UNAVAILABLE"})
                rejected += 1
                continue

            adv = sig.get("adv20")
            if not finite(adv) or adv <= 0:
                raise ValueError("signal ADV must be positive and finite")

            slot_budget = previous_nav / config.slots
            liq_budget = adv * config.max_participation * (1.0 + fee)
            avail_cash = min(cash, entry_cash_budget)

            if config.sizing_mode == "atr_volatility_parity":
                atr_val = sig.get("atr14")
                if not finite(atr_val) or atr_val <= 0:
                    raise ValueError("signal atr14 must be positive and finite for ATR sizing")
                stop_dist = config.stop_atr_mult * float(atr_val)
                max_shares_by_risk = (previous_nav * config.risk_per_trade) / stop_dist
                risk_budget = max_shares_by_risk * b.open * (1.0 + fee)
                budget = min(slot_budget, risk_budget, avail_cash, liq_budget)
                entry_atr = float(atr_val)
            else:
                budget = min(slot_budget, avail_cash, liq_budget)
                entry_atr = float(sig.get("atr14") or (b.open * 0.03))

            if budget <= 1e-8:
                continue

            quantity = budget / (b.open * (1.0 + fee))
            notional = quantity * b.open
            cost = notional * fee
            total_outlay = notional + cost
            cash -= total_outlay
            entry_cash_budget -= total_outlay
            fees += cost
            turnover += notional
            positions[s] = {
                "symbol": s,
                "engine": sig.get("engine", "PRIMARY"),
                "sector": sec,
                "signal_date": prior_day,
                "entry_date": day,
                "entry_index": n,
                "entry_price": b.open,
                "entry_atr": entry_atr,
                "quantity": quantity,
                "cost_basis": total_outlay,
                "entry_fee": cost,
                "due": n + config.hold - 1,
                "last_mark": b.open,
                "peak_close": b.open,
                "pending_exit_reason": None,
                "exit_signal_date": None,
                "stale": False,
                "delayed_exit": False,
                "adv20": adv,
            }
            entry_count += 1

        # 3. End-of-day mark-to-market & exit evaluation at completed close
        for s in sorted(list(positions)):
            pos = positions[s]
            b = panel.bars.get(s, {}).get(day)
            if b is None or not b.valid:
                stale = True
                pos["stale"] = True
                warnings.append({"date": day, "symbol": s, "kind": "HOLDING_MARK_UNVERIFIED"})
                continue

            pos["last_mark"] = b.close
            pos["peak_close"] = max(pos["peak_close"], b.close)
            held = n - pos["entry_index"] + 1

            if config.exit_mode == "fixed_hold_close":
                if n >= pos["due"]:
                    gross = pos["quantity"] * b.close
                    cost = gross * fee
                    net = gross - cost
                    cash += net
                    fees += cost
                    turnover += gross
                    trades.append({
                        "symbol": s,
                        "engine": pos["engine"],
                        "sector": pos["sector"],
                        "signal_date": pos["signal_date"],
                        "entry_date": pos["entry_date"],
                        "entry_price": pos["entry_price"],
                        "exit_signal_date": day,
                        "exit_date": day,
                        "exit_price": b.close,
                        "exit_timing": "SCHEDULED_CLOSE",
                        "exit_reason": "FIXED_HOLD",
                        "quantity": pos["quantity"],
                        "cost_basis": pos["cost_basis"],
                        "entry_fee": pos["entry_fee"],
                        "exit_fee": cost,
                        "pnl": net - pos["cost_basis"],
                        "net_return": net / pos["cost_basis"] - 1.0,
                        "holding_sessions": held,
                        "stale": pos["stale"],
                        "delayed_exit": n > pos["due"],
                    })
                    del positions[s]
            else:
                if pos["pending_exit_reason"] is None:
                    reason = None
                    if b.close <= pos["entry_price"] - config.stop_atr_mult * pos["entry_atr"]:
                        reason = "HARD_STOP_ATR"
                    elif held >= 2 and b.close <= pos["peak_close"] - config.trail_atr_mult * pos["entry_atr"]:
                        reason = "CHANDELIER_TRAIL_ATR"
                    else:
                        ma10_val = _completed_ma10(panel, s, dates, n)
                        if (
                            held >= config.min_trend_hold
                            and ma10_val is not None
                            and b.close < ma10_val
                            and b.close < pos["entry_price"] * (1.0 + config.breakeven_buffer)
                        ):
                            reason = "MA10_STALL_BREAK"
                        elif held >= config.max_hold:
                            reason = "MAX_HOLD_CAP"
                    if reason is not None:
                        pos["pending_exit_reason"] = reason
                        pos["exit_signal_date"] = day

        if stale:
            stale_days += 1
        value = math.fsum(p["quantity"] * p["last_mark"] for p in positions.values())
        equity = cash + value
        if not math.isfinite(equity) or equity <= 0 or cash < -1e-6:
            raise ValueError("portfolio_v2 accounting invariant failed")

        nav.append({
            "date": day,
            "equity": equity,
            "cash": max(cash, 0.0),
            "exposure": value / equity,
            "positions": len(positions),
            "turnover": turnover / previous_nav,
            "stale": stale,
        })
        previous_nav = equity

    return {
        "config": asdict(config),
        "nav": nav,
        "trades": trades,
        "warnings": warnings,
        "entry_count": entry_count,
        "rejected_entries": rejected,
        "open_positions": len(positions),
        "open_book": [dict(positions[k]) for k in sorted(positions)],
        "stale_days": stale_days,
        "fees": fees,
        "status": "BLOCKED_DATA" if stale_days or rejected else "DIAGNOSTIC_ONLY",
    }
