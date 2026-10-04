import importlib.util,json
from pathlib import Path
import pytest


def test_immutable_run_and_hash_tampering(tmp_path):
    assert importlib.util.find_spec('research_lab.experiment'), 'experiment module missing'
    from research_lab.experiment import save_experiment,content_id
    r={'schema_version':1,'data':{'a':1},'description':'中文 <tag>'};r['run_id']=content_id(r)
    path=save_experiment(r,tmp_path);assert json.loads((path/'result.json').read_text())==r
    assert save_experiment(r,tmp_path)==path
    (path/'result.json').write_text('{}')
    with pytest.raises(ValueError):save_experiment(r,tmp_path)
    r['data']['a']=2
    with pytest.raises(ValueError):save_experiment(r,tmp_path)


def test_baseline_uses_same_initial_cost_and_retains_bad_mark():
    assert importlib.util.find_spec('research_lab.experiment'), 'experiment module missing'
    from research_lab.experiment import buy_hold
    from research_lab.data import Bar,Panel
    ds=['2024-01-02','2024-01-03','2024-01-04']
    p=Panel(ds,{'QQQ':{ds[0]:Bar(ds[0],100,102,99,100,100,True),ds[1]:Bar(ds[1],100,102,99,50,100,False),ds[2]:Bar(ds[2],100,110,99,110,100,True)}},[],[])
    out=buy_hold(p,'QQQ',ds,initial=1000,cost_bps=20)
    assert out['nav'][0]['equity']==pytest.approx(1000/1.001)
    assert out['nav'][1]['stale'] is True
    assert out['nav'][-1]['equity']==pytest.approx(1000/1.001*1.1*.999)
    assert out['nav'][-1]['positions']==0
    stopped=buy_hold(p,'QQQ',ds[:2],initial=1000,cost_bps=20)
    assert stopped['nav'][-1]['positions']==1 and stopped['nav'][-1]['exposure']==1


def test_render_escapes_data_and_result_id(tmp_path):
    assert importlib.util.find_spec('research_lab.render'), 'render module missing'
    from research_lab.render import render
    html=render({'run_id':'id','danger':'</script><script>alert(1)</script>'})
    assert '</script><script>alert(1)</script>' not in html
    assert '__LAB_DATA__' not in html


def test_equal_weight_rebalances_using_previous_close_members():
    from research_lab import experiment
    assert hasattr(experiment,'equal_weight'),'equal-weight baseline missing'
    from research_lab.data import Bar,Panel
    ds=['2024-01-02','2024-01-03','2024-01-04']
    raw={'X':[(100,100),(100,110),(110,121)],'Y':[(100,100),(100,90),(90,81)]}
    p=Panel(ds,{s:{d:Bar(d,o,max(o,c),min(o,c),c,1e6,True) for d,(o,c) in zip(ds,prices)} for s,prices in raw.items()},[],[])
    b=experiment.equal_weight(p,{d:['X','Y'] for d in ds},ds,cost_bps=0,initial=1000)
    assert b['nav'][0]['equity']==1000 and b['nav'][0]['positions']==0
    assert b['nav'][-1]['equity']==pytest.approx(1000)
    assert b['status']=='DIAGNOSTIC_ONLY'


def test_snapshot_manifest_rejects_changed_missing_or_extra_inputs():
    from research_lab import data
    assert hasattr(data,'verify_manifest'),'manifest verification missing'
    expected=[{'provider':'ibkr','file':'A_daily.json','sha256':'aaa'}]
    data.verify_manifest(expected,expected)
    for bad in [[{'provider':'ibkr','file':'A_daily.json','sha256':'bbb'}],[],expected+[{'provider':'ibkr','file':'B_daily.json','sha256':'ccc'}]]:
        with pytest.raises(ValueError):data.verify_manifest(bad,expected)


def test_equal_weight_is_identical_across_python_hash_seeds():
    import os,subprocess,sys
    program = '''
from datetime import date,timedelta
import hashlib,math
from research_lab.data import Bar,Panel
from research_lab.experiment import equal_weight,canonical,content_id
ds=[str(date(2024,1,2)+timedelta(days=i)) for i in range(120)]
symbols=['AAPL','AMD','AMZN','AVGO','META','MSFT','NVDA','TSLA']
bars={s:{d:Bar(d,50+j*13+i*.123,1000,1,50+j*13+i*.123+math.sin(i+j)*4,1e6,True) for i,d in enumerate(ds)} for j,s in enumerate(symbols)}
p=Panel(ds,bars,[],[])
r=equal_weight(p,{d:symbols for d in ds},ds)
print(hashlib.sha256(canonical(r).encode()).hexdigest(),content_id(r))
'''
    outputs=[subprocess.check_output([sys.executable,'-c',program],env=dict(os.environ,PYTHONHASHSEED=str(seed)),text=True) for seed in [1,2,3]]
    assert len(set(outputs))==1,outputs


def test_local_server_rejects_foreign_host_for_get_and_head(tmp_path):
    from research_lab import cli
    assert hasattr(cli,'LocalResearchHandler'),'local Host guard missing'
    from functools import partial
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer
    from threading import Thread
    (tmp_path/'lab.html').write_text('private research')
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(cli.LocalResearchHandler,directory=str(tmp_path)))
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    try:
        for method,host,expected in [('GET',f'localhost:{server.server_port}',200),('GET','foreign.example',403),('HEAD','foreign.example',403)]:
            connection=HTTPConnection('127.0.0.1',server.server_port,timeout=3)
            connection.request(method,'/lab.html',headers={'Host':host})
            response=connection.getresponse();assert response.status==expected;response.read();connection.close()
    finally:
        server.shutdown();server.server_close();worker.join()
