import json,csv,re,html,datetime as D,zoneinfo,glob
CO={
 "AAPL":dict(cik="0000320193",filings={2024:"0000320193-24-000123",2025:"0000320193-25-000079"},
   std=["CommercialPaper","LongTermDebtCurrent"],R=dict(IS=3,BS=5,CF=8)),
 "JNJ":dict(cik="0000200406",filings={2024:"0000200406-25-000038",2025:"0000200406-26-000016"},
   std=["ShortTermBorrowings"],R=dict(IS=5,BS=3,CF=10)),
 "ADBE":dict(cik="0000796343",filings={2024:"0000796343-25-000004",2025:"0000796343-26-000003"},
   std=["DebtCurrent"],R=dict(IS=5,BS=3,CF=8)),
}
FLOW=["GrossProfit","NetIncomeLoss","NetCashProvidedByUsedInOperatingActivities","DepreciationDepletionAndAmortization"]
INST=["Assets","AssetsCurrent","CashAndCashEquivalentsAtCarryingValue","LiabilitiesCurrent","AccruedIncomeTaxesCurrent"]
ET=zoneinfo.ZoneInfo("America/New_York")
def rfile_vals(t,acc,r):
    fn=f"raw/filings/{t}_{acc.replace('-','')}_R{r}.htm"
    out={}
    try:h=open(fn,errors="ignore").read()
    except: return out
    for row in re.findall(r"<tr.*?</tr>",h,re.S):
        m=re.search(r"defref_([A-Za-z\-_0-9]+)",row)
        if not m or "<th" in row[:20] and "class=\"tl\"" in row: pass
        cells=[html.unescape(re.sub(r"<[^>]+>","",c)).strip() for c in re.findall(r"<t[hd][^>]*>.*?</t[hd]>",row,re.S)]
        if not m or len(cells)<3: continue
        def num(s):
            s=s.replace("$","").replace(",","").strip();neg=s.startswith("(")
            s=s.strip("()");
            try:return -float(s) if neg else float(s)
            except:return None
        out.setdefault(m.group(1).replace("us-gaap_",""),[num(cells[1]),num(cells[2])])
    return out
rows=[]
for t,c in CO.items():
    sub=json.load(open(f"raw/submissions_{c['cik']}.json"))["filings"]["recent"]
    cf=json.load(open(f"raw/companyfacts_{c['cik']}.json"))["facts"]["us-gaap"]
    amend=[(sub["form"][i],sub["accessionNumber"][i]) for i in range(len(sub["form"])) if sub["form"][i].startswith("10-K/A")]
    for fy,acc in c["filings"].items():
        i=sub["accessionNumber"].index(acc)
        form=sub["form"][i];filed=sub["filingDate"][i];acc_dt=sub["acceptanceDateTime"][i];rep=sub["reportDate"][i]
        accu=D.datetime.fromisoformat(acc_dt.replace("Z","+00:00"))
        acc_et=accu.astimezone(ET).isoformat()
        rv={}
        for k,r in c["R"].items(): rv.update({kk:(k,v) for kk,v in rfile_vals(t,acc,r).items()})
        tags=FLOW+INST+c["std"]
        for tag in tags:
            ents=[x for x in cf.get(tag,{}).get("units",{}).get("USD",[]) if x["accn"]==acc and x["form"]=="10-K"]
            for x in ents:
                if "start" in x:
                    dur=(D.date.fromisoformat(x["end"])-D.date.fromisoformat(x["start"])).days
                    if not 360<=dur<=372: continue
                    ptype="FY(duration %dd)"%dur
                else: ptype="instant"
                # restatement check: same tag/end/start in other filings with differing value
                others=[y for y in cf[tag]["units"]["USD"] if y["end"]==x["end"] and y.get("start")==x.get("start") and y["accn"]!=acc]
                restated=[(y["accn"],y["val"]) for y in others if y["val"]!=x["val"]]
                col=0 if x["end"]==rep else 1
                rf=rv.get(tag)
                rfval=None;rfsrc=None
                if rf and rf[1][col] is not None: rfval=rf[1][col]*1e6;rfsrc=rf[0]
                rows.append(dict(ticker=t,cik=c["cik"],accession=acc,form=form,filing_date=filed,acceptance_utc=acc_dt,acceptance_et=acc_et,
                  period_end_fy=rep,fiscal_year_label=fy,period_type=ptype,tag="us-gaap:"+tag,unit="USD",currency="USD",scale="as stored in XBRL (full USD)",
                  start=x.get("start"),end=x["end"],value=x["val"],companyfacts_fy=x["fy"],companyfacts_fp=x["fp"],
                  later_restatement=";".join(f"{a}:{v}" for a,v in restated) or "none",amendments_10KA_in_recent_submissions=";".join(a for _,a in amend) or "none",
                  rfile_value=rfval,rfile_page=rfsrc,rfile_matches=("" if rfval is None else rfval==x["val"])))
w=csv.DictWriter(open("data_points.csv","w",newline=""),fieldnames=list(rows[0].keys()));w.writeheader();w.writerows(rows)
print(len(rows))
import collections
print(collections.Counter((r["ticker"],r["rfile_matches"]) for r in rows))
for r in rows:
    if r["rfile_matches"] is not True: print(r["ticker"],r["accession"],r["tag"],r["end"],r["value"],r["rfile_value"],r["rfile_matches"],r["later_restatement"])
