#!/usr/bin/env python3
"""Build data_points.csv from raw SEC files (v2, audit 2026-10-11).

  python extract.py --raw _raw --out data_points.csv

Changes vs v1 (see docs/research/evidence/acceptance_time_audit.md):
 * Acceptance time: the SEC *filing index page* 'Accepted' value (US Eastern wall clock,
   cross-checked against the .hdr.sgml <ACCEPTANCE-DATETIME>) is the authority.
   acceptance_utc / acceptance_et are derived from it. The raw submissions-JSON
   `acceptanceDateTime` is kept verbatim in acceptance_raw_submissions_field and compared;
   for AAPL it is 4h (EDT) / 5h (EST) later than the true instant, so v1 reported it wrongly.
 * New inputs: short-term investments (Sloan dCash scope), AAPL income-taxes-payable from the
   10-K note (R page recorded in rfile_page/rfile_ref).
"""
from __future__ import annotations

import argparse, csv, datetime as D, html, json, pathlib, re
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
CO = {
    "AAPL": dict(cik="0000320193", filings={2024: "0000320193-24-000123", 2025: "0000320193-25-000079"},
                 std=["CommercialPaper", "LongTermDebtCurrent"], sti="MarketableSecuritiesCurrent",
                 R={"0000320193-24-000123": dict(IS=3, BS=5, CF=8, NOTE_OTHER_CURRENT_LIAB=50),
                    "0000320193-25-000079": dict(IS=3, BS=5, CF=8, NOTE_OTHER_CURRENT_LIAB=49)}),
    "JNJ": dict(cik="0000200406", filings={2024: "0000200406-25-000038", 2025: "0000200406-26-000016"},
                std=["ShortTermBorrowings"], sti="MarketableSecuritiesCurrent",
                R={a: dict(IS=5, BS=3, CF=10) for a in ("0000200406-25-000038", "0000200406-26-000016")}),
    "ADBE": dict(cik="0000796343", filings={2024: "0000796343-25-000004", 2025: "0000796343-26-000003"},
                 std=["DebtCurrent"], sti="ShortTermInvestments",
                 R={a: dict(IS=5, BS=3, CF=8) for a in ("0000796343-25-000004", "0000796343-26-000003")}),
}
FLOW = ["GrossProfit", "NetIncomeLoss", "NetCashProvidedByUsedInOperatingActivities", "DepreciationDepletionAndAmortization"]
INST = ["Assets", "AssetsCurrent", "CashAndCashEquivalentsAtCarryingValue", "LiabilitiesCurrent", "AccruedIncomeTaxesCurrent"]


def index_accepted_naive(raw: pathlib.Path, t: str, acc: str) -> str:
    nd = acc.replace("-", "")
    h = (raw / "filing_index" / f"{t}_{nd}_index.htm").read_text(errors="ignore")
    m = re.search(r'Accepted</div>\s*<div[^>]*>\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s*<', h)
    assert m, f"no Accepted time in index page {t} {acc}"
    hdr = (raw / "filing_index" / f"{t}_{nd}_hdr.sgml").read_text(errors="ignore")
    hm = re.search(r"ACCEPTANCE-DATETIME>(\d{14})", hdr)
    assert hm, f"no hdr ACCEPTANCE-DATETIME {t} {acc}"
    hdr_dt = D.datetime.strptime(hm.group(1), "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M:%S")
    assert hdr_dt == m.group(1), f"index page {m.group(1)} != hdr {hdr_dt} for {acc}"
    return m.group(1)


def rfile_vals(raw: pathlib.Path, t: str, acc: str, r: int) -> dict:
    fn = raw / "filings" / f"{t}_{acc.replace('-', '')}_R{r}.htm"
    out = {}
    if not fn.exists():
        return out
    h = fn.read_text(errors="ignore")
    for row in re.findall(r"<tr.*?</tr>", h, re.S):
        m = re.search(r"defref_([A-Za-z\-_0-9]+)", row)
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[hd][^>]*>.*?</t[hd]>", row, re.S)]
        if not m or len(cells) < 3:
            continue

        def num(s):
            s = s.replace("$", "").replace(",", "").strip()
            neg = s.startswith("(")
            s = s.strip("()")
            try:
                return -float(s) if neg else float(s)
            except ValueError:
                return None
        out.setdefault(m.group(1).replace("us-gaap_", ""), [num(cells[1]), num(cells[2])])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="_raw")
    ap.add_argument("--out", default="data_points.csv")
    a = ap.parse_args()
    raw = pathlib.Path(a.raw)
    rows = []
    for t, c in CO.items():
        sub = json.load(open(raw / f"submissions_{c['cik']}.json"))["filings"]["recent"]
        cf = json.load(open(raw / f"companyfacts_{c['cik']}.json"))["facts"]["us-gaap"]
        amend = [(sub["form"][i], sub["accessionNumber"][i]) for i in range(len(sub["form"])) if sub["form"][i].startswith("10-K/A")]
        for fy, acc in c["filings"].items():
            i = sub["accessionNumber"].index(acc)
            form, filed, rep = sub["form"][i], sub["filingDate"][i], sub["reportDate"][i]
            raw_field = sub["acceptanceDateTime"][i]
            naive = index_accepted_naive(raw, t, acc)
            acc_utc_dt = D.datetime.strptime(naive, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ET).astimezone(D.timezone.utc)
            acc_et = acc_utc_dt.astimezone(ET).isoformat()
            acc_utc = acc_utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            raw_dt = D.datetime.fromisoformat(raw_field.replace("Z", "+00:00"))
            check = "raw_field_matches_index" if raw_dt == acc_utc_dt else "RAW_FIELD_MISMATCH_index_used"
            rv = {}
            for k, r in c["R"][acc].items():
                rv.update({kk: (k, r, v) for kk, v in rfile_vals(raw, t, acc, r).items()})
            tags = FLOW + INST + c["std"] + [c["sti"]]
            for tag in tags:
                ents = [x for x in cf.get(tag, {}).get("units", {}).get("USD", []) if x["accn"] == acc and x["form"] == "10-K"]
                for x in ents:
                    if "start" in x:
                        dur = (D.date.fromisoformat(x["end"]) - D.date.fromisoformat(x["start"])).days
                        if not 360 <= dur <= 372:
                            continue
                        ptype = "FY(duration %dd)" % dur
                    else:
                        ptype = "instant"
                    others = [y for y in cf[tag]["units"]["USD"] if y["end"] == x["end"] and y.get("start") == x.get("start") and y["accn"] != acc]
                    restated = [(y["accn"], y["val"]) for y in others if y["val"] != x["val"]]
                    col = 0 if x["end"] == rep else 1
                    rf = rv.get(tag)
                    rfval = rfsrc = rfref = None
                    if rf and rf[2][col] is not None:
                        rfval, rfsrc, rfref = rf[2][col] * 1e6, rf[0], f"R{rf[1]}.htm"
                    rows.append(dict(
                        ticker=t, cik=c["cik"], accession=acc, form=form, filing_date=filed,
                        acceptance_utc=acc_utc, acceptance_et=acc_et,
                        acceptance_index_et_naive=naive, acceptance_raw_submissions_field=raw_field, acceptance_check=check,
                        period_end_fy=rep, fiscal_year_label=fy, period_type=ptype, tag="us-gaap:" + tag, unit="USD", currency="USD",
                        scale="as stored in XBRL (full USD)", start=x.get("start"), end=x["end"], value=x["val"],
                        companyfacts_fy=x["fy"], companyfacts_fp=x["fp"],
                        later_restatement=";".join(f"{aa}:{v}" for aa, v in restated) or "none",
                        amendments_10KA_in_recent_submissions=";".join(aa for _, aa in amend) or "none",
                        rfile_value=rfval, rfile_page=rfsrc, rfile_ref=rfref,
                        rfile_matches=("" if rfval is None else rfval == x["val"])))
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    import collections
    print(len(rows), "rows")
    print(collections.Counter((r["ticker"], r["rfile_matches"]) for r in rows))
    print(collections.Counter(r["acceptance_check"] for r in rows))


if __name__ == "__main__":
    main()
