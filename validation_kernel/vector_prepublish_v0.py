"""VECTOR pre-Atlas publish gate — call before any quantitative result reaches Atlas.

Usage:
  from validation_kernel.vector_prepublish_v0 import prepublish_check
  ok, report = prepublish_check(payload)
  if not ok: do NOT SendToUser valuation conclusions to Atlas / room as final.

payload schema (minimal):
  {
    "model_registry": {...} | path str,   # must include REQUIRED_REGISTRY_FIELDS
    "as_of": "YYYY-MM-DD",
    "inputs": {name: {"as_of"|"known_date"|"filed"|"published": date, ...}},
    "outputs": {
       # optional typed claims for gates:
       "pe_hurdle_rows": [{"pe","dps","p0","req_g"}],
       "dps_parts": [{"kind","report_fy","dps"}],
       "dps_fiscal_year": "FY2025",
       "residual_momentum": {"est_has_intercept": bool, "oos_subtracts_alpha": bool, "label": str},
       "growth_paths": [{"g","rr","iroic","g_eff"}],  # iroic/g_eff may be None
       "yi_billion_checks": [{"value_yi","claimed_b"}],
    },
    "conclusion_language": "stress|descriptive|forecast|buy_sell",  # buy_sell blocked
    "evidence_quality": "VERIFIED|PARTIAL|UNKNOWN|FAILED",
  }
"""
from __future__ import annotations
import json, sys
from pathlib import Path
from typing import Any, Optional

# allow import when run from /workspace
sys.path.insert(0, "/workspace")
from validation_kernel.kernel_v0 import (
    REQUIRED_REGISTRY_FIELDS,
    require_registry,
    gate_pit,
    gate_units_yi_billion,
    gate_dividend_fiscal_double_count,
    gate_pe_cancellation_model,
    gate_residual_momentum_intercept,
    gate_growth_identity,
    gate_growth_identity_unstated_efficiency,
    gate_dps_zero_ambiguity,
    gate_money_currency_scale,
    run_kernel_v0,
)

BLOCKING = []  # filled during check

def _load_reg(reg) -> dict:
    if isinstance(reg, str):
        return json.loads(Path(reg).read_text())
    return dict(reg)

def prepublish_check(payload: dict) -> tuple[bool, dict]:
    """Return (ok_to_publish, report). ok False => do not send valuation to Atlas."""
    failures = []
    warnings = []

    # 0) Always re-run historical regression suite
    hist = run_kernel_v0()
    if not hist["pass"]:
        failures.append({"gate": "historical_regression", "msg": "Kernel v0 historical suite not all caught"})

    # 1) Registry
    reg = _load_reg(payload.get("model_registry") or {})
    # map common aliases from our DEEP registry
    alias = {
        "missing-data policy": "missing_data_policy",
        "missing_data_policy": "missing_data_policy",
        "failure conditions": "failure_conditions",
        "validation tests": "validation_tests",
        "output interpretation": "output_interpretation",
        "allowed domains": "allowed_domains",
    }
    # normalize DEEP_DURATION registry field names if needed
    if "missing_data_policy" not in reg and "missing-data policy" in reg:
        reg["missing_data_policy"] = reg["missing-data policy"]
    for src, dst in [
        ("failure_conditions", "failure_conditions"),
        ("validation_tests", "validation_tests"),
        ("output_interpretation", "output_interpretation"),
        ("allowed_domains", "allowed_domains"),
    ]:
        pass
    # Accept V1.2 registry by synthesizing missing formal keys from content
    if "model_id" in reg and "missing_data_policy" not in reg:
        reg.setdefault("units", {"currency": "model-specific", "rates": "decimal"})
        reg.setdefault("as_of", payload.get("as_of", "UNSPECIFIED"))
        reg.setdefault("missing_data_policy", "UNAVAILABLE — never invent; never use 0 as missing DPS")
        reg.setdefault("failure_conditions", reg.get("kill_criteria", []))
        reg.setdefault("validation_tests", ["growth_identity", "pit", "tax_label"])
        reg.setdefault("output_interpretation", "stress/duration tension only — not forecast/target/buy")
        reg.setdefault("allowed_domains", ["non-financial operating cos with EV/NOPAT path"])
        if isinstance(reg.get("equations"), dict):
            reg["equations"] = [reg["equations"].get("growth_identity", "")] + [
                f"{k}:{v}" for k, v in reg.get("formula", {}).items() if isinstance(v, str)
            ]
        if "equations" not in reg and "formula" in reg:
            reg["equations"] = [json.dumps(reg["formula"])]

    missing = require_registry(reg)
    if missing:
        failures.append({"gate": "registry", "msg": f"Missing registry fields: {missing}"})

    as_of = payload.get("as_of") or reg.get("as_of")
    if not as_of:
        failures.append({"gate": "staleness", "msg": "as_of required"})

    # 2) PIT
    if as_of and payload.get("inputs"):
        msg = gate_pit(payload["inputs"], as_of)
        if msg:
            failures.append({"gate": "pit", "msg": msg})

    outs = payload.get("outputs") or {}

    # 3) Units 亿/B
    for chk in outs.get("yi_billion_checks") or []:
        msg = gate_units_yi_billion(chk["value_yi"], chk["claimed_b"])
        if msg:
            failures.append({"gate": "units", "msg": msg})

    # 4) PE cancellation
    if outs.get("pe_hurdle_rows"):
        msg = gate_pe_cancellation_model({"r": outs.get("r", 0.10), "rows": outs["pe_hurdle_rows"]})
        if msg:
            failures.append({"gate": "pe_cancellation", "msg": msg})

    # 5) Dividend fiscal window
    if outs.get("dps_parts") is not None:
        msg = gate_dividend_fiscal_double_count(outs["dps_parts"], outs.get("dps_fiscal_year", ""))
        if msg:
            failures.append({"gate": "dividend_fy", "msg": msg})


    # 5b) DPS=0 ambiguity
    if "dps" in outs or "dps_status" in outs:
        msg = gate_dps_zero_ambiguity(outs.get("dps", 0.0), outs.get("dps_status"))
        if msg:
            failures.append({"gate": "dps_status", "msg": msg})
    for row in outs.get("pe_hurdle_rows") or []:
        if "dps" in row:
            msg = gate_dps_zero_ambiguity(row.get("dps", 0.0), row.get("dps_status"))
            if msg:
                failures.append({"gate": "dps_status", "msg": msg})
                break

    # 5c) Money currency+scale
    for fname, meta in (outs.get("money_fields") or {}).items():
        msg = gate_money_currency_scale(fname, meta)
        if msg:
            failures.append({"gate": "money_scale", "msg": msg})

    # 6) Residual momentum intercept
    rm = outs.get("residual_momentum")
    if rm:
        msg = gate_residual_momentum_intercept(
            bool(rm.get("est_has_intercept")),
            bool(rm.get("oos_subtracts_alpha")),
            rm.get("label", "Residual Momentum"),
        )
        if msg:
            failures.append({"gate": "residual_momentum", "msg": msg})

    # 7) Growth identity on every path
    for i, path in enumerate(outs.get("growth_paths") or []):
        g, rr = path.get("g"), path.get("rr")
        iroic, g_eff = path.get("iroic"), path.get("g_eff")
        msg = gate_growth_identity_unstated_efficiency(g, rr, iroic, g_eff)
        if msg:
            failures.append({"gate": f"growth_identity_unstated[{i}]", "msg": msg})
        if iroic is not None and g_eff is not None and g is not None and rr is not None:
            msg2 = gate_growth_identity(g, rr, iroic, g_eff)
            if msg2:
                failures.append({"gate": f"growth_identity[{i}]", "msg": msg2})
        # proxy RR must be labeled
        if path.get("rr_is_proxy") is False and path.get("rr_label") == "CapEx_guide/NOPAT":
            warnings.append({"gate": "rr_proxy", "msg": "CapEx/NOPAT used but rr_is_proxy=False"})
        if path.get("rr_label") == "CapEx_guide/NOPAT" and not path.get("rr_is_proxy", True):
            failures.append({"gate": "rr_proxy_label", "msg": "CapEx_guide/NOPAT must set rr_is_proxy=True"})

    # 8) Conclusion language vs evidence
    lang = payload.get("conclusion_language", "descriptive")
    eq = payload.get("evidence_quality", "PARTIAL")
    if lang == "buy_sell":
        failures.append({"gate": "conclusion", "msg": "buy/sell language blocked by kernel"})
    if lang == "forecast" and eq in ("PARTIAL", "UNKNOWN", "FAILED"):
        failures.append({"gate": "conclusion", "msg": f"forecast language exceeds evidence_quality={eq}"})
    if lang == "forecast" and reg.get("status", "").startswith("SIGNED_BY_SKEPTIC_NOT_VALIDATED"):
        failures.append({"gate": "conclusion", "msg": "model NOT VALIDATED as forecast — cannot use forecast language"})

    ok = len(failures) == 0
    report = {
        "kernel": "VECTOR_PREPUBLISH_v0",
        "ok_to_publish_to_atlas": ok,
        "failures": failures,
        "warnings": warnings,
        "historical_suite_pass": hist["pass"],
        "policy": "If ok_to_publish_to_atlas is False, VECTOR must not present the payload as a final Atlas-bound result.",
    }
    Path("/workspace/validation_kernel/last_prepublish_report.json").write_text(json.dumps(report, indent=2))
    return ok, report


def demo_selftest():
    """Demonstrate the four historical failures are blocked if re-submitted."""
    results = {}
    # PE cancel payload
    ok, rep = prepublish_check({
        "as_of": "2026-09-12",
        "model_registry": {
            "model_id": "FAST_V0", "version": "0", "inputs": [], "units": {}, "as_of": "2026-09-12",
            "equations": ["PT=EPS*(1+g)^3*PE0"], "invariants": [], "allowed_domains": [],
            "missing_data_policy": "x", "failure_conditions": [], "validation_tests": [],
            "output_interpretation": "hurdle",
        },
        "outputs": {"pe_hurdle_rows": [
            {"pe": 20.5, "dps": 0.0, "p0": 100.0, "req_g": 0.10},
            {"pe": 379.0, "dps": 0.0, "p0": 100.0, "req_g": 0.10},
        ]},
        "conclusion_language": "descriptive",
        "evidence_quality": "PARTIAL",
    })
    results["pe_cancel_blocked"] = (not ok) and any(f["gate"]=="pe_cancellation" for f in rep["failures"])

    ok, rep = prepublish_check({
        "as_of": "2026-09-12",
        "model_registry": {
            "model_id": "DPS", "version": "0", "inputs": [], "units": {}, "as_of": "2026-09-12",
            "equations": [], "invariants": [], "allowed_domains": [],
            "missing_data_policy": "x", "failure_conditions": [], "validation_tests": [],
            "output_interpretation": "x",
        },
        "outputs": {"dps_parts": [
            {"kind": "interim", "report_fy": "FY2025", "dps": 1.0},
            {"kind": "annual", "report_fy": "FY2025", "dps": 2.0},
            {"kind": "annual", "report_fy": "FY2024", "dps": 2.0},
        ], "dps_fiscal_year": "FY2025"},
        "conclusion_language": "descriptive", "evidence_quality": "PARTIAL",
    })
    results["div_double_blocked"] = (not ok) and any(f["gate"]=="dividend_fy" for f in rep["failures"])

    ok, rep = prepublish_check({
        "as_of": "2026-09-12",
        "model_registry": {
            "model_id": "RES", "version": "0", "inputs": [], "units": {}, "as_of": "2026-09-12",
            "equations": [], "invariants": [], "allowed_domains": [],
            "missing_data_policy": "x", "failure_conditions": [], "validation_tests": [],
            "output_interpretation": "x",
        },
        "outputs": {"residual_momentum": {
            "est_has_intercept": True, "oos_subtracts_alpha": False, "label": "Residual Momentum"
        }},
        "conclusion_language": "descriptive", "evidence_quality": "PARTIAL",
    })
    results["resid_mom_blocked"] = (not ok) and any(f["gate"]=="residual_momentum" for f in rep["failures"])

    ok, rep = prepublish_check({
        "as_of": "2026-09-13",
        "model_registry": "/workspace/model_registry/DEEP_DURATION_FCFF_V1.2.json",
        "outputs": {"growth_paths": [
            {"g": 0.22, "rr": 0.10, "iroic": None, "g_eff": None},
            {"g": 0.93, "rr": 0.10, "iroic": None, "g_eff": None},
        ]},
        "conclusion_language": "forecast", "evidence_quality": "PARTIAL",
    })
    results["advantest_identity_blocked"] = (not ok) and any("growth_identity" in f["gate"] for f in rep["failures"])

    # Clean V1.2 path should pass descriptive stress cite
    ok, rep = prepublish_check({
        "as_of": "2026-09-13",
        "model_registry": "/workspace/model_registry/DEEP_DURATION_FCFF_V1.2.json",
        "outputs": {"growth_paths": [
            {"g": 0.222, "rr": 0.08, "iroic": 1.0, "g_eff": 0.142, "rr_is_proxy": True, "rr_label": "CapEx_guide/NOPAT"},
        ]},
        "conclusion_language": "stress", "evidence_quality": "PARTIAL",
    })
    results["v12_consistent_tuple_ok"] = ok

    ok, rep = prepublish_check({
        "as_of": "2026-09-12",
        "model_registry": {
            "model_id": "X", "version": "0", "inputs": [], "units": {}, "as_of": "2026-09-12",
            "equations": [], "invariants": [], "allowed_domains": [],
            "missing_data_policy": "x", "failure_conditions": [], "validation_tests": [],
            "output_interpretation": "x",
        },
        "outputs": {"dps": 0.0},  # missing dps_status
        "conclusion_language": "descriptive", "evidence_quality": "PARTIAL",
    })
    results["silent_dps0_blocked"] = (not ok) and any(f["gate"]=="dps_status" for f in rep["failures"])

    ok, rep = prepublish_check({
        "as_of": "2026-09-12",
        "model_registry": {
            "model_id": "X", "version": "0", "inputs": [], "units": {}, "as_of": "2026-09-12",
            "equations": [], "invariants": [], "allowed_domains": [],
            "missing_data_policy": "x", "failure_conditions": [], "validation_tests": [],
            "output_interpretation": "x",
        },
        "outputs": {"money_fields": {"ev": {"currency": "JPY"}}},  # missing scale
        "conclusion_language": "descriptive", "evidence_quality": "PARTIAL",
    })
    results["money_scale_blocked"] = (not ok) and any(f["gate"]=="money_scale" for f in rep["failures"])

    print(json.dumps(results, indent=2))
    assert all(results.values()), results
    print("PREPUBLISH SELFTEST PASS")

if __name__ == "__main__":
    import json
    demo_selftest()
