#!/usr/bin/env python3
"""Standalone hand recompute of GP/A and Sloan accruals from data_points.csv (v2, audit 2026-10-11).
stdlib only; does NOT import repo code (see crosscheck_repo_code.py for the repo-code cross-check).

GP/A  = GrossProfit / Assets(end of FY).
Sloan (1996) accruals = (dCA - dCash) - (dCL - dSTD - dTP) - Dep, scaled by average total assets.
  dCash (PRIMARY) = change in (cash and cash equivalents + short-term investments)
  'previous (cash only)' = the v1 definition (cash and equivalents only), kept for comparison.
  Dep = the single cash-flow-statement line tagged us-gaap:DepreciationDepletionAndAmortization.
  Component list per company (explicit; NO other amortization/accretion tag is added or removed):
    AAPL  us-gaap:DepreciationDepletionAndAmortization  (CF label 'Depreciation and amortization')
    JNJ   us-gaap:DepreciationDepletionAndAmortization  (CF label 'Depreciation and amortization of property and intangibles')
    ADBE  us-gaap:DepreciationDepletionAndAmortization  (CF label 'Depreciation, amortization and accretion'  <- INCLUDES ACCRETION, not separable here)
CF alternative = (NetIncome - CFO) / average total assets (labelled alternative, NOT Sloan's original).
Compustat item -> XBRL tag mapping is UNVERIFIED. Sloan is NOT usable for scoring stocks.
"""
import csv, json, sys
DATA = sys.argv[1] if len(sys.argv) > 1 else "data_points.csv"
RESULTS_OUT = sys.argv[2] if len(sys.argv) > 2 else "results.json"
USED_OUT = sys.argv[3] if len(sys.argv) > 3 else "used_inputs.json"
USED = set()
STD = {"AAPL": ["CommercialPaper", "LongTermDebtCurrent"], "JNJ": ["ShortTermBorrowings"], "ADBE": ["DebtCurrent"]}
STI = {"AAPL": "MarketableSecuritiesCurrent", "JNJ": "MarketableSecuritiesCurrent", "ADBE": "ShortTermInvestments"}
DEP_TAGS = {"AAPL": ["DepreciationDepletionAndAmortization"], "JNJ": ["DepreciationDepletionAndAmortization"],
            "ADBE": ["DepreciationDepletionAndAmortization"]}
rows = list(csv.DictReader(open(DATA)))


def val(t, acc, tag, end):
    m = [r for r in rows if r["ticker"] == t and r["accession"] == acc and r["tag"] == "us-gaap:" + tag and r["end"] == end]
    assert len(m) == 1, (t, acc, tag, end, len(m))
    USED.add((t, acc, "us-gaap:" + tag, end))
    return float(m[0]["value"])


def src(t, acc, tag, end):
    m = [r for r in rows if r["ticker"] == t and r["accession"] == acc and r["tag"] == "us-gaap:" + tag and r["end"] == end][0]
    return m["rfile_ref"] or "companyfacts-only"


out = []
for t in STD:
    accs = sorted({r["accession"] for r in rows if r["ticker"] == t},
                  key=lambda a: [r["period_end_fy"] for r in rows if r["accession"] == a][0])
    for acc in accs:
        rs = [r for r in rows if r["accession"] == acc]
        end = rs[0]["period_end_fy"]
        prior = sorted({r["end"] for r in rs if r["tag"] == "us-gaap:Assets"})[0]
        assert prior < end
        g = lambda tag, e=end: val(t, acc, tag, e)
        gp = g("GrossProfit"); A = g("Assets"); Ap = g("Assets", prior); Aavg = (A + Ap) / 2
        dCA = g("AssetsCurrent") - g("AssetsCurrent", prior)
        dCashOnly = g("CashAndCashEquivalentsAtCarryingValue") - g("CashAndCashEquivalentsAtCarryingValue", prior)
        dSTI = g(STI[t]) - g(STI[t], prior)
        dCash = dCashOnly + dSTI
        dCL = g("LiabilitiesCurrent") - g("LiabilitiesCurrent", prior)
        dSTD = sum(g(x) - g(x, prior) for x in STD[t])
        dTP = g("AccruedIncomeTaxesCurrent") - g("AccruedIncomeTaxesCurrent", prior)
        dep = sum(g(x) for x in DEP_TAGS[t])
        acc_bs = (dCA - dCash) - (dCL - dSTD - dTP) - dep
        acc_bs_cash_only = (dCA - dCashOnly) - (dCL - dSTD - dTP) - dep
        ni = g("NetIncomeLoss"); cfo = g("NetCashProvidedByUsedInOperatingActivities")
        out.append(dict(
            t=t, acc=acc, end=end, prior=prior, gp=gp, A=A, Ap=Ap, Aavg=Aavg, dCA=dCA, dCashOnly=dCashOnly,
            dSTI=dSTI, dCash=dCash, dCL=dCL, dSTD=dSTD, dTP=dTP, dep=dep, dep_tags=DEP_TAGS[t],
            sti_tag=STI[t], std_tags=STD[t],
            tp_source=src(t, acc, "AccruedIncomeTaxesCurrent", end), sti_source=src(t, acc, STI[t], end),
            gpa=gp / A, accr_bs=acc_bs, sloan=acc_bs / Aavg,
            sloan_previous_cash_only=acc_bs_cash_only / Aavg,
            ni=ni, cfo=cfo, accr_cf=ni - cfo, sloan_cf=(ni - cfo) / Aavg))
json.dump(out, open(RESULTS_OUT, "w"), indent=1)
json.dump(sorted(USED), open(USED_OUT, "w"), indent=0)  # every data_points.csv row read by val(): the inputs actually used
for o in out:
    print(f"{o['t']} FY end {o['end']} ({o['acc']}) prior {o['prior']}")
    for k in ["gp", "A", "Ap", "Aavg", "dCA", "dCashOnly", "dSTI", "dCash", "dCL", "dSTD", "dTP", "dep", "ni", "cfo"]:
        print(f"   {k:9s} {o[k]:>20,.0f}")
    print(f"   sources: sti={o['sti_tag']}@{o['sti_source']} tax_payable@{o['tp_source']} dep_tags={o['dep_tags']}")
    print(f"   GP/A = {o['gp']:,.0f}/{o['A']:,.0f} = {o['gpa']:.6f}")
    print(f"   Sloan BS accruals (cash+ST inv) = {(o['accr_bs']):,.0f}; scaled = {o['sloan']:.6f}   [previous (cash only) = {o['sloan_previous_cash_only']:.6f}]")
    print(f"   CF-version (NI-CFO)={o['accr_cf']:,.0f}; scaled = {o['sloan_cf']:.6f}")
