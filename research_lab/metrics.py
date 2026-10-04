"""Portfolio metrics, calendar-year bookkeeping and paired circular-block uncertainty."""
from datetime import date
from statistics import fmean,stdev
import math,random


def summarize(nav,initial=10000,trades=None):
    if not nav:raise ValueError('empty NAV')
    eq=[initial]+[r['equity'] for r in nav]
    returns=[b/a-1 for a,b in zip(eq,eq[1:])]
    peak=initial;dds=[]
    for e in eq[1:]:peak=max(peak,e);dds.append(e/peak-1)
    days=(date.fromisoformat(nav[-1]['date'])-date.fromisoformat(nav[0]['date'])).days
    cagr=(eq[-1]/initial)**(365.2425/days)-1 if days>0 else None
    vol=stdev(returns)*math.sqrt(252) if len(returns)>1 else None
    sharpe=fmean(returns)*252/vol if vol and vol>0 else None
    yearly=[];base=initial
    for y in sorted({r['date'][:4] for r in nav}):
        rows=[r for r in nav if r['date'].startswith(y)];last=rows[-1]['equity']
        yearly.append({'year':y,'return':last/base-1,'first':rows[0]['date'],'last':rows[-1]['date'],'partial':rows[0]['date'][5:]>'01-07' or rows[-1]['date'][5:]<'12-24'})
        base=last
    trades=trades or [];wins=[t['pnl'] for t in trades if t['pnl']>0];losses=[t['pnl'] for t in trades if t['pnl']<0]
    return {'total_return':eq[-1]/initial-1,'cagr':cagr,'max_drawdown':min(dds),'volatility':vol,'sharpe_rf0':sharpe,'year_returns':yearly,'mean_exposure':fmean(r['exposure'] for r in nav),'turnover_total':sum(r['turnover'] for r in nav),'completed_trades':len(trades),'win_rate':len(wins)/len(trades) if trades else None,'profit_factor':sum(wins)/-sum(losses) if losses else None,'terminal_equity':eq[-1],'calendar_days':days,'session_count':len(nav),'drawdown':dds,'daily_returns':returns}


def quantile(values,q):
    a=sorted(values);x=(len(a)-1)*q;i=int(x);return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(x-i)


def paired_interval(a,b,draws=1000,block=20,seed=20261004):
    if len(a)!=len(b) or len(a)<block:raise ValueError('aligned histories longer than block required')
    delta=[x-y for x,y in zip(a,b)];n=len(delta);rng=random.Random(seed);out=[]
    for _ in range(draws):
        selected=[]
        while len(selected)<n:
            k=rng.randrange(n);selected.extend(delta[(k+j)%n] for j in range(block))
        out.append(fmean(selected[:n])*252)
    return {'annualized_mean_daily_difference':fmean(delta)*252,'ci95':[quantile(out,.025),quantile(out,.975)],'block_sessions':block,'draws':draws,'seed':seed,'meaning':'Descriptive paired date-block interval; not selection/multiplicity correction; not CAGR alpha.'}


def regression_diagnostic(y,x,lag=5):
    """OLS against QQQ price returns, RF=0; HAC uncertainty is descriptive only."""
    if len(x)!=len(y) or len(x)<3:raise ValueError('aligned samples required')
    n=len(x);mx=fmean(x);my=fmean(y);xx=sum((v-mx)**2 for v in x)
    if xx<=0:return {'beta':None,'annualized_intercept_rf0':None,'hac_ci95':None}
    beta=sum((a-mx)*(b-my) for a,b in zip(x,y))/xx;alpha=my-beta*mx
    residual=[b-alpha-beta*a for a,b in zip(x,y)];scores=[e*(1/n-mx*(v-mx)/xx) for v,e in zip(x,residual)]
    bandwidth=min(lag,n-1);var=sum(z*z for z in scores)
    for k in range(1,bandwidth+1):var+=2*(1-k/(bandwidth+1))*sum(scores[i]*scores[i-k] for i in range(k,n))
    se=math.sqrt(max(var*n/(n-2),0))
    return {'beta':beta,'annualized_intercept_rf0':alpha*252,'hac_ci95':[(alpha-1.96*se)*252,(alpha+1.96*se)*252] if n>30 else None,'hac_lag':bandwidth,'n':n,'interpretation':'Price-return OLS with RF=0; excludes sector/momentum factors, varying exposure and selection corrections; not proven alpha.'}
