
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation_kernel.kernel_v0 import (
    gate_pe_cancellation_model,
    gate_dividend_fiscal_double_count,
    gate_residual_momentum_intercept,
    gate_growth_identity_unstated_efficiency,
)

EX = ROOT / "failure_museum" / "exhibits"

def test_001_pe_cancellation():
    data = json.loads((EX / "001_pe_cancellation.json").read_text())
    msg = gate_pe_cancellation_model(data["claims"])
    assert msg is not None, "001 must be auto-caught"

def test_002_dividend_double_count():
    data = json.loads((EX / "002_ashare_dividend_double_count.json").read_text())
    msg = gate_dividend_fiscal_double_count(data["parts"], data["fiscal_year"])
    assert msg is not None, "002 must be auto-caught"

def test_003_residual_momentum_omit_intercept():
    data = json.loads((EX / "003_residual_momentum_omit_intercept.json").read_text())
    msg = gate_residual_momentum_intercept(
        data["est_has_intercept"], data["oos_subtracts_alpha"], data["label"]
    )
    assert msg is not None, "003 must be auto-caught"

def test_004_growth_reinvestment_roic():
    data = json.loads((EX / "004_growth_reinvestment_roic.json").read_text())
    msgs = [gate_growth_identity_unstated_efficiency(**p) for p in data["paths"]]
    assert all(m is not None for m in msgs), "004 must be auto-caught"
