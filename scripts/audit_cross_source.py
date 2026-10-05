"""Read-only IBKR snapshot vs independent Yahoo snapshot audit; no source mixing."""
from pathlib import Path
import json,hashlib,collections
import numpy as np
import pandas as pd
import exchange_calendars as xc

import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ibkr',type=Path,required=True)
parser.add_argument('--yahoo',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
IB=args.ibkr
YH=args.yahoo
args.output.parent.mkdir(parents=True,exist_ok=True)
COLS=['open','high','low','close','volume']
manifest={x['symbol']:x for x in json.loads((YH/'manifest.json').read_text())}
cal=xc.get_calendar('XNYS',start='2015-01-01',end='2027-01-01')
results=[];invalid_ib_evidence=[];invalid_yahoo_evidence=[];yahoo_frames={}

def invalid(a):
    return (~np.isfinite(a).all(axis=1)|(a[COLS[:4]]<=0).any(axis=1)|(a.volume<0)|
            (a.high<a[['open','close']].max(axis=1))|
            (a.low>a[['open','close']].min(axis=1))|(a.high<a.low))

for ip in sorted(IB.glob('*_daily.json')):
    ib=json.loads(ip.read_text());s=ib['symbol'];yp=YH/f'{s}.json';yh=json.loads(yp.read_text())
    root=yh['data']['chart']['result'][0];meta=root['meta'];quote=root['indicators']['quote'][0]
    it=pd.DatetimeIndex(pd.to_datetime(ib['data']['time'],utc=True));yi=pd.DatetimeIndex(pd.to_datetime(root['timestamp'],unit='s',utc=True))
    idates=pd.DatetimeIndex(it.tz_convert('America/New_York').date);ydates=pd.DatetimeIndex(yi.tz_convert('America/New_York').date)
    a=pd.DataFrame({k:ib['data'][k] for k in COLS},index=idates,dtype=float)
    b=pd.DataFrame({k:quote[k] for k in COLS},index=ydates,dtype=float);yahoo_frames[s]=b
    common=a.index.intersection(b.index);ab=a.loc[common];bb=b.loc[common];ibad=invalid(a);ybad=invalid(b)
    ratio=bb.close/ab.close;pricebps=(ratio-1)*10000
    ex=cal.sessions_in_range(common.min(),common.max())
    ar=a.close.reindex(ex).pct_change(fill_method=None);br=b.close.reindex(ex).pct_change(fill_method=None)
    pairs=pd.DataFrame({'ibkr':ar,'yahoo':br}).dropna();delta=(pairs.yahoo-pairs.ibkr)*10000
    worst=[]
    for dt in delta.abs().nlargest(5).index:
        worst.append({'date':str(dt.date()),'return_difference_bps':float(delta.loc[dt]),'ibkr_return':float(pairs.loc[dt,'ibkr']),'yahoo_return':float(pairs.loc[dt,'yahoo'])})
    exy=cal.sessions_in_range(ydates.min(),ydates.max());illegal=ydates.difference(exy)
    typesok=(s in ['QQQ','SOXX'] and meta['instrumentType']=='ETF') or (s not in ['QQQ','SOXX'] and meta['instrumentType']=='EQUITY')
    exchange={'NASDAQ':{'NMS','NGM','NCM'},'NYSE':{'NYQ'},'ARCA':{'PCX'},'AMEX':{'ASE'}}.get(ib['contract']['exchange'],set())
    diagnostics=[]
    if meta.get('symbol')!=s or yh.get('requested_symbol')!=s:diagnostics.append('SYMBOL_MISMATCH')
    if meta.get('currency')!='USD':diagnostics.append('CURRENCY_MISMATCH')
    if not typesok:diagnostics.append('TYPE_MISMATCH')
    if meta.get('exchangeName') not in exchange:diagnostics.append('EXCHANGE_METADATA_MISMATCH')
    h=hashlib.sha256(yp.read_bytes()).hexdigest()
    source={'ibkr_file':ip.name,'ibkr_contract_id':ib['contract']['underlying_contract_id'],
       'ibkr_description':ib['contract'].get('description'),'ibkr_known_at':ib['retrieved_at'],
       'yahoo_name':meta.get('longName'),'yahoo_exchange':meta.get('exchangeName'),
       'yahoo_type':meta.get('instrumentType'),'yahoo_currency':meta.get('currency'),
       'yahoo_source':yh['url'],'yahoo_known_at':yh['known_at'],'yahoo_sha256':h,
       'manifest_hash_match':manifest[s]['sha256']==h,'identity_diagnostics':diagnostics,
       'identity_scope':'Metadata and observed path corroboration; not a CUSIP/ISIN security-master certification'}
    results.append({'symbol':s,**source,'yahoo_rows':len(b),'yahoo_first':str(ydates.min().date()),
       'yahoo_last':str(ydates.max().date()),'yahoo_missing_internal_sessions':len(exy.difference(ydates)),
       'yahoo_non_session_dates':[str(t.date()) for t in illegal],'yahoo_duplicate_dates':int(ydates.duplicated().sum()),
       'common_rows':len(common),'ibkr_only_rows':len(a.index.difference(b.index)),
       'yahoo_invalid_rows':int(ybad.sum()),'ibkr_invalid_common_rows':int(ibad.loc[common].sum()),
       'ibkr_invalid_common_yahoo_valid':int((ibad.loc[common]&~ybad.loc[common]).sum()),
       'common_close_diff_bps':{'median_absolute':float(pricebps.abs().median()),'p99_absolute':float(pricebps.abs().quantile(.99)),
           'maximum_absolute':float(pricebps.abs().max()),'above1bps':int((pricebps.abs()>1).sum()),
           'above10bps':int((pricebps.abs()>10).sum()),'above100bps':int((pricebps.abs()>100).sum())},
       'common_adjacent_return_pairs':len(pairs),'return_correlation':float(pairs.ibkr.corr(pairs.yahoo)),
       'close_return_diff_bps':{'mean_absolute':float(delta.abs().mean()),'p99_absolute':float(delta.abs().quantile(.99)),
           'maximum_absolute':float(delta.abs().max()),'above1bps':int((delta.abs()>1).sum()),
           'above10bps':int((delta.abs()>10).sum()),'above100bps':int((delta.abs()>100).sum())},
       'worst_return_differences':worst,'yahoo_actions':{k:len(v) for k,v in root.get('events',{}).items()}})
    for dt in a.index[ibad]:
        rec={'symbol':s,'date':str(dt.date()),'has_yahoo_row':dt in b.index}
        if dt in b.index:
            rec.update(yahoo_bar_valid=not bool(ybad.loc[dt]),close_difference_bps=float(pricebps.loc[dt]),
                yahoo_changed_fields_over_1cent=[k for k in COLS[:4] if abs(b.loc[dt,k]-a.loc[dt,k])>.0100001])
        invalid_ib_evidence.append(rec)
    for dt in b.index[ybad]:
        invalid_yahoo_evidence.append({'symbol':s,'date':str(dt.date()),
            'has_nan_or_nonfinite':not bool(np.isfinite(b.loc[dt]).all()),
            'close_above_high':bool(b.loc[dt,'close']>b.loc[dt,'high']),
            'close_below_low':bool(b.loc[dt,'close']<b.loc[dt,'low'])})

all_common=sum(r['common_rows'] for r in results)
summary={'files':len(results),'yahoo_rows':sum(r['yahoo_rows'] for r in results),'common_rows':all_common,
 'all_manifest_hashes_match':all(r['manifest_hash_match'] for r in results),
 'metadata_identity_diagnostics':{r['symbol']:r['identity_diagnostics'] for r in results if r['identity_diagnostics']},
 'yahoo_invalid_rows':len(invalid_yahoo_evidence),'ibkr_invalid_rows':len(invalid_ib_evidence),
 'ibkr_invalid_with_valid_yahoo_counterpart':sum(x.get('yahoo_bar_valid',False) for x in invalid_ib_evidence),
 'common_closes_above1bps_difference':sum(r['common_close_diff_bps']['above1bps'] for r in results),
 'common_closes_above10bps_difference':sum(r['common_close_diff_bps']['above10bps'] for r in results),
 'common_closes_above100bps_difference':sum(r['common_close_diff_bps']['above100bps'] for r in results),
 'return_pairs':sum(r['common_adjacent_return_pairs'] for r in results),
 'return_diffs_above1bps':sum(r['close_return_diff_bps']['above1bps'] for r in results),
 'return_diffs_above10bps':sum(r['close_return_diff_bps']['above10bps'] for r in results),
 'return_diffs_above100bps':sum(r['close_return_diff_bps']['above100bps'] for r in results),
 'calendar_defects':{r['symbol']:{k:r[k] for k in ['yahoo_non_session_dates','yahoo_duplicate_dates','yahoo_missing_internal_sessions']} for r in results if r['yahoo_non_session_dates'] or r['yahoo_duplicate_dates'] or r['yahoo_missing_internal_sessions']}}
out={'status':'INDEPENDENT_SECONDARY_SOURCE_AUDIT_NOT_BLENDED','summary':summary,'per_symbol':results,
     'ibkr_invalid_row_counterparts':invalid_ib_evidence,'yahoo_invalid_rows':invalid_yahoo_evidence}
index=cal.sessions_in_range('2016-10-04','2026-10-02');window=index[index>=pd.Timestamp('2022-01-03')]
counter={k:0 for k in ['ibkr_primary_eligible','yahoo_primary_eligible','both_feature_ready_symbol_sessions',
    'differing_signals_both_ready','primary_both_ready_ibkr','primary_both_ready_yahoo',
    'yahoo_signal_only_when_ibkr_features_missing','ibkr_signal_only_when_yahoo_features_missing']}
signal_rows=[]
def signal_features(d):
    ma=d.close.rolling(5,min_periods=5).mean()
    tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1,skipna=False)
    atr=tr.rolling(5,min_periods=5).mean().shift(1);lag=ma.shift(1);s1=ma.diff().shift(1);s3=(ma.shift(1)-ma.shift(3))/2
    distance=(d.close-lag)/atr;gap=(d.open-d.close.shift())/atr
    ready=pd.concat([lag,atr,s1,s3,distance,gap],axis=1).notna().all(axis=1)
    signal=(atr>0)&(s1>0)&(s3>=0)&distance.between(-.1,.2)&(gap.abs()<=.5)
    return ready,signal
for r in results:
    s=r['symbol']
    if s in ['QQQ','SOXX']:continue
    ib=json.loads((IB/f'{s}_daily.json').read_text())['data']
    a=pd.DataFrame({k:ib[k] for k in COLS},index=pd.DatetimeIndex(pd.to_datetime(ib['time'],utc=True).tz_convert('America/New_York').date))
    a.loc[invalid(a),:]=np.nan
    ar,asig=signal_features(a.reindex(index));br,bsig=signal_features(yahoo_frames[s].reindex(index))
    ar,asig,br,bsig=[v.loc[window] for v in [ar,asig,br,bsig]];ready=ar&br
    row={'symbol':s,'ibkr_primary_eligible':int(asig.sum()),'yahoo_primary_eligible':int(bsig.sum()),
        'both_feature_ready_symbol_sessions':int(ready.sum()),'differing_signals_both_ready':int(((asig!=bsig)&ready).sum()),
        'primary_both_ready_ibkr':int((asig&ready).sum()),'primary_both_ready_yahoo':int((bsig&ready).sum()),
        'yahoo_signal_only_when_ibkr_features_missing':int((bsig&~ar).sum()),
        'ibkr_signal_only_when_yahoo_features_missing':int((asig&~br).sum())}
    signal_rows.append(row)
    for k in counter:counter[k]+=row[k]
out['primary_signal_reproduction_2022_onward']={'counts':counter,'per_symbol':signal_rows,
    'scope':'No optimization. Independent recreation of the old lagged-MA5 expression on each intact source. Not portfolio results.'}
args.output.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
priority=['QQQ','AMD','MU','META','SNDK','WDC','NVDA']
print(json.dumps({'summary':summary,'priority':[{k:r[k] for k in ['symbol','yahoo_rows','common_rows','yahoo_invalid_rows','ibkr_invalid_common_yahoo_valid','common_close_diff_bps','close_return_diff_bps','worst_return_differences']} for r in results if r['symbol'] in priority]},ensure_ascii=False,indent=2,allow_nan=False))
