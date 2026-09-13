"""VALIDATION KERNEL v0 — catch contract violations before Atlas sees Vector outputs.
Not a stock analyzer. Synthetic + algebraic gates only.
"""
from __future__ import annotations
import json, math
from dataclasses import dataclass, asdict, field
from typing import Any, Callable, Optional

REQUIRED_REGISTRY_FIELDS = [
    "model_id", "version", "inputs", "units", "as_of", "equations",
    "invariants", "allowed_domains", "missing_data_policy",
    "failure_conditions", "validation_tests", "output_interpretation",
]

@dataclass
class GateResult:
    name: str
    caught: bool  # True = kernel correctly BLOCKED the bad output
    reason: str
    detail: dict = field(default_factory=dict)

def require_registry(reg: dict) -> list[str]:
    missing = [k for k in REQUIRED_REGISTRY_FIELDS if k not in reg]
    return missing

# --- Gate implementations ---

def gate_pit(inputs: dict, as_of: str) -> Optional[str]:
    """Fail if any input as_of / known_date > analysis as_of."""
    for name, meta in inputs.items():
        if not isinstance(meta, dict):
            continue
        for key in ("as_of", "known_date", "filed", "published"):
            d = meta.get(key)
            if d and str(d) > str(as_of):
                return f"PIT fail: {name}.{key}={d} > as_of={as_of}"
    return None

def gate_units_yi_billion(value_yi: float, claimed_b: float, tol: float = 0.01) -> Optional[str]:
    """1亿元 = 0.1B CNY. Catch 846亿 → 846B misread."""
    correct_b = value_yi * 0.1
    if abs(claimed_b - correct_b) / max(abs(correct_b), 1e-9) > tol and abs(claimed_b - value_yi) < tol * max(abs(value_yi), 1):
        return f"Unit fail: {value_yi}亿元 claimed as {claimed_b}B (looks like 亿→B identity); correct ≈ {correct_b}B"
    if abs(claimed_b - value_yi) < 1e-6 and value_yi > 10:
        return f"Unit fail: claimed_B equals 亿元 figure ({value_yi}) — likely missed 亿 conversion"
    return None

def gate_dividend_fiscal_double_count(dps_parts: list[dict], fiscal_year: str) -> Optional[str]:
    """Reject ex-date windows that pull two annuals across fiscal years."""
    annuals = [p for p in dps_parts if p.get("kind") == "annual"]
    years = {p.get("report_fy") for p in annuals}
    if len(years) > 1:
        return f"Dividend fail: annual DPS parts span FYs {years} — double-count risk (000651-class)"
    # also: if sum includes prior FY annual while labeling as single FY
    if fiscal_year and any(p.get("report_fy") and p.get("report_fy") != fiscal_year and p.get("kind") == "annual" for p in dps_parts):
        return f"Dividend fail: non-{fiscal_year} annual included in FY attribution window"
    return None

def gate_pe_cancellation(pe0: float, g: float, r: float = 0.10, dps: float = 0.0, p0: float = 100.0, eps0: float = None) -> Optional[str]:
    """Detect FAST V0 degeneracy: PE_T=PE_0 ⇒ non-payer req g ≡ r."""
    if eps0 is None:
        eps0 = p0 / pe0
    # model: PT = eps0*(1+g)^3*pe0; with pe_t=pe0, PT/P0=(1+g)^3
    # solve g for total return r with flat DPS
    # P0*(1+r)^3 = P0*(1+g)^3 + 3*dps  ⇒ (1+g)^3 = (1+r)^3 - 3*dps/P0
    target = (1 + r) ** 3 - 3 * dps / p0
    if target <= 0:
        return None
    g_implied = target ** (1 / 3) - 1
    if abs(dps) < 1e-12:
        # identity: g must equal r regardless of pe0
        if abs(g - r) < 1e-9:
            return (
                f"Algebraic degeneracy: with PE_T=PE_0 and DPS=0, required g≡{r:.0%} "
                f"independent of PE (PE={pe0} cancelled). FAST V0-class failure."
            )
    else:
        # g depends only on yield, not PE — check two PEs give same g
        pass
    # Explicit cancel test: same g for two different PEs
    g_a = g_implied  # from cash yield only when PE cancels
    # If caller claims different hurdles for PE=8 vs PE=379 with DPS=0, that's fine;
    # if they claim same g=r for both, degeneracy is the *model*, catch when pe varies but g fixed at r
    return None

def gate_pe_cancellation_model(claims: dict) -> Optional[str]:
    """claims: list of {pe, dps, req_g, r} under PE_T=PE_0."""
    rows = claims.get("rows", [])
    r = claims.get("r", 0.10)
    zero_dps = [x for x in rows if abs(x.get("dps", 0)) < 1e-12]
    if len(zero_dps) >= 2:
        gs = [x["req_g"] for x in zero_dps]
        pes = [x["pe"] for x in zero_dps]
        if max(gs) - min(gs) < 1e-6 and abs(gs[0] - r) < 1e-6 and max(pes) / min(pes) > 2:
            return (
                f"Algebraic degeneracy CAUGHT: non-payer req_g={gs[0]:.1%} equals r for PEs {pes} "
                f"— valuation cancelled (FAST V0)."
            )
    # yield-only dependence
    with_dps = [x for x in rows if x.get("dps", 0) > 0]
    if len(with_dps) >= 2:
        # same yield different PE → same g ⇒ PE cancelled
        from collections import defaultdict
        by_y = defaultdict(list)
        for x in with_dps:
            y = round(x["dps"] / x["p0"], 6)
            by_y[y].append(x)
        for y, xs in by_y.items():
            if len(xs) < 2:
                continue
            if max(x["pe"] for x in xs) / min(x["pe"] for x in xs) > 2:
                if max(x["req_g"] for x in xs) - min(x["req_g"] for x in xs) < 1e-6:
                    return (
                        f"Algebraic degeneracy CAUGHT: req_g depends only on yield={y:.2%} not PE "
                        f"(PEs {[x['pe'] for x in xs]})."
                    )
    return None

def gate_residual_momentum_intercept(est_has_intercept: bool, oos_subtracts_alpha: bool, label: str) -> Optional[str]:
    if est_has_intercept and not oos_subtracts_alpha and "residual" in label.lower():
        return (
            "Residual Momentum degeneracy CAUGHT: OLS estimated with intercept but OOS uses "
            "ε=rx−β′F (omits α) ⇒ reported residual = α+ε."
        )
    return None

def gate_growth_identity(g: float, rr: float, iroic: float, g_eff: float, tol: float = 1e-6) -> Optional[str]:
    rhs = rr * iroic + g_eff
    if abs(g - rhs) > tol * max(1.0, abs(g)):
        return (
            f"Invariant fail CAUGHT: g={g:.6f} ≠ RR×iROIC+g_eff={rhs:.6f} "
            f"(RR={rr}, iROIC={iroic}, g_eff={g_eff}). Advantest V1.1-class."
        )
    # also catch silent absurd: RR fixed low, g high, g_eff unstated (None)
    return None

def gate_growth_identity_unstated_efficiency(g: float, rr: float, iroic: Optional[float], g_eff: Optional[float]) -> Optional[str]:
    if g_eff is None and iroic is None and rr is not None and g is not None:
        implied = g / rr if rr else float("inf")
        if rr < 0.15 and g > 0.20:
            return (
                f"Invariant fail CAUGHT: g={g:.1%} with RR={rr:.1%} and g_eff/iROIC unstated "
                f"(implied iROIC≈{implied:.0%} if g_eff=0). Reinvestment/growth inconsistency."
            )
    if g_eff is None and iroic is not None and rr is not None:
        # check identity assuming g_eff=0
        if abs(g - rr * iroic) > 1e-4 and g > rr * iroic + 0.05:
            return (
                f"Invariant fail CAUGHT: g={g:.1%} exceeds RR×iROIC={rr*iroic:.1%} with g_eff unstated."
            )
    return None



# --- Ledger next-patch gates ---

def gate_dps_zero_ambiguity(dps: float, dps_status: str | None) -> Optional[str]:
    """DPS=0 without explicit status is illegal — non-payer vs UNAVAILABLE must be flagged."""
    if dps is None:
        return "DPS fail: dps is None without dps_status"
    if abs(float(dps)) < 1e-15:
        if dps_status not in ("verified_non_payer", "UNAVAILABLE", "sourced_zero"):
            return (
                "DPS fail CAUGHT: DPS=0 without dps_status in "
                "{verified_non_payer | UNAVAILABLE | sourced_zero} — silent zero ambiguity"
            )
        if dps_status == "UNAVAILABLE":
            return (
                "DPS fail CAUGHT: DPS=0 with dps_status=UNAVAILABLE — zero must not placeholder missing; "
                "hurdle must be UNAVAILABLE"
            )
    return None

def gate_money_currency_scale(field_name: str, meta: dict) -> Optional[str]:
    """Every money field needs currency + scale (yen/CNY/USD + million/亿元/B/raw)."""
    if not isinstance(meta, dict):
        return f"Unit fail: {field_name} money meta must be dict with currency+scale"
    cur = meta.get("currency")
    scale = meta.get("scale")
    allowed_cur = {"JPY", "yen", "CNY", "USD", "EUR"}
    allowed_scale = {"raw", "million", "billion", "B", "亿元", "亿", "T", "trillion"}
    if cur not in allowed_cur:
        return f"Unit fail CAUGHT: {field_name} missing/invalid currency={cur!r} (need one of {sorted(allowed_cur)})"
    if scale not in allowed_scale:
        return f"Unit fail CAUGHT: {field_name} missing/invalid scale={scale!r} (need one of {sorted(allowed_scale)})"
    return None

# --- Four historical failures as synthetic known-answer tests ---

def test_pe_cancellation() -> GateResult:
    claims = {
        "r": 0.10,
        "rows": [
            {"pe": 20.5, "dps": 0.0, "p0": 100.0, "req_g": 0.10},  # AMZN-like
            {"pe": 379.0, "dps": 0.0, "p0": 100.0, "req_g": 0.10},  # TSLA-like
        ],
    }
    msg = gate_pe_cancellation_model(claims)
    return GateResult("HIST_PE_cancellation", caught=msg is not None, reason=msg or "MISSED — degeneracy not flagged", detail=claims)

def test_000651_dividend_double_count() -> GateResult:
    # synthetic: FY25 window wrongly includes FY24 annual
    parts = [
        {"kind": "interim", "report_fy": "FY2025", "dps": 1.0},
        {"kind": "annual", "report_fy": "FY2025", "dps": 2.0},
        {"kind": "annual", "report_fy": "FY2024", "dps": 2.0},  # double-count
    ]
    msg = gate_dividend_fiscal_double_count(parts, "FY2025")
    return GateResult("HIST_000651_dividend_double_count", caught=msg is not None, reason=msg or "MISSED", detail={"parts": parts, "sum_wrong": 5.0, "sum_correct_fy25": 3.0})

def test_residual_momentum_omit_alpha() -> GateResult:
    msg = gate_residual_momentum_intercept(est_has_intercept=True, oos_subtracts_alpha=False, label="Residual Momentum")
    return GateResult("HIST_residual_momentum_omit_intercept", caught=msg is not None, reason=msg or "MISSED", detail={})

def test_advantest_reinvestment_growth() -> GateResult:
    # V1.1 style: RR=0.10, g=0.22, g_eff and iROIC unstated
    msg = gate_growth_identity_unstated_efficiency(g=0.22, rr=0.10, iroic=None, g_eff=None)
    msg2 = gate_growth_identity_unstated_efficiency(g=0.93, rr=0.10, iroic=None, g_eff=None)
    caught = msg is not None and msg2 is not None
    return GateResult(
        "HIST_advantest_reinvestment_growth",
        caught=caught,
        reason=(msg or "") + " | " + (msg2 or "MISSED high-g case"),
        detail={"cases": [{"g": 0.22, "rr": 0.10}, {"g": 0.93, "rr": 0.10}]},
    )

def run_kernel_v0() -> dict:
    results = [
        test_pe_cancellation(),
        test_000651_dividend_double_count(),
        test_residual_momentum_omit_alpha(),
        test_advantest_reinvestment_growth(),
    ]
    report = {
        "kernel": "VALIDATION_KERNEL_v0",
        "purpose": "Prevent conclusions that violate data/model contracts — not stock analysis",
        "historical_failures": [
            {
                "failure": r.name,
                "caught_automatically": r.caught,
                "missed": not r.caught,
                "reason": r.reason,
                "detail": r.detail,
            }
            for r in results
        ],
        "pass": all(r.caught for r in results),
        "registry_required_fields": REQUIRED_REGISTRY_FIELDS,
    }
    return report

if __name__ == "__main__":
    rep = run_kernel_v0()
    path = "/workspace/validation_kernel/v0_report.json"
    with open(path, "w") as f:
        json.dump(rep, f, indent=2)
    print(json.dumps(rep, indent=2))
    print("PASS" if rep["pass"] else "FAIL", "->", path)
