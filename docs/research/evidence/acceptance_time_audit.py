#!/usr/bin/env python3
"""Audit of SEC acceptance times: submissions-JSON `acceptanceDateTime` vs the accession header
<ACCEPTANCE-DATETIME> (US Eastern wall clock, also shown as 'Accepted' on the filing index page).
Re-fetches live from SEC (UA below, <=4 req/s). Output: acceptance_time_audit_results.json + a table on stdout.

  python acceptance_time_audit.py [--per-month-sample]
"""
import argparse, datetime as D, json, re, sys, time, urllib.request
from zoneinfo import ZoneInfo
UA = {"User-Agent": "QuantDetective research contact-research@example.com"}
ET = ZoneInfo("America/New_York")
CIKS = {"AAPL": "0000320193", "JNJ": "0000200406", "ADBE": "0000796343"}
SIX = {"0000320193-24-000123", "0000320193-25-000079", "0000200406-25-000038", "0000200406-26-000016",
       "0000796343-25-000004", "0000796343-26-000003"}


def get(url):
    time.sleep(0.3)
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "ignore")


out = []
for t, cik in CIKS.items():
    sub = json.loads(get(f"https://data.sec.gov/submissions/CIK{cik}.json"))["filings"]["recent"]
    picked, seen = [], set()
    for i, acc in enumerate(sub["accessionNumber"]):
        m = sub["filingDate"][i][:7]
        if acc in SIX or (m not in seen and "2024-01" <= m <= "2026-09" and len(picked) < (26 if t == "AAPL" else 10)):
            seen.add(m); picked.append(i)
    for i in picked:
        acc = sub["accessionNumber"][i]; nd = acc.replace("-", ""); ci = str(int(cik))
        base = f"https://www.sec.gov/Archives/edgar/data/{ci}/{nd}"
        hdr = get(f"{base}/{acc}.hdr.sgml")
        hm = re.search(r"ACCEPTANCE-DATETIME>(\d{14})", hdr)
        if not hm:
            continue
        naive = D.datetime.strptime(hm.group(1), "%Y%m%d%H%M%S")
        idx_acc = None
        if acc in SIX:
            ih = get(f"{base}/{acc}-index.htm")
            idx_acc = re.search(r"Accepted</div>\s*<div[^>]*>\s*([\d\- :]+?)\s*<", ih).group(1)
        true_utc = naive.replace(tzinfo=ET).astimezone(D.timezone.utc)
        raw = sub["acceptanceDateTime"][i]
        raw_dt = D.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        out.append(dict(ticker=t, accession=acc, form=sub["form"][i], filing_date=sub["filingDate"][i],
                        raw_submissions_field=raw, hdr_acceptance_et_wall=naive.strftime("%Y-%m-%d %H:%M:%S"),
                        index_page_accepted=idx_acc, et_zone=true_utc.astimezone(ET).tzname(),
                        true_utc_from_index=true_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        raw_minus_true_utc_hours=(raw_dt - true_utc).total_seconds() / 3600, in_six=acc in SIX))
json.dump(out, open(sys.argv[0].replace(".py", "_results.json"), "w"), indent=1)
for tkr in CIKS:
    r = [x for x in out if x["ticker"] == tkr]
    diffs = sorted({x["raw_minus_true_utc_hours"] for x in r})
    print(f"{tkr}: {len(r)} filings audited; raw-minus-true-UTC hours seen: {diffs}")
for x in out:
    if x["in_six"]:
        print(x["ticker"], x["accession"], "raw", x["raw_submissions_field"], "| index Accepted", x["index_page_accepted"], x["et_zone"], "| true UTC", x["true_utc_from_index"], "| diff h", x["raw_minus_true_utc_hours"])
