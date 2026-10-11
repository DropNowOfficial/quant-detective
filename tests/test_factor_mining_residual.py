from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import importlib
import math
import numpy as np
import pytest
from research_lab.factor_mining.contracts import Evidence


def api():
    assert (Path(__file__).parents[1]/'research_lab/factor_mining/residual.py').exists(), 'residual calculator missing'
    return importlib.import_module('research_lab.factor_mining.residual')


def rows(n=146):
    out=[]
    for i in range(n):
        t=datetime(2025,1,1,21,tzinfo=timezone.utc)+timedelta(days=i)
        m=.01*math.sin(i*.7);s=m+.006*math.cos(i*.51)
        r=.001+1.2*m+.7*(s-m)+.003*math.sin(i*1.37)
        e=Evidence(t,t+timedelta(minutes=1),t+timedelta(minutes=1),'synthetic://returns',f'v{i}','synthetic-generated','SYNTHETIC_FIXTURE')
        out.append(api().ReturnDay('ACME',t,r,m,s,e,'MKT_FIXTURE','SEC_FIXTURE'))
    return out


def calc(data,**kw):
    d=dict(entity='ACME',sessions=tuple(r.session_close for r in data),decision_time=data[-1].session_close+timedelta(minutes=2),
           market_benchmark='MKT_FIXTURE',sector_benchmark='SEC_FIXTURE',
           membership_vintage='membership-2024',benchmark_available_at=data[0].session_close-timedelta(days=1))
    d.update(kw)
    return api().residual_momentum(data,**d)


def test_default_is_custom_one_step_oos_forecast_errors_not_paper_alpha():
    data=rows();out=calc(data);expected=[]
    for i in range(126,146):
        block=data[i-126:i]
        x=np.array([[1.,r.market_return,r.sector_return-r.market_return] for r in block]);y=np.array([r.stock_return for r in block])
        b=np.linalg.solve(x.T@x,x.T@y)
        row=data[i];expected.append(row.stock_return-np.array([1.,row.market_return,row.sector_return-row.market_return])@b)
    assert out.forecast_errors==pytest.approx(expected)
    assert out.observation.value==pytest.approx(sum(expected)/np.std(expected,ddof=1))
    assert out.observation.factor_id=='custom.residual_momentum_20d_market_sector_fit126'
    assert len(out.fit_end_times)==20 and all(t<data[126+i].session_close for i,t in enumerate(out.fit_end_times))


def test_current_day_shock_cannot_leak_into_its_fitted_beta():
    data=rows();base=calc(data);changed=data[:-1]+[replace(data[-1],stock_return=data[-1].stock_return+.2)]
    shocked=calc(changed)
    assert shocked.forecast_errors[:-1]==base.forecast_errors[:-1]
    assert shocked.forecast_errors[-1]-base.forecast_errors[-1]==pytest.approx(.2)


def test_future_decision_vintage_is_blocked():
    data=rows();e=data[0].evidence;future=data[-1].session_close+timedelta(days=1)
    data[0]=replace(data[0],evidence=replace(e,available_at=future,first_seen_at=future))
    assert calc(data).observation.reason=='NOT_YET_AVAILABLE'


def test_late_revision_cannot_enter_historical_fit_even_if_known_now():
    data=rows();e=data[0].evidence;late=data[130].session_close
    data[0]=replace(data[0],evidence=replace(e,available_at=late,first_seen_at=late))
    o=calc(data).observation
    assert o.value is None and o.reason=='FIT_VINTAGE_NOT_AVAILABLE'


def test_future_benchmark_selection_is_blocked():
    data=rows();o=calc(data,benchmark_available_at=data[-1].session_close).observation
    assert o.reason=='BENCHMARK_NOT_AVAILABLE_AT_FORMATION'


@pytest.mark.parametrize('field', ['stock_return','market_return','sector_return'])
@pytest.mark.parametrize('value', [None,float('nan'),True])
def test_missing_or_nonfinite_returns_do_not_fill(field,value):
    data=rows();data[2]=replace(data[2],**{field:value})
    assert calc(data).observation.value is None


def test_perfect_model_zero_residual_variance_is_missing():
    data=[replace(r,stock_return=.001+1.2*r.market_return+.7*(r.sector_return-r.market_return)) for r in rows()]
    assert calc(data).observation.reason=='ZERO_RESIDUAL_VARIANCE'


def test_singular_regressors_and_unverified_adjustment_are_missing():
    data=rows();same=[replace(r,sector_return=r.market_return) for r in data]
    assert calc(same).observation.reason=='RANK_DEFICIENT_FIT'
    data[0]=replace(data[0],return_basis='PRICE_RETURN')
    assert calc(data).observation.reason=='UNVERIFIED_RETURN_BASIS'


def test_short_sample_and_window_boundaries():
    assert calc(rows(145)).observation.reason=='INSUFFICIENT_HISTORY'
    for fit,signal in [(0,20),(3,20),(126,1),(True,20)]:
        with pytest.raises(ValueError): calc(rows(),fit_window=fit,signal_window=signal)


def test_duplicate_dates_entity_and_event_mismatch_reject():
    data=rows()
    with pytest.raises(ValueError): calc(data+[data[-1]])
    with pytest.raises(ValueError): calc(data[:-1]+[replace(data[-1],session_close=data[-2].session_close+timedelta(minutes=1))])
    with pytest.raises(ValueError): calc([replace(data[0],symbol='OTHER')]+data[1:])
    with pytest.raises(ValueError): calc(data,membership_vintage='')


def test_missing_session_cannot_shorten_the_fitting_lattice():
    data=rows();lattice=tuple(r.session_close for r in data)
    o=calc(data[:40]+data[41:],sessions=lattice).observation
    assert o.reason=='MISSING_SOURCE_SESSION' and o.value is None


def test_same_day_revision_cannot_enter_pre_session_formation():
    data=rows();row=data[125];late=data[126].session_close-timedelta(minutes=1)
    data[125]=replace(row,stock_return=.9,evidence=replace(row.evidence,available_at=late,first_seen_at=late))
    assert calc(data).observation.reason=='FIT_VINTAGE_NOT_AVAILABLE'


def test_same_day_benchmark_selection_is_blocked():
    data=rows()
    assert calc(data,benchmark_available_at=data[126].session_close-timedelta(minutes=1)).observation.reason=='BENCHMARK_NOT_AVAILABLE_AT_FORMATION'


def test_equal_benchmark_identities_are_not_two_distinct_return_series():
    with pytest.raises(ValueError): calc(rows(),market_benchmark='SAME',sector_benchmark='SAME')


def test_return_rows_expose_benchmark_identity_fields():
    fields=api().ReturnDay.__dataclass_fields__
    assert 'market_benchmark' in fields and 'sector_benchmark' in fields


def test_return_row_benchmark_identity_mismatch_rejects():
    data=rows();data[1]=replace(data[1],market_benchmark='OTHER')
    with pytest.raises(ValueError): calc(data)
