"""Frozen rules; all windows end at signal close, never at execution-day close."""
from statistics import fmean

VARIANTS={'primary':'MA5 前日锚定回踩','completed_ma5':'MA5 当日收盘变体','volume08':'前日锚定 + 日量比≥0.8','trend':'同池趋势对照'}


def compute_features(panel,variant='primary'):
    if variant not in VARIANTS:raise ValueError('unknown variant')
    out={};dates=panel.dates;q=panel.bars.get('QQQ',{})
    for symbol,bydate in panel.bars.items():
        if symbol in {'QQQ','SOXX'}:continue
        records={};seq=[bydate.get(d) for d in dates]
        for i,d in enumerate(dates):
            row={'eligible':False,'ma5':None,'atr5':None,'slope1':None,'slope3':None,'d5':None,'gap':None,'adv20':None,'rs20':None,'volume_ratio':None,'close':None,'reason':'INSUFFICIENT_OR_INVALID_HISTORY'}
            records[d]=row
            if i<60:continue
            w=seq[i-60:i+1]
            if len(w)!=61 or any(b is None or not b.valid for b in w):continue
            qw=[q.get(x) for x in dates[i-20:i+1]]
            if any(b is None or not b.valid for b in qw):row['reason']='BENCHMARK_INVALID';continue
            k=i if variant=='completed_ma5' else i-1
            ma=fmean(seq[j].close for j in range(k-4,k+1))
            prev=fmean(seq[j].close for j in range(k-5,k))
            prev2=fmean(seq[j].close for j in range(k-6,k-1))
            tr=[max(seq[j].high-seq[j].low,abs(seq[j].high-seq[j-1].close),abs(seq[j].low-seq[j-1].close)) for j in range(k-4,k+1)]
            atr=fmean(tr);cur=seq[i]
            if atr<=0:row['reason']='ZERO_ATR';continue
            adv=fmean(seq[j].close*seq[j].volume for j in range(i-20,i))
            av=fmean(seq[j].volume for j in range(i-20,i))
            if av<=0:row['reason']='ZERO_VOLUME';continue
            rs=cur.close/seq[i-20].close-qw[-1].close/qw[0].close
            distance=(cur.close-ma)/atr;gap=(cur.open-seq[i-1].close)/atr
            common=cur.close>=5 and adv>=2e7 and atr/cur.close<=.12 and ma-prev>0 and ma-prev2>=0
            geom=-.10<=distance<=.20 and abs(gap)<=.50
            eligible=common and (variant=='trend' or geom)
            if variant=='volume08':eligible=eligible and cur.volume/av>=.8
            row.update(eligible=eligible,ma5=ma,atr5=atr,slope1=ma-prev,slope3=(ma-prev2)/2,d5=distance,gap=gap,adv20=adv,rs20=rs,volume_ratio=cur.volume/av,close=cur.close,reason='ELIGIBLE' if eligible else 'RULES_NOT_MET')
        out[symbol]=records
    return out


def build_signals(features,dates):
    out={d:[] for d in dates}
    for s,rows in features.items():
        previous=False
        for d in dates:
            r=rows[d];yes=r['eligible']
            if yes and not previous:out[d].append({'symbol':s,'adv20':r['adv20'],'rs20':r['rs20']})
            previous=yes
    for choices in out.values():choices.sort(key=lambda x:(-x['rs20'],-x['adv20'],x['symbol']))
    return out
