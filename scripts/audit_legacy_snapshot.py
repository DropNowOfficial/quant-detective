"""Independent, read-only source/research audit. Writes derived metadata only.

Run with Python environment containing pandas/numpy/exchange_calendars.
No network, source mutation, portfolio optimization, orders, or raw-data export.
"""
from pathlib import Path
import collections
import hashlib
import json
import numpy as np
import pandas as pd
import exchange_calendars as xc

import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--legacy-root',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
SOURCE=args.legacy_root
OUT=args.output
OUT.parent.mkdir(parents=True,exist_ok=True)
COLS = ['open', 'high', 'low', 'close', 'volume']
END = pd.Timestamp('2026-10-02')
cal = xc.get_calendar('XNYS', start='2021-01-01', end='2027-01-01')
expected = cal.sessions_in_range('2021-10-04', END)
manifest = {x['file']: x for x in json.loads((SOURCE/'research/request_manifest.json').read_text())}
prior_hashes = json.loads((SOURCE/'research/results/input_sha256.json').read_text())
documents, frames, cleaned, bads, features, metadata = {}, {}, {}, {}, {}, {}
hash_checks, contract_defects, date_defects, actions, split_evidence = [], [], [], [], []

for p in sorted((SOURCE/'research/raw').glob('*_daily.json')):
    doc = json.loads(p.read_text()); d = doc['data']; s = doc['symbol']
    documents[s] = doc
    ts = pd.DatetimeIndex(pd.to_datetime(d['time'], utc=True))
    dates = pd.DatetimeIndex(ts.tz_convert('America/New_York').date)
    a = pd.DataFrame({k:d[k] for k in COLS},index=dates,dtype=float)
    b = (~np.isfinite(a).all(axis=1) | (a[COLS[:4]] <= 0).any(axis=1) |
         (a.volume < 0) | (a.high < a.low) |
         (a.high < a[['open','close']].max(axis=1)) |
         (a.low > a[['open','close']].min(axis=1)))
    x=a.copy();x.loc[b,:]=np.nan
    frames[s]=a.reindex(expected);cleaned[s]=x.reindex(expected);bads[s]=b.reindex(expected,fill_value=False)
    h=hashlib.sha256(p.read_bytes()).hexdigest()
    hash_checks.append({'file':p.name,'request_manifest_matches':manifest[p.name]['sha256']==h,
                        'prior_results_manifest_matches':prior_hashes[p.name]==h})
    request={**manifest[p.name]['request'],**doc['request']}
    if not (doc['symbol']==doc['contract']['symbol'] and
            request['contract_id']==doc['contract']['underlying_contract_id'] and
            request.get('outside_rth') is False and request.get('security_type')=='STK' and
            request.get('step')=='ONE_DAY' and d.get('chart_step')==86400 and d.get('source')=='Last'):
        contract_defects.append(s)
    illegal=[str(t) for t,dt in zip(ts,dates) if not cal.is_session(dt) or cal.session_open(dt)!=t]
    if dates.has_duplicates or not dates.is_monotonic_increasing or illegal:
        date_defects.append({'symbol':s,'duplicate_dates':int(dates.duplicated().sum()),'illegal':illegal})
    own_expected=cal.sessions_in_range(dates.min(),dates.max())
    metadata[s]={'bars':len(a),'first':str(dates.min().date()),'last':str(dates.max().date()),
                 'invalid':int(b.sum()),'missing_internal':len(own_expected.difference(dates)),
                 'corporate_action_count':len(d.get('corp_actions',[])),
                 'retrieved_at':doc['retrieved_at'],'source':d.get('source'),'sha256':h,
                 'has_adjustment_contract':any('adjust' in str(k).lower() for k in d)}
    for act in d.get('corp_actions',[]):
        actdate=pd.to_datetime(act['date'],format='%Y%m%d')
        rec={'symbol':s,**act,'parsed_date':str(actdate.date())};actions.append(rec)
        if 'split' in act['type'].lower() and actdate in a.index:
            j=a.index.get_loc(actdate)
            split_evidence.append({'symbol':s,'date':str(actdate.date()),'reported_ratio':float(act['value']),
              'split_day_open_over_prior_close':float(a.open.iloc[j]/a.close.iloc[j-1]),
              'post20_over_pre20_median_volume':float(a.volume.iloc[j:j+20].median()/a.volume.iloc[max(0,j-20):j].median())})
    # Independent daily signal implementation; all source dates explicitly reindexed first.
    pclose=x.reindex(expected).close.shift(1)
    zz=cleaned[s]
    ma=zz.close.rolling(5,min_periods=5).mean()
    tr=pd.DataFrame({'range':zz.high-zz.low,'up':(zz.high-pclose).abs(),
                    'down':(zz.low-pclose).abs()}).max(axis=1,skipna=False)
    f=pd.DataFrame(index=expected)
    f['ma5']=ma.shift(1);f['atr5']=tr.rolling(5,min_periods=5).mean().shift(1)
    f['slope1']=ma.diff().shift(1);f['slope3']=(ma.shift(1)-ma.shift(3))/2
    f['d5']=(zz.close-f.ma5)/f.atr5;f['gap']=(zz.open-zz.close.shift(1))/f.atr5
    f['primary']=(f.atr5>0)&(f.slope1>0)&(f.slope3>=0)&f.d5.between(-.1,.2)&(f.gap.abs()<=.5)
    features[s]=f

symbols=[s for s in frames if s not in ['QQQ','SOXX']]
periods={}
for label,start,end in [('old_3y','2023-10-02','2026-10-02'),('new_2022_onward','2022-01-03','2026-10-02'),
                       ('development_2022_2024','2022-01-03','2024-12-31'),('validation_2025','2025-01-01','2025-12-31'),
                       ('retrospective_2026','2026-01-01','2026-10-02')]:
    ix=expected[(expected>=pd.Timestamp(start))&(expected<=pd.Timestamp(end))]
    present=pd.DataFrame({s:frames[s].loc[ix].notna().all(axis=1) for s in frames})
    valid=pd.DataFrame({s:cleaned[s].loc[ix].notna().all(axis=1) for s in frames})
    periods[label]={'start':start,'end':end,'sessions':len(ix),
         'observed_rows_all45':int(present.sum().sum()),'valid_rows_all45':int(valid.sum().sum()),
         'invalid_rows_all45':int((present&~valid).sum().sum()),
         'observed_rows_strategy43':int(present[symbols].sum().sum()),
         'valid_rows_strategy43':int(valid[symbols].sum().sum()),
         'min_present_strategy_symbols':int(present[symbols].sum(axis=1).min()),
         'max_present_strategy_symbols':int(present[symbols].sum(axis=1).max()),
         'min_valid_strategy_symbols':int(valid[symbols].sum(axis=1).min()),
         'max_valid_strategy_symbols':int(valid[symbols].sum(axis=1).max()),
         'sessions_any_invalid_strategy_bar':int((present[symbols]&~valid[symbols]).any(axis=1).sum()),
         'qqq_invalid_dates':[str(t.date()) for t in ix[bads['QQQ'].loc[ix]]],
         'soxx_invalid_dates':[str(t.date()) for t in ix[bads['SOXX'].loc[ix]]],
         'symbols_start_after_period_start':{s:metadata[s]['first'] for s in symbols if pd.Timestamp(metadata[s]['first'])>pd.Timestamp(start)}}

stored=pd.read_csv(SOURCE/'research/results/event_outcomes.csv')
primary=stored[(stored.variant=='primary')&(stored.horizon==3)].copy()
reconstructed=[];events_dropped=[];event_counts=collections.Counter();all_lattice=[];signal_lattice=[]
oldix=expected[(expected>=pd.Timestamp('2023-10-02'))&(expected<=END)]
for s in symbols:
    f=features[s];d=cleaned[s];mask=f.primary
    transitions=mask&~mask.shift(1,fill_value=False)&(expected>=pd.Timestamp('2023-10-02'))
    event_counts['eligible_days']+=int(mask.loc[oldix].sum())
    event_counts['transitions']+=int(transitions.sum());occupied=-1
    event_vec=pd.Series(np.nan,index=expected)
    for i in np.flatnonzero(transitions.to_numpy()):
        if i<occupied:
            event_counts['overlap_removed']+=1;continue
        occupied=i+3
        if i+3>=len(expected):event_counts['right_censored']+=1;continue
        z=d.iloc[i+1:i+4]
        if not np.isfinite(z.to_numpy()).all():
            event_counts['invalid_outcome']+=1
            events_dropped.append({'symbol':s,'signal_date':str(expected[i].date()),
              'bad_outcome_dates':[str(t.date()) for t in z.index[z.isna().any(axis=1)]]})
            continue
        ret=float(z.close.iloc[-1]/z.open.iloc[0]-1)
        reconstructed.append({'symbol':s,'signal_date':str(expected[i].date()),'gross':ret,
            'entry_date':str(expected[i+1].date()),'exit_date':str(expected[i+3].date())})
        event_vec.iloc[i]=ret
    healthy=f[['ma5','atr5','slope1','slope3','d5','gap']].notna().all(axis=1)
    valid=d.notna().all(axis=1)
    baseline=(d.close.shift(-3)/d.open.shift(-1)-1).where(healthy&valid.shift(-1,fill_value=False)&valid.shift(-2,fill_value=False)&valid.shift(-3,fill_value=False))
    all_lattice.append(baseline.loc[oldix].to_numpy());signal_lattice.append(event_vec.loc[oldix].to_numpy())
recon=pd.DataFrame(reconstructed)
joined=primary.merge(recon,on=['symbol','signal_date','entry_date','exit_date'],suffixes=('_saved','_check'),how='outer',indicator=True)
n=np.isfinite(np.array(signal_lattice)).sum(axis=1)
baseline=np.nanmean(np.array(all_lattice),axis=1)
stock_control=float(np.dot(n,baseline)/n.sum())
occupied=pd.Series(0,index=oldix)
entries=pd.Series(0,index=oldix)
for row in primary.itertuples():
    occupied.loc[row.entry_date:row.exit_date]+=1
    entries.loc[row.entry_date]+=1

qqq_missing=primary[primary.qqq_gross.isna()][['symbol','signal_date','entry_date','exit_date','gross']].to_dict('records')
strict_qqq_returns=cleaned['QQQ'].close.pct_change(fill_method=None).loc[oldix]
raw_qqq_returns=frames['QQQ'].close.pct_change(fill_method=None).loc[oldix]
diag_invalid_qqq=[]
for dt in oldix[bads['QQQ'].loc[oldix]]:
    a=frames['QQQ'].loc[dt]
    diag_invalid_qqq.append({'date':str(dt.date()),
      'close_outside_low_high_by':float(max(a.low-a.close,a.close-a.high,0)),
      'unverified_raw_close_close_return':float(raw_qqq_returns.loc[dt])})

intraday=[]
for p in sorted((SOURCE/'research/raw').glob('*_5m.json')):
    doc=json.loads(p.read_text());d=doc['data'];s=doc['symbol']
    times=pd.DatetimeIndex(pd.to_datetime(d['time'],utc=True))
    a=pd.DataFrame({k:d[k] for k in COLS},index=times,dtype=float)
    localdates=pd.DatetimeIndex(times.tz_convert('America/New_York').date)
    full=[]
    for dt in sorted(set(localdates)):
        z=a.loc[localdates==dt]
        ex=pd.date_range(cal.session_open(dt),cal.session_close(dt)-pd.Timedelta(minutes=5),freq='5min')
        valid=(np.isfinite(z.to_numpy()).all() and (z[COLS[:4]]>0).all().all() and
          (z.high>=z[['open','close']].max(axis=1)).all() and (z.low<=z[['open','close']].min(axis=1)).all() and
          (z.volume>=0).all() and z.volume.sum()>0)
        if z.index.equals(ex) and valid:full.append(dt)
    intraday.append({'symbol':s,'bars':len(a),'sessions':len(set(localdates)),
      'intact_sessions':len(full),'max_prior_intact_sessions':sum(dt<localdates.max() for dt in full),
      'first':str(times.min()),'last':str(times.max())})

event_reconciliation={'saved_primary_rows':len(primary),'independently_reconstructed_rows':len(recon),
  'join_counts':{str(k):int(v) for k,v in joined['_merge'].value_counts().items()},
  'max_gross_absolute_difference':float((joined.gross_saved-joined.gross_check).abs().max()),
  'gross_mean':float(recon.gross.mean()),'net10bps_mean':float(recon.gross.mean()-.001),
  'same_stock_gross':stock_control,'same_stock_net10bps':stock_control-.001,
  'same_stock_increment':float(recon.gross.mean()-stock_control),'counts':dict(event_counts),
  'dropped_for_invalid_future_rows':events_dropped,
  'qqq_unmatched_n':len(qqq_missing),'qqq_unmatched_events':qqq_missing,
  'unbounded_accepted_event_occupancy':{'max':int(occupied.max()),'mean':float(occupied.mean()),
     'sessions_above5':int((occupied>5).sum()),'sessions_above10':int((occupied>10).sum()),
     'sum_positions_above5':int((occupied-5).clip(lower=0).sum()),
     'max_same_day_entries':int(entries.max()),'entry_days_more_than5':int((entries>5).sum())}}

out={'audit_scope':'Read-only independent source and prior event-study verification; not portfolio certification',
 'source_root':str(SOURCE),'daily_files':len(frames),'daily_rows':sum(m['bars'] for m in metadata.values()),
 'invalid_all_rows':sum(m['invalid'] for m in metadata.values()),'strategy_symbols':len(symbols),
 'raw_manifest_checks':hash_checks,'contract_defects':contract_defects,'date_defects':date_defects,
 'periods':periods,'per_symbol':metadata,
 'corporate_actions':{'all_records':len(actions),'types':dict(collections.Counter(x['type'] for x in actions)),
    'symbols_with_action_array':sum('corp_actions' in d['data'] for d in documents.values()),
    'symbols_without_action_array':sum('corp_actions' not in d['data'] for d in documents.values()),
    'in_2022_onward':sum('2022-01-03'<=x['parsed_date']<='2026-10-02' for x in actions),
    'splits_source_evidence':split_evidence,
    'noncash_nonsplit_records':[x for x in actions if x['type'] not in ['CashDividends','Splits']]},
 'old_event_reconciliation':event_reconciliation,
 'qqq_invalid_diagnostic':diag_invalid_qqq,
 'intraday':{'files':len(intraday),'bars':sum(x['bars'] for x in intraday),
     'earliest':min(x['first'] for x in intraday),'latest':max(x['last'] for x in intraday),
     'max_prior_intact_sessions':max(x['max_prior_intact_sessions'] for x in intraday),
     'symbols_with20_prior_intact_sessions':sum(x['max_prior_intact_sessions']>=20 for x in intraday),
     'intact_sessions_distribution':dict(collections.Counter(x['intact_sessions'] for x in intraday)),
     'session_count_distribution':dict(collections.Counter(x['sessions'] for x in intraday))}}
# Extra counts remain reproducible from this same independent program.
a=np.array(signal_lattice).T;b=np.array(all_lattice).T
rng=np.random.default_rng(20261004);boot_net=[];boot_increment=[]
for _ in range(2000):
    starts=rng.integers(0,len(a),size=int(np.ceil(len(a)/20)))
    idx=((starts[:,None]+np.arange(20))%len(a)).flatten()[:len(a)]
    aa,bb=a[idx],b[idx];n=np.isfinite(aa).sum(axis=0);bn=np.isfinite(bb).sum(axis=0)
    m=np.divide(np.nansum(bb,axis=0),bn,out=np.full(len(bn),np.nan),where=bn>0)
    signal=np.nansum(aa)/n.sum();control=np.nansum(m*n)/n.sum()
    boot_net.append(signal-.001);boot_increment.append(signal-control)
more={'meta_pre_rename_uncovered_sessions_since2022':int(((expected>=pd.Timestamp('2022-01-03'))&(expected<pd.Timestamp('2022-06-09'))).sum()),
    'sndk_when_issued_bars':len(frames['SNDK'].loc['2025-02-13':'2025-02-21'].dropna()),
    'sndk_first_primary_signal':str(features['SNDK'].index[features['SNDK'].primary][0].date()),
    'all_hashes_match':all(x['request_manifest_matches'] and x['prior_results_manifest_matches'] for x in hash_checks),
    'independent_old_net95ci':np.quantile(boot_net,[.025,.975]).tolist(),
    'independent_old_increment95ci':np.quantile(boot_increment,[.025,.975]).tolist()}
ix=expected[expected>=pd.Timestamp('2022-01-03')];occup=pd.Series(0,index=ix)
cnt={k:0 for k in ['eligible','transitions','overlap_removed','invalid_outcome','right_censored','complete_events']};new_bad=[]
for s in symbols:
    mask=features[s].primary;trans=mask&~mask.shift(1,fill_value=False)&(expected>=ix[0])
    cnt['eligible']+=int(mask.loc[ix].sum());cnt['transitions']+=int(trans.sum());busy=-1
    for i in np.flatnonzero(trans):
        if i<busy:cnt['overlap_removed']+=1;continue
        busy=i+3
        if i+3>=len(expected):cnt['right_censored']+=1;continue
        z=cleaned[s].iloc[i+1:i+4]
        if not np.isfinite(z).all().all():
            cnt['invalid_outcome']+=1
            new_bad.append({'symbol':s,'signal_date':str(expected[i].date()),'bad_outcome_dates':[str(t.date()) for t in z.index[z.isna().any(axis=1)]]})
            continue
        cnt['complete_events']+=1;occup.loc[expected[i+1]:expected[i+3]]+=1
more['new_window_event_counts']=cnt;more['new_window_invalid_future_event_exclusions']=new_bad
more['new_window_unbounded_event_occupancy']={'max':int(occup.max()),'days_above5':int((occup>5).sum()),'mean':float(occup.mean())}
viol=[];causes={k:0 for k in ['nonfinite','nonpositive_price','negative_volume','open_outside_high_low','close_outside_high_low']}
for s,a in frames.items():
    present=a.dropna(how='all');inv=present[(present.high<present.close)|(present.low>present.close)]
    causes['nonfinite']+=int((~np.isfinite(present)).any(axis=1).sum())
    causes['nonpositive_price']+=int((present[COLS[:4]]<=0).any(axis=1).sum())
    causes['negative_volume']+=int((present.volume<0).sum())
    causes['open_outside_high_low']+=int(((present.open>present.high)|(present.open<present.low)).sum())
    causes['close_outside_high_low']+=len(inv)
    for dt,r in inv.iterrows():viol.append({'symbol':s,'date':str(dt.date()),'magnitude':float(max(r.close-r.high,r.low-r.close)),'relative_to_close':float(max(r.close-r.high,r.low-r.close)/r.close)})
more['invalid_causes']=causes
more['invalid_close_deviation']={'median':float(np.median([r['magnitude'] for r in viol])),
    'max':max(viol,key=lambda z:z['magnitude']),'at_most_one_cent':sum(r['magnitude']<=.010000001 for r in viol)}
more['wdc_spinoff_adjacent_unverified_source_open_priorclose_ratio']=float(frames['WDC'].loc['2025-02-24','open']/frames['WDC'].loc['2025-02-21','close'])
out['supplemental_independent_checks']=more
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(json.dumps({k:out[k] for k in ['daily_files','daily_rows','invalid_all_rows','strategy_symbols','contract_defects','date_defects','intraday','supplemental_independent_checks']},ensure_ascii=False,indent=2,allow_nan=False))
