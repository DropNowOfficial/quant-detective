"""Reuse audit regression intent, adding explicitly dated daily evidence.
Original undated failing fixture is preserved in the delivered audit evidence.
All HTTP responses here are synthetic, not reconstructed original responses.
"""
from datetime import datetime,timezone
import pytest
from market_data import us_watch
from market_data.quote_validity import session_bounds
NOW=datetime.fromisoformat('2026-10-15T13:45:46.170120+00:00')
PRICE=720.510009765625

def chart(points,meta=None):
 return {'chart':{'result':[{'meta':{'symbol':'QQQ','instrumentType':'ETF','currency':'USD','exchangeTimezoneName':'America/New_York',**(meta or {})},'timestamp':[int(t.timestamp()) for t,p in points],'indicators':{'quote':[{k:[p if k!='volume' else 1 for t,p in points] for k in ('open','high','low','close','volume')}]}}]}}

def proxy(chart_reference=747.58, *, now=NOW, quote_at=None, reference_date='2026-10-14', reference_price=728.95, daily_override=None, received=None):
 quote_at=quote_at or datetime.fromisoformat('2026-10-15T13:45:36+00:00')
 intraday=chart([(quote_at,PRICE)],{'chartPreviousClose':chart_reference,'previousClose':728.95})
 opening=session_bounds(datetime.fromisoformat(reference_date).date())[0]
 daily=chart([(opening,reference_price)]) if daily_override is None else daily_override
 def fetch(url,**kw):
  return {'data':daily if 'interval=1d' in url else intraday,'receipt':{'received_at_utc':(received or now).isoformat()}}
 return us_watch._market_proxy(fetch,'QQQ',now)

def test_qqq_reference_is_previous_session_close():
 r=proxy();assert r['prior_reference']==728.95
 assert r['reference_session_date']=='2026-10-14'
 assert r['change_pct']==pytest.approx(-1.1578286874)

def test_daily_return_does_not_change_with_chart_window_reference():
 assert proxy(747.58)['change_pct']==proxy(705.35)['change_pct']

@pytest.mark.parametrize('instant',['2026-10-15T08:10:00+00:00','2026-10-15T14:10:00+00:00','2026-10-15T22:10:00+00:00'])
def test_premarket_rth_post_use_same_session_prior_close(instant):
 t=datetime.fromisoformat(instant);r=proxy(now=t,quote_at=t)
 assert r['reference_session_date']=='2026-10-14';assert r['prior_reference']==728.95

@pytest.mark.parametrize('day,prior',[('2026-10-19','2026-10-16'),('2026-09-08','2026-09-04'),('2026-01-02','2025-12-31')])
def test_weekend_holiday_and_year_boundary(day,prior):
 t=datetime.fromisoformat(day+'T14:35:00+00:00');r=proxy(now=t,quote_at=t,reference_date=prior)
 assert r['reference_session_date']==prior;assert r['prior_reference']==728.95

@pytest.mark.parametrize('instant',['2026-10-17T14:35:00+00:00','2026-12-25T14:35:00+00:00','2026-10-16T01:10:00+00:00'])
def test_non_session_quote_is_unknown(instant):
 t=datetime.fromisoformat(instant);r=proxy(now=t,quote_at=t)
 assert r['change_pct'] is None

def test_cross_date_stale_quote_not_relabelled_by_fetch_clock():
 r=proxy(now=datetime.fromisoformat('2026-10-16T13:45:46+00:00'))
 assert r['change_pct'] is None
 assert r['reference_status']=='UNKNOWN'

def test_utc_next_date_is_same_new_york_postmarket_session():
 t=datetime.fromisoformat('2026-12-02T00:10:00+00:00')
 r=proxy(now=t,quote_at=t,reference_date='2026-11-30')
 assert r['quote_session_date']=='2026-12-01';assert r['prior_reference']==728.95

@pytest.mark.parametrize('change',['missing','duplicate','zero','wrong_day','future_receipt','undated_meta_only','midnight_timestamp'])
def test_bad_reference_is_unknown_despite_metadata(change):
 opening=session_bounds(datetime(2026,10,14).date())[0]
 points=[(opening,728.95)]
 if change=='missing':points=[]
 if change=='duplicate':points*=2
 if change=='zero':points=[(opening,0)]
 if change=='wrong_day':points=[(session_bounds(datetime(2026,10,13).date())[0],728.95)]
 if change=='undated_meta_only':points=[(session_bounds(datetime(2026,10,8).date())[0],747.58)]
 if change=='midnight_timestamp':points=[(datetime(2026,10,14,tzinfo=timezone.utc),728.95)]
 receipt=datetime(2027,1,1,tzinfo=timezone.utc) if change=='future_receipt' else None
 r=proxy(daily_override=chart(points,{'previousClose':728.95}),received=receipt)
 assert r['change_pct'] is None;assert r['prior_reference'] is None

@pytest.mark.parametrize('received,valid',[('2026-11-27T18:01:00+00:00',True),('2026-11-27T17:59:00+00:00',False)])
def test_early_close_reference_completion_and_independent_receipt(received,valid):
 now=datetime.fromisoformat('2026-11-30T14:40:00+00:00')
 opening=session_bounds(datetime(2026,11,27).date())[0]
 def fetch(*a,**k):return {'data':chart([(opening,728.95)]),'receipt':{'received_at_utc':received}}
 r=us_watch._qqq_reference(fetch,int(now.timestamp()),now)
 assert (r['reference_status']=='VERIFIED')==valid
 if valid:assert r['reference_close_utc']=='2026-11-27T18:00:00+00:00'

@pytest.mark.parametrize('day,prior,instant',[('2026-03-09','2026-03-06','13:40'),('2026-11-02','2026-10-30','14:40')])
def test_dst_transition_uses_exchange_sessions(day,prior,instant):
 t=datetime.fromisoformat(day+'T'+instant+':00+00:00')
 assert proxy(now=t,quote_at=t,reference_date=prior)['reference_session_date']==prior

def test_daily_fetch_failure_keeps_quote_but_unknown_change():
 t=datetime.fromisoformat('2026-10-15T13:45:36+00:00')
 def fetch(url,**kw):
  if 'interval=1d' in url:raise RuntimeError('source unavailable')
  return {'data':chart([(t,PRICE)]),'receipt':{'received_at_utc':NOW.isoformat()}}
 r=us_watch._market_proxy(fetch,'QQQ',NOW)
 assert r['price']==PRICE and r['change_pct'] is None
 assert r['reference_status']=='UNKNOWN'

def test_quote_expires_during_reference_fetch():
 t=datetime.fromisoformat('2026-10-15T13:45:36+00:00')
 opening=session_bounds(datetime(2026,10,14).date())[0]
 def fetch(url,**kw):return {'data':chart([(opening,728.95)]) if 'interval=1d' in url else chart([(t,PRICE)]),'receipt':{'received_at_utc':NOW.isoformat()}}
 moments=iter([NOW,NOW,datetime.fromisoformat('2026-10-15T14:00:00+00:00')])
 r=us_watch._market_proxy(fetch,'QQQ',NOW,lambda:next(moments))
 assert r['change_pct'] is None and not r['quote_validity']['eligible']
