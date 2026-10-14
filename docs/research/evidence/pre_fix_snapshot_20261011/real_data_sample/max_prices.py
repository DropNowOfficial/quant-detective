#!/usr/bin/env python3
"""MAX from real prices using the repo's FIXED (calendar-tz explicit) implementation (v2, audit 2026-10-11).

  python max_prices.py --raw _raw --out-dir _out   # default out-dir is untracked _out/

Source: Yahoo chart API JSON (raw close + adjclose; adjustment convention is Yahoo's, observed not documented),
raw close cross-checked against Nasdaq.com historical JSON. Daily return r_t = adj_t/adj_{t-1}-1.
Trading calendar = the dates of Yahoo daily bars converted with the exchange timezone America/New_York
(calendar_tz is an explicit parameter; same instant -> same value in any offset, shown below).

known_at of each return row is ASSUMED to be the 16:00 ET close of that session (we cannot know the vendor's
publication time); this lets the as-of selection logic run, it is NOT point-in-time evidence.
Also recomputes with an independent standalone implementation and asserts agreement.
Not an investment claim.
"""
from __future__ import annotations

import argparse, datetime as D, json, pathlib, subprocess, sys
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from factor_candidates import DailyReturn, max_lottery  # noqa: E402

ET = ZoneInfo("America/New_York")
UTC = D.timezone.utc
TICKERS = ["AAPL", "JNJ", "ADBE"]
BASIS = "yahoo_adjclose_split_dividend_adjusted_close_to_close"


def load(raw: pathlib.Path, tk: str):
    c = json.load(open(raw / "prices" / f"yahoo_{tk}.json"))["chart"]["result"][0]
    ts, q, adj = c["timestamp"], c["indicators"]["quote"][0], c["indicators"]["adjclose"][0]["adjclose"]
    dates = [D.datetime.fromtimestamp(t, ET).date() for t in ts]
    assert len(set(dates)) == len(dates), "duplicate dates"
    assert dates == sorted(dates), "unsorted"
    rows = [(d, q["close"][i], adj[i]) for i, d in enumerate(dates)]
    missing = [d.isoformat() for d, c_, a in rows if c_ is None or a is None]
    rows = [r for r in rows if r[1] is not None and r[2] is not None]
    nq = json.load(open(raw / "prices" / f"nasdaq_{tk}.json"))["data"]["tradesTable"]["rows"]
    nqc = {D.datetime.strptime(r["date"], "%m/%d/%Y").date(): float(r["close"].replace("$", "").replace(",", "")) for r in nq}
    diffs = [(d.isoformat(), c_, nqc.get(d)) for d, c_, a in rows if nqc.get(d) is None or abs(nqc[d] - c_) > 0.005]
    return c, rows, missing, diffs


def standalone_prior_month(rets, year, month):
    sub = [(d, r) for d, r in rets if (d.year, d.month) == (year, month)]
    return max(sub, key=lambda x: x[1])


def standalone_fixed(rets, end, n=21):
    sub = [x for x in rets if x[0] <= end][-n:]
    return max(sub, key=lambda x: x[1]), sub[0][0], sub[-1][0], len(sub)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="_raw")
    ap.add_argument("--out-dir", default="_out")
    a = ap.parse_args()
    raw, out_dir = pathlib.Path(a.raw), pathlib.Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {}
    as_of_now = D.datetime(2026, 10, 11, 12, 0, tzinfo=UTC)               # 08:00 ET Oct 11 (after retrieval)
    as_of_sep30 = D.datetime(2026, 9, 30, 20, 0, tzinfo=ET)               # after the Sep-30 close
    for tk in TICKERS:
        c, rows, missing, nq_diffs = load(raw, tk)
        rets = [(rows[i][0], rows[i][2] / rows[i - 1][2] - 1) for i in range(1, len(rows))]
        cal = [d for d, _ in rets]
        recs = [DailyReturn(security=tk, day=d, known_at=D.datetime(d.year, d.month, d.day, 16, 0, tzinfo=ET),
                            source="yahoo_chart_api_v8", adjustment_basis=BASIS, revision=0, ret=r) for d, r in rets]
        kw = dict(calendar_tz="America/New_York")
        prim_now = max_lottery(recs, tk, as_of_now, cal, **kw)
        # same instant, other spellings -> identical
        spell = {z: max_lottery(recs, tk, as_of_now.astimezone(ZoneInfo(z)), cal, **kw)
                 for z in ("UTC", "America/New_York", "Asia/Tokyo", "Pacific/Auckland")}
        assert len(set(spell.values())) == 1, spell
        fx_sep30 = max_lottery(recs, tk, as_of_sep30, cal, variant="fixed_21_trading_days", **kw)
        fx_now = max_lottery(recs, tk, as_of_now, cal, variant="fixed_21_trading_days", **kw)
        sp = standalone_prior_month(rets, 2026, 9)
        f1, f1s, f1e, f1n = standalone_fixed(rets, D.date(2026, 9, 30))
        f2, f2s, f2e, f2n = standalone_fixed(rets, D.date(2026, 10, 9))
        assert prim_now == sp[1] and fx_sep30 == f1[1] and fx_now == f2[1], "repo code != standalone"
        res[tk] = dict(
            n_days=len(rows), first=rows[0][0].isoformat(), last=rows[-1][0].isoformat(), missing_dates=missing,
            calendar_tz="America/New_York", as_of_now_utc=as_of_now.isoformat(),
            MAX_primary_prior_calendar_month_Sep2026=dict(ret=prim_now, date=sp[0].isoformat(), n_returns_in_month=sum(1 for d in cal if d.month == 9)),
            same_instant_other_spellings=spell,
            MAX_fixed_21_trading_days_asof_2026_09_30=dict(ret=fx_sep30, window=[f1s.isoformat(), f1e.isoformat()], n=f1n),
            MAX_fixed_21_trading_days_asof_2026_10_11=dict(ret=fx_now, window=[f2s.isoformat(), f2e.isoformat()], n=f2n),
            repo_code_equals_standalone=True,
            raw_close_vs_nasdaq_mismatch_count=len(nq_diffs), raw_close_vs_nasdaq_mismatches=nq_diffs[:5],
            dividends=c.get("events", {}).get("dividends"), splits=c.get("events", {}).get("splits"))
        with open(out_dir / f"daily_{tk}.csv", "w") as f:
            f.write("date,raw_close,adj_close,daily_ret_from_adj\n")
            for i, (d, c_, ad) in enumerate(rows):
                f.write(f"{d.isoformat()},{c_},{ad},{'' if i == 0 else repr(ad / rows[i - 1][2] - 1)}\n")
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
        tree = subprocess.run(["git", "rev-parse", "HEAD:factor_candidates"], capture_output=True, text=True, cwd=HERE.parents[2]).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "factor_candidates", "docs/research/real_data_sample_20261011"],
                                    capture_output=True, text=True, cwd=HERE.parents[2]).stdout.strip())
    except OSError:
        head = tree = "unknown"; dirty = None
    res["_code_version"] = dict(git_head=head, factor_candidates_tree=tree, factor_or_sample_dirty=dirty)
    (out_dir / "max_results.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
