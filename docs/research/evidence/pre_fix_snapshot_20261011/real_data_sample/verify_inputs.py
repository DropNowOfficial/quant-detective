#!/usr/bin/env python3
"""Re-verify committed sample values against freshly downloaded raw files (fetch_all.py output).

  python verify_inputs.py --raw _raw [--ret-tol 1e-4] [--adj-rel-tol 1e-6]

SEC (exact comparison, stdlib):
  * every data_points.csv value == fresh companyfacts fact (same tag/accn/start/end);
  * every face-statement / note value == fresh R-page value (rfile_value);
  * acceptance: fresh filing-index 'Accepted' (ET) and hdr ACCEPTANCE-DATETIME == committed
    acceptance_index_et_naive; fresh submissions acceptanceDateTime == committed raw field.
Prices (hash first, tolerance fallback):
  * if SHA256 of a fresh Yahoo/Nasdaq file equals the originally-used hash -> MATCH;
  * otherwise MISMATCH is printed and the committed daily_<T>.csv (the originally used values) is
    compared with the fresh download: dates identical, raw close within 0.005, daily returns
    within --ret-tol (abs) and adjusted close within --adj-rel-tol (relative). The tolerance
    comparison is stated in the output; adjusted close may legitimately change on re-download
    (dividends / vendor revisions).
Exit 0 only if every comparison passes.
"""
from __future__ import annotations
import argparse, csv, datetime as D, json, pathlib, re, sys
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import extract  # noqa: E402

ET = ZoneInfo("America/New_York")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(HERE / "_raw"))
    ap.add_argument("--max-fresh", default=str(HERE / "_out" / "max_results.json"))
    ap.add_argument("--ret-tol", type=float, default=1e-4)
    ap.add_argument("--adj-rel-tol", type=float, default=1e-6)
    a = ap.parse_args()
    raw = pathlib.Path(a.raw)
    fails: list[str] = []
    rows = list(csv.DictReader(open(HERE / "data_points.csv")))

    # ---- hash status from fetch_all
    log = json.load(open(raw / "retrieval_log.json"))
    status = {f["path"]: f["status"] for f in log["files"]}
    print("fetch_all hash counts:", log["counts"], "| retrieved", log["started_utc"], "->", log["finished_utc"])
    for p, s in status.items():
        if s == "MISMATCH":
            print(f"  MISMATCH (byte-level) {p}  -> falling back to value/tolerance comparison below")

    # ---- SEC facts
    cf_cache, n_fact = {}, 0
    for r in rows:
        cik = r["cik"]
        cf = cf_cache.setdefault(cik, json.load(open(raw / f"companyfacts_{cik}.json"))["facts"]["us-gaap"])
        tag = r["tag"].split(":", 1)[1]
        hits = [x for x in cf[tag]["units"]["USD"] if x["accn"] == r["accession"] and x["form"] == "10-K"
                and x["end"] == r["end"] and (x.get("start") or "") == (r["start"] or "")]
        if len(hits) != 1 or float(hits[0]["val"]) != float(r["value"]):
            fails.append(f"companyfacts {r['ticker']} {r['accession']} {tag} {r['end']}: committed {r['value']} fresh {[h['val'] for h in hits]}")
        n_fact += 1
    print(f"SEC companyfacts: {n_fact} data points compared (exact)")

    # ---- R pages
    n_r = 0
    rcache = {}
    for r in rows:
        if not r["rfile_ref"]:
            continue
        rn = int(r["rfile_ref"][1:-4])
        key = (r["ticker"], r["accession"], rn)
        vals = rcache.setdefault(key, extract.rfile_vals(raw, *key))
        tag = r["tag"].split(":", 1)[1]
        col = 0 if r["end"] == r["period_end_fy"] else 1
        got = vals.get(tag, [None, None])[col]
        got = None if got is None else got * 1e6
        if got is None or float(r["rfile_value"]) != got:
            fails.append(f"R page {key} {tag}: committed {r['rfile_value']} fresh {got}")
        n_r += 1
    print(f"SEC R pages: {n_r} values compared (exact)")

    # ---- acceptance times
    seen = set()
    for r in rows:
        k = (r["ticker"], r["accession"])
        if k in seen:
            continue
        seen.add(k)
        naive = extract.index_accepted_naive(raw, *k)    # also asserts index == hdr
        sub = json.load(open(raw / f"submissions_{r['cik']}.json"))["filings"]["recent"]
        raw_field = sub["acceptanceDateTime"][sub["accessionNumber"].index(r["accession"])]
        if naive != r["acceptance_index_et_naive"]:
            fails.append(f"acceptance index {k}: committed {r['acceptance_index_et_naive']} fresh {naive}")
        if raw_field != r["acceptance_raw_submissions_field"]:
            fails.append(f"acceptance raw field {k}: committed {r['acceptance_raw_submissions_field']} fresh {raw_field}")
    print(f"SEC acceptance times: {len(seen)} filings compared (index page, hdr, submissions field)")

    # ---- prices
    for tk in ("AAPL", "JNJ", "ADBE"):
        ys, ns = status.get(f"prices/yahoo_{tk}.json"), status.get(f"prices/nasdaq_{tk}.json")
        orig = {r["date"]: r for r in csv.DictReader(open(HERE / f"daily_{tk}.csv"))}
        c = json.load(open(raw / "prices" / f"yahoo_{tk}.json"))["chart"]["result"][0]
        q, adj = c["indicators"]["quote"][0], c["indicators"]["adjclose"][0]["adjclose"]
        fresh = {D.datetime.fromtimestamp(t, ET).date().isoformat(): (q["close"][i], adj[i]) for i, t in enumerate(c["timestamp"])}
        print(f"{tk}: yahoo sha256 {ys}; nasdaq sha256 {ns}")
        if set(fresh) != set(orig):
            fails.append(f"{tk}: date sets differ: only-fresh {sorted(set(fresh)-set(orig))[:3]} only-committed {sorted(set(orig)-set(fresh))[:3]}")
            continue
        if ys == "MATCH":
            print(f"  yahoo: byte-identical to original -> no tolerance needed")
        else:
            ds = sorted(orig)
            max_raw = max(abs(float(orig[d]["raw_close"]) - fresh[d][0]) for d in ds)
            max_adj = max(abs(float(orig[d]["adj_close"]) / fresh[d][1] - 1) for d in ds)
            max_ret = 0.0
            for i in range(1, len(ds)):
                r_new = fresh[ds[i]][1] / fresh[ds[i - 1]][1] - 1
                max_ret = max(max_ret, abs(float(orig[ds[i]]["daily_ret_from_adj"]) - r_new))
            ok = max_raw <= 0.005 and max_adj <= a.adj_rel_tol and max_ret <= a.ret_tol
            print(f"  yahoo MISMATCH -> tolerance comparison (raw close <=0.005, adj rel <= {a.adj_rel_tol}, return abs <= {a.ret_tol}): "
                  f"max|raw diff|={max_raw:.6g} max adj rel diff={max_adj:.3g} max return diff={max_ret:.3g} -> {'WITHIN TOLERANCE' if ok else 'OUT OF TOLERANCE'}")
            if not ok:
                fails.append(f"{tk}: yahoo re-download differs beyond tolerance")
        nq = json.load(open(raw / "prices" / f"nasdaq_{tk}.json"))["data"]["tradesTable"]["rows"]
        nqc = {D.datetime.strptime(r["date"], "%m/%d/%Y").date().isoformat(): float(r["close"].replace("$", "").replace(",", "")) for r in nq}
        bad = [d for d in orig if nqc.get(d) is None or abs(nqc[d] - fresh[d][0]) > 0.005]
        print(f"  fresh yahoo raw close vs fresh nasdaq close: {len(orig) - len(bad)}/{len(orig)} within 0.005")
        if bad:
            fails.append(f"{tk}: nasdaq vs yahoo raw close mismatches {bad[:3]}")

    # ---- MAX recompute (fixed tz-correct implementation) vs committed baseline
    fresh_max = pathlib.Path(a.max_fresh)
    if fresh_max.exists():
        base, new = json.load(open(HERE / "max_results.json")), json.load(open(fresh_max))
        for tk in ("AAPL", "JNJ", "ADBE"):
            for key in ("MAX_primary_prior_calendar_month_Sep2026", "MAX_fixed_21_trading_days_asof_2026_09_30",
                        "MAX_fixed_21_trading_days_asof_2026_10_11"):
                d = abs(base[tk][key]["ret"] - new[tk][key]["ret"])
                tag = "exact" if d == 0 else f"diff {d:.3g} (tolerance {a.ret_tol})"
                print(f"MAX {tk} {key}: baseline {base[tk][key]['ret']:.6f} fresh {new[tk][key]['ret']:.6f} -> {tag}")
                if d > a.ret_tol:
                    fails.append(f"MAX {tk} {key} differs by {d}")
    else:
        fails.append(f"missing fresh MAX results {fresh_max} (run max_prices.py first)")

    print("VERIFY_INPUTS_OK" if not fails else "VERIFY_INPUTS_FAIL")
    for f in fails:
        print("  FAIL:", f)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
