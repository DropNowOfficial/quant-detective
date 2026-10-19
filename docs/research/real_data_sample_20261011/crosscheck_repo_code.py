#!/usr/bin/env python3
"""Cross-check: feed data_points.csv through the repo's factor_candidates code (GP/A, Sloan) and
compare to the standalone recompute.py results. Run at the repo HEAD so results correspond to the
fixed code. Prior-year statement = comparative column of the SAME 10-K (same known_at, ref ':comparative').

  python crosscheck_repo_code.py
"""
import csv, datetime as D, json, pathlib, subprocess, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from factor_candidates import BalanceSheetFact, FundamentalFact, gross_profitability, sloan_accruals  # noqa: E402

rows = list(csv.DictReader(open(HERE / "data_points.csv")))
res = json.load(open(HERE / "results.json"))
STD = {"AAPL": ["CommercialPaper", "LongTermDebtCurrent"], "JNJ": ["ShortTermBorrowings"], "ADBE": ["DebtCurrent"]}
STI = {"AAPL": "MarketableSecuritiesCurrent", "JNJ": "MarketableSecuritiesCurrent", "ADBE": "ShortTermInvestments"}


def v(t, acc, tag, end):
    m = [r for r in rows if r["ticker"] == t and r["accession"] == acc and r["tag"] == "us-gaap:" + tag and r["end"] == end]
    assert len(m) == 1
    return float(m[0]["value"])


out, ok = [], True
for o in res:
    t, acc, end, prior = o["t"], o["acc"], o["end"], o["prior"]
    r0 = [r for r in rows if r["accession"] == acc][0]
    known = D.datetime.fromisoformat(r0["acceptance_utc"].replace("Z", "+00:00"))
    as_of = known + D.timedelta(minutes=1)
    common = dict(security=t, known_at=known, source="SEC EDGAR 10-K via companyfacts/R pages (data_points.csv)",
                  period_type="annual", currency="USD", unit_scale=1.0, revision=0)

    def bs(e, ref):
        g = lambda tag: v(t, acc, tag, e)
        return BalanceSheetFact(
            period_end=D.date.fromisoformat(e), statement_ref=ref, current_assets=g("AssetsCurrent"),
            cash=g("CashAndCashEquivalentsAtCarryingValue"), short_term_investments=g(STI[t]),
            current_liabilities=g("LiabilitiesCurrent"), short_term_debt=sum(g(x) for x in STD[t]),
            tax_payable=g("AccruedIncomeTaxesCurrent"),
            depreciation_amortization=g("DepreciationDepletionAndAmortization") if e == end else None,
            total_assets=g("Assets"), **common)
    cur, prv = bs(end, acc), bs(prior, acc + ":comparative")
    sloan = sloan_accruals([cur, prv], t, as_of)
    ff = FundamentalFact(period_end=D.date.fromisoformat(end), statement_ref=acc,
                         revenue=v(t, acc, "GrossProfit", end), cogs=0.0, total_assets=v(t, acc, "Assets", end), **common)
    gpa = gross_profitability([ff], t, as_of)   # revenue=GrossProfit, cogs=0 -> GP/A (GrossProfit is a reported tag)
    d1, d2 = abs(sloan - o["sloan"]), abs(gpa - o["gpa"])
    ok &= d1 < 1e-12 and d2 < 1e-12
    out.append(dict(ticker=t, accession=acc, as_of_utc=as_of.isoformat(), repo_sloan=sloan, standalone_sloan=o["sloan"],
                    repo_gpa=gpa, standalone_gpa=o["gpa"], abs_diff_sloan=d1, abs_diff_gpa=d2))
    print(f"{t} {end} repo Sloan {sloan:.6f} vs standalone {o['sloan']:.6f} | repo GP/A {gpa:.6f} vs {o['gpa']:.6f}")
head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
tree = subprocess.run(["git", "rev-parse", "HEAD:factor_candidates"], capture_output=True, text=True, cwd=HERE.parents[2]).stdout.strip()
json.dump(dict(git_head=head, factor_candidates_tree=tree, all_agree_within_tol=bool(ok), rows=out),
          open(HERE / "crosscheck_repo_code_results.json", "w"), indent=1)
print("CROSSCHECK_OK" if ok else "CROSSCHECK_FAIL")
sys.exit(0 if ok else 1)
