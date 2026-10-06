"""Fixed dates verify the adapter, including real XNYS holiday/DST rules."""
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from market_data import session_clock
from market_data.quality import BarFact, BarQualityInput, evaluate_bars
from market_data.quality_profiles import US_PUBLIC_5M_POLICY
from market_data.session_clock import SessionSchedule, schedule_at

ET = ZoneInfo("America/New_York")


def stamp(date, hour, minute=0, second=0):
    return int(datetime.fromisoformat(date).replace(hour=hour, minute=minute, second=second, tzinfo=ET).timestamp() * 1000)


def schedule(now):
    return schedule_at(now, calendar_name="XNYS", interval_ms=300_000, grace_ms=60_000)


def test_publication_grace_determines_expected_completed_watermark():
    early = schedule(stamp("2026-10-06", 9, 35, 59))
    due = schedule(stamp("2026-10-06", 9, 36))
    assert early.phase == "REGULAR"
    assert early.expected_completed_ends == ()
    assert due.expected_completed_ends == (stamp("2026-10-06", 9, 35),)
    assert due.previous_session == "2026-10-05"


def test_half_day_and_dst_use_session_clock():
    half = schedule(stamp("2026-11-27", 12, 59))
    assert half.open_ms == stamp("2026-11-27", 9, 30)
    assert half.close_ms == stamp("2026-11-27", 13)
    assert schedule(stamp("2026-11-27", 13)).phase == "AFTER_HOURS"
    before_dst = schedule(stamp("2026-03-06", 10))
    after_dst = schedule(stamp("2026-03-09", 10))
    assert datetime.fromtimestamp(before_dst.open_ms / 1000, timezone.utc).isoformat() == "2026-03-06T14:30:00+00:00"
    assert datetime.fromtimestamp(after_dst.open_ms / 1000, timezone.utc).isoformat() == "2026-03-09T13:30:00+00:00"
    # Synthetic short session verifies core deadlines independently of the adapter.
    start = stamp("2026-11-27", 12, 45)
    end = stamp("2026-11-27", 13)
    now = stamp("2026-11-27", 12, 56, 30)
    clock = SessionSchedule("REGULAR", start, end, "2026-11-25", (start + 300_000, start + 600_000))
    bars = (BarFact(start, start + 300_000, True), BarFact(start + 300_000, start + 600_000, True))
    result = evaluate_bars(BarQualityInput(now, now, bars, 0, 20, "2026-11-25"), US_PUBLIC_5M_POLICY, clock)
    assert result.confirmation_ok is True
    assert result.valid_until_ms == stamp("2026-11-27", 12, 58, 30)


@pytest.mark.parametrize("date,previous", [("2026-10-10", "2026-10-09"), ("2026-07-03", "2026-07-02")])
def test_weekend_and_exchange_holiday_are_closed(date, previous):
    result = schedule(stamp(date, 10))
    assert result.phase == "CLOSED"
    assert result.open_ms is None
    assert result.close_ms is None
    assert result.previous_session == previous
    assert result.expected_completed_ends == ()


def test_premarket_does_not_expect_today_uncompleted_bars():
    result = schedule(stamp("2026-10-06", 8))
    assert result.phase == "PREMARKET"
    assert result.expected_completed_ends == ()
    assert result.previous_session == "2026-10-05"


def test_calendar_error_is_explicit_and_does_not_guess_weekdays(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic calendar unavailable")
    monkeypatch.setattr(session_clock, "_load_xnys_calendar", unavailable)
    result = schedule(stamp("2026-10-06", 10))
    assert result == SessionSchedule("CALENDAR_ERROR", None, None, None, ())


def test_unknown_calendar_is_not_treated_as_continuous():
    result = schedule_at(1_000_000, calendar_name="INVALID", interval_ms=60_000, grace_ms=15_000)
    assert result.phase == "CALENDAR_ERROR"


def test_continuous_schedule_crosses_midnight_without_losing_window():
    now = int(datetime(2026, 10, 6, 0, 0, 20, tzinfo=timezone.utc).timestamp() * 1000)
    result = schedule_at(now, calendar_name="CONTINUOUS", interval_ms=60_000, grace_ms=15_000)
    assert result.phase == "CONTINUOUS"
    assert result.open_ms is None
    assert result.close_ms is None
    assert result.expected_completed_ends == (now - 20_000,)


def test_clock_result_is_deterministic_and_frozen():
    now = stamp("2026-10-06", 10)
    result = schedule(now)
    assert result == schedule(now)
    with pytest.raises(FrozenInstanceError):
        result.phase = "CLOSED"


@pytest.mark.parametrize("kwargs", [
    {"now_ms": True}, {"now_ms": -1}, {"interval_ms": 0}, {"grace_ms": -1},
])
def test_invalid_clock_configuration_fails_closed(kwargs):
    args = {"now_ms": 1_000_000, "calendar_name": "CONTINUOUS", "interval_ms": 60_000, "grace_ms": 15_000, **kwargs}
    assert schedule_at(**args).phase == "CALENDAR_ERROR"
