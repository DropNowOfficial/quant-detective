"""Additional falsification controls; never changes or tunes signal rules."""
import json
import math
import numpy as np
import pandas as pd
from .study import ROOT, START, END, CORE, DISCOVERY, COLS, features, load_daily


def same_stock_control(event_returns,all_date_returns,draws=2000,block=20,seed=20261004):
    """Jointly resample dates and re-estimate both means on every bootstrap draw.

    Unconditional asset means are weighted by that draw's signal count per asset.
    This controls current-universe composition, not all regime or selection bias.
    """
    a=np.asarray(event_returns,float);b=np.asarray(all_date_returns,float)
    if a.shape!=b.shape or a.ndim!=2:raise ValueError('equal date-by-symbol matrices required')
    def evaluate(idx):
        aa=a[idx];bb=b[idx];n=np.isfinite(aa).sum(axis=0);bn=np.isfinite(bb).sum(axis=0)
        if n.sum()==0:return (np.nan,np.nan,np.nan)
        means=np.divide(np.nansum(bb,axis=0),bn,out=np.full(len(bn),np.nan),where=bn>0)
        if np.any((n>0)&~np.isfinite(means)):return (np.nan,np.nan,np.nan)
        signal=float(np.nansum(aa)/n.sum())
        baseline=float(np.nansum(means*n)/n.sum())
        return signal,baseline,signal-baseline
    point=evaluate(np.arange(len(a)))
    rng=np.random.default_rng(seed);deltas=[]
    for _ in range(draws):
        starts=rng.integers(0,len(a),size=math.ceil(len(a)/block))
        idx=((starts[:,None]+np.arange(block))%len(a)).reshape(-1)[:len(a)]
        delta=evaluate(idx)[2]
        if np.isfinite(delta):deltas.append(delta)
    ci=[float(x) for x in np.quantile(deltas,[.025,.975])] if deltas else [None,None]
    return dict(signal_gross=point[0],same_stock_gross=point[1],increment=point[2],ci_increment=ci)


def run():
    frames,_,_,calendar=load_daily(ROOT/'raw');out=ROOT/'results'
    dates=calendar[(calendar>=START)&(calendar<=END)]
    events=pd.read_csv(out/'event_outcomes.csv');primary=events[(events.variant=='primary')&(events.horizon==3)]
    symbols=CORE+DISCOVERY
    a=np.full((len(dates),len(symbols)),np.nan);b=a.copy();warmup=[]
    for j,s in enumerate(symbols):
        d=frames[s];f=features(d)
        valid=d[COLS].notna().all(axis=1)
        healthy=f[['ma5','atr5','slope1','slope3','d5','gap']].notna().all(axis=1)
        ret=(d.close.shift(-3)/d.open.shift(-1)-1).where(healthy&valid.shift(-1,fill_value=False)&valid.shift(-2,fill_value=False)&valid.shift(-3,fill_value=False))
        b[:,j]=ret.reindex(dates).to_numpy()
        for row in primary[primary.symbol==s].to_dict('records'):
            a[dates.get_loc(pd.Timestamp(row['signal_date'])),j]=row['gross']
        columns=['ma5','atr5','slope1','slope3','d5','gap','daily_volume_ratio','primary','monotonic','daily_volume08']
        try:
            pd.testing.assert_frame_equal(f[columns].tail(1),features(d.tail(100))[columns].tail(1),rtol=1e-10,atol=1e-10)
            passed=True
        except AssertionError:passed=False
        warmup.append(dict(symbol=s,main_indicator_100_bar_warmup_matches=passed,
            excluded_from_comparison='VPR ranks intentionally require their specified 756-observation history'))
    control=same_stock_control(a,b)
    control.update(signal_net_10bps=control['signal_gross']-.001,same_stock_net_10bps=control['same_stock_gross']-.001,
        note='Post-study falsification diagnostic, not signal optimization. Overlapping all-date baseline windows; date blocks resample both series. Current-universe, time/regime mismatch and multiplicity limitations remain.')
    (out/'same_stock_control.json').write_text(json.dumps(control,indent=2,allow_nan=False)+'\n')
    pd.DataFrame(warmup).to_csv(out/'warmup_checks.csv',index=False)
    records=[]
    for j,s in enumerate(symbols):
        records.append(dict(symbol=s,events=int(np.isfinite(a[:,j]).sum()),baseline_dates=int(np.isfinite(b[:,j]).sum()),
            signal_gross=float(np.nanmean(a[:,j])),same_stock_gross=float(np.nanmean(b[:,j]))))
    pd.DataFrame(records).to_csv(out/'same_stock_baseline_by_symbol.csv',index=False)
    # Current raw AAOI replay reconciles with the earlier independently saved audit.
    aa=json.loads((out/'aaoi_counterexample.json').read_text())
    assert aa['bars']==78 and aa['closes_in_band']==0 and aa['bar_ranges_touching_band']==0
    assert abs(aa['ma5']-101.094)<1e-9 and abs(aa['atr5']-7.84598)<1e-9
    assert sum(x['main_indicator_100_bar_warmup_matches'] for x in warmup)==43
    print(json.dumps(control,indent=2));print('Warmup checks: 43/43; AAOI independent numeric reconciliation: PASS')
    return control


if __name__=='__main__':run()
