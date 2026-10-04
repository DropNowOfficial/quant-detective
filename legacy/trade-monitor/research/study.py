"""Frozen daily event study. Not a live HARNESS implementation or portfolio backtest.

Run: python -m research.study
Only reads saved source responses; no network, order or notification operations.
"""
from pathlib import Path
import argparse
import hashlib
import json
import math
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
START=pd.Timestamp('2023-10-02')
END=pd.Timestamp('2026-10-02')
CORE='AMD INTC SNDK MU MRVL GLW GOOG META ARM NVDA AVGO TSM ASML AMAT LRCX KLAC ANET VRT ORCL MSFT AMZN DELL AAPL CRCL MSTR SPCX'.split()
DISCOVERY='SMCI CRDO ALAB COHR LITE AAOI QCOM MPWR MCHP NXPI ETN VST CEG HPE P WDC STX'.split()
HORIZONS=(1,3,5,10,20)
VARIANTS=('primary','monotonic','daily_volume08')
COLS=['open','high','low','close','volume']


def clean_bars(df):
    """Quarantine contradictory observations, never fix prices or compress time."""
    x=df[COLS].astype(float).copy()
    finite=pd.Series(np.isfinite(x.to_numpy()).all(axis=1),index=x.index)
    bad=(~finite)|(x[['open','high','low','close']]<=0).any(axis=1)|(x.volume<0)
    bad|=(x.low>x[['open','close']].min(axis=1))|(x.high<x[['open','close']].max(axis=1))|(x.high<x.low)
    x.loc[bad,COLS]=np.nan
    return x,bad


def prior_rank(arr):
    if not np.isfinite(arr[-1]):return np.nan
    hist=arr[:-1];hist=hist[np.isfinite(hist)]
    if len(hist)<126:return np.nan
    return float(((hist<arr[-1]).sum()+.5*(hist==arr[-1]).sum())/len(hist))


def features(d):
    f=pd.DataFrame(index=d.index)
    ma=d.close.rolling(5,min_periods=5).mean()
    tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1,skipna=False)
    f['ma5']=ma.shift(1)
    f['atr5']=tr.rolling(5,min_periods=5).mean().shift(1)
    f['slope1']=ma.diff().shift(1)
    # OLS slope of three equally spaced points is (last-first)/2.
    f['slope3']=(ma.shift(1)-ma.shift(3))/2
    f['monotonic3']=(ma.diff().shift(1)>=0)&(ma.diff().shift(2)>=0)
    f['d5']=(d.close-f.ma5)/f.atr5
    f['gap']=(d.open-d.close.shift(1))/f.atr5
    f['daily_volume_ratio']=d.volume/d.volume.rolling(20,min_periods=20).mean().shift(1)
    base=(f.atr5>0)&(f.slope1>0)&(f.slope3>=0)&f.d5.between(-.10,.20)&(f.gap.abs()<=.50)
    f['primary']=base
    f['monotonic']=base&f.monotonic3
    f['daily_volume08']=base&(f.daily_volume_ratio>=.8)
    ma20=d.close.rolling(20,min_periods=20).mean()
    atr20=tr.rolling(20,min_periods=20).mean()
    stretch=(d.close-ma20)/atr20
    f['price_percentile']=stretch.rolling(757,min_periods=127).apply(prior_rank,raw=True)
    ranks=[]
    for h in (5,20,60):
        ret=d.close.pct_change(h,fill_method=None)
        ranks.append(ret.rolling(757,min_periods=127).apply(prior_rank,raw=True))
    f['return_percentile']=pd.concat(ranks,axis=1).mean(axis=1,skipna=False)
    f['statistical_value_proxy']=1-f.price_percentile
    return f


def event_outcomes(d,mask,h,start=None,end=None):
    """After close signal, next open entry, day i+h close exit; no stop assumptions."""
    events=mask&~mask.shift(1,fill_value=False)
    if start is not None:events&=d.index>=start
    if end is not None:events&=d.index<=end
    counts=dict(raw_eligible=int(mask.loc[events.index[(events.index>=(start or events.index.min()))&(events.index<=(end or events.index.max()))]].sum()),
                transitions=int(events.sum()),overlap_removed=0,right_censored=0,invalid_outcome=0)
    rows=[];occupied_until=-1
    for i in np.flatnonzero(events.to_numpy()):
        if i<occupied_until:
            counts['overlap_removed']+=1;continue
        occupied_until=i+h
        if i+h>=len(d):counts['right_censored']+=1;continue
        future=d.iloc[i+1:i+h+1]
        if not np.isfinite(future[COLS].to_numpy()).all():counts['invalid_outcome']+=1;continue
        entry=float(future.iloc[0].open);exit_price=float(future.iloc[-1].close)
        rows.append(dict(signal_i=int(i),signal_date=str(d.index[i].date()),entry_date=str(d.index[i+1].date()),
            exit_date=str(d.index[i+h].date()),entry=entry,exit=exit_price,gross=exit_price/entry-1,
            mae=min(0.,float(future.low.min()/entry-1)),mfe=max(0.,float(future.high.max()/entry-1))))
    return rows,counts


def block_interval(lattice,draws=2000,block=20,seed=20261004):
    """Circular moving blocks on a session lattice; NaN = no qualified event.

    Resamples time, not isolated trades. Two-dimensional input clusters symbols
    on the same dates. Descriptive 95% intervals, no selection-bias correction.
    """
    a=np.asarray(lattice,dtype=float)
    if a.ndim==1:a=a[:,None]
    if np.isfinite(a).sum()<2:return (None,None)
    n=len(a);rng=np.random.default_rng(seed);means=[]
    totals=np.nansum(a,axis=1);counts=np.isfinite(a).sum(axis=1)
    for _ in range(draws):
        starts=rng.integers(0,n,size=math.ceil(n/block))
        idx=((starts[:,None]+np.arange(block))%n).reshape(-1)[:n]
        count=counts[idx].sum()
        if count:means.append(float(totals[idx].sum()/count))
    return tuple(float(v) for v in np.quantile(means,[.025,.975])) if means else (None,None)


def load_daily(rawdir):
    frames={};receipts={};quarantine=[]
    for path in sorted(rawdir.glob('*_daily.json')):
        doc=json.loads(path.read_text());data=doc['data'];symbol=doc['symbol']
        required=['time']+COLS
        if any(k not in data for k in required):raise ValueError(f'{symbol}: missing source array')
        if len({len(data[k]) for k in required})!=1:raise ValueError(f'{symbol}: unequal source lengths')
        df=pd.DataFrame({k:data[k] for k in required})
        times=pd.to_datetime(df.pop('time'),utc=True)
        df.index=pd.DatetimeIndex(times.dt.tz_convert('America/New_York').dt.date)
        if not df.index.is_monotonic_increasing or df.index.has_duplicates:raise ValueError(f'{symbol}: nonunique or unordered dates')
        df=df.loc[:END]
        clean,bad=clean_bars(df)
        for date,row in df.loc[bad].iterrows():quarantine.append(dict(symbol=symbol,date=str(date.date()),reason='invalid_ohlcv',**row.to_dict()))
        receipts[symbol]=dict(symbol=symbol,contract_id=doc['contract']['underlying_contract_id'],
            source_file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            raw_bars=len(df),window_bars=int(((df.index>=START)&(df.index<=END)).sum()),
            first=str(df.index.min().date()),last=str(df.index.max().date()),invalid_all=int(bad.sum()),
            invalid_window=int(bad.loc[START:END].sum()),corporate_actions_returned=len(data.get('corp_actions',[])),
            adjustment_policy='not_explicitly_reported',price_type=data.get('source'),
            limited_three_year_coverage=bool(df.index.min()>START))
        frames[symbol]=clean
    if 'QQQ' not in frames or 'SOXX' not in frames:raise ValueError('benchmarks missing')
    calendar=frames['QQQ'].index
    for symbol,frame in frames.items():
        unknown=frame.index.difference(calendar)
        if len(unknown):raise ValueError(f'{symbol}: sessions absent from benchmark calendar: {unknown.tolist()}')
        missing=calendar[(calendar>=frame.index.min())&(calendar<=frame.index.max())].difference(frame.index)
        receipts[symbol]['missing_internal_sessions']=len(missing)
        receipts[symbol]['missing_session_dates']=[str(x.date()) for x in missing]
        frames[symbol]=frame.reindex(calendar)
    return frames,receipts,quarantine,calendar


def benchmark_return(df,row):
    x=df.loc[row['entry_date']:row['exit_date']]
    if not len(x) or not np.isfinite(x[COLS].to_numpy()).all():return np.nan
    return float(x.iloc[-1].close/x.iloc[0].open-1)


def run(rawdir=ROOT/'raw',out=ROOT/'results'):
    out.mkdir(parents=True,exist_ok=True)
    frames,receipts,quarantine,calendar=load_daily(rawdir)
    universe=CORE+DISCOVERY
    missing=[s for s in universe if s not in frames]
    if missing:raise ValueError(f'universe data missing: {missing}')
    dates=calendar[(calendar>=START)&(calendar<=END)]
    fold_chunks=np.array_split(dates,4)
    foldmap={date:i+1 for i,chunk in enumerate(fold_chunks) for date in chunk}
    allrows=[];summary=[];latest=[];stability=[];checks=[];featuremap={}
    pooled_net=[];pooled_excess=[]
    for symbol in universe:
        df=frames[symbol];f=features(df);featuremap[symbol]=f
        # Check fixed features against unseen-tail mutation and truncated prefixes.
        for cut in sorted(set([len(df)//2,len(df)-60])):
            changed=df.copy();changed.iloc[cut:,:4]*=3
            try:
                pd.testing.assert_frame_equal(f.iloc[:cut],features(changed).iloc[:cut])
                pd.testing.assert_frame_equal(f.iloc[:cut],features(df.iloc[:cut]))
                passed=True
            except AssertionError:passed=False
            checks.append(dict(symbol=symbol,cut=str(df.index[cut].date()),future_tail_invariant=passed))
        for variant in VARIANTS:
            for h in HORIZONS:
                rows,counts=event_outcomes(df,f[variant],h,start=START,end=END)
                for r in rows:
                    r.update(symbol=symbol,variant=variant,horizon=h,
                        qqq_gross=benchmark_return(frames['QQQ'],r),soxx_gross=benchmark_return(frames['SOXX'],r))
                    for cost in (0,10,25,50):r[f'net_{cost}bps']=r['gross']-cost/10000
                    r['excess_qqq']=r['gross']-r['qqq_gross']
                    r['excess_soxx']=r['gross']-r['soxx_gross']
                    r['fold']=foldmap[pd.Timestamp(r['signal_date'])]
                    r['fold_boundary_purged']=foldmap.get(pd.Timestamp(r['exit_date']))!=r['fold']
                    r['price_percentile']=f.loc[r['signal_date'],'price_percentile']
                    r['return_percentile']=f.loc[r['signal_date'],'return_percentile']
                    r['fundamental_value_available']=False
                allrows.extend(rows)
                stats=dict(symbol=symbol,variant=variant,horizon=h,**counts,n=len(rows),
                    classification='UNRATED_DAILY_PROXY',limited_history=receipts[symbol]['limited_three_year_coverage'])
                if rows:
                    table=pd.DataFrame(rows)
                    for cost in (0,10,25,50):
                        stats[f'mean_net_{cost}bps']=float(table[f'net_{cost}bps'].mean())
                    stats.update(median_net_10bps=float(table.net_10bps.median()),win_rate_10bps=float((table.net_10bps>0).mean()),
                        mean_mae=float(table.mae.mean()),mean_mfe=float(table.mfe.mean()),
                        qqq_matched_n=int(table.qqq_gross.notna().sum()),soxx_matched_n=int(table.soxx_gross.notna().sum()),
                        mean_excess_qqq=float(table.excess_qqq.mean()),mean_excess_soxx=float(table.excess_soxx.mean()))
                    if variant=='primary' and h==3:
                        lattice=np.full(len(dates),np.nan);excess=lattice.copy()
                        for r in rows:
                            j=dates.get_loc(pd.Timestamp(r['signal_date']));lattice[j]=r['net_10bps'];excess[j]=r['excess_qqq']
                        stats['ci_net_low'],stats['ci_net_high']=block_interval(lattice)
                        stats['ci_excess_low'],stats['ci_excess_high']=block_interval(excess)
                        pooled_net.append(lattice);pooled_excess.append(excess)
                    positive=0
                    for k in range(1,5):
                        z=table[(table.fold==k)&~table.fold_boundary_purged]
                        m=float(z.net_10bps.mean()) if len(z) else np.nan
                        positive+=int(np.isfinite(m) and m>0)
                        stability.append(dict(symbol=symbol,variant=variant,horizon=h,fold=k,n=len(z),mean_net_10bps=m,
                            mean_excess_qqq=float(z.excess_qqq.mean()) if len(z) else np.nan))
                    stats['positive_folds']=positive
                    stats['fold_purged_n']=int(table.fold_boundary_purged.sum())
                summary.append(stats)
        recent=f.iloc[-1]
        latest.append(dict(symbol=symbol,date=str(df.index[-1].date()),close=df.iloc[-1].close,
            ma5=recent.ma5,atr5=recent.atr5,d5=recent.d5,slope1=recent.slope1,slope3=recent.slope3,
            daily_proxy_eligible=bool(recent.primary),price_percentile=recent.price_percentile,
            return_percentile=recent.return_percentile,statistical_value_proxy=recent.statistical_value_proxy,
            fundamental_value=None,status='CONTEXT_ONLY_NOT_INTRADAY_PROMOTION'))
    sums=pd.DataFrame(summary);events=pd.DataFrame(allrows)
    pd.DataFrame(receipts.values()).to_csv(out/'data_quality.csv',index=False)
    pd.DataFrame(quarantine).to_csv(out/'quarantined_bars.csv',index=False)
    sums.to_csv(out/'all_variants_horizons.csv',index=False)
    sums[(sums.variant=='primary')&(sums.horizon==3)].to_csv(out/'primary_3day.csv',index=False)
    events.to_csv(out/'event_outcomes.csv',index=False)
    pd.DataFrame(stability).to_csv(out/'chronological_stability.csv',index=False)
    pd.DataFrame(latest).to_csv(out/'latest_context.csv',index=False)
    pd.DataFrame(checks).to_csv(out/'lookahead_checks.csv',index=False)
    primary=events[(events.variant=='primary')&(events.horizon==3)]
    aggregate=dict(mode='retrospective_daily_proxy_event_study',rule_version='daily-proxy-2026-10-04-v1',
        window=[str(START.date()),str(END.date())],calendar_sessions=len(dates),symbols=len(universe),benchmarks=2,
        source_bars=sum(r['raw_bars'] for r in receipts.values()),invalid_bars=len(quarantine),
        invalid_window=sum(r['invalid_window'] for r in receipts.values()),
        eligible_days=int(sums[(sums.variant=='primary')&(sums.horizon==3)].raw_eligible.sum()),
        completed_nonoverlap_3d_events=len(primary),mean_net_10bps=float(primary.net_10bps.mean()),
        median_net_10bps=float(primary.net_10bps.median()),win_rate_10bps=float((primary.net_10bps>0).mean()),
        mean_excess_qqq=float(primary.excess_qqq.mean()),qqq_matched_n=int(primary.excess_qqq.notna().sum()),
        ci_net=block_interval(np.column_stack(pooled_net)),ci_excess_qqq=block_interval(np.column_stack(pooled_excess)),
        lookahead_checks=len(checks),lookahead_checks_passed=sum(x['future_tail_invariant'] for x in checks),
        parameter_variants=list(VARIANTS),strategy_symbol_variants=len(universe)*len(VARIANTS),
        fold_boundaries=[[str(x[0].date()),str(x[-1].date())] for x in fold_chunks],
        trading_ready=False,full_intraday_harness_validated=False,fundamental_value_available=False,
        caveats=['Current selected universe, not point-in-time membership; survivorship/selection bias remains',
            'No actual portfolio, execution, size, stop or exit rules; fixed horizons are research outcomes',
            'Close data can differ from high/low conventions; invalid rows quarantined, no invented corrections',
            'Corporate action adjustment convention not documented; price returns exclude dividend cash flows',
            'Same-source QQQ date index is a calendar proxy, not an independently certified exchange calendar',
            'Three prespecified variants, 43 symbols and five horizons; no multiplicity-adjusted profitability claim',
            'Chronological folds and bootstrap are retrospective diagnostics, not genuinely unseen OOS validation',
            'All Fit ratings remain UNRATED_DAILY_PROXY; no historical rule updates were sent to live tasks'])
    (out/'summary.json').write_text(json.dumps(aggregate,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(rawdir.glob('*.json'))}
    (out/'input_sha256.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(aggregate,ensure_ascii=False,indent=2))
    return aggregate


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--raw',type=Path,default=ROOT/'raw');parser.add_argument('--output',type=Path,default=ROOT/'results')
    args=parser.parse_args();run(args.raw,args.output)
