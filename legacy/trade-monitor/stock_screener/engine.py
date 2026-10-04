"""Causal screening and explicit evidence. Ranking is not a return forecast."""
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import platform

import exchange_calendars as xc
import numpy as np
import pandas as pd

from . import __version__
from .calendar import aware_timestamp, selected_session
from .data import load_instrument, load_manifest, validate_universe


@dataclass(frozen=True)
class Policy:
    min_price: float = 5.0
    min_adv20: float = 20000000.0
    max_atr_fraction: float = .12
    min_rs20: float | None = None

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key == 'min_rs20' and value is None: continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{key} 必须是有限数字')
            if key in {'min_price','min_adv20'} and value < 0: raise ValueError(f'{key} 不得为负')
            if key == 'max_atr_fraction' and not 0 < value <= 1: raise ValueError('ATR 比例必须大于 0 且不超过 1')
            if key == 'min_rs20' and not -10 <= value <= 10: raise ValueError('相对收益门槛超出支持范围')


def factors(frame, benchmarks):
    close = frame.close
    ma5 = float(close.iloc[-6:-1].mean())
    atr_parts = np.vstack([(frame.high-frame.low).to_numpy(),
                           (frame.high-close.shift()).abs().to_numpy(),
                           (frame.low-close.shift()).abs().to_numpy()])
    atr5 = float(np.max(atr_parts, axis=0)[-6:-1].mean())
    if not math.isfinite(atr5) or atr5 <= 0: raise ValueError('ZERO_ATR')
    current = float(close.iloc[-1])
    adv20 = float((close*frame.volume).iloc[-21:-1].mean())
    avg_volume = float(frame.volume.iloc[-21:-1].mean())
    if avg_volume <= 0: raise ValueError('ZERO_VOLUME')
    f = dict(close=current, ma5=ma5, atr5=atr5, atr_fraction=atr5/current,
             slope1=ma5-float(close.iloc[-7:-2].mean()),
             slope3=(ma5-float(close.iloc[-8:-3].mean()))/2,
             d5=(current-ma5)/atr5, gap=(float(frame.open.iloc[-1])-float(close.iloc[-2]))/atr5,
             adv20=adv20, volume_ratio=float(frame.volume.iloc[-1])/avg_volume,
             return60=current/float(close.iloc[-61])-1,
             candidate_low=ma5-.10*atr5, candidate_high=ma5+.20*atr5,
             watch_low=ma5-.35*atr5, watch_high=ma5+.40*atr5)
    for horizon in [5,20]:
        ret = current/float(close.iloc[-horizon-1])-1
        f[f'return{horizon}'] = ret
        for symbol, b in benchmarks.items():
            f[f'rs{horizon}_{symbol.lower()}'] = ret-(float(b.close.iloc[-1])/float(b.close.iloc[-horizon-1])-1)
    if not all(math.isfinite(x) for x in f.values()): raise ValueError('NONFINITE_FACTOR')
    return f


def classify(f, policy):
    tests = [
        ('min_price','价格',f['close'],f'>= {policy.min_price:g} USD',f['close']>=policy.min_price,'common'),
        ('min_adv20','前 20 日平均成交额',f['adv20'],f'>= {policy.min_adv20:g} USD',f['adv20']>=policy.min_adv20,'common'),
        ('max_atr_fraction','ATR5 / 收盘价',f['atr_fraction'],f'<= {policy.max_atr_fraction:g}',f['atr_fraction']<=policy.max_atr_fraction,'common'),
        ('slope1','MA5 一阶变化',f['slope1'],'> 0',f['slope1']>0,'common'),
        ('slope3','MA5 三点 OLS 斜率',f['slope3'],'>= 0',f['slope3']>=0,'common'),
        ('candidate_band','候选距离 / ATR',f['d5'],'[-0.10, +0.20]',-.10<=f['d5']<=.20,'candidate'),
        ('candidate_gap','候选跳空 / ATR',f['gap'],'绝对值 <= 0.50',abs(f['gap'])<=.50,'candidate'),
        ('watch_band','观察距离 / ATR',f['d5'],'[-0.35, +0.40]',-.35<=f['d5']<=.40,'watch'),
        ('watch_gap','观察跳空 / ATR',f['gap'],'绝对值 <= 0.80',abs(f['gap'])<=.80,'watch')]
    if policy.min_rs20 is not None:
        tests.append(('min_rs20','20 日相对 QQQ 收益',f['rs20_qqq'],f'>= {policy.min_rs20:g}',f['rs20_qqq']>=policy.min_rs20,'common'))
    checks = [dict(key=k,label=l,value=float(v),threshold=t,passed=bool(p),scope=s) for k,l,v,t,p,s in tests]
    common = all(x['passed'] for x in checks if x['scope']=='common')
    if common and all(x['passed'] for x in checks if x['scope']=='candidate'): state='CANDIDATE'
    elif common and all(x['passed'] for x in checks if x['scope']=='watch'): state='WATCH'
    else: state='NOT_MATCHED'
    return state, checks


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def engine_hash():
    h = hashlib.sha256()
    for path in sorted(p for p in Path(__file__).parent.iterdir() if p.suffix in {'.py','.html'}):
        h.update(path.name.encode()); h.update(path.read_bytes())
    return h.hexdigest()


def screen(raw_dir, universe, policy, as_of, session=None):
    if not isinstance(policy, Policy): raise ValueError('policy 必须为已验证的 Policy')
    instruments = validate_universe(universe)
    as_of = aware_timestamp(as_of)
    target, latest = selected_session(as_of, session)
    manifest = load_manifest(raw_dir)
    loaded = {i['symbol']:load_instrument(raw_dir,i,target,manifest) for i in instruments}
    benchmark_symbols = ['QQQ','SOXX']
    unavailable = [s for s in benchmark_symbols if loaded[s][2]]
    benchmarks = {s:loaded[s][0] for s in benchmark_symbols}
    rows = []
    for item in instruments:
        if item['group']=='benchmark': continue
        frame, evidence, problems = loaded[item['symbol']]
        blocks = list(problems)
        if unavailable: blocks.append('BENCHMARK_UNAVAILABLE')
        row = dict(**item, state='DATA_BLOCKED', rank=None, factors={}, checks=[], block_codes=blocks,
                   warnings=['PRICE_ADJUSTMENT_UNVERIFIED','FUNDAMENTALS_UNAVAILABLE','EARNINGS_CALENDAR_UNAVAILABLE'],
                   source=evidence, validation='UNVALIDATED_SCREEN', intraday='NOT_EVALUATED', history=[])
        if not blocks:
            try:
                row['factors'] = factors(frame, benchmarks)
                row['state'], row['checks'] = classify(row['factors'],policy)
                row['history'] = [dict(date=str(d.date()),close=float(v)) for d,v in frame.close.items()]
                if row['factors']['d5']>.35: row['warnings'].append('STRETCHED_ABOVE_035_ATR')
            except ValueError as exc:
                row['block_codes'].append(str(exc))
        rows.append(row)
    state_order = {'CANDIDATE':0,'WATCH':1,'NOT_MATCHED':2,'DATA_BLOCKED':3}
    rows.sort(key=lambda r:(state_order[r['state']],-r['factors'].get('rs20_qqq',-1e100),-r['factors'].get('adv20',0),r['symbol']))
    counts = Counter()
    for row in rows:
        counts[row['state']]+=1
        if row['state'] in {'CANDIDATE','WATCH'}: row['rank']=counts[row['state']]
    meta = dict(schema_version=1, engine_version=__version__, engine_sha256=engine_hash(),
                as_of=as_of.isoformat(), session=str(target.date()), expected_latest_session=str(latest.date()),
                mode='HISTORICAL_RECONSTRUCTION' if target<latest else 'LATEST_SNAPSHOT',
                historical_data_vintage='CURRENT_DOWNLOAD_NOT_POINT_IN_TIME',
                calendar='XNYS', completion_buffer_minutes=30,
                policy=asdict(policy), universe=universe, count=len(rows),
                counts={k:counts[k] for k in state_order},
                benchmarks={s:dict(source=loaded[s][1],block_codes=loaded[s][2]) for s in benchmark_symbols},
                ranking='within state: RS20 vs QQQ descending, prior ADV20 descending, symbol ascending',
                data_refresh='IMPORT_PROVIDER_SNAPSHOTS_THEN_RECOMPUTE',
                dependencies=dict(python=platform.python_version(),pandas=pd.__version__,numpy=np.__version__,exchange_calendars=xc.__version__))
    # A reconstructed as-of preceding the download is never labelled live evidence.
    if any(e['retrieved_at'] and aware_timestamp(e['retrieved_at'])>as_of for _,e,_ in loaded.values()):
        meta['mode']='HISTORICAL_RECONSTRUCTION'
    result = {**meta,'rows':rows}
    result['run_id']=hashlib.sha256(canonical(result).encode()).hexdigest()[:24]
    return result
