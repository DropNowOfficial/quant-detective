"""Documented counts of data_points.csv must equal counts derived by script from the data."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "docs/research/real_data_sample_20261011"


def test_counts_json_matches_derivation():
    out = subprocess.run([sys.executable, str(S / "derive_counts.py"), "--print"], capture_output=True, text=True, check=True).stdout
    derived = json.loads(out)
    assert json.loads((S / "counts.json").read_text()) == derived
    assert derived["rows"] == derived["used_inputs"] + derived["context_extra_rows"]


def test_docs_state_derived_counts_and_no_stale_numbers():
    c = json.loads((S / "counts.json").read_text())
    docs = [S / "REPORT.md", S / "reproduce.md", ROOT / "docs/research/evidence/acceptance_time_audit.md"]
    for d in docs[:1]:
        t = d.read_text()
        assert f"{c['rows']} rows" in t and f"{c['used_inputs']} used" in t, d
    allowed = {v for k, v in c.items() if isinstance(v, int)}
    for k in ("rows_per_ticker", "used_per_ticker"):
        allowed |= set(c[k].values())
    allowed |= {e["rows"] for e in json.loads((S / "price_manifest.json").read_text())["files"].values()}  # daily price rows
    stale = [r"\b148 rows\b", r"\b120/120\b", r"all 160 rows", r"56 of 160 rows"]
    for d in docs:
        t = d.read_text()
        for pat in stale:
            if pat == r"all 160 rows" and c["rows"] == 160:
                continue
            if pat == r"56 of 160 rows" and c["rows"] == 160:
                continue
            assert not re.search(pat, t), (d, pat)
        for m in re.finditer(r"\b(\d+) (?:rows|data points)\b", t):
            assert int(m.group(1)) in allowed, (d, m.group(0))
