"""Analytical fixtures, not real market observations."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import pytest

from market_data.features import analyze, rank_rows

STEP = 60_000
NOW = 121 * STEP + 5_000

def iso(t):
    return datetime.fromtimestamp(t/1000, timezone.utc).isoformat()

def observation(market='binance_spot', symbol='ETHUSDT', slope=0.1):
    rows=[]
    for i in range(121):
        c=100+i*slope
        v=50 if i==120 else 20
        rows.append(dict(open_time_ms=i*STEP, open_time_utc=iso(i*STEP), open=c-.02, high=c+.25,
                         low=c-.25, close=c, volume=v, base_volume=v, quote_volume=c*v,
                         taker_buy_volume=v*.6 if market=='binance_spot' else None, closed=True))
    return dict(ok=True, market=market, source='fixture '+market, symbol=symbol, interval='1m',
                rows=rows, received_at_utc=iso(NOW), requested_at_utc=iso(NOW-100),
                http_status=200, source_url='https://example.test', dropped_rows=0, warnings=[])

def baseline(market='binance_spot'):
    return observation(market, 'BTCUSDT', 0.01)

def run(obs=None, bench=None, **kw):
    return analyze(obs or observation(), bench or baseline(), {}, NOW, **kw)

def test_known_math_and_exact_vwap_and_ready():
    out=run()
    assert out['status']=='READY'
    m=out['metrics']; assert m['ma5']==pytest.approx(111.8)
    assert m['ma5_slope']==pytest.approx(.1)
    assert m['atr14']==pytest.approx(.5)
    assert m['distance_atr']==pytest.approx(.4)
    assert m['rvol20']==pytest.approx(2.5)
    assert m['vwap_basis']=='quote/base'
    assert m['rs20_pp']==pytest.approx(((112/110-1)-(101.2/101-1))*100)
    assert out['rule_id']=='MINUTE_MA5_V1'
    assert all(c['passed'] for c in out['checks'])
    assert math.isfinite(out['score'])

def test_unclosed_and_future_prices_cannot_change_signal_metrics():
    original=observation(); changed=deepcopy(original)
    changed['rows'] += [dict(original['rows'][-1], open_time_ms=121*STEP, close=1e6, closed=False),
                        dict(original['rows'][-1], open_time_ms=200*STEP, close=1e9, closed=True)]
    a,b=run(original),run(changed)
    assert a['metrics']==b['metrics'] and a['status']==b['status']
    assert b['price']==1e6 and b['price_unclosed']

def test_capture_open_flag_does_not_age_into_closed():
    obs=observation(); obs['rows'][-1]['closed']=False
    out=analyze(obs,baseline(),{},NOW+2*STEP)
    assert out['signal_time_ms']==120*STEP and out['status']=='STALE'

@pytest.mark.parametrize('failure', ['gap','conflict','nan','negative','dropped'])
def test_data_failure_blocks(failure):
    obs=observation()
    if failure=='gap': obs['rows'].pop(-8)
    if failure=='conflict': obs['rows'].append(dict(obs['rows'][-5], close=80))
    if failure=='nan': obs['rows'][-1]['close']=float('nan')
    if failure=='negative': obs['rows'][-1]['volume']=-1
    if failure=='dropped': obs['dropped_rows']=1
    out=run(obs); assert out['status']=='BLOCKED'
    assert out['score'] is None

@pytest.mark.parametrize('change', ['market','interval','timeline'])
def test_benchmark_identity_or_timeline_missing_blocks(change):
    bench=baseline()
    if change=='market': bench['market']='gate_usdt'
    if change=='interval': bench['interval']='5m'
    if change=='timeline': bench['rows'].pop(-21)
    assert run(bench=bench)['status']=='BLOCKED'

def test_zero_volumes_remain_missing_no_division_or_ready():
    obs=observation()
    for r in obs['rows']: r.update(volume=0,base_volume=0,quote_volume=0)
    out=run(obs)
    assert out['status']=='BLOCKED' and out['metrics']['rvol20'] is None
    assert out['metrics']['vwap60'] is None

def test_gate_quote_contract_units_require_multiplier():
    obs=observation('gate_usdt'); bench=baseline('gate_usdt')
    for r in obs['rows']: r['base_volume']=None; r['quote_volume']*=.01
    out=analyze(obs,bench,{'quanto_multiplier':'.01'},NOW)
    assert out['metrics']['vwap_basis']=='quote/base'
    expected=sum(r['quote_volume'] for r in obs['rows'][-60:])/sum(r['volume']*.01 for r in obs['rows'][-60:])
    assert out['metrics']['vwap60']==pytest.approx(expected)
    approx=analyze(obs,bench,{},NOW)
    assert approx['metrics']['vwap_basis']=='HLC3 approximation'
    assert approx['metrics']['taker_buy_ratio'] is None

def test_failed_response_clears_prices_and_never_reuses_success():
    obs=observation(); obs.update(ok=False,http_status=429,error='HTTP 429')
    out=run(obs); assert out['status']=='ERROR' and out['price'] is None

def test_response_stale_independent_of_candle_duration():
    obs=observation(); obs['received_at_utc']=iso(NOW-37000)
    assert run(obs)['status']=='STALE'

def test_missing_quote_liquidity_blocks_positive_threshold():
    obs=observation()
    for r in obs['rows']: r['quote_volume']=None
    out=run(obs,min_turnover=1)
    assert out['metrics']['turnover60'] is None and out['status']=='BLOCKED'

def test_stable_rank_and_invalid_threshold():
    a=run(); b=deepcopy(a); a['symbol']='Z'; b['symbol']='A'
    assert [r['symbol'] for r in rank_rows([a,b])]==['A','Z']
    for band in [float('nan'),-1,100,True]:
        with pytest.raises(ValueError): run(band=band)

def test_provider_retains_source_quote_and_base_volumes():
    from market_data.providers import get_candles
    def fetch(data):
        return lambda url,kind='json': {'data':data,'receipt':{'url':url,'ok':True,'http_status':200,'requested_at_utc':iso(NOW),'received_at_utc':iso(NOW)}}
    spot=get_candles('binance_spot','BTCUSDT','1m',1,fetch([[60000,'2','3','1','2','10',119999,'20',2,'4','8',0]]),now_ms=200000)
    assert spot['rows'][0]['quote_volume']==20 and spot['rows'][0]['base_volume']==10
    gate=get_candles('gate_usdt','BTC_USDT','1m',1,fetch([{'t':60,'o':'2','h':'3','l':'1','c':'2','v':'100','sum':'20'}]),now_ms=200000)
    assert gate['rows'][0]['quote_volume']==20 and gate['rows'][0]['base_volume'] is None
    okx=get_candles('okx_swap','BTC-USDT-SWAP','1m',1,fetch({'code':'0','data':[['60000','2','3','1','2','100','10','20','1']]}),now_ms=200000)
    assert okx['rows'][0]['quote_volume']==20 and okx['rows'][0]['base_volume']==10


def test_provider_conflicting_duplicate_is_not_silently_certified():
    from market_data.providers import get_candles
    a=[60000,'2','3','1','2','10',119999,'20',2,'4','8',0];b=list(a);b[4]='2.1'
    fetch=lambda url,kind='json':{'data':[a,b],'receipt':{'url':url,'http_status':200,'requested_at_utc':iso(NOW),'received_at_utc':iso(NOW),'ok':True}}
    out=get_candles('binance_spot','BTCUSDT','1m',2,fetch,now_ms=200000)
    assert out['dropped_rows']==1


def test_band_explanation_compares_absolute_distance_to_threshold():
    out=run(observation(slope=-.1))
    band=next(c for c in out['checks'] if c['key']=='band')
    assert band['value']==abs(out['metrics']['distance_atr'])
    assert band['operator']=='≤'


@pytest.mark.parametrize('interval,step', [('1m', 60_000), ('5m', 300_000), ('15m', 900_000)])
def test_shared_gate_preserves_minute_math(interval, step):
    obs, bench = observation(), baseline()
    now = 121 * step + 5_000
    for source in (obs, bench):
        source.update(interval=interval, received_at_utc=iso(now), requested_at_utc=iso(now-100))
        for i, row in enumerate(source['rows']):
            row.update(open_time_ms=i*step, open_time_utc=iso(i*step))
    # Golden output recorded from the unchanged calculator at A4 BASE.
    baseline_result = {'metrics': {
        'closed_price': 112.0, 'ma5': 111.8, 'ma20': 111.05,
        'ma5_slope': 0.09999999999999432, 'ma5_short_slope': 0.10000000000000142,
        'atr14': 0.5, 'atr_pct': 0.4464285714285714, 'distance_atr': 0.4000000000000057,
        'vwap60': 109.1219512195122, 'vwap_basis': 'quote/base', 'rvol20': 2.5,
        'return20_pct': 1.8181818181818077, 'rs20_pp': 1.6201620162016095,
        'turnover60': 134220.0, 'taker_buy_ratio': 0.6,
    }}
    new_result = analyze(obs, bench, {}, now)
    assert new_result['metrics'] == baseline_result['metrics']
    assert new_result['status'] == 'READY' and new_result['score'] == 100.0
    assert new_result['quality']['confirmation_ok'] is True
    assert new_result['quality_policy_id'] == f'minute_ma5_v1_{step}ms'
    assert new_result['quality']['evaluated_at_ms'] == now
    assert new_result['quality_evidence']['captured_at_ms'] == now
    assert new_result['valid_until_ms'] == now + 36_000


@pytest.mark.parametrize('failure', ['short', 'gap', 'benchmark_alignment'])
def test_61_bar_gap_and_benchmark_alignment_remain_blocked(failure):
    obs, bench = observation(), baseline()
    if failure == 'short': obs['rows'] = obs['rows'][-60:]
    elif failure == 'gap': obs['rows'].pop(-8)
    else: bench['rows'].pop(-21)
    out = run(obs, bench)
    assert out['status'] == 'BLOCKED' and out['score'] is None
    assert out['quality']['confirmation_ok'] is False


def test_benchmark_quality_keeps_only_existing_21_bar_dependency():
    bench = baseline(); bench['rows'] = bench['rows'][-21:]
    out = run(bench=bench)
    assert out['status'] == 'READY'
    dependency = out['benchmark_dependency']
    assert dependency['quality']['sample_count'] == 21
    assert dependency['quality']['confirmation_ok'] is True
    assert dependency['quality_policy_id'].endswith('_benchmark_return21')
    assert dependency['captured_at_ms'] == NOW


def test_minute_source_capture_cannot_certify_bar_completed_after_receipt():
    obs = observation(); obs['received_at_utc'] = iso(121*STEP-1)
    out = run(obs)
    assert out['status'] == 'BLOCKED'
    assert 'BAR_AFTER_CAPTURE' in out['quality']['reason_codes']


@pytest.mark.parametrize('deadline', ['response', 'bar', 'benchmark'])
def test_shared_minute_validity_is_exclusive(deadline):
    from market_data.features import stale_reason
    obs, bench = observation(), baseline()
    if deadline == 'benchmark': bench['received_at_utc'] = iso(NOW-4_000)
    if deadline == 'bar':
        end = 121*STEP; now = end+STEP+15_000
        for source in (obs, bench): source['received_at_utc'] = iso(now)
        out = analyze(obs, bench, {}, now)
        assert out['status'] == 'STALE'
    else:
        out = run(obs, bench)
        expiry = NOW+36_000-(4_000 if deadline == 'benchmark' else 0)
        assert out['valid_until_ms'] == expiry
        assert stale_reason(out, expiry-1) is None
        assert stale_reason(out, expiry) is not None
