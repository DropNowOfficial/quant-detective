"""Conservative quality state: only explicit offline repair clears faults."""
from dataclasses import dataclass, replace, asdict
import hashlib
from .contracts import canonical

@dataclass(frozen=True)
class QualityState:
    instrument_key: str
    last_complete_end_ms: int|None = None
    last_fingerprint: str|None = None
    paused: bool = False
    reason: str|None = None

@dataclass(frozen=True)
class QualityResult:
    eligible: bool
    reason: str
    state: QualityState


def fingerprint(bar):
    data=asdict(bar); data.pop('received_ms')
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def check_bar(bar,state,*,now_ms,stale_after_ms,session_open):
    if type(now_ms) is not int or type(stale_after_ms) is not int or stale_after_ms<0:
        raise ValueError('invalid freshness configuration')
    if session_open is not None and type(session_open) is not bool:
        raise ValueError('session_open must be boolean or null')
    def reject(reason,pause=True):
        return QualityResult(False,reason,replace(state,paused=True,reason=reason) if pause else state)
    if bar.instrument.key()!=state.instrument_key: return reject('source_changed')
    if bar.received_ms>now_ms or (bar.complete and bar.end_ms>now_ms): return reject('future_data')
    if not bar.complete: return reject('incomplete',False)
    fp=fingerprint(bar)
    if state.last_complete_end_ms==bar.end_ms:
        if state.last_fingerprint==fp: return reject('duplicate',False)
        return reject('conflicting_duplicate')
    if state.last_complete_end_ms is not None and bar.end_ms<state.last_complete_end_ms:
        return reject('out_of_order')
    if session_open is None: return reject('session_unknown')
    if not session_open: return reject('market_closed',False)
    if now_ms-bar.end_ms>stale_after_ms: return reject('stale')
    if state.paused: return reject('paused',False)
    if state.last_complete_end_ms is not None and bar.start_ms!=state.last_complete_end_ms:
        return reject('gap')
    return QualityResult(True,'valid',QualityState(state.instrument_key,bar.end_ms,fp))


def repair_sequence(bars,checkpoint):
    """Validate <=10,000 supplied closed bars; no invented calendar or live recovery."""
    if not bars or len(bars)>10000:
        return QualityResult(False,'invalid_repair_batch',checkpoint)
    current=replace(checkpoint,paused=False,reason=None)
    for bar in sorted(bars,key=lambda b:(b.start_ms,b.end_ms)):
        result=check_bar(bar,current,now_ms=bar.received_ms,stale_after_ms=max(0,bar.received_ms-bar.end_ms),session_open=True)
        if not result.eligible and result.reason!='duplicate':
            return QualityResult(False,result.reason,replace(checkpoint,paused=True,reason=result.reason))
        current=result.state
    if current.last_complete_end_ms==checkpoint.last_complete_end_ms:
        return QualityResult(False,'no_repair_progress',checkpoint)
    return QualityResult(True,'repaired',current)
