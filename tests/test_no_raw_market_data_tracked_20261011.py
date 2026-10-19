"""AGENTS.md rule 6: no raw market datasets in Git (manifests/hashes only)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_no_raw_market_data as chk  # noqa: E402


def test_checker_flags_price_csvs_and_raw_dirs():
    bad = chk.violations(["docs/x/daily_AAPL.csv", "a/_raw/prices/yahoo_AAPL.json", "b/raw/c.json",
                          "market_cache/x.json", "docs/ok/manifest.json", "docs/ok/data_points.csv"])
    assert set(bad) == {"docs/x/daily_AAPL.csv", "a/_raw/prices/yahoo_AAPL.json", "b/raw/c.json", "market_cache/x.json"}


def test_checker_flags_ohlc_like_csv_content(tmp_path):
    p = tmp_path / "prices.csv"
    p.write_text("date,raw_close,adj_close,daily_ret_from_adj\n2026-01-01,1,1,\n")
    assert chk.looks_like_price_csv(p)
    q = tmp_path / "other.csv"
    q.write_text("ticker,cik\nAAPL,1\n")
    assert not chk.looks_like_price_csv(q)


def test_repo_tracks_no_raw_market_files():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    assert chk.violations(files, ROOT) == []


def test_gitignore_covers_price_csv_and_raw_dirs():
    gi = (ROOT / "docs/research/real_data_sample_20261011/.gitignore").read_text()
    for pat in ("_raw/", "_out/", "daily_*.csv"):
        assert pat in gi


def test_price_manifest_present_and_complete():
    import json
    m = json.loads((ROOT / "docs/research/real_data_sample_20261011/price_manifest.json").read_text())
    for tk in ("AAPL", "JNJ", "ADBE"):
        e = m["files"][f"daily_{tk}.csv"]
        assert len(e["sha256"]) == 64 and e["rows"] > 0 and len(e["raw_close_sha256"]) == 64
