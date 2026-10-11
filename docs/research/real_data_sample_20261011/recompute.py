#!/usr/bin/env python3
"""Standalone hand recompute of GP/A and Sloan accruals from data_points.csv (no repo code).
GP/A = GrossProfit / Assets(end of FY).   [also reported on average assets? no: end-of-year, stated]
Sloan(1996) accruals = (dCA - dCash) - (dCL - dSTD - dTP) - Dep ; scaled by average total assets (A_t + A_t-1)/2
CF version = (NetIncome - CFO) / average total assets   [labelled alternative]
All deltas = value(FY end) - value(prior FY end) taken from the SAME 10-K (comparative column). Units: USD."""
import csv
from collections import defaultdict
STD={"AAPL":["CommercialPaper","LongTermDebtCurrent"],"JNJ":["ShortTermBorrowings"],"ADBE":["DebtCurrent"]}
rows=list(csv.DictReader(open("data_points.csv")))
def val(t,acc,tag,end):
    m=[r for r in rows if r["ticker"]==t and r["accession"]==acc and r["tag"]=="us-gaap:"+tag and r["end"]==end]
    assert len(m)==1,(t,acc,tag,end,len(m))
    return float(m[0]["value"])
out=[]
for t in STD:
    accs=sorted({r["accession"] for r in rows if r["ticker"]==t},key=lambda a:[r["period_end_fy"] for r in rows if r["accession"]==a][0])
    for acc in accs:
        rs=[r for r in rows if r["accession"]==acc]
        end=rs[0]["period_end_fy"]
        prior=sorted({r["end"] for r in rs if r["tag"]=="us-gaap:Assets"})[0]
        assert prior<end
        g=lambda tag,e=end:val(t,acc,tag,e)
        gp=g("GrossProfit");A=g("Assets");Ap=g("Assets",prior);Aavg=(A+Ap)/2
        dCA=g("AssetsCurrent")-g("AssetsCurrent",prior)
        dCash=g("CashAndCashEquivalentsAtCarryingValue")-g("CashAndCashEquivalentsAtCarryingValue",prior)
        dCL=g("LiabilitiesCurrent")-g("LiabilitiesCurrent",prior)
        dSTD=sum(g(x)-g(x,prior) for x in STD[t])
        dTP=g("AccruedIncomeTaxesCurrent")-g("AccruedIncomeTaxesCurrent",prior)
        dep=g("DepreciationDepletionAndAmortization")
        acc_bs=(dCA-dCash)-(dCL-dSTD-dTP)-dep
        ni=g("NetIncomeLoss");cfo=g("NetCashProvidedByUsedInOperatingActivities")
        out.append(dict(t=t,acc=acc,end=end,prior=prior,gp=gp,A=A,Ap=Ap,Aavg=Aavg,dCA=dCA,dCash=dCash,dCL=dCL,dSTD=dSTD,dTP=dTP,dep=dep,
            gpa=gp/A,accr_bs=acc_bs,sloan=acc_bs/Aavg,ni=ni,cfo=cfo,accr_cf=ni-cfo,sloan_cf=(ni-cfo)/Aavg))
import json;json.dump(out,open("results.json","w"),indent=1)
for o in out:
    print(f"{o['t']} FY end {o['end']} ({o['acc']}) prior {o['prior']}")
    for k in ["gp","A","Ap","Aavg","dCA","dCash","dCL","dSTD","dTP","dep","ni","cfo"]: print(f"   {k:5s} {o[k]:>20,.0f}")
    print(f"   GP/A = {o['gp']:,.0f}/{o['A']:,.0f} = {o['gpa']:.6f}")
    print(f"   Sloan BS accruals = {o['accr_bs']:,.0f}; scaled = {o['sloan']:.6f}")
    print(f"   CF-version (NI-CFO)={o['accr_cf']:,.0f}; scaled = {o['sloan_cf']:.6f}")
