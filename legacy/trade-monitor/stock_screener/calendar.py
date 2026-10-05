"""Explicit exchange sessions and conservative completed-day availability."""
from functools import lru_cache
import exchange_calendars as xc
import pandas as pd


@lru_cache(maxsize=1)
def exchange_calendar():
    return xc.get_calendar('XNYS', start='2000-01-01', end='2035-12-31')


def aware_timestamp(value):
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('截止时间必须为带时区的 ISO 时间') from exc
    if pd.isna(ts) or ts.tzinfo is None:
        raise ValueError('截止时间必须带时区，例如 2026-10-04T03:40:00Z')
    return ts.tz_convert('UTC')


def last_completed_session(as_of):
    stamp = aware_timestamp(as_of)
    cal = exchange_calendar()
    local_day = pd.Timestamp(stamp.tz_convert('America/New_York').date())
    if local_day < cal.first_session or local_day > cal.last_session:
        raise ValueError('日期超出交易日历支持范围 2000–2035')
    session = cal.date_to_session(local_day, direction='previous')
    if cal.session_close(session) + pd.Timedelta(minutes=30) > stamp:
        session = cal.previous_session(session)
    return session


def selected_session(as_of, session=None):
    latest = last_completed_session(as_of)
    if session is None:
        return latest, latest
    try:
        target = pd.Timestamp(session)
        if target.tzinfo is not None or target != target.normalize():
            raise ValueError('历史日期应为 YYYY-MM-DD')
        if not exchange_calendar().is_session(target):
            raise ValueError('所选日期不是美股交易日')
        if target > latest:
            raise ValueError('所选交易日尚未完成，或仍在收盘缓冲期')
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    return target, latest


def required_sessions(target, count=61):
    cal = exchange_calendar()
    index = cal.sessions.get_loc(target)
    if index < count-1:
        raise ValueError(f'交易日历内不足 {count} 个先前/当前会话')
    return cal.sessions[index-count+1:index+1]
