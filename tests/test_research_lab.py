"""Hand-derived contracts for causal, cash-constrained research accounting."""
from copy import deepcopy
from datetime import date,timedelta
import json,math
from pathlib import Path
import pytest


def api():
    import importlib.util
    assert importlib.util.find_spec('research_lab.portfolio'), 'portfolio implementation missing'
    from research_lab.data import Bar,Panel
    from research_lab.portfolio import Config,simulate
    from research_lab.metrics import summarize
    return Bar,Panel,Config,simulate,summarize


def panel(prices):
    Bar,Panel,*_=api(); dates=[f'2024-01-{i+2:02d}' for i in range(len(prices))]
    bars={s:{d:Bar(d,p[0],max(p),min(p),p[1],100000,True) for d,p in zip(dates,ps)} for s,ps in prices.items()} if isinstance(prices,dict) else {}
    return Panel(dates,bars,[],[])


def test_next_open_entry_cost_and_cash_cap():
    Bar,Panel,Config,simulate,_=api();dates=['2024-01-02','2024-01-03','2024-01-04']
    bars={'X':{d:Bar(d,o,max(o,c),min(o,c),c,1e6,True) for d,o,c in zip(dates,[80,100,120],[80,110,120])}}
    p=Panel(dates,bars,[],[])
    out=simulate(p,{'2024-01-02':[{'symbol':'X','adv20':1e9}]},Config(slots=1,hold=2,cost_bps=20,initial=1000),start=dates[0],end=dates[-1])
    t=out['trades'][0]
    assert t['entry_date']=='2024-01-03' and t['exit_date']=='2024-01-04'
    assert t['quantity']==pytest.approx(1000/100.1)
    assert out['nav'][-1]['equity']==pytest.approx(1000/100.1*120*.999)
    assert min(v['cash'] for v in out['nav'])>=-1e-8


def test_invalid_future_close_does_not_cancel_valid_morning_entry():
    Bar,Panel,Config,simulate,_=api(); ds=['2024-01-02','2024-01-03','2024-01-04']
    p=Panel(ds,{'X':{ds[0]:Bar(ds[0],100,101,99,100,1e6,True),ds[1]:Bar(ds[1],100,102,99,50,1e6,False),ds[2]:Bar(ds[2],103,104,102,103,1e6,True)}},[],[])
    out=simulate(p,{ds[0]:[{'symbol':'X','adv20':1e9}]},Config(slots=1,hold=1,cost_bps=0,initial=1000),start=ds[0],end=ds[-1])
    assert out['trades'][0]['entry_date']==ds[1]
    assert out['status']=='BLOCKED_DATA' and out['stale_days']==1
    assert out['trades'][0]['exit_date']==ds[2]


def test_no_end_window_future_filtering():
    Bar,Panel,Config,simulate,_=api();ds=['2024-01-02','2024-01-03']
    p=Panel(ds,{'X':{d:Bar(d,100,101,99,100,1e6,True) for d in ds}},[],[])
    out=simulate(p,{ds[0]:[{'symbol':'X','adv20':1e9}]},Config(slots=1,hold=10,cost_bps=0),start=ds[0],end=ds[-1])
    assert out['open_positions']==1 and out['trades']==[]
    assert out['entry_count']==1


def test_signal_is_not_used_same_day_and_positions_do_not_duplicate():
    Bar,Panel,Config,simulate,_=api();ds=[f'2024-01-{x:02d}' for x in range(2,8)]
    p=Panel(ds,{'X':{d:Bar(d,100,101,99,100,1e6,True) for d in ds}},[],[])
    out=simulate(p,{d:[{'symbol':'X','adv20':1e9}] for d in ds},Config(slots=5,hold=3,cost_bps=0),start=ds[0],end=ds[-1])
    assert out['nav'][0]['positions']==0
    assert max(r['positions'] for r in out['nav'])==1
    assert out['entry_count']==2


@pytest.mark.parametrize('kw',[{'hold':0},{'slots':0},{'cost_bps':-1},{'initial':float('nan')},{'slots':True}])
def test_invalid_parameters_fail_closed(kw):
    *_,Config,simulate,summarize=api()
    with pytest.raises(ValueError):Config(**kw)


def test_annualization_uses_calendar_time_and_initial_capital():
    *_,summarize=api()
    rows=[{'date':'2023-01-01','equity':900,'exposure':0,'turnover':0},{'date':'2024-01-01','equity':1100,'exposure':0,'turnover':0}]
    m=summarize(rows,initial=1000)
    assert m['total_return']==pytest.approx(.1)
    assert m['cagr']==pytest.approx(1.1**(365.2425/365)-1)
    assert m['max_drawdown']==pytest.approx(-.1)
    assert m['year_returns'][0]['return']==pytest.approx(-.1)
    assert m['year_returns'][1]['return']==pytest.approx(1100/900-1)


def test_feature_prefix_is_invariant_and_missing_is_not_zero():
    Bar,Panel,*_=api()
    from research_lab.features import compute_features
    ds=[(date(2023,1,1)+timedelta(days=i)).isoformat() for i in range(85)]
    bars={s:{d:Bar(d,100+i/10,101+i/10,99+i/10,100+i/10,1e6,True) for i,d in enumerate(ds)} for s in ['X','QQQ']}
    a=Panel(ds,bars,[],[]);f=compute_features(a)
    b=deepcopy(a)
    for d in ds[70:]:b.bars['X'][d]=Bar(d,1000,1010,990,1000,1e6,True)
    g=compute_features(b)
    assert {d:f['X'][d] for d in ds[:70]}=={d:g['X'][d] for d in ds[:70]}
    assert f['X'][ds[65]]['ma5']==pytest.approx(106.2)
    b.bars['X'].pop(ds[62]);h=compute_features(b)
    assert h['X'][ds[65]]['eligible'] is False
    assert h['X'][ds[65]]['ma5'] is None


def test_loader_rejects_schema_time_and_preserves_invalid_row(tmp_path):
    api()
    from research_lab.data import load_panel
    d={'symbol':'QQQ','retrieved_at':'2024-01-04T23:00:00Z','contract':{'symbol':'QQQ'},'data':{'time':['2024-01-03T14:30:00Z'],'open':[100],'high':[101],'low':[99],'close':[50],'volume':[1000]}}
    (tmp_path/'QQQ_daily.json').write_text(json.dumps(d))
    p=load_panel(tmp_path,'ibkr')
    b=p.bars['QQQ']['2024-01-03']
    assert not b.valid and b.open==100 and p.issues
    d['data']['volume']=[];(tmp_path/'QQQ_daily.json').write_text(json.dumps(d))
    with pytest.raises(ValueError):load_panel(tmp_path,'ibkr')


def test_lagged_and_completed_ma5_have_distinct_definitions():
    Bar,Panel,*_=api()
    from research_lab.features import compute_features
    ds=[(date(2023,1,1)+timedelta(days=i)).isoformat() for i in range(70)]
    p=Panel(ds,{s:{d:Bar(d,100+i,101+i,99+i,100+i,1e6,True) for i,d in enumerate(ds)} for s in ['X','QQQ']},[],[])
    lag=compute_features(p,'primary')['X'][ds[-1]];complete=compute_features(p,'completed_ma5')['X'][ds[-1]]
    assert lag['ma5']==166 and complete['ma5']==167


def test_incomplete_source_session_excluded(tmp_path):
    api()
    from research_lab.data import load_panel
    d={'symbol':'QQQ','retrieved_at':'2024-01-04T18:00:00Z','contract':{'symbol':'QQQ'},'data':{'time':['2024-01-03T14:30:00Z','2024-01-04T14:30:00Z'],'open':[100,100],'high':[101,101],'low':[99,99],'close':[100,100],'volume':[1000,1000]}}
    (tmp_path/'QQQ_daily.json').write_text(json.dumps(d));p=load_panel(tmp_path,'ibkr')
    assert '2024-01-04' not in p.bars['QQQ']


def test_stale_previous_mark_stops_new_entries():
    Bar,Panel,Config,simulate,_=api();ds=['2024-01-02','2024-01-03','2024-01-04']
    bars={s:{d:Bar(d,100,101,99,100,1e6,True) for d in ds} for s in ['X','Y']}
    bars['X'][ds[1]]=Bar(ds[1],100,101,99,50,1e6,False)
    p=Panel(ds,bars,[],[])
    out=simulate(p,{ds[0]:[{'symbol':'X','adv20':1e9}],ds[1]:[{'symbol':'Y','adv20':1e9}]},Config(slots=2,hold=2,cost_bps=0),start=ds[0],end=ds[-1])
    assert out['entry_count']==1


def test_regression_alpha_and_beta_hand_derived():
    from research_lab import metrics
    assert hasattr(metrics,'regression_diagnostic'),'regression missing'
    m=metrics.regression_diagnostic([-.019,.001,.021],[-.01,0,.01])
    assert m['beta']==pytest.approx(2)
    assert m['annualized_intercept_rf0']==pytest.approx(.252)
