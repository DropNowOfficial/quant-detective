"""Explicit synthetic range demonstration, not the user's production strategy."""
from decimal import Decimal
from dataclasses import replace
from .contracts import Decision, RuleState, event_id


def evaluate(bar,spec,state,quality,*,position_basis=None):
    evidence=dict(instrument_key=bar.instrument.key(),rule_id=spec.id,rule_version=spec.version,purpose=spec.purpose,
        ohlc={k:str(getattr(bar,k)) for k in ('open','high','low','close')},
        bounds={k:str(getattr(spec,k)) if getattr(spec,k) is not None else None for k in ('lower','upper')},
        protection_fraction=str(spec.protection_fraction) if spec.protection_fraction is not None else None,
        start_ms=bar.start_ms,end_ms=bar.end_ms,quality_reason=quality.reason)
    def result(kind,code,next_state=None):
        return Decision(kind,code,event_id(bar,spec,code),evidence,next_state or state)
    if not quality.eligible:
        kind='health' if quality.reason in ('gap','out_of_order','conflicting_duplicate','source_changed','future_data','stale','session_unknown') and state.health!=quality.reason else 'suppressed'
        return result(kind,'quality_'+quality.reason,replace(state,health=quality.reason))
    if spec.purpose=='protection' and spec.protection_fraction is None:
        return result('suppressed','rule_incomplete')
    if spec.purpose!='entry': return result('suppressed','unsupported_rule')
    if any(not isinstance(v,Decimal) or not v.is_finite() or v<0 for v in (spec.lower,spec.upper)):
        return result('suppressed','rule_incomplete')
    if spec.lower>spec.upper: return result('suppressed','invalid_range')
    inside=bar.high>=spec.lower and bar.low<=spec.upper
    next_state=RuleState(bar.end_ms,inside,'valid')
    if not inside: return result('no_signal','range_not_touched',next_state)
    if state.in_zone: return result('no_signal','already_in_zone',next_state)
    return result('signal','range_entered',next_state)
