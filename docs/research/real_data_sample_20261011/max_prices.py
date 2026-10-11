#!/usr/bin/env python3
"""Standalone MAX computation. Source: Yahoo chart API JSON (raw close + adjclose), cross-check raw close vs Nasdaq.com historical JSON.
Daily return r_t = adj_t/adj_{t-1}-1 (adjclose = Yahoo split+dividend adjusted). MAX primary = max r_t for t in prior full calendar month (Sept 2026).
Alternative = max r over last 21 trading-day returns ending at stated date. Not an investment claim."""
import json,datetime as D,zoneinfo
ET=zoneinfo.ZoneInfo("America/New_York")
out={}
for tk in ["AAPL","JNJ","ADBE"]:
    c=json.load(open(f"raw/prices/yahoo_{tk}.json"))["chart"]["result"][0]
    ts=c["timestamp"];q=c["indicators"]["quote"][0];adj=c["indicators"]["adjclose"][0]["adjclose"]
    dates=[D.datetime.fromtimestamp(t,ET).date().isoformat() for t in ts]
    assert len(set(dates))==len(dates),"duplicate dates"
    assert dates==sorted(dates),"unsorted"
    rows=[(d,q["close"][i],adj[i]) for i,d in enumerate(dates)]
    miss=[d for d,c_,a in rows if c_ is None or a is None]
    rows=[r for r in rows if r[1] is not None and r[2] is not None]
    rets=[(rows[i][0],rows[i][2]/rows[i-1][2]-1) for i in range(1,len(rows))]
    # nasdaq cross-check
    nq=json.load(open(f"raw/prices/nasdaq_{tk}.json"))["data"]["tradesTable"]["rows"]
    nqc={D.datetime.strptime(r["date"],"%m/%d/%Y").date().isoformat():float(r["close"].replace("$","").replace(",","")) for r in nq}
    diffs=[(d,c_,nqc.get(d)) for d,c_,a in rows if nqc.get(d) is None or abs(nqc[d]-c_)>0.005]
    sep=[(d,r) for d,r in rets if d.startswith("2026-09")]
    mx=max(sep,key=lambda x:x[1])
    def alt(end):
        sub=[x for x in rets if x[0]<=end][-21:]
        return max(sub,key=lambda x:x[1]),sub[0][0],sub[-1][0],len(sub)
    a1=alt("2026-09-30");a2=alt("2026-12-31")
    out[tk]=dict(n_days=len(rows),first=rows[0][0],last=rows[-1][0],missing_dates=miss,sept_n_returns=len(sep),
      MAX_primary_prior_full_month_Sep2026=dict(ret=mx[1],date=mx[0]),
      MAX_alt_21d_ending_2026_09_30=dict(ret=a1[0][1],date=a1[0][0],window=[a1[1],a1[2]],n=a1[3]),
      MAX_alt_21d_ending_last_avail=dict(ret=a2[0][1],date=a2[0][0],window=[a2[1],a2[2]],n=a2[3]),
      raw_close_vs_nasdaq_mismatch_count=len(diffs),raw_close_vs_nasdaq_mismatches=diffs[:5],
      adj_vs_raw_last=(rows[-1][1],rows[-1][2]),dividends=c.get("events",{}).get("dividends"),splits=c.get("events",{}).get("splits"))
    with open(f"daily_{tk}.csv","w") as f:
        f.write("date,raw_close,adj_close,daily_ret_from_adj\n");prev=None
        for i,(d,c_,a) in enumerate(rows): f.write(f"{d},{c_},{a},{'' if i==0 else repr(a/rows[i-1][2]-1)}\n")
json.dump(out,open("max_results.json","w"),indent=1)
print(json.dumps(out,indent=1))
