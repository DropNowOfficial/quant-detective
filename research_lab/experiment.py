"""Frozen scenario grid and immutable evidence, not a parameter optimizer."""
from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
from statistics import fmean
import hashlib,json,os,platform,shutil,tempfile
from . import __version__
from .data import load_panel,finite,verify_manifest
from .features import VARIANTS,compute_features,build_signals
from .portfolio import Config,simulate
from .metrics import summarize,paired_interval,regression_diagnostic


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def content_id(value):return hashlib.sha256(canonical({k:v for k,v in value.items() if k!='run_id'}).encode()).hexdigest()[:24]


def save_experiment(result,output):
    if result.get('run_id')!=content_id(result):raise ValueError('result hash does not match')
    output=Path(output);output.mkdir(parents=True,exist_ok=True);target=output/result['run_id']
    data=(canonical(result)+'\n').encode()
    if target.exists():
        if (target/'result.json').read_bytes()!=data:raise ValueError('immutable evidence conflict')
        return target
    scratch=Path(tempfile.mkdtemp(prefix='.writing-',dir=output))
    try:
        (scratch/'result.json').write_bytes(data);scratch.rename(target)
    finally:
        if scratch.exists():shutil.rmtree(scratch)
    return target


def buy_hold(panel,symbol,dates,initial=10000,cost_bps=10):
    """Buy at first session open; cost-inclusive, final close liquidation if verifiable."""
    rows=panel.bars.get(symbol,{})
    first=rows.get(dates[0]);fee=cost_bps/20000
    if first is None or not finite(first.open) or first.open<=0:return {'status':'UNAVAILABLE','nav':[],'metrics':None}
    shares=initial/(first.open*(1+fee));mark=first.open;nav=[];stale_count=0
    for i,d in enumerate(dates):
        b=rows.get(d);stale=b is None or not b.valid
        if stale:stale_count+=1
        else:mark=b.close
        equity=shares*mark
        liquidated=i==len(dates)-1 and not stale
        if liquidated:equity*=1-fee
        nav.append({'date':d,'equity':equity,'exposure':0 if liquidated else 1,'turnover':int(i==0)+int(liquidated),'stale':stale,'positions':0 if liquidated else 1})
    return {'status':'BLOCKED_DATA' if stale_count else 'DIAGNOSTIC_ONLY','nav':nav,'metrics':summarize(nav,initial=initial),'stale_days':stale_count}



def equal_weight(panel,members,dates,initial=10000,cost_bps=10):
    """Daily next-open rebalanced current-universe benchmark; cash is finite."""
    fee=cost_bps/20000;cash=initial;positions={};marks={};nav=[];stale_days=0;errors=0;prev=initial
    for i,day in enumerate(dates):
        wanted=sorted(set(members.get(dates[i-1],[]))) if i else []
        validopen={s:panel.bars.get(s,{}).get(day) for s in sorted(set(wanted)|set(positions))}
        if any(b is None or not finite(b.open) or b.open<=0 for b in validopen.values()):
            errors+=1;can_rebalance=False
        else:can_rebalance=not (nav and nav[-1]['stale'])
        turnover=0
        if can_rebalance:
            # Equal weights refer to prior-close NAV; sell first and cap purchases by cash.
            targets={s:prev/len(wanted) if s in wanted else 0 for s in validopen} if wanted else {s:0 for s in validopen}
            for symbol,b in validopen.items():
                held=positions.get(symbol,0);desired=targets[symbol]/b.open
                if held>desired:
                    amount=(held-desired)*b.open;cash+=amount*(1-fee);turnover+=amount;positions[symbol]=desired
            for symbol in wanted:
                b=validopen[symbol];held=positions.get(symbol,0)
                amount=min(max(targets[symbol]-held*b.open,0),max(cash,0)/(1+fee))
                positions[symbol]=held+amount/b.open;cash-=amount*(1+fee);turnover+=amount;marks[symbol]=b.open
            positions={s:q for s,q in positions.items() if q>1e-12}
        stale=False
        for symbol in positions:
            b=panel.bars.get(symbol,{}).get(day)
            if b is None or not b.valid:stale=True
            else:marks[symbol]=b.close
        if stale:stale_days+=1
        if i==len(dates)-1 and not stale:
            total=sum(q*marks[s] for s,q in positions.items());cash+=total*(1-fee);turnover+=total;positions={}
        value=sum(q*marks[s] for s,q in positions.items());equity=cash+value
        if cash<-.00001 or equity<=0:raise ValueError('EW ledger invariant failed')
        nav.append({'date':day,'equity':equity,'exposure':value/equity,'positions':len(positions),'stale':stale,'turnover':turnover/prev})
        prev=equity
    return {'status':'BLOCKED_DATA' if stale_days or errors else 'DIAGNOSTIC_ONLY','nav':nav,'metrics':summarize(nav,initial=initial),'stale_days':stale_days,'invalid_open_days':errors}


def historical_members(panel):
    members={d:[] for d in panel.dates}
    for symbol,rows in panel.bars.items():
        if symbol in {'QQQ','SOXX'}:continue
        streak=0
        for d in panel.dates:
            b=rows.get(d);streak=streak+1 if b and b.valid else 0
            if streak>=61:members[d].append(symbol)
    return members


def compact_nav(nav,dd):
    return [[round(r['equity'],6),round(x,8),round(r['exposure'],8),r.get('positions',0),r.get('stale',False)] for r,x in zip(nav,dd)]


def clean_metrics(metrics):return {k:v for k,v in metrics.items() if k not in {'drawdown','daily_returns'}}


def ledger_summary(trades):
    grouped={}
    for t in trades:
        r=grouped.setdefault(t['symbol'],{'symbol':t['symbol'],'pnl':0,'trades':0})
        r['pnl']+=t['pnl'];r['trades']+=1
    return sorted(grouped.values(),key=lambda x:-x['pnl'])


def lesson_examples(panel,features,trades):
    if not trades:return []
    ordered=sorted(trades,key=lambda x:x['net_return'])
    picks=[('最差已完成交易',ordered[0]),('中位附近交易',ordered[len(ordered)//2]),('最好已完成交易',ordered[-1])]
    picks.extend((f'{y} 首笔（按时间选取）',next(t for t in trades if t['signal_date'].startswith(y))) for y in sorted({t['signal_date'][:4] for t in trades}))
    lessons=[];seen=set()
    for label,t in picks:
        key=(t['symbol'],t['signal_date'])
        if key in seen:continue
        seen.add(key);i=panel.dates.index(t['signal_date']);end=panel.dates.index(t['exit_date']);ds=panel.dates[max(0,i-20):end+1];points=[]
        for d in ds:
            b=panel.bars[t['symbol']].get(d);f=features[t['symbol']].get(d,{})
            points.append({'date':d,'close':b.close if b and b.valid else None,**{k:f.get(k) for k in ['ma5','atr5','d5','gap','eligible']}})
        lessons.append({'label':label,'trade':t,'points':points,'signal_index':ds.index(t['signal_date']),'entry_index':ds.index(t['entry_date']),'exit_index':ds.index(t['exit_date'])})
    return lessons


def dataset(panel,start,end,label,id):
    dates=[d for d in panel.dates if start<=d<=end]
    variants={v:compute_features(panel,v) for v in VARIANTS};signals={v:build_signals(f,panel.dates) for v,f in variants.items()}
    scenarios=[];full={};primary=None
    for variant in VARIANTS:
        for hold in [1,3,5,10]:
            for cost in [0,10,25,50]:
                r=simulate(panel,signals[variant],Config(hold=hold,cost_bps=cost),start=start,end=end)
                m=summarize(r['nav'],trades=r['trades']);sid=f'{variant}-h{hold}-c{cost}'
                scenarios.append({'id':sid,'variant':variant,'label':VARIANTS[variant],'hold':hold,'cost_bps':cost,'status':r['status'],'metrics':clean_metrics(m),'metrics_scope':'DIAGNOSTIC_PRICE_RETURN','validated_metrics':None,'nav':compact_nav(r['nav'],m['drawdown']),'stale_days':r['stale_days'],'fees':r['fees'],'entry_count':r['entry_count'],'open_positions':r['open_positions']})
                if hold==3 and cost==10:
                    full[variant]=(r,m)
                    if variant=='primary':primary=r
    baselines={};baseline_full={}
    for s in ['QQQ','SOXX','EW']:
        r=equal_weight(panel,historical_members(panel),dates) if s=='EW' else buy_hold(panel,s,dates)
        baseline_full[s]=r
        baselines[s]={'status':r['status'],'metrics':clean_metrics(r['metrics']) if r['metrics'] else None,'nav':compact_nav(r['nav'],r['metrics']['drawdown']) if r['metrics'] else [],'stale_days':r.get('stale_days')}
    p,pm=full['primary'];tm=full['trend'][1]
    diag={'primary_vs_trend':paired_interval(pm['daily_returns'],tm['daily_returns']),
          'concentration':ledger_summary(p['trades']),
          'primary_warnings':p['warnings'],
          'period_status':'RETROSPECTIVE_RECONSTRUCTION_NOT_UNTOUCHED_OOS',
          'trial_disclosure':{'visible_grid':64,'historical_effective_trials':'UNKNOWN','pbo':'NOT_ESTIMATED','deflated_sharpe':'NOT_ESTIMATED'}}
    if baseline_full['QQQ']['metrics']:
        diag['primary_vs_qqq']=paired_interval(pm['daily_returns'],baseline_full['QQQ']['metrics']['daily_returns'])
        diag['regression_vs_qqq']=regression_diagnostic(pm['daily_returns'],baseline_full['QQQ']['metrics']['daily_returns'])
    # Fixed-configuration subperiod resets, not folds selected by their result.
    periods=[]
    for a,b in [('2017-01-03','2019-12-31'),('2020-01-01','2021-12-31'),('2022-01-03','2024-12-31'),('2025-01-01','2025-12-31'),('2026-01-01','2026-10-02')]:
        a=max(a,start);b=min(b,end)
        if a>b or not any(a<=d<=b for d in dates):continue
        z=simulate(panel,signals['primary'],Config(),start=a,end=b);zm=summarize(z['nav'],trades=z['trades'])
        periods.append({'start':z['nav'][0]['date'],'end':z['nav'][-1]['date'],'metrics':clean_metrics(zm),'status':z['status'],'starts_in_cash':True})
    diag['subperiods']=periods
    diag['leave_one_out']=[]
    for excluded in sorted(variants['primary']):
        altered={d:[s for s in queue if s['symbol']!=excluded] for d,queue in signals['primary'].items()}
        z=simulate(panel,altered,Config(),start=start,end=end);zm=summarize(z['nav'],trades=z['trades'])
        diag['leave_one_out'].append({'excluded':excluded,'cagr':zm['cagr'],'max_drawdown':zm['max_drawdown'],'completed_trades':len(z['trades']),'status':z['status']})
    # Capital/capacity sensitivity holds all other settings fixed.
    diag['capacity']=[]
    for capital in [10000,1000000,100000000]:
        z=simulate(panel,signals['primary'],Config(initial=capital),start=start,end=end)
        diag['capacity'].append({'initial':capital,'metrics':clean_metrics(summarize(z['nav'],initial=capital,trades=z['trades'])),'participation_cap':.01,'status':z['status']})
    return {'id':id,'label':label,'start':dates[0],'end':dates[-1],'dates':dates,'source_rows':sum(sum(d in rows for d in dates) for rows in panel.bars.values()),'invalid_rows':sum(i['kind']=='INVALID_OHLCV' and start<=i['date']<=end for i in panel.issues),'calendar_status':panel.calendar_status,'scenarios':scenarios,'baselines':baselines,'primary_trades':primary['trades'],'lessons':lesson_examples(panel,variants['primary'],primary['trades']),'diagnostics':diag}


LIMITS=[
'所有年化为指定现金组合的历史价格收益诊断，不是预期收益；不含分红再投资、闲置现金利息、税、融资和精确订单簿冲击。',
'固定当前43股观察池存在幸存者与选择偏差；没有历史成分、退市证券或真实当时的数据版本。',
'快照 known_at 是下载时间，早于该时间的计算都是历史重建；逐日揭示教学不能把已看过的历史变成真正盲测。',
'主规则保留前日 MA5/ATR5 锚点；当日完成 MA5、量比变体是单独实验。日量比不能代替同分钟 RVOL，日线不能复原 VWAP/5分钟确认。',
'源数据复权口径与分拆权益仍未完全认证。严格数据状态与诊断净值分开，缺价没有被删除，也没有当成真实0收益。',
'64格参数矩阵每数据集完整披露，不按最好年化挑赢家；有效总试验次数未知，不能捏造PBO或Deflated Sharpe。',
'同池趋势对照控制资金、持仓数和成本；QQQ/SOXX持有对照风险与暴露不同。配对区块区间是描述统计，不是风险调整后的alpha证明。',
'入场使用下一交易日已知open，收盘退出是事先固定持有期。缺失整根日线可能使历史open不可用，缺失后延迟退出属于数据故障诊断。',
'结束时未到期仓位按最后有效价估值，未伪造退出；已完成交易统计不含未平仓。平均暴露是收盘暴露，1日持有的盘中风险不能由此判断。',
'数据快照未进公开Git。精确重现历史数值需要授权快照及匹配SHA256；公开CI使用合成反例验证代码，不证明真实市场绩效。'
]


def run_experiment(ibkr,yahoo,source_catalog,extra_review=None,expected_manifest=None):
    panels=[load_panel(ibkr,'ibkr'),load_panel(yahoo,'yahoo')]
    if expected_manifest is not None:
        verify_manifest([dict(provider=p.provider,**r) for p in panels for r in p.receipts],expected_manifest)
    defs=[(panels[0],'2022-01-03','2026-10-02','IBKR · 2022–2026 原始证据','ibkr_5y'),(panels[1],'2022-01-03','2026-10-02','Yahoo · 同窗口独立复核','yahoo_5y'),(panels[1],'2017-01-03','2026-10-02','Yahoo · 2017–2026 长历史','yahoo_10y')]
    datasets=[dataset(*d) for d in defs]
    manifest=[dict(provider=p.provider,**r) for p in panels for r in p.receipts]
    code=hashlib.sha256()
    for p in sorted(p for p in Path(__file__).parent.iterdir() if p.suffix in {'.py','.html'}):code.update(p.name.encode());code.update(p.read_bytes())
    known=max(r['known_at'] for r in manifest)
    result={'schema_version':1,'version':__version__,'snapshot_known_at':known,'meta':{'mode':'RETROSPECTIVE_PRICE_RETURN_RESEARCH','code_sha256':code.hexdigest(),'python':platform.python_version(),'source_rows':sum(r['rows'] for r in manifest),'source_files':len(manifest),'universe':'43_CURRENT_WATCHLIST_STOCKS_NOT_PIT','strategy':'MA5_ATR_DAILY_PROXY','initial_capital':10000,'currency':'USD','visible_scenario_count':192,'raw_market_data_in_git':False,'real_orders_enabled':False},'datasets':datasets,'sources':source_catalog,'manifest':manifest,'data_reconciliation':extra_review or {},'limits':LIMITS,'evidence_gates':[
      {'name':'可复跑代码与源文件指纹','status':'PASS','detail':'数据、规则、代码均有指纹；相同输入得到相同实验ID。'},
      {'name':'历史数据源一致性','status':'PARTIAL','detail':'两个完整来源独立回测；SNDK/SPCX等个别日期差异保留并披露。'},
      {'name':'历史股票池与复权/分拆权益','status':'BLOCKED','detail':'当前观察池、历史成分和分拆现金/证券权益未认证。'},
      {'name':'真正未见样本与多重检验','status':'BLOCKED','detail':'已查看历史仅作回顾诊断；需要冻结后前向观测。'},
      {'name':'完整盘中 HARNESS','status':'BLOCKED','detail':'同分钟RVOL、完整VWAP、5分钟确认和原始保护退出尚缺。'},
      {'name':'实盘执行与资金风险','status':'BLOCKED','detail':'无券商下单功能；必须先影子记录、模拟成交和故障演练。'}]}
    result['run_id']=content_id(result);return result


def public_summary(result):
    """Only aggregate derived statistics and hashes; exclude raw bars/trade prices."""
    return {k:v for k,v in result.items() if k not in {'datasets'}}|{'datasets':[{k:v for k,v in d.items() if k not in {'scenarios','primary_trades','lessons'}}|{'scenarios':[{k:v for k,v in s.items() if k!='nav'} for s in d['scenarios']], 'baselines':{k:{kk:vv for kk,vv in x.items() if kk!='nav'} for k,x in d['baselines'].items()}} for d in result['datasets']]}
