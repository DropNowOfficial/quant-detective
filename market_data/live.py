"""Bounded background polling. Observations and session replay live only in memory."""
from __future__ import annotations
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
import re
import time
import uuid

from . import providers
from .features import analyze, rank_rows, stale_reason, timestamp, validate_thresholds, RULE_ID
from .transport import fetch

REFRESH_SECONDS = 12
BATCH_SIZE = 12
BASES = ('BTC','ETH','SOL','BNB','XRP','DOGE','ADA','AVAX','LINK','LTC','SUI','TON')
BENCHMARKS = {'binance_spot':'BTCUSDT','gate_usdt':'BTC_USDT','okx_swap':'BTC-USDT-SWAP',
              'binance_usdm':'BTCUSDT','us_equity':'QQQ','cn_equity':'510300'}


def normalize_config(config):
    if set(config)-{'market','interval','scope','symbols','band','min_rvol','min_turnover'}:
        raise ValueError('不支持的扫描参数')
    out=dict(market=config.get('market','binance_spot'),interval=config.get('interval','5m'),
             scope=config.get('scope','watchlist'),symbols=config.get('symbols',''),
             band=float(config.get('band',.5)),min_rvol=float(config.get('min_rvol',1.2)),
             min_turnover=float(config.get('min_turnover',0)))
    if out['market'] not in providers.MARKETS or out['interval'] not in providers.INTERVALS:
        raise ValueError('市场或周期无效')
    if out['scope'] not in {'watchlist','all'}:
        raise ValueError('扫描范围只支持watchlist或all')
    symbols=out['symbols']
    if not isinstance(symbols,str): raise ValueError('symbols必须是逗号分隔字符串')
    if symbols:
        items=sorted(set(s.strip().upper() for s in symbols.split(',')))
        if len(items)>12 or any(not re.fullmatch(r'[A-Z0-9][A-Z0-9._^=/-]{0,79}',s) for s in items):
            raise ValueError('关注池最多12个有效代码；更多品种使用全市场分批扫描')
        out['symbols']=','.join(items)
    if out['scope']=='all': out['symbols']=''
    validate_thresholds(out['band'],out['min_rvol'],out['min_turnover'])
    return out


def eligible(market,instrument):
    symbol=instrument['symbol']
    if market in {'binance_spot','binance_usdm'}:
        return instrument.get('quote_asset')=='USDT' or (not instrument.get('quote_asset') and symbol.endswith('USDT'))
    if market=='gate_usdt': return symbol.endswith('_USDT') or symbol.endswith('USDT')
    if market.startswith('okx_'): return '-USDT-' in symbol
    return True


def choose_defaults(market, instruments):
    available={r['symbol'] for r in instruments}
    if market=='us_equity': wanted=('NVDA','AMD','MU','INTC','SNDK','MRVL','GLW','GOOG','META','ARM','VRT','AAPL')
    elif market=='cn_equity': wanted=('600519','300750','601318','000001','600036','002415','000333','002594','601012','000858','300059','600900')
    elif market=='gate_usdt': wanted=tuple(s+'_USDT' for s in BASES)
    elif market=='okx_swap': wanted=tuple(s+'-USDT-SWAP' for s in BASES)
    else: wanted=tuple(s+'USDT' for s in BASES)
    chosen=[s for s in wanted if s in available]
    return chosen or sorted(available)[:12]


@dataclass
class Session:
    config: dict
    last_access: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    due: float = 0
    cycle: int = 0
    cursor: int = 0
    round: int = 0
    failures: int = 0
    state: str = 'WARMING'
    error: str | None = None
    universe: list = field(default_factory=list)
    instruments: dict = field(default_factory=dict)
    observations: dict = field(default_factory=dict)
    analyses: dict = field(default_factory=dict)
    benchmark: dict | None = None
    catalog: dict | None = None
    history: deque = field(default_factory=lambda: deque(maxlen=30))
    transitions: deque = field(default_factory=lambda: deque(maxlen=120))
    last_completed: float | None = None
    last_duration: float | None = None
    cycle_error: str | None = None
    benchmark_error: dict | None = None


class LiveService:
    def __init__(self, fetcher=fetch, catalog_fn=None, candles_fn=None, benchmark_fn=None, clock=time.time, autostart=True):
        self.fetcher=fetcher; self.catalog_fn=catalog_fn or providers.get_catalog
        self.candles_fn=candles_fn or providers.get_candles; self.clock=clock
        self.benchmark_fn=benchmark_fn or providers.get_benchmark
        self.sessions={}; self.catalogs={}; self.lock=Lock(); self.cycle_lock=Lock(); self.stop=Event()
        self.pool=ThreadPoolExecutor(max_workers=4,thread_name_prefix='qd-public-get')
        self.thread=None
        if autostart:
            self.thread=Thread(target=self._run,daemon=True,name='qd-live-scanner');self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread: self.thread.join(timeout=1)
        self.pool.shutdown(wait=False,cancel_futures=True)

    def _run(self):
        while not self.stop.is_set():
            self.tick()
            self.stop.wait(.2)

    def _expire(self,now):
        for key,s in list(self.sessions.items()):
            if now-s.last_access>90: del self.sessions[key]

    def _render(self,s,now,detail=None):
        config=s.config
        rows=[]
        for symbol,obs in s.observations.items():
            if detail is not None and symbol!=detail: continue
            # Immutable features are computed once per received batch. Status polls
            # must not redo 121-bar math for the entire growing market universe.
            row=dict(s.analyses[symbol])
            received=timestamp(row.get('received_at_utc'))
            row['response_age_seconds']=max(0,(now*1000-received)/1000) if received is not None else None
            if row['status'] in {'READY','WATCH','OUT'}:
                stale=stale_reason(row,int(now*1000))
                if stale:row.update(status='STALE',reason=stale,score=None)
            if s.cycle_error:
                row.update(status='ERROR',reason='本轮扫描失败：'+s.cycle_error,score=None,
                           price=None,metrics={},checks=[],bars=[],http_status=(s.catalog or {}).get('http_status'))
            if detail is None:
                for key in ('bars','checks','warnings'):row.pop(key,None)
            rows.append(row)
        return rank_rows(rows)

    def snapshot(self,config,client='default'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',client): raise ValueError('客户端标识无效')
        config=normalize_config(config); now=self.clock()
        with self.lock:
            self._expire(now)
            s=self.sessions.get(client)
            if s is None or s.config!=config:
                if s is None and len(self.sessions)>=4:
                    raise ValueError('最多4个同时扫描的页面；关闭闲置页面90秒后释放')
                s=Session(config=config,last_access=now,due=now);self.sessions[client]=s
            s.last_access=now
            rows=self._render(s,now)
            return dict(session_id=s.id,config=config,rule_id=RULE_ID,data_mode='PUBLIC_POLLING',
                        refresh_seconds=REFRESH_SECONDS,server_time_ms=int(now*1000),
                        source=providers.MARKETS[config['market']]['source'],state=s.state,error=s.error,
                        benchmark_error=s.benchmark_error,
                        cycle=s.cycle,last_completed_ms=int(s.last_completed*1000) if s.last_completed is not None else None,
                        last_cycle_duration_seconds=s.last_duration,next_refresh_seconds=max(0,round(s.due-now,1)),
                        catalog_http_status=(s.catalog or {}).get('http_status'),
                        catalog_url=(s.catalog or {}).get('source_url'),
                        catalog_warnings=(s.catalog or {}).get('warnings',[]),
                        coverage=dict(catalog_total=len((s.catalog or {}).get('instruments',[])),universe=len(s.universe),
                                      scanned=len(s.observations),fresh=sum(r['status'] in {'READY','WATCH','OUT'} for r in rows),
                                      batch_size=BATCH_SIZE,remaining_in_round=len(s.universe)-s.cursor if s.cursor else (0 if s.round else len(s.universe)),
                                      round=s.round,scope_note='USDT计价品种分批扫描' if config['scope']=='all' and not config['market'].endswith('equity') else '所选来源目录'),
                        rows=rows,transitions=list(s.transitions)[-30:])

    def history(self,session_id):
        with self.lock:
            for s in self.sessions.values():
                if s.id==session_id: return dict(session_id=s.id,snapshots=deepcopy(list(s.history)))
        raise ValueError('扫描会话不存在或已过期')

    def detail(self,session_id,symbol):
        with self.lock:
            for s in self.sessions.values():
                if s.id==session_id:
                    rows=self._render(s,self.clock(),symbol)
                    if rows:return rows[0]
                    raise ValueError('此品种尚未扫描')
        raise ValueError('扫描会话不存在或已过期')

    def tick(self):
        if self.stop.is_set() or not self.cycle_lock.acquire(blocking=False):return False
        try:
            now=self.clock()
            with self.lock:
                self._expire(now)
                ready=[s for s in self.sessions.values() if s.due<=now]
                if not ready:return False
                s=min(ready,key=lambda s:(s.due,s.id));s.due=now+REFRESH_SECONDS
            self._cycle(s,now)
            return True
        finally: self.cycle_lock.release()

    def _current(self,s):
        return any(v is s for v in self.sessions.values()) and not self.stop.is_set()

    def _cycle(self,s,started):
        c=s.config; market=c['market']
        try:
            cached=self.catalogs.get(market)
            if not cached or self.clock()-cached[0]>600:
                catalog=self.catalog_fn(market,self.fetcher)
                if catalog.get('ok'): self.catalogs[market]=(self.clock(),catalog)
            else:catalog=cached[1]
            if not catalog.get('ok'):
                with self.lock:
                    if self._current(s):s.catalog=catalog
                raise ValueError(catalog.get('error') or '目录读取失败')
            instruments={r['symbol']:r for r in catalog.get('instruments',[])}
            if c['scope']=='all': universe=sorted(k for k,r in instruments.items() if eligible(market,r))
            elif c['symbols']: universe=c['symbols'].split(',')
            else:universe=choose_defaults(market,list(instruments.values()))
            if not universe:raise ValueError('目录没有适合当前扫描的品种')
            unknown=[k for k in universe if k not in instruments]
            if unknown:raise ValueError('代码不在所选来源目录：'+', '.join(unknown))
            incompatible=[k for k in universe if not eligible(market,instruments[k])]
            if incompatible:
                raise ValueError('当前加密扫描仅比较USDT计价品种；其他计价可在原始K线页查询：'+', '.join(incompatible))
            cursor=s.cursor if s.universe==universe else 0
            batch=universe[cursor:cursor+BATCH_SIZE]
            benchmark_symbol=BENCHMARKS.get(market)
            if benchmark_symbol not in instruments and market!='cn_equity':benchmark_symbol=None
            symbols=list(dict.fromkeys(([benchmark_symbol] if benchmark_symbol else [])+batch))
            if self.stop.is_set(): return
            def query(symbol):
                if self.stop.is_set(): return None
                try:
                    if market=='cn_equity' and symbol==benchmark_symbol:
                        return self.benchmark_fn(market,c['interval'],122,self.fetcher,now_ms=int(self.clock()*1000))
                    return self.candles_fn(market,symbol,c['interval'],122,self.fetcher,now_ms=int(self.clock()*1000))
                except Exception as exc:
                    return dict(ok=False,market=market,source=providers.MARKETS[market]['source'],symbol=symbol,
                                interval=c['interval'],rows=[],error=f'读取失败：{type(exc).__name__}: {str(exc)[:200]}')
            futures={symbol:self.pool.submit(query,symbol) for symbol in symbols}
            results={symbol:future.result() for symbol,future in futures.items()}
            if self.stop.is_set():return
            now=self.clock();benchmark=results.get(benchmark_symbol)
            with self.lock:
                if not self._current(s):return
                prior={r['symbol']:r['status'] for r in self._render(s,now)}
                s.cycle_error=None
                s.catalog=catalog;s.instruments=instruments;s.universe=universe;s.benchmark=benchmark
                # Remove delisted / removed symbols on refreshed directory.
                s.observations={k:v for k,v in s.observations.items() if k in universe}
                s.analyses={k:v for k,v in s.analyses.items() if k in universe}
                for symbol in batch:
                    obs=results[symbol]
                    if obs is not None:
                        obs=dict(obs);obs['_benchmark']=benchmark;s.observations[symbol]=obs
                        s.analyses[symbol]=analyze(obs,benchmark,instruments.get(symbol,{}),int(now*1000),
                                                  band=c['band'],min_rvol=c['min_rvol'],min_turnover=c['min_turnover'])
                s.cycle+=1;s.cursor=cursor+len(batch)
                if s.cursor>=len(universe):s.cursor=0;s.round+=1
                failed=sum(not (results[k] or {}).get('ok') for k in batch)
                benchmark_failed=bool(benchmark_symbol and benchmark and not benchmark.get('ok'))
                s.benchmark_error=({k:benchmark.get(k) for k in ('symbol','error','http_status','source_url','requested_at_utc','received_at_utc')}
                                   if benchmark_failed else None)
                s.state='ERROR' if failed==len(batch) or benchmark_failed else 'PARTIAL' if failed else 'RUNNING'
                s.error=('同源基准 '+benchmark_symbol+'：'+str(benchmark.get('error')) if benchmark_failed else
                         next((results[k].get('error') for k in batch if not results[k].get('ok')),None))
                s.failures=s.failures+1 if failed==len(batch) or benchmark_failed else 0
                delay=min(120,REFRESH_SECONDS*2**min(s.failures,4))
                # Start-to-start cadence; no overlap. A slow batch honestly delays the next one.
                s.due=max(started+delay,now+(REFRESH_SECONDS if failed else 0))
                s.last_completed=now;s.last_duration=now-started
                evaluated=self._render(s,now)
                batch_rows=[dict(r,checks=s.analyses[r['symbol']].get('checks',[])) for r in evaluated if r['symbol'] in batch]
                for r in batch_rows:
                    if r['status']!=prior.get(r['symbol']):
                        s.transitions.append(dict(at_ms=int(now*1000),symbol=r['symbol'],previous=prior.get(r['symbol']),
                                                  status=r['status'],reason=r['reason'],signal_time_ms=r['signal_time_ms']))
                s.history.append(dict(at_ms=int(now*1000),cycle=s.cycle,config=deepcopy(c),rows=deepcopy(batch_rows),
                                      label='HISTORICAL_OBSERVATION_BATCH',rule_id=RULE_ID))
        except Exception as exc:
            with self.lock:
                if not self._current(s):return
                now=self.clock()
                prior={r['symbol']:r['status'] for r in self._render(s,now)}
                s.state='ERROR';s.error=str(exc)[:500];s.cycle_error=s.error;s.failures+=1
                s.cycle+=1;s.last_completed=now;s.last_duration=now-started
                s.due=self.clock()+min(120,REFRESH_SECONDS*2**min(s.failures,4))
                failed_rows=self._render(s,now)
                for row in failed_rows:
                    if prior.get(row['symbol'])!='ERROR':
                        s.transitions.append(dict(at_ms=int(now*1000),symbol=row['symbol'],previous=prior.get(row['symbol']),
                                                  status='ERROR',reason=s.error,signal_time_ms=row['signal_time_ms']))
                s.history.append(dict(at_ms=int(now*1000),cycle=s.cycle,config=deepcopy(c),rows=deepcopy(failed_rows[:12]),
                                      label='HISTORICAL_FAILURE',error=s.error,rule_id=RULE_ID))
