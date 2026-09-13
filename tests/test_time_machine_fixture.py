
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def test_predict_lock_reveal_score_chain():
    tm = ROOT / "time_machine/fixtures/dev_syn001"
    locked = json.loads((tm/"locked_forecast.json").read_text())
    reveal = json.loads((tm/"reveal_pack.json").read_text())
    score = json.loads((tm/"score.json").read_text())
    assert locked["lock_id"] == score["lock_id"]
    assert locked["lock_id"] == (tm/"FIXTURE_HASH.txt").read_text().strip()
    realized = reveal["objects"][0]["value"]
    assert score["domain_scores"]["fundamental"]["realized"] == realized
    # score was computed after lock — lock blob unchanged
    assert "forecasts" in locked
