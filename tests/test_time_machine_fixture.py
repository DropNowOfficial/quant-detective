"""Legacy fixture files remain consistent; executable cycle is primary."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "time_machine/fixtures/dev_syn001"


def test_predict_pack_has_known_at_rule():
    pack = json.loads((FIX / "predict_pack.json").read_text())
    assert "known_at_rule" in pack
    assert pack["simulation_date"] == "2024-06-28"


def test_reveal_pack_future_known_at_marked_reveal_only():
    pack = json.loads((FIX / "reveal_pack.json").read_text())
    for o in pack["objects"]:
        if o["known_at"][:10] > pack["simulation_date"]:
            assert o.get("reveal_only") is True
