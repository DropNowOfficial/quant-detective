from datetime import date, timedelta
from factor_candidates.gross_profitability import FundamentalFact, gross_profitability
from factor_candidates.max_lottery import DailyReturn, max_lottery
pe=date(2025,12,31); ka=date(2026,2,20); asof=date(2026,3,1)
a=FundamentalFact("A",pe,ka,"s1",revenue=100,cogs=80,total_assets=100)
b=FundamentalFact("A",pe,ka,"s2",revenue=100,cogs=50,total_assets=100)
print("P1 GP/A same period_end+known_at conflict:", gross_profitability([a,b],"A",asof), gross_profitability([b,a],"A",asof), "(order-dependent if differ)")
days=[date(2026,2,1)+timedelta(i) for i in range(21)]
mk=lambda r1,r2:[DailyReturn("A",days[0],ka-timedelta(60),"s",r1),DailyReturn("A",days[0],ka-timedelta(60),"s",r2)]+[DailyReturn("A",d,ka-timedelta(60),"s",0.01) for d in days[1:]]
asof2=days[-1]
print("P2 MAX same-day same-known_at conflict:", max_lottery(mk(0.10,0.02),"A",asof2,days), max_lottery(mk(0.02,0.10),"A",asof2,days))
dd=days+[days[-1]]  # duplicate last day
dup=days[1:]+[days[-1]]*1
rows=[DailyReturn("A",d,date(2026,1,1),"s",0.05 if d==days[0] else 0.01) for d in days]
print("P3 duplicate trading days (days[0] outside window?):", max_lottery(rows,"A",asof2,days[:1]+days[1:]+[days[-1]]), "vs clean", max_lottery(rows,"A",asof2,days))
print("P4 date-only known_at accepted (no timezone/time):", gross_profitability([a],"A",ka))
print("P5 empty security accepted:", gross_profitability([FundamentalFact("",pe,ka,"s",100,80,100)],"",asof))
