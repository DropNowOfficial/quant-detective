"""Seeded adversarial paths test accounting rather than promises of profitability."""
import random
from datetime import date,timedelta
import pytest
from research_lab.data import Bar,Panel
from research_lab.portfolio import Config,simulate


def test_250_random_cash_ledgers_and_prefix_invariance():
    rng=random.Random(20261004)
    ds=[(date(2024,1,1)+timedelta(days=i)).isoformat() for i in range(35)]
    for _ in range(250):
        symbols=['A','B','C','D'];bars={s:{} for s in symbols}
        for s in symbols:
            price=100
            for d in ds:
                o=price*(1+rng.uniform(-.08,.08));price=o*(1+rng.uniform(-.08,.08))
                bars[s][d]=Bar(d,o,max(o,price)*1.01,min(o,price)*.99,price,1e7,True)
        signals={d:[{'symbol':s,'adv20':1e8} for s in rng.sample(symbols,rng.randrange(5))] for d in ds}
        conf=Config(slots=rng.randint(1,4),hold=rng.randint(1,10),cost_bps=rng.choice([0,10,25,50]))
        p=Panel(ds,bars,[],[]);allrun=simulate(p,signals,conf);prefix=simulate(p,signals,conf,end=ds[20])
        assert allrun['nav'][:21]==prefix['nav']
        assert all(r['cash']>=0 and 0<=r['positions']<=conf.slots and r['equity']>0 for r in allrun['nav'])
        realized=sum(t['pnl'] for t in allrun['trades'])
        openpnl=sum(p['quantity']*p['last_mark']-p['cost_basis'] for p in allrun['open_book'])
        assert allrun['nav'][-1]['equity']==pytest.approx(conf.initial+realized+openpnl,rel=1e-11)
