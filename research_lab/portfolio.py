"""Finite cash, explicit fills, daily mark ledger. Diagnostics cannot certify data."""
from dataclasses import dataclass,asdict
import math
from .data import finite


@dataclass(frozen=True)
class Config:
    slots:int=5
    hold:int=3
    cost_bps:float=10
    initial:float=10000
    max_participation:float=.01
    def __post_init__(self):
        for v in [self.slots,self.hold]:
            if isinstance(v,bool) or not isinstance(v,int) or not 1<=v<=100:raise ValueError('slots/hold must be integers 1..100')
        for v in [self.cost_bps,self.initial,self.max_participation]:
            if not finite(v):raise ValueError('parameters must be finite')
        if not 0<=self.cost_bps<=1000 or self.initial<=0 or not 0<self.max_participation<=1:raise ValueError('parameter out of range')


def simulate(panel,signals,config=Config(),start=None,end=None):
    dates=[d for d in panel.dates if (start is None or d>=start) and (end is None or d<=end)]
    if not dates:raise ValueError('empty test interval')
    cash=config.initial;positions={};trades=[];nav=[];warnings=[];previous_nav=config.initial
    fee=config.cost_bps/20000;entry_count=0;stale_days=0;fees=0;rejected=0
    for n,day in enumerate(dates):
        turnover=0;stale=False
        # Morning queue was fixed at the previous completed close within this experiment.
        queue=signals.get(dates[n-1],[]) if n else []
        if nav and nav[-1]['stale']:
            warnings.append({'date':day,'symbol':None,'kind':'NEW_ENTRIES_PAUSED_AFTER_STALE_MARK'})
            queue=[]
        for sig in queue:
            s=sig['symbol']
            if s in positions or len(positions)>=config.slots:continue
            b=panel.bars.get(s,{}).get(day)
            # Never use this day's future high/low/close validity to choose an open fill.
            if b is None or not finite(b.open) or b.open<=0:
                warnings.append({'date':day,'symbol':s,'kind':'ENTRY_OPEN_UNAVAILABLE'});rejected+=1;continue
            budget=min(previous_nav/config.slots,cash)
            adv=sig.get('adv20')
            if not finite(adv) or adv<=0:raise ValueError('signal ADV must be positive')
            budget=min(budget,adv*config.max_participation*(1+fee))
            if budget<=1e-8:continue
            quantity=budget/(b.open*(1+fee));notional=quantity*b.open;cost=notional*fee
            cash-=notional+cost;fees+=cost;turnover+=notional
            positions[s]={'symbol':s,'signal_date':dates[n-1],'entry_date':day,'entry_price':b.open,'quantity':quantity,'cost_basis':notional+cost,'entry_fee':cost,'due':n+config.hold-1,'last_mark':b.open,'stale':False,'adv20':adv}
            entry_count+=1
        # Closing fills are scheduled before the close; invalid closing evidence blocks them.
        for s,pos in list(positions.items()):
            b=panel.bars.get(s,{}).get(day)
            if b is None or not b.valid:
                stale=True;pos['stale']=True
                warnings.append({'date':day,'symbol':s,'kind':'HOLDING_MARK_UNVERIFIED'})
                continue
            pos['last_mark']=b.close
            if n>=pos['due']:
                gross=pos['quantity']*b.close;cost=gross*fee;net=gross-cost
                cash+=net;fees+=cost;turnover+=gross
                trades.append({k:v for k,v in pos.items() if k not in {'due','last_mark'}}|{'exit_date':day,'exit_price':b.close,'exit_fee':cost,'pnl':net-pos['cost_basis'],'net_return':net/pos['cost_basis']-1,'holding_sessions':n-dates.index(pos['entry_date'])+1,'delayed_exit':n>pos['due']})
                del positions[s]
        if stale:stale_days+=1
        value=sum(p['quantity']*p['last_mark'] for p in positions.values());equity=cash+value
        if not math.isfinite(equity) or equity<=0 or cash<-.000001:raise ValueError('portfolio accounting invariant failed')
        nav.append({'date':day,'equity':equity,'cash':max(cash,0),'exposure':value/equity,'positions':len(positions),'turnover':turnover/previous_nav,'stale':stale})
        previous_nav=equity
    return {'config':asdict(config),'nav':nav,'trades':trades,'warnings':warnings,'entry_count':entry_count,'rejected_entries':rejected,'open_positions':len(positions),'open_book':list(positions.values()),'stale_days':stale_days,'fees':fees,'status':'BLOCKED_DATA' if stale_days or rejected else 'DIAGNOSTIC_ONLY'}
