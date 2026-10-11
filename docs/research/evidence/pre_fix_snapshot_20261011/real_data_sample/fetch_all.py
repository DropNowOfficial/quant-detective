#!/usr/bin/env python3
"""Download every raw input of the real-data sample into an UNTRACKED directory,
record retrieval times, and compare SHA256 with the committed manifests.

  python fetch_all.py --out _raw            # default: ./_raw  (git-ignored)

Raw data is never committed (AGENTS.md rule 6). Manifests committed here:
  SHA256SUMS.txt                 hashes of the ORIGINALLY used files (2026-10-11 ~07:51-07:56 ET)
  SHA256SUMS_audit_20261011.txt  hashes of files added by the 2026-10-11 audit (index pages, R49/R50)

Hash outcome per file:  MATCH | MISMATCH | NO_EXPECTED_HASH.
MISMATCH is expected for some files (SEC companyfacts/submissions are regenerated and
grow; Yahoo JSON meta changes and adjusted close may be revised). verify_inputs.py then
falls back to comparing VALUES within a stated tolerance and says so.
Stdlib only. SEC: descriptive User-Agent, <= 4 requests/second.
"""
from __future__ import annotations

import argparse, datetime as dt, hashlib, json, pathlib, sys, time, urllib.error, urllib.request
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
SEC_UA = "QuantDetective research contact-research@example.com"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) QuantDetective-research"
ET = ZoneInfo("America/New_York")
CO = {
    "AAPL": dict(cik="0000320193", cik_int="320193", accs=["0000320193-24-000123", "0000320193-25-000079"]),
    "JNJ": dict(cik="0000200406", cik_int="200406", accs=["0000200406-25-000038", "0000200406-26-000016"]),
    "ADBE": dict(cik="0000796343", cik_int="796343", accs=["0000796343-25-000004", "0000796343-26-000003"]),
}
# R pages (face statements + detail pages) used by extract.py; see FilingSummary for titles
RPAGES = {
    "AAPL": {"0000320193-24-000123": list(range(2, 9)) + [50], "0000320193-25-000079": list(range(2, 9)) + [49]},
    "JNJ": {"0000200406-25-000038": list(range(2, 12)), "0000200406-26-000016": list(range(2, 12))},
    "ADBE": {"0000796343-25-000004": list(range(2, 10)), "0000796343-26-000003": list(range(2, 10))},
}
WINDOW = ("2026-07-01", "2026-10-09")   # inclusive trading-day window used originally


def targets():
    out = []  # (relpath, url, user_agent, is_sec)
    for t, c in CO.items():
        out.append((f"submissions_{c['cik']}.json", f"https://data.sec.gov/submissions/CIK{c['cik']}.json", SEC_UA, True))
        out.append((f"companyfacts_{c['cik']}.json", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c['cik']}.json", SEC_UA, True))
        for acc in c["accs"]:
            nd = acc.replace("-", "")
            base = f"https://www.sec.gov/Archives/edgar/data/{c['cik_int']}/{nd}"
            out.append((f"filing_index/{t}_{nd}_index.htm", f"{base}/{acc}-index.htm", SEC_UA, True))
            out.append((f"filing_index/{t}_{nd}_hdr.sgml", f"{base}/{acc}.hdr.sgml", SEC_UA, True))
            if t == "AAPL":
                out.append((f"filing_index/{t}_{nd}_FilingSummary.xml", f"{base}/FilingSummary.xml", SEC_UA, True))
            for r in RPAGES[t][acc]:
                out.append((f"filings/{t}_{nd}_R{r}.htm", f"{base}/R{r}.htm", SEC_UA, True))
        p1 = int(dt.datetime(2026, 7, 1, tzinfo=ET).timestamp())
        p2 = int(dt.datetime(2026, 10, 10, tzinfo=ET).timestamp())
        out.append((f"prices/yahoo_{t}.json",
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?interval=1d&period1={p1}&period2={p2}&events=div%7Csplit",
                    BROWSER_UA, False))
        out.append((f"prices/nasdaq_{t}.json",
                    f"https://api.nasdaq.com/api/quote/{t}/historical?assetclass=stocks&fromdate={WINDOW[0]}&todate={WINDOW[1]}&limit=9999",
                    BROWSER_UA, False))
    return out


def load_manifest(path: pathlib.Path) -> dict[str, str]:
    m = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                h, p = line.split(None, 1)
                m[p.strip().lstrip("./")] = h
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "_raw"))
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    expected = {**load_manifest(HERE / "SHA256SUMS.txt"), **load_manifest(HERE / "SHA256SUMS_audit_20261011.txt")}
    log, counts = [], {"MATCH": 0, "MISMATCH": 0, "NO_EXPECTED_HASH": 0, "FETCH_FAILED": 0}
    started = dt.datetime.now(dt.timezone.utc)
    for rel, url, ua, is_sec in targets():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        t0 = dt.datetime.now(dt.timezone.utc)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            dest.write_bytes(body)
            sha = hashlib.sha256(body).hexdigest()
            exp = expected.get(rel)
            status = "NO_EXPECTED_HASH" if exp is None else ("MATCH" if exp == sha else "MISMATCH")
        except (urllib.error.URLError, TimeoutError) as e:
            sha, exp, status = None, expected.get(rel), "FETCH_FAILED"
            print(f"FETCH_FAILED {rel}: {e}", file=sys.stderr)
        counts[status] += 1
        print(f"{status:17s} {rel}")
        log.append(dict(path=rel, url=url, retrieved_at_utc=t0.isoformat(timespec="seconds"), sha256=sha,
                        expected_sha256=exp, status=status))
        time.sleep(0.3 if is_sec else 0.6)
    (out / "retrieval_log.json").write_text(json.dumps(
        dict(started_utc=started.isoformat(timespec="seconds"), finished_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             counts=counts, files=log), indent=1))
    print("SUMMARY", json.dumps(counts))
    if counts["MISMATCH"]:
        print("MISMATCH: some re-downloaded files differ byte-for-byte from the originals. This is expected for "
              "regenerated/live sources; run verify_inputs.py, which compares values within stated tolerances.")
    return 0 if counts["FETCH_FAILED"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
