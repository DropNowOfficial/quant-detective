"""Validate daemon liveness evidence; this does not certify quote coverage."""
from datetime import datetime, timezone
import math

MODES = {'IBKR_LIVE_PLUS_PUBLIC_STRUCTURE', 'DEGRADED_PUBLIC_ONLY'}


def state_health(data, max_age, now=None):
    if not isinstance(max_age, (int, float)) or not math.isfinite(max_age) or max_age <= 0:
        raise ValueError('INVALID_MAX_AGE')
    if not isinstance(data, dict) or data.get('mode') not in MODES:
        raise ValueError('INVALID_RUNTIME_MODE')
    run_id = data.get('run_id')
    if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128 or not run_id.isascii() or any(c.isspace() or not c.isprintable() for c in run_id):
        raise ValueError('INVALID_RUN_ID')
    try:
        stamp = datetime.fromisoformat(data['generated_at_utc'])
        if stamp.utcoffset() is None:
            raise ValueError
        age = ((now or datetime.now(timezone.utc)) - stamp.astimezone(timezone.utc)).total_seconds()
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError('INVALID_STATE_TIME') from None
    if age < -5 or age > max_age:
        raise ValueError('STATE_TIME_OUT_OF_RANGE')
    return age
