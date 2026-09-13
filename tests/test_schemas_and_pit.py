
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation_kernel.kernel_v0 import gate_pit, require_registry, run_kernel_v0
from validation_kernel.vector_prepublish_v0 import prepublish_check

def test_kernel_historical_suite():
    assert run_kernel_v0()["pass"]

def test_pit_rejects_future_input():
    msg = gate_pit({"x": {"known_at": "2025-01-02"}}, "2025-01-01")
    assert msg is not None

def test_pit_allows_past_input():
    msg = gate_pit({"x": {"known_at": "2025-01-01"}}, "2025-01-02")
    assert msg is None

def test_tm_predict_pack_has_no_reveal_objects():
    pred = json.loads((ROOT/"time_machine/fixtures/dev_syn001/predict_pack.json").read_text())
    sim = pred["simulation_date"]
    for o in pred["objects"]:
        assert o["known_at"][:10] <= sim
        assert not o.get("reveal_only")

def test_tm_lock_hash_stable():
    locked = json.loads((ROOT/"time_machine/fixtures/dev_syn001/locked_forecast.json").read_text())
    expected = (ROOT/"time_machine/fixtures/dev_syn001/FIXTURE_HASH.txt").read_text().strip()
    assert locked["lock_id"] == expected
    # recompute
    blob = {k: locked[k] for k in locked if k != "lock_id"}
    import hashlib, json as J
    h = hashlib.sha256(J.dumps(blob, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert h == expected

def test_prepublish_blocks_pe_cancel():
    ok, rep = prepublish_check({
        "as_of": "2026-09-12",
        "model_registry": {
            "model_id": "FAST_V0", "version": "0", "inputs": [], "units": {}, "as_of": "2026-09-12",
            "equations": ["x"], "invariants": [], "allowed_domains": [],
            "missing_data_policy": "x", "failure_conditions": [], "validation_tests": [],
            "output_interpretation": "x",
        },
        "outputs": {"pe_hurdle_rows": [
            {"pe": 20.5, "dps": 0.0, "p0": 100.0, "req_g": 0.10, "dps_status": "verified_non_payer"},
            {"pe": 379.0, "dps": 0.0, "p0": 100.0, "req_g": 0.10, "dps_status": "verified_non_payer"},
        ]},
        "conclusion_language": "descriptive",
        "evidence_quality": "PARTIAL",
    })
    assert ok is False
