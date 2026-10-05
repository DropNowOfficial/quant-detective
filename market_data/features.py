"""Versioned minute-bar rules. No daily-HARNESS or profitability certification."""
from __future__ import annotations
from datetime import datetime
import math
from statistics import fmean
from .providers import INTERVALS

RULE_ID = 'MINUTE_MA5_V1'
FRESH_RESPONSE_MS = 36_000


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timestamp(value):
    try:
        return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000)
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def validate_thresholds(band=.5, min_rvol=1.2, min_turnover=0):
    for name, value, low, high in [('band', band, .1, 2), ('min_rvol', min_rvol, .1, 10),
                                   ('min_turnover', min_turnover, 0, 1e12)]:
        if not finite(value) or not low <= value <= high:
            raise ValueError(f'{name} 必须在 {low} 到 {high} 之间')


def closed_rows(observation, now_ms, step):
    """Never turn a captured unfinished candle into a finished observation."""
    selected = {}
    for row in observation.get('rows', []):
        t = row.get('open_time_ms')
        if not finite(t) or int(t) != t or t < 0:
            raise ValueError('无效 K 线时间')
        if t + step > now_ms or not row.get('closed'):
            continue
        values = [row.get(k) for k in ('open', 'high', 'low', 'close', 'volume')]
        if not all(finite(v) for v in values):
            raise ValueError('K 线含缺失或非有限值')
        o, h, l, c, v = values
        if min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
            raise ValueError('K 线 OHLC 或成交量矛盾')
        if t in selected and any(selected[t].get(k) != row.get(k) for k in ('open','high','low','close','volume','quote_volume','base_volume')):
            raise ValueError('同一时间有相互矛盾的 K 线')
        selected[t] = row
    return sorted(selected.values(), key=lambda r: r['open_time_ms'])


def vwap(rows, instrument, market):
    bases, quotes = [], []
    for r in rows:
        base = r.get('base_volume')
        if market == 'gate_usdt' and base is None:
            try:
                multiplier = float(instrument.get('quanto_multiplier'))
                if math.isfinite(multiplier) and multiplier > 0:
                    base = r['volume'] * multiplier
            except (TypeError, ValueError):
                pass
        bases.append(base)
        quotes.append(r.get('quote_volume'))
    if (all(finite(v) and v >= 0 for v in bases + quotes) and sum(bases) > 0):
        return sum(quotes) / sum(bases), 'quote/base'
    denominator = sum(r['volume'] for r in rows)
    return ((sum((r['high'] + r['low'] + r['close']) / 3 * r['volume'] for r in rows) / denominator)
            if denominator > 0 else None), 'HLC3 approximation'


def stale_reason(row, now_ms):
    received = timestamp(row.get('received_at_utc'))
    end = row.get('signal_time_ms')
    step = INTERVALS[row['interval']] * 1000
    if received is None or received > now_ms + 5000:
        return '响应时间缺失或本机时钟偏差'
    if now_ms - received > FRESH_RESPONSE_MS:
        return '成功抓取已超过36秒，等待刷新'
    benchmark_received=timestamp(row.get('benchmark_received_at_utc'))
    if benchmark_received is not None and now_ms-benchmark_received>FRESH_RESPONSE_MS:
        return '同源基准抓取已超过36秒，等待刷新'
    if end is None or now_ms - end > step + 15_000:
        return '最新已收盘K线过时（含休市或源延迟）'
    return None


def analyze(observation, benchmark, instrument, now_ms, band=.5, min_rvol=1.2, min_turnover=0):
    validate_thresholds(band, min_rvol, min_turnover)
    interval = observation.get('interval')
    if interval not in INTERVALS:
        raise ValueError('周期只支持1m、5m、15m')
    market = observation.get('market')
    step = INTERVALS[interval] * 1000
    out = {k: observation.get(k) for k in ('market','source','symbol','interval','source_url',
                                          'requested_at_utc','received_at_utc','http_status','volume_unit')}
    out.update(rule_id=RULE_ID, status='BLOCKED', reason=None, price=None, price_unclosed=False,
               price_time_ms=None, score=None, metrics={}, checks=[], signal_time_ms=None,
               benchmark_symbol=benchmark.get('symbol') if benchmark else None,
               benchmark_received_at_utc=benchmark.get('received_at_utc') if benchmark else None,
               warnings=observation.get('warnings', []), bars=[])
    if not observation.get('ok'):
        out.update(status='ERROR', reason=observation.get('error') or '行情请求失败')
        return out
    observed_at = timestamp(observation.get('received_at_utc'))
    out['response_age_seconds'] = max(0, (now_ms-observed_at)/1000) if observed_at is not None else None
    requested_at = timestamp(observation.get('requested_at_utc'))
    out['request_duration_ms'] = max(0, observed_at-requested_at) if observed_at is not None and requested_at is not None else None
    try:
        rows = closed_rows(observation, now_ms, step)
        visible = [r for r in observation.get('rows', []) if finite(r.get('open_time_ms')) and r['open_time_ms'] <= now_ms
                   and finite(r.get('close')) and r['close'] > 0]
        if visible:
            last = max(visible, key=lambda r: r['open_time_ms'])
            out.update(price=last['close'], price_unclosed=not last.get('closed', False), price_time_ms=last['open_time_ms'])
        if rows:
            out['signal_time_ms'] = rows[-1]['open_time_ms'] + step
        if observation.get('dropped_rows', 0):
            raise ValueError('本次来源存在被剔除的无效K线')
        stale = stale_reason(out, now_ms)
        if stale:
            out.update(status='STALE', reason=stale)
            return out
        if len(rows) < 61:
            raise ValueError(f'至少需要61根已收盘K线；当前{len(rows)}根')
        rows = rows[-121:]
        if any(b['open_time_ms']-a['open_time_ms'] != step for a,b in zip(rows[-61:], rows[-60:])):
            raise ValueError('最近61根K线不连续；不能跨缺口计算')
        if benchmark and not benchmark.get('ok'):
            raise ValueError('同源基准获取失败：' + str(benchmark.get('error') or benchmark.get('http_status')))
        if not benchmark or benchmark.get('market') != market or benchmark.get('interval') != interval:
            raise ValueError('同来源、同周期基准缺失或身份不一致')
        if benchmark.get('dropped_rows',0):
            raise ValueError('基准存在无效K线')
        bm_rows = closed_rows(benchmark, now_ms, step)
        bm = {r['open_time_ms']:r for r in bm_rows}
        last21 = rows[-21:]
        if any(r['open_time_ms'] not in bm for r in last21):
            raise ValueError('基准与品种的20根收益时间未严格对齐')
        bm_received = timestamp(benchmark.get('received_at_utc'))
        if bm_received is None or now_ms-bm_received > FRESH_RESPONSE_MS or bm_received > now_ms+5000:
            raise ValueError('基准抓取已过时或时间无效')
        closes = [r['close'] for r in rows]
        ma5, previous_ma5, older_ma5 = [fmean(closes[-5-i:len(closes)-i]) for i in (0,1,2)]
        ma20 = fmean(closes[-20:])
        true_ranges = [max(r['high']-r['low'], abs(r['high']-p['close']), abs(r['low']-p['close'])) for p,r in zip(rows[-15:-1],rows[-14:])]
        atr = fmean(true_ranges)
        if atr <= 0:
            raise ValueError('ATR14为零，距离无法计算')
        current_vwap,basis = vwap(rows[-60:],instrument,market)
        previous_vwap,_ = vwap(rows[-61:-1],instrument,market)
        average_volume = fmean(r['volume'] for r in rows[-21:-1])
        rvol = rows[-1]['volume']/average_volume if average_volume>0 else None
        start,end = last21[0]['open_time_ms'],last21[-1]['open_time_ms']
        ret = (closes[-1]/closes[-21]-1)*100
        bm_ret = (bm[end]['close']/bm[start]['close']-1)*100
        quote_values = [r.get('quote_volume') for r in rows[-60:]]
        turnover = sum(quote_values) if all(finite(v) and v>=0 for v in quote_values) else None
        buy=rows[-1].get('taker_buy_volume') if market=='binance_spot' else None
        buy_ratio=buy/rows[-1]['volume'] if finite(buy) and 0<=buy<=rows[-1]['volume'] and rows[-1]['volume']>0 else None
        out['metrics'] = dict(closed_price=closes[-1],ma5=ma5,ma20=ma20,ma5_slope=ma5-previous_ma5,
                              ma5_short_slope=(ma5-older_ma5)/2,atr14=atr,atr_pct=atr/closes[-1]*100,
                              distance_atr=(closes[-1]-ma5)/atr,vwap60=current_vwap,vwap_basis=basis,
                              rvol20=rvol,return20_pct=ret,rs20_pp=ret-bm_ret,turnover60=turnover,taker_buy_ratio=buy_ratio)
        if rvol is None or current_vwap is None or previous_vwap is None:
            raise ValueError('成交量基准为零，RVOL或VWAP缺失')
        if min_turnover>0 and turnover is None:
            raise ValueError('成交额缺失，不能验证流动性门槛')
        m=out['metrics']
        def check(key,label,value,threshold,passed):
            operator = '≤' if key in {'band','volatility'} else '≥' if key in {'rvol','rs','liquidity'} else '>'
            details = []
            if key=='slope':
                details=[dict(label='单步斜率',value=m['ma5_slope'],operator='>',threshold=0),
                         dict(label='三点斜率',value=m['ma5_short_slope'],operator='>',threshold=0)]
            elif key=='vwap':
                details=[dict(label='本根收盘',value=closes[-1],operator='>',threshold=current_vwap),
                         dict(label='前根收盘',value=closes[-2],operator='>',threshold=previous_vwap)]
            out['checks'].append(dict(key=key,label=label,value=value,threshold=threshold,passed=bool(passed),operator=operator,details=details))
        check('trend','收盘价 > MA20（20根）',closes[-1],ma20,closes[-1]>ma20)
        check('slope','MA5单步与三点斜率 > 0',m['ma5_slope'],0,min(m['ma5_slope'],m['ma5_short_slope'])>0)
        check('band','距MA5绝对值 / ATR ≤ 门槛',abs(m['distance_atr']),band,abs(m['distance_atr'])<=band)
        above=closes[-1]>current_vwap and closes[-2]>previous_vwap
        check('vwap','连续2根收盘 > 各自VWAP60',closes[-1]-current_vwap,0,above)
        check('rvol','RVOL：本根 / 前20根均量',rvol,min_rvol,rvol>=min_rvol)
        check('rs','20根收益强于同源基准',m['rs20_pp'],0,m['rs20_pp']>=0)
        check('volatility','ATR14 / 收盘价 ≤ 12%',m['atr_pct'],12,m['atr_pct']<=12)
        if min_turnover>0:
            check('liquidity','60根成交额达到门槛',turnover,min_turnover,turnover>=min_turnover)
        out['score']=round(sum(c['passed'] for c in out['checks'])/len(out['checks'])*100,1)
        failed=[c for c in out['checks'] if not c['passed']]
        status='READY' if not failed else 'OUT' if any(c['key'] in {'trend','slope','volatility','liquidity'} for c in failed) else 'WATCH'
        reason='所有分钟规则已匹配' if not failed else '；'.join(c['label'] for c in failed)
        out.update(status=status,reason=reason)
        out['bars']=[{k:r[k] for k in ('open_time_ms','open','high','low','close','volume')} for r in rows[-80:]]
        stale=stale_reason(out,now_ms)
        if stale: out.update(status='STALE',reason=stale,score=None)
    except (ValueError,KeyError,TypeError,OverflowError,ZeroDivisionError) as exc:
        out.update(status='BLOCKED',reason=str(exc),score=None)
    return out


def rank_rows(rows):
    order={'READY':0,'WATCH':1,'OUT':2,'BLOCKED':3,'STALE':4,'ERROR':5}
    def key(r):
        rs=r.get('metrics',{}).get('rs20_pp')
        return (order.get(r['status'],9), -(r['score'] if r.get('score') is not None else -1),
                -(rs if finite(rs) else -1e100),r['symbol'])
    return [dict(row,rank=i+1) for i,row in enumerate(sorted(rows,key=key))]
