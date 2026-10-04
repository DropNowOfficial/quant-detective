"""Provider-neutral, immutable offline quote contracts. No network access."""
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def canonical(value):
    def encode(obj):
        if isinstance(obj, Decimal):
            text = format(obj, 'f')
            return (text.rstrip('0').rstrip('.') if '.' in text else text) if obj else '0'
        raise TypeError(type(obj).__name__)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=encode, allow_nan=False)

@dataclass(frozen=True)
class Instrument:
    venue: str
    symbol: str
    asset_class: str
    currency: str
    source: str
    timezone: str
    session_id: str

    def __post_init__(self):
        for v in asdict(self).values():
            if not isinstance(v,str) or not v.strip() or v != v.strip():
                raise ValueError('instrument fields must be nonempty trimmed strings')
        if self.asset_class not in ('stock','perpetual'):
            raise ValueError('unsupported asset class')
        try: ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as e: raise ValueError('unknown timezone') from e

    def key(self): return canonical(asdict(self))

@dataclass(frozen=True)
class Bar:
    instrument: Instrument
    start_ms: int
    end_ms: int
    received_ms: int
    interval_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    complete: bool

    def __post_init__(self):
        if not isinstance(self.instrument,Instrument): raise ValueError('invalid instrument')
        for name in ('start_ms','end_ms','received_ms','interval_ms'):
            v=getattr(self,name)
            if type(v) is not int or v<0: raise ValueError(f'{name} must be nonnegative integer')
        if self.interval_ms<=0 or self.end_ms-self.start_ms!=self.interval_ms:
            raise ValueError('invalid interval boundaries')
        if type(self.complete) is not bool: raise ValueError('complete must be boolean')
        if self.received_ms<self.start_ms or (self.complete and self.received_ms<self.end_ms):
            raise ValueError('reception precedes available bar')
        for name in ('open','high','low','close','volume'):
            v=getattr(self,name)
            if not isinstance(v,Decimal) or not v.is_finite() or v<0:
                raise ValueError(f'invalid {name}')
        if not self.low<=min(self.open,self.close)<=max(self.open,self.close)<=self.high:
            raise ValueError('invalid OHLC ordering')

@dataclass(frozen=True)
class RuleSpec:
    id: str
    version: str
    purpose: str
    lower: Decimal|None
    upper: Decimal|None
    protection_fraction: Decimal|None

@dataclass(frozen=True)
class RuleState:
    last_end_ms: int|None = None
    in_zone: bool = False
    health: str = 'unknown'

@dataclass(frozen=True)
class Decision:
    kind: str
    code: str
    event_id: str
    evidence: dict
    next_state: RuleState


def parse_bar(raw):
    fields=set(Bar.__dataclass_fields__)|{'schema_version'}
    if not isinstance(raw,dict) or set(raw)!=fields or type(raw['schema_version']) is not int or raw['schema_version']!=1:
        raise ValueError('expected exact schema version 1 bar fields')
    try:
        data={k:v for k,v in raw.items() if k!='schema_version'}
        if not isinstance(data['instrument'],dict) or set(data['instrument'])!=set(Instrument.__dataclass_fields__):
            raise ValueError('invalid instrument fields')
        data['instrument']=Instrument(**data['instrument'])
        for name in ('open','high','low','close','volume'):
            if not isinstance(data[name],str): raise ValueError(f'{name} must be decimal string')
            data[name]=Decimal(data[name])
        return Bar(**data)
    except (TypeError,InvalidOperation) as e: raise ValueError('invalid bar') from e


def canonical_bar(bar): return canonical({'schema_version':1,**asdict(bar)})


def event_id(bar,rule,code):
    payload=[bar.instrument.key(),rule.id,rule.version,bar.start_ms,bar.end_ms,code]
    return hashlib.sha256(canonical(payload).encode()).hexdigest()
