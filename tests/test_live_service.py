from copy import deepcopy
from threading import Event, Thread
import pytest
from market_data.live import LiveService, normalize_config
from test_live_features import observation, iso, NOW

class Source:
    def __init__(self, n=30):
        self.n=n; self.calls=[]; self.catalog_calls=0; self.clock=NOW/1000; self.fail=False
    def catalog(self,market,fetch):
        self.catalog_calls+=1
        return {'ok':True,'market':market,'source':market,'instruments':[{'symbol':s,'name':s} for s in ['BTCUSDT','ETHUSDT','SOLUSDT']+[f'X{i}USDT' for i in range(self.n)]], 'warnings':[]}
    def candles(self,market,symbol,interval,limit,fetch,now_ms=None):
        self.calls.append((market,symbol,interval,limit)); out=observation(market,symbol)
        out.update(interval=interval,received_at_utc=iso(self.clock*1000),requested_at_utc=iso(self.clock*1000-50))
        if self.fail: out.update(ok=False,error='HTTP 429',http_status=429,rows=[])
        return out
    def service(self):
        return LiveService(catalog_fn=self.catalog,candles_fn=self.candles,clock=lambda:self.clock,autostart=False)


def test_service_default_catalog_filter_periodic_refresh_and_staleness():
    s=Source(); live=s.service()
    try:
        snap=live.snapshot({'market':'binance_spot','interval':'1m'})
        assert snap['state']=='WARMING'
        assert live.tick()
        snap=live.snapshot({'market':'binance_spot','interval':'1m'})
        assert {r['symbol'] for r in snap['rows']}=={'BTCUSDT','ETHUSDT','SOLUSDT'}
        assert snap['coverage']['universe']==3 and snap['cycle']==1
        assert snap['refresh_seconds']==12 and snap['data_mode']=='PUBLIC_POLLING'
        count=len(s.calls); assert not live.tick() and len(s.calls)==count
        s.clock+=12; assert live.tick(); assert len(s.calls)==count*2
        s.clock+=37; snap=live.snapshot({'market':'binance_spot','interval':'1m'})
        assert all(r['status']=='STALE' for r in snap['rows'])
        assert live.history(snap['session_id'])['snapshots']
    finally: live.close()


def test_all_rotates_and_never_claims_every_symbol_is_refreshed_in_one_cycle():
    s=Source();live=s.service()
    try:
        config={'market':'binance_spot','interval':'1m','scope':'all'}
        live.snapshot(config); live.tick(); first=live.snapshot(config)
        assert first['coverage']['universe']==33 and first['coverage']['scanned']==12
        assert first['coverage']['remaining_in_round']==21
        s.clock+=12; live.tick(); second=live.snapshot(config)
        assert second['coverage']['scanned']==24 and second['coverage']['remaining_in_round']==9
        s.clock+=12; live.tick(); final=live.snapshot(config)
        assert final['coverage']['scanned']==33 and final['coverage']['round']==1
        assert s.catalog_calls==1
    finally: live.close()


def test_failure_replaces_price_and_backoff_is_respected():
    s=Source();live=s.service()
    try:
        config={'market':'binance_spot','interval':'1m','symbols':'ETHUSDT'}
        live.snapshot(config);live.tick();s.clock+=12;s.fail=True;live.tick()
        snap=live.snapshot(config)
        assert snap['rows'][0]['status']=='ERROR' and snap['rows'][0]['price'] is None
        assert snap['next_refresh_seconds']>=24
        assert not live.tick()
    finally: live.close()


def test_symbol_must_be_from_own_catalog_and_tabs_are_isolated():
    s=Source();live=s.service()
    try:
        bad={'market':'binance_spot','symbols':'FAKEUSDT'}
        live.snapshot(bad,client='a');live.tick()
        assert live.snapshot(bad,client='a')['state']=='ERROR'
        assert not s.calls
        good={'market':'binance_spot','symbols':'ETHUSDT','interval':'1m'}
        live.snapshot(good,client='b');live.tick()
        assert live.snapshot(good,client='b')['rows'][0]['symbol']=='ETHUSDT'
        assert live.snapshot(bad,client='a')['rows']==[]
    finally: live.close()


def test_generation_guard_and_idle_expiry():
    s=Source();started=Event();release=Event();base=s.candles
    def slow(*args,**kw):
        started.set();release.wait(2);return base(*args,**kw)
    live=LiveService(catalog_fn=s.catalog,candles_fn=slow,clock=lambda:s.clock,autostart=False)
    try:
        live.snapshot({'market':'binance_spot','interval':'1m','symbols':'ETHUSDT'},client='a')
        t=Thread(target=live.tick);t.start();assert started.wait(1)
        newer=live.snapshot({'market':'gate_usdt','interval':'1m'},client='a')
        release.set();t.join()
        out=live.snapshot({'market':'gate_usdt','interval':'1m'},client='a')
        assert out['session_id']==newer['session_id'] and out['rows']==[]
        s.clock+=91;count=len(s.calls);assert not live.tick();assert len(s.calls)==count
    finally: release.set();live.close()


def test_no_overlapping_cycles():
    s=Source();started=Event();release=Event();base=s.candles
    def slow(*args,**kw): started.set();release.wait(2);return base(*args,**kw)
    live=LiveService(catalog_fn=s.catalog,candles_fn=slow,clock=lambda:s.clock,autostart=False)
    try:
        live.snapshot({'market':'binance_spot','interval':'1m'})
        t=Thread(target=live.tick);t.start();assert started.wait(1)
        assert not live.tick()
        release.set();t.join()
    finally: release.set();live.close()

@pytest.mark.parametrize('config',[{'interval':'1d'},{'market':'evil'},{'symbols':'ETHUSDT,'},{'scope':'anything'},{'band':float('inf')},{'symbols':','.join(f'X{i}' for i in range(13))}])
def test_invalid_config(config):
    with pytest.raises(ValueError): normalize_config(config)


def test_cross_quote_custom_symbol_is_rejected_before_comparing_with_btc_usdt():
    s=Source(); original=s.catalog
    def catalog(*args):
        out=original(*args);out['instruments'].append({'symbol':'ETHBTC','quote_asset':'BTC'});return out
    live=LiveService(catalog_fn=catalog,candles_fn=s.candles,clock=lambda:s.clock,autostart=False)
    try:
        config={'market':'binance_spot','symbols':'ETHBTC'}
        live.snapshot(config);live.tick()
        out=live.snapshot(config)
        assert out['state']=='ERROR' and not out['rows'] and not s.calls
    finally:live.close()


def test_cycle_failure_immediately_demotes_prior_ready_and_records_error():
    s=Source();live=s.service();config={'market':'binance_spot','interval':'1m','symbols':'ETHUSDT'}
    try:
        live.snapshot(config);live.tick()
        assert live.snapshot(config)['rows'][0]['status']=='READY'
        live.catalogs.clear()
        live.catalog_fn=lambda *a:{'ok':False,'error':'HTTP 451','http_status':451,'instruments':[],'requested_at_utc':iso(s.clock*1000),'received_at_utc':iso(s.clock*1000)}
        s.clock+=12;live.tick();out=live.snapshot(config)
        assert out['state']=='ERROR' and out['rows'][0]['status']=='ERROR'
        assert '451' in out['rows'][0]['reason'] and out['rows'][0]['score'] is None
        assert out['transitions'][-1]['status']=='ERROR'
        assert live.history(out['session_id'])['snapshots'][-1]['error']=='HTTP 451'
    finally:live.close()


def test_required_benchmark_failure_is_visible_and_backs_off():
    s=Source();base=s.candles
    def failed_benchmark(market,symbol,*args,**kwargs):
        out=base(market,symbol,*args,**kwargs)
        if symbol=='BTCUSDT':out.update(ok=False,error='HTTP 429',http_status=429,rows=[])
        return out
    live=LiveService(catalog_fn=s.catalog,candles_fn=failed_benchmark,clock=lambda:s.clock,autostart=False)
    try:
        config={'market':'binance_spot','interval':'1m','symbols':'ETHUSDT'}
        live.snapshot(config);live.tick();out=live.snapshot(config)
        assert out['state']=='ERROR' and '429' in out['error']
        assert out['benchmark_error']['http_status']==429
        assert out['next_refresh_seconds']>=24
        assert out['rows'][0]['status']=='BLOCKED' and '429' in out['rows'][0]['reason']
    finally:live.close()


def test_cn_separately_validated_etf_benchmark_does_not_need_stock_catalog_membership():
    s=Source();seen=[]
    def catalog(market,fetch):return {'ok':True,'instruments':[{'symbol':'600519'}]}
    def benchmark(market,interval,limit,fetch,now_ms=None):
        seen.append((market,interval));return s.candles(market,'510300',interval,limit,fetch,now_ms=now_ms)
    live=LiveService(catalog_fn=catalog,candles_fn=s.candles,benchmark_fn=benchmark,clock=lambda:s.clock,autostart=False)
    try:
        config={'market':'cn_equity','interval':'1m','symbols':'600519'}
        live.snapshot(config);live.tick();out=live.snapshot(config)
        assert seen==[('cn_equity','1m')]
        assert out['rows'][0]['benchmark_symbol']=='510300'
        assert out['rows'][0]['status']=='READY'
    finally:live.close()


def test_snapshot_reads_do_not_recompute_unchanged_features(monkeypatch):
    from market_data import live as module
    original=module.analyze;count=[]
    def tracked(*a,**kw):count.append(1);return original(*a,**kw)
    monkeypatch.setattr(module,'analyze',tracked)
    s=Source();service=s.service();config={'market':'binance_spot','interval':'1m'}
    try:
        service.snapshot(config);service.tick();before=len(count)
        for _ in range(5):service.snapshot(config)
        assert len(count)==before,'browser status polls must not recalculate the entire universe'
    finally:service.close()


def test_cached_feature_status_still_expires_older_benchmark():
    s=Source();base=s.candles
    def older_benchmark(market,symbol,*a,**kw):
        out=base(market,symbol,*a,**kw)
        if symbol=='BTCUSDT':out['received_at_utc']=iso(s.clock*1000-10000)
        return out
    live=LiveService(catalog_fn=s.catalog,candles_fn=older_benchmark,clock=lambda:s.clock,autostart=False)
    try:
        config={'market':'binance_spot','interval':'1m','symbols':'ETHUSDT'}
        live.snapshot(config);live.tick();assert live.snapshot(config)['rows'][0]['status']=='READY'
        s.clock+=30
        assert live.snapshot(config)['rows'][0]['status']=='STALE'
    finally:live.close()
