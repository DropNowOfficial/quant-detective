"""Explicit-time exchange schedules. Calendar failures never guess weekdays.

REGULAR and CONTINUOUS are usable trading phases. XNYS pre/post-market
and closed days retain their own phases. CALENDAR_ERROR is fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class SessionSchedule:
    phase: str
    open_ms: int | None
    close_ms: int | None
    previous_session: str | None
    expected_completed_ends: tuple[int, ...]


def _load_xnys_calendar(start: str, end: str):
    import exchange_calendars

    # Explicit bounds avoid the dependency's wall-clock-derived default range.
    return exchange_calendars.get_calendar("XNYS", start=start, end=end)


def _timestamp_ms(timestamp) -> int:
    return int(timestamp.value // 1_000_000)


def schedule_at(now_ms: int, *, calendar_name: str, interval_ms: int,
                grace_ms: int) -> SessionSchedule:
    """Return the bar ends due by ``now_ms`` after the publication grace.

    Continuous markets have no artificial midnight session boundary: their
    singleton expected watermark is epoch-aligned, and consumers check their
    required trailing window. XNYS supplies all due ends in today's RTH session.
    """
    error = SessionSchedule("CALENDAR_ERROR", None, None, None, ())
    if (type(now_ms) is not int or now_ms < 0 or type(interval_ms) is not int
            or interval_ms <= 0 or type(grace_ms) is not int or grace_ms < 0):
        return error
    if calendar_name == "CONTINUOUS":
        end = ((now_ms - grace_ms) // interval_ms) * interval_ms
        return SessionSchedule("CONTINUOUS", None, None, None, (end,) if end > 0 else ())
    if calendar_name != "XNYS":
        return error
    try:
        now = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=now_ms)
        day = now.astimezone(ZoneInfo("America/New_York")).date()
        calendar = _load_xnys_calendar((day - timedelta(days=370)).isoformat(),
                                       (day + timedelta(days=7)).isoformat())
        session = calendar.date_to_session(day.isoformat(), direction="previous")
        session_id = session.date().isoformat()
        if session_id != day.isoformat():
            return SessionSchedule("CLOSED", None, None, session_id, ())
        previous = calendar.previous_session(session).date().isoformat()
        open_ms = _timestamp_ms(calendar.session_open(session))
        close_ms = _timestamp_ms(calendar.session_close(session))
        if now_ms < open_ms:
            phase = "PREMARKET"
        elif now_ms < close_ms:
            phase = "REGULAR"
        else:
            phase = "AFTER_HOURS"
        due = min(now_ms - grace_ms, close_ms)
        ends = tuple(range(open_ms + interval_ms, due + 1, interval_ms))
        # Some supported intervals can leave a shorter final exchange bar.
        if due >= close_ms and (close_ms - open_ms) % interval_ms:
            ends += (close_ms,)
        return SessionSchedule(phase, open_ms, close_ms, previous, ends)
    except Exception:
        # Missing/out-of-range/broken calendars are unavailable, never a weekday
        # approximation. Callers get a stable explicit error representation.
        return error
