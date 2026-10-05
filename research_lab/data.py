"""Snapshots retain provenance and questionable rows instead of repairing prices."""
from dataclasses import dataclass,field
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib,json,math


def finite(value):
    return not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)


@dataclass(frozen=True)
class Bar:
    date:str
    open:float|None
    high:float|None
    low:float|None
    close:float|None
    volume:float|None
    valid:bool


@dataclass
class Panel:
    dates:list
    bars:dict
    receipts:list
    issues:list
    provider:str='fixture'
    calendar_status:str='SYNTHETIC'


def valid_bar(values):
    o,h,l,c,v=values
    return all(finite(x) for x in values) and min(o,h,l,c)>0 and v>=0 and l<=min(o,c)<=max(o,c)<=h


def aware(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('known_at must include timezone')
    return d


def load_panel(directory,provider='ibkr'):
    directory=Path(directory);receipts=[];issues=[];bars={}
    if provider not in {'ibkr','yahoo'}:raise ValueError('unsupported provider')
    paths=sorted(directory.glob('*_daily.json' if provider=='ibkr' else '*.json'))
    for p in paths:
        if p.name=='manifest.json':continue
        doc=json.loads(p.read_text());digest=hashlib.sha256(p.read_bytes()).hexdigest()
        if provider=='ibkr':
            symbol=doc['symbol'];a=doc['data'];known=doc['retrieved_at'];times=a['time']
            if doc.get('contract',{}).get('symbol')!=symbol:raise ValueError('contract identity mismatch')
            cols=[a[k] for k in ['open','high','low','close','volume']]
            source='IBKR saved price-history response';actions=a.get('corp_actions',[])
            identity=doc.get('contract',{});adjustment='UNVERIFIED_PROVIDER_CONVENTION'
        else:
            a=doc['data']['chart']['result'][0];symbol=doc['requested_symbol'];known=doc['known_at']
            if a['meta']['symbol']!=symbol:raise ValueError('secondary identity mismatch')
            times=[datetime.fromtimestamp(t,timezone.utc).isoformat() for t in a['timestamp']]
            q=a['indicators']['quote'][0];cols=[q[k] for k in ['open','high','low','close','volume']]
            source=doc['url'];actions=a.get('events',{});identity={k:a['meta'].get(k) for k in ['symbol','longName','exchangeName','instrumentType','currency']}
            adjustment='PROVIDER_OHLC_PRICE_RETURN_NO_DIVIDEND_REINVESTMENT'
        fetched=aware(known)
        if len({len(times),*(len(c) for c in cols)})!=1:raise ValueError(f'{symbol}: unequal arrays')
        if symbol in bars:raise ValueError('duplicate symbol')
        rows={};last='';bad=0;future=0
        for t,*v in zip(times,*cols):
            stamp=aware(t);day=stamp.astimezone(ZoneInfo('America/New_York')).date().isoformat()
            if day<=last:raise ValueError(f'{symbol}: duplicate or unsorted dates')
            last=day
            completion=datetime.fromisoformat(day+'T16:30:00').replace(tzinfo=ZoneInfo('America/New_York'))
            if stamp>fetched or fetched<completion:future+=1;continue
            okay=valid_bar(v)
            if not okay:bad+=1;issues.append({'symbol':symbol,'date':day,'kind':'INVALID_OHLCV'})
            rows[day]=Bar(day,*v,okay)
        if not rows:raise ValueError(f'{symbol}: empty snapshot')
        bars[symbol]=rows
        receipts.append({'symbol':symbol,'source':source,'known_at':known,'file':p.name,'sha256':digest,'rows':len(rows),'first':min(rows),'last':max(rows),'invalid_rows':bad,'future_rows_excluded':future,'identity':identity,'corporate_actions_count':len(actions) if isinstance(actions,list) else sum(len(x) for x in actions.values()),'adjustment':adjustment,'historical_pit':'UNAVAILABLE_CURRENT_VINTAGE','action_coverage':'UNVERIFIED'})
    if 'QQQ' not in bars:raise ValueError('QQQ calendar/benchmark missing')
    dates=sorted(bars['QQQ']);cal='PROVIDER_SESSION_LATTICE_UNVERIFIED'
    try:
        import exchange_calendars as xc
        # Calendar construction requires start < end, including one-session inputs.
        c=xc.get_calendar('XNYS',start=str(date.fromisoformat(dates[0])-timedelta(days=7)),end=str(date.fromisoformat(dates[-1])+timedelta(days=7)))
        expected=[str(d.date()) for d in c.sessions_in_range(dates[0],dates[-1])]
        missing=set(expected)-set(dates);extra=set(dates)-set(expected)
        cal='XNYS_VERIFIED' if not missing and not extra else 'XNYS_MISMATCH'
        issues.extend({'symbol':'QQQ','date':d,'kind':'CALENDAR_MISSING'} for d in sorted(missing))
        issues.extend({'symbol':'QQQ','date':d,'kind':'NON_SESSION_BAR'} for d in sorted(extra))
        dates=expected
    except ImportError:pass
    for s,rows in bars.items():
        lo,hi=min(rows),max(rows)
        issues.extend({'symbol':s,'date':d,'kind':'MISSING_SESSION'} for d in dates if lo<=d<=hi and d not in rows)
    return Panel(dates,bars,receipts,issues,provider,cal)


def verify_manifest(actual,expected):
    """A replay must use exactly the pinned provider/file/hash set."""
    def index(rows):
        out={}
        for r in rows:
            key=(r['provider'],r['file'])
            if key in out:raise ValueError('duplicate manifest identity')
            out[key]=r['sha256']
        return out
    a=index(actual);b=index(expected)
    if a!=b:
        changed=[f'{p}/{f}' for (p,f) in sorted(set(a)|set(b)) if a.get((p,f))!=b.get((p,f))]
        raise ValueError('INPUT_MISMATCH: '+', '.join(changed))
