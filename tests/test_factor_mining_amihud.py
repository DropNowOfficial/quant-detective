from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib
import pytest
from research_lab.factor_mining.contracts import Evidence


def api():
    assert (Path(__file__).parents[1]/'research_lab/factor_mining/amihud.py').exists(), 'Amihud calculator missing'
    return importlib.import_module('research_lab.factor_mining.amihud')


def rows(n=20):
    out=[]
    for i in range(n):
        t=datetime(2026,1,1,21,tzinfo=timezone.utc)+timedelta(days=i)
        e=Evidence(t,t+timedelta(minutes=1),t+timedelta(minutes=1),'synthetic://ohlcv',f'v{i}','synthetic-generated','SYNTHETIC_FIXTURE')
        out.append(api().LiquidityDay('ACME',t,.02,100.,1000.,'USD',True,e))
    return out


def calc(data, **kw):
    lattice=kw.pop('sessions',tuple(x.session_close for x in data))
    decision=kw.pop('decision_time',max(lattice)+timedelta(minutes=2))
    return api().amihud(data,entity='ACME',sessions=lattice,decision_time=decision,**kw)


def test_twenty_day_mean_uses_decimal_return_and_raw_dollar_volume():
    o=calc(rows())
    assert o.value==pytest.approx(.02/(100*1000)) and o.unit=='USD^-1'
    assert dict(o.diagnostics)['valid_sessions']==20 and o.research_state=='NEEDS BACKTEST'


def test_sixty_day_is_a_separately_identified_adaptation():
    o=calc(rows(60),window=60)
    assert o.factor_id=='custom.amihud_60d' and o.value==pytest.approx(2e-7)


def test_true_zero_return_is_zero_not_missing():
    o=calc([replace(r,total_return=0.) for r in rows()])
    assert o.value==0 and o.status=='VALID'


def test_raw_price_volume_split_invariance():
    original=rows();split=[replace(r,raw_close=50.,raw_share_volume=2000.) for r in original]
    assert calc(original).value==pytest.approx(calc(split).value)


def test_missing_sessions_are_not_compressed_out_of_window():
    data=rows();lattice=tuple(r.session_close for r in data)
    o=calc(data[4:],sessions=lattice)
    assert o.status=='VALID' and dict(o.diagnostics)['coverage']==.8
    assert 'MISSING_SESSION' in o.quality_flags
    o=calc(data[5:],sessions=lattice)
    assert o.value is None and o.reason=='INSUFFICIENT_VALID_SESSIONS'


@pytest.mark.parametrize('changes', [
    {'raw_share_volume':0.}, {'raw_share_volume':-1.}, {'raw_close':None},
    {'total_return':float('nan')}, {'total_return':True}, {'actions_verified':False},
    {'currency':'EUR'}, {'close_basis':'ADJUSTED'}, {'volume_basis':'SPLIT_ADJUSTED'},
    {'return_basis':'UNADJUSTED_PRICE'}, {'raw_close':1e308,'raw_share_volume':1e308},
])
def test_bad_daily_data_is_missing_not_zero(changes):
    o=calc([replace(r,**changes) for r in rows()])
    assert o.value is None and o.status=='MISSING' and dict(o.diagnostics)['valid_sessions']==0


def test_future_vintage_blocks_retroactive_computation():
    data=rows();r=data[0];future=data[-1].session_close+timedelta(days=2)
    data[0]=replace(r,evidence=replace(r.evidence,available_at=future,first_seen_at=future))
    o=calc(data)
    assert o.status=='BLOCKED' and o.reason=='NOT_YET_AVAILABLE' and o.value is None


def test_duplicate_session_never_chooses_version_by_input_order():
    data=rows()
    with pytest.raises(ValueError,match='duplicate'): calc(data+[replace(data[0],total_return=.3)],sessions=tuple(r.session_close for r in data))


def test_wrong_entity_and_event_time_cannot_mix():
    data=rows()
    with pytest.raises(ValueError): calc([replace(data[0],symbol='OTHER')]+data[1:])
    with pytest.raises(ValueError): calc([replace(data[0],session_close=data[0].session_close-timedelta(minutes=1))]+data[1:])


def test_open_future_or_duplicate_session_lattice_rejects():
    data=rows();lattice=tuple(r.session_close for r in data)
    with pytest.raises(ValueError): calc(data,decision_time=lattice[-1]-timedelta(seconds=1))
    with pytest.raises(ValueError): calc(data,sessions=lattice[:-1]+(lattice[-2],))
    with pytest.raises(ValueError): calc(data,window=21)


def test_two_intraday_timestamps_cannot_count_as_two_daily_sessions():
    data=rows();lattice=tuple(r.session_close for r in data)
    altered=lattice[:-1]+(lattice[-2]+timedelta(minutes=1),)
    with pytest.raises(ValueError): calc(data[:-1],sessions=altered)


def test_extreme_integer_dollar_volume_is_missing_not_exception():
    o=calc([replace(r,raw_close=10**300,raw_share_volume=10**300) for r in rows()])
    assert o.value is None and o.status=='MISSING'


def test_smallest_subnormal_daily_ratio_keeps_representable_mean():
    o=calc([replace(r,total_return=5e-324,raw_close=1.,raw_share_volume=1.) for r in rows()])
    assert o.value==5e-324 and o.status=='VALID'
