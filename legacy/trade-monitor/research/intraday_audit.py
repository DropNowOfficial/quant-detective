"""Read-only 5m coverage and geometry audit. Never promotes inadequate history."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from .study import ROOT, END, CORE, DISCOVERY, COLS, clean_bars, features, load_daily


def run(rawdir=ROOT/'raw',out=ROOT/'results'):
    out.mkdir(parents=True,exist_ok=True)
    daily,_,_,_=load_daily(rawdir)
    coverage=[];decisions=[];aaoi=None
    for symbol in CORE+DISCOVERY+['QQQ','SOXX']:
        path=rawdir/f'{symbol}_5m.json'
        doc=json.loads(path.read_text());src=doc['data']
        if len({len(src[k]) for k in ['time']+COLS})!=1:raise ValueError(f'{symbol}: array length mismatch')
        d=pd.DataFrame({k:src[k] for k in COLS},index=pd.to_datetime(src['time'],utc=True))
        if d.index.has_duplicates or not d.index.is_monotonic_increasing:raise ValueError(f'{symbol}: duplicate/order')
        x,bad=clean_bars(d);local=x.index.tz_convert('America/New_York')
        days=pd.Index(local.date)
        available_end=pd.Timestamp('2026-10-03T00:00:00Z')
        complete=(x.index+pd.Timedelta(minutes=5)<=available_end)
        valid_sessions={}
        for date in sorted(set(days)):
            group=x[days==date]
            expected=pd.date_range(str(date)+' 09:30',str(date)+' 15:55',freq='5min',tz='America/New_York').tz_convert('UTC')
            valid_sessions[date]=bool(group.index.equals(expected) and np.isfinite(group.to_numpy()).all() and group.volume.sum()>0)
        f=features(daily[symbol]);maxprior=0;pre_rvol=0;full=0
        for date in sorted(set(days)):
            g=x[days==date];day=pd.Timestamp(date)
            if day not in f.index:continue
            context=f.loc[day];ma=float(context.ma5);atr=float(context.atr5)
            pclose=daily[symbol].close.shift(1).loc[day]
            has_open=len(g)>0 and g.index[0].tz_convert('America/New_York').strftime('%H:%M')=='09:30'
            gap=float((g.iloc[0].open-pclose)/atr) if has_open and atr>0 else np.nan
            prior=sum(ok for dt,ok in valid_sessions.items() if dt<date)
            maxprior=max(maxprior,prior)
            typical=(g.high+g.low+g.close)/3
            # Cumulative NaN must poison later VWAP; never silently bridge gaps.
            vwap=(typical*g.volume).cumsum(skipna=False)/g.volume.cumsum(skipna=False).replace(0,np.nan)
            distance=(g.close-ma)/atr
            above=(g.close>=ma)&(g.close>=vwap)
            hold=above&above.shift(1,fill_value=False)
            geometry=distance.between(-.1,.2)&hold&(abs(gap)<=.5)&(context.slope1>0)&(context.slope3>=0)&((g.close-vwap)/atr<=.25)
            # A full-day integrity flag is for retrospective data QA, not live signals.
            geometry&=valid_sessions[date]
            pre_rvol+=int(geometry.sum())
            for ts,row in g.iterrows():
                i=g.index.get_loc(ts)
                decisions.append(dict(symbol=symbol,bar_start=str(ts),bar_end=str(ts+pd.Timedelta(minutes=5)),
                    session=str(date),bar_complete=bool(ts+pd.Timedelta(minutes=5)<=available_end),
                    whole_session_integrity=valid_sessions[date],prior_complete_sessions=prior,
                    ma5=ma,atr5=atr,d5=float(distance.iloc[i]),vwap_ohlcv_estimate=float(vwap.iloc[i]),
                    gap_atr=gap,two_closed_bars_hold=bool(hold.iloc[i]),pre_rvol_geometry=bool(geometry.iloc[i]),
                    promoted=False,reason='INSUFFICIENT_20_PRIOR_SESSIONS' if prior<20 else 'TRADE_VWAP_AND_FULL_RULES_UNVERIFIED'))
            if symbol=='AAOI' and date==END.date():
                aaoi=dict(date=str(date),bars=len(g),ma5=ma,atr5=atr,slope1=float(context.slope1),slope3=float(context.slope3),
                    lower=ma-.1*atr,upper=ma+.2*atr,stretch_limit=ma+.35*atr,
                    lowest_low=float(g.low.min()),closes_in_band=int(distance.between(-.1,.2).sum()),
                    bar_ranges_touching_band=int(((g.high>=ma-.1*atr)&(g.low<=ma+.2*atr)).sum()),
                    prior_complete_sessions=prior,exact_harness_qualified=0)
        coverage.append(dict(symbol=symbol,source_rows=len(d),first=str(d.index[0]),last=str(d.index[-1]),
            invalid_bars=int(bad.sum()),complete_bars=int(complete.sum()),sessions=len(valid_sessions),
            intact_full_sessions=sum(valid_sessions.values()),max_prior_full_sessions=maxprior,
            pre_rvol_geometry_bars=pre_rvol,exact_harness_qualified=full,
            status='BLOCKED_HISTORY_AND_VWAP_FIDELITY',vwap_method='HLC3 volume-weighted estimate, not trade VWAP'))
    pd.DataFrame(coverage).to_csv(out/'intraday_coverage.csv',index=False)
    pd.DataFrame(decisions).to_csv(out/'intraday_decisions.csv',index=False)
    (out/'aaoi_counterexample.json').write_text(json.dumps(aaoi,indent=2,allow_nan=False)+'\n')
    receipt=dict(symbols=len(coverage),bars=sum(x['source_rows'] for x in coverage),
        max_prior_full_sessions=max(x['max_prior_full_sessions'] for x in coverage),
        eligible_historical_fit_samples=0,reason='No symbol has 20 prior intact RTH sessions; HLC3 VWAP remains a proxy',
        note='No half-days occur in this input range. Session integrity is retrospective QA, not a causal live gate.',aaoi=aaoi)
    (out/'intraday_summary.json').write_text(json.dumps(receipt,indent=2,allow_nan=False)+'\n')
    print(json.dumps(receipt,indent=2))
    return receipt


if __name__=='__main__':run()
