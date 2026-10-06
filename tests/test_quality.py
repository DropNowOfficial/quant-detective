"""Synthetic, explicit-time quality decisions; no market data or network."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from market_data.quality import (
    BarFact, BarQualityInput, QualityPolicy, evaluate_bars, evaluate_quote,
)
from market_data.quality_profiles import (
    IBKR_QUOTE_POLICY, MINUTE_POLICY, US_PUBLIC_5M_POLICY,
    ibkr_quote_policy, minute_policy,
)
from market_data.session_clock import SessionSchedule, schedule_at

ET = ZoneInfo("America/New_York")


def ms(hour, minute, second=0):
    return int(datetime(2026, 10, 6, hour, minute, second, tzinfo=ET).timestamp() * 1000)


def us_schedule(now):
    return schedule_at(now, calendar_name="XNYS", interval_ms=300_000, grace_ms=60_000)


def fact(hour, minute, **kwargs):
    end = ms(hour, minute)
    return BarFact(end - 300_000, end, **{"closed_at_capture": True, **kwargs})


def data(now, bars, **kwargs):
    return BarQualityInput(**{
        "now_ms": now, "captured_at_ms": now, "bars": tuple(bars),
        "invalid_rows": 0, "complete_rvol_sessions": 20,
        "last_daily_session": "2026-10-05", **kwargs,
    })


def test_old_bar_is_not_refreshed_by_new_receipt():
    now = ms(15, 0)
    result = evaluate_bars(data(now, [fact(9, 35)]), US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert result.confirmation_ok is False
    assert "STALE_BAR" in result.reason_codes
    assert result.last_bar_end_ms == ms(9, 35)


def test_open_at_capture_never_ages_into_completed():
    bars = [fact(9, 35), fact(9, 40, closed_at_capture=False)]
    captured = ms(9, 40, 30)
    initial = evaluate_bars(data(captured, bars), US_PUBLIC_5M_POLICY, us_schedule(captured))
    later = ms(9, 41, 30)
    waited = evaluate_bars(data(later, bars, captured_at_ms=captured), US_PUBLIC_5M_POLICY, us_schedule(later))
    completed_at_capture = (initial.sample_count, initial.last_bar_end_ms)
    completed_after_wait = (waited.sample_count, waited.last_bar_end_ms)
    assert completed_after_wait == completed_at_capture == (1, ms(9, 35))
    assert waited.confirmation_ok is False
    assert "STALE_BAR" in waited.reason_codes


def test_fill_forward_does_not_advance_watermark():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40, is_fill_forward=True)]),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.last_bar_end_ms == ms(9, 35)
    assert result.sample_count == 1
    assert result.confirmation_ok is False
    assert "STALE_BAR" in result.reason_codes


def test_public_5m_is_not_subject_to_36s_ttl():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)]),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is True
    assert result.confirmation_ok is True
    assert result.reason_codes == ()
    assert result.sample_count == 2
    assert result.expected_bar_end_ms == ms(9, 40)
    assert result.valid_until_ms == ms(9, 43, 30)


def test_only_one_bar_is_observable_but_not_confirmable():
    now = ms(9, 36, 30)
    result = evaluate_bars(data(now, [fact(9, 35)]), US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is True
    assert result.confirmation_ok is False
    assert "INSUFFICIENT_COMPLETED_BARS" in result.reason_codes


def test_rvol_needs_twenty_complete_sessions():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)], complete_rvol_sessions=19),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is True
    assert result.confirmation_ok is False
    assert "INSUFFICIENT_RVOL_HISTORY" in result.reason_codes


def test_daily_session_must_match_official_previous_session():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)], last_daily_session="2026-10-02"),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert "STALE_DAILY_SESSION" in result.reason_codes


def test_missing_bar_in_required_session_window_is_rejected():
    now = ms(9, 46, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 45)]),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert "BAR_GAP" in result.reason_codes


def test_invalid_rows_cannot_be_hidden_by_valid_bars():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)], invalid_rows=1),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert "INVALID_ROWS" in result.reason_codes


@pytest.mark.parametrize("bad", [
    BarFact(ms(9, 35), ms(9, 35), True),
    BarFact(True, ms(9, 35), True),
    BarFact(ms(9, 30), ms(9, 35), "yes"),
    BarFact(ms(9, 30), ms(9, 35), True, "yes"),
])
def test_malformed_facts_fail_closed_without_silent_dropping(bad):
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40), bad]),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert "INVALID_BAR" in result.reason_codes


def test_duplicate_bars_do_not_count_as_two_completed_bars():
    now = ms(9, 36, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 35)]),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.sample_count == 1
    assert result.confirmation_ok is False
    assert "DUPLICATE_BAR" in result.reason_codes


@pytest.mark.parametrize("changes,reason", [
    ({"captured_at_ms": ms(9, 41, 30) + 5_001}, "FUTURE_CAPTURE"),
    ({"captured_at_ms": ms(9, 39, 29)}, "STALE_RESPONSE"),
    ({"invalid_rows": -1}, "INVALID_INPUT"),
    ({"complete_rvol_sessions": True}, "INVALID_INPUT"),
])
def test_bad_capture_and_metadata_fail_closed(changes, reason):
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)], **changes),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert reason in result.reason_codes


def test_closed_bar_cannot_end_after_capture():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)], captured_at_ms=ms(9, 39, 59)),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert "BAR_AFTER_CAPTURE" in result.reason_codes


def test_future_bar_fails_closed():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40), fact(9, 45)]),
                           US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert "FUTURE_BAR" in result.reason_codes


def test_valid_until_is_earliest_response_or_next_bar_deadline():
    now = ms(9, 44, 30)
    bars = [fact(9, 35), fact(9, 40)]
    result = evaluate_bars(data(now, bars), US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.confirmation_ok is True
    assert result.valid_until_ms == ms(9, 46)
    due = ms(9, 46)
    expired = evaluate_bars(data(due, bars, captured_at_ms=now), US_PUBLIC_5M_POLICY, us_schedule(due))
    assert expired.confirmation_ok is False
    assert "STALE_BAR" in expired.reason_codes


def test_calendar_unavailable_fails_closed():
    now = ms(9, 41, 30)
    unavailable = SessionSchedule("CALENDAR_ERROR", None, None, None, ())
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)]), US_PUBLIC_5M_POLICY, unavailable)
    assert result.observation_ok is False
    assert result.confirmation_ok is False
    assert result.valid_until_ms is None
    assert "CALENDAR_UNAVAILABLE" in result.reason_codes


@pytest.mark.parametrize("phase", ["PREMARKET", "AFTER_HOURS", "CLOSED", "UNKNOWN"])
def test_non_trading_phase_cannot_confirm(phase):
    now = ms(9, 41, 30)
    schedule = replace(us_schedule(now), phase=phase)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)]), US_PUBLIC_5M_POLICY, schedule)
    assert result.observation_ok is False
    assert result.confirmation_ok is False
    assert "SESSION_NOT_OPEN" in result.reason_codes


def test_same_inputs_same_result():
    now = ms(9, 41, 30)
    input_ = data(now, [fact(9, 35), fact(9, 40)])
    schedule = us_schedule(now)
    assert evaluate_bars(input_, US_PUBLIC_5M_POLICY, schedule) == evaluate_bars(input_, US_PUBLIC_5M_POLICY, schedule)


def test_quality_records_are_immutable():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)]), US_PUBLIC_5M_POLICY, us_schedule(now))
    for record, field in [(fact(9, 35), "end_ms"), (US_PUBLIC_5M_POLICY, "interval_ms"),
                          (data(now, []), "now_ms"), (result, "state")]:
        with pytest.raises(FrozenInstanceError):
            setattr(record, field, None)


@pytest.mark.parametrize("interval", [60_000, 300_000, 900_000])
def test_minute_profile_preserves_ttl_age_and_sixty_one_bar_window(interval):
    policy = minute_policy(interval)
    end = 100 * interval
    now = end + 16_000
    bars = tuple(BarFact(n * interval, (n + 1) * interval, True) for n in range(39, 100))
    input_ = BarQualityInput(now, now, bars, 0, 0, None)
    schedule = schedule_at(now, calendar_name="CONTINUOUS", interval_ms=interval, grace_ms=15_000)
    good = evaluate_bars(input_, policy, schedule)
    assert good.confirmation_ok is True
    assert good.sample_count == 61
    assert policy.response_max_age_ms == 36_000
    assert policy.bar_age_limit_ms == interval + 15_000
    assert MINUTE_POLICY == minute_policy(60_000)
    old_response = evaluate_bars(replace(input_, captured_at_ms=now - 36_001), policy, schedule)
    assert "STALE_RESPONSE" in old_response.reason_codes
    assert old_response.observation_ok is False
    insufficient = evaluate_bars(replace(input_, bars=bars[1:]), policy, schedule)
    assert insufficient.confirmation_ok is False
    assert "INSUFFICIENT_COMPLETED_BARS" in insufficient.reason_codes


def test_continuous_minimum_window_rejects_internal_gap():
    interval = 60_000
    now = 6_016_000
    bars = tuple(BarFact(n * interval, (n + 1) * interval, True) for n in range(38, 100) if n != 75)
    result = evaluate_bars(BarQualityInput(now, now, bars, 0, 0, None), minute_policy(interval),
                           schedule_at(now, calendar_name="CONTINUOUS", interval_ms=interval, grace_ms=15_000))
    assert result.observation_ok is False
    assert "BAR_GAP" in result.reason_codes


def quote(**changes):
    return {"last": 100.0, "last_status": "TRADE", "market_data_availability": "R",
            "updated_ms": 1_000_000, **changes}


def test_quote_freshness_comes_from_source_updated_ms():
    good = evaluate_quote(quote(), now_ms=1_004_000, max_age_ms=6_000)
    assert good.confirmation_ok is True
    assert good.valid_until_ms == 1_006_000
    assert good.last_bar_end_ms == 1_000_000
    stale = evaluate_quote(quote(captured_at_ms=1_007_000), now_ms=1_007_000, max_age_ms=6_000)
    assert stale.observation_ok is False
    assert "STALE_QUOTE" in stale.reason_codes


@pytest.mark.parametrize("changes,reason", [
    ({"updated_ms": None}, "INVALID_QUOTE_TIMESTAMP"),
    ({"updated_ms": True}, "INVALID_QUOTE_TIMESTAMP"),
    ({"updated_ms": 1_000_000.0}, "INVALID_QUOTE_TIMESTAMP"),
    ({"updated_ms": 0}, "INVALID_QUOTE_TIMESTAMP"),
    ({"updated_ms": 1_010_001}, "FUTURE_QUOTE"),
    ({"last": float("inf")}, "INVALID_QUOTE_PRICE"),
    ({"last": -1}, "INVALID_QUOTE_PRICE"),
    ({"last": True}, "INVALID_QUOTE_PRICE"),
    ({"last_status": "HALTED"}, "QUOTE_NOT_TRADE"),
    ({"last_status": None}, "QUOTE_NOT_TRADE"),
    ({"market_data_availability": "D"}, "QUOTE_NOT_REALTIME"),
    ({"market_data_availability": None}, "QUOTE_NOT_REALTIME"),
])
def test_quote_bad_source_evidence_fails_closed(changes, reason):
    result = evaluate_quote(quote(**changes), now_ms=1_005_000, max_age_ms=6_000)
    assert result.observation_ok is False
    assert result.confirmation_ok is False
    assert result.valid_until_ms is None
    assert reason in result.reason_codes


def test_optional_quote_event_time_is_an_independent_expiry():
    result = evaluate_quote(quote(event_ms=998_000), now_ms=1_002_000, max_age_ms=6_000)
    assert result.confirmation_ok is True
    assert result.valid_until_ms == 1_004_000
    stale = evaluate_quote(quote(event_ms=990_000), now_ms=1_002_000, max_age_ms=6_000)
    assert stale.confirmation_ok is False
    assert "STALE_QUOTE_EVENT" in stale.reason_codes


def test_ibkr_profile_uses_three_snapshot_periods():
    assert IBKR_QUOTE_POLICY.response_max_age_ms == 6_000
    assert ibkr_quote_policy(3).response_max_age_ms == 9_000
    with pytest.raises(ValueError):
        ibkr_quote_policy(0)
    with pytest.raises(ValueError):
        minute_policy(0)


@pytest.mark.parametrize("changes", [
    {"open_ms": "bad"}, {"close_ms": "bad"}, {"close_ms": None},
    {"open_ms": ms(10, 0), "close_ms": ms(9, 30)},
    {"expected_completed_ends": (ms(9, 35), ms(9, 35))},
    {"expected_completed_ends": (ms(9, 45),)},
])
def test_malformed_schedule_is_a_quality_veto(changes):
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)]), US_PUBLIC_5M_POLICY,
                           replace(us_schedule(now), **changes))
    assert result.observation_ok is False
    assert "INVALID_SCHEDULE" in result.reason_codes


def test_continuous_schedule_cannot_carry_malformed_session_bounds():
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)]), US_PUBLIC_5M_POLICY,
                           SessionSchedule("CONTINUOUS", None, "bad", None, ()))
    assert result.observation_ok is False
    assert "INVALID_SCHEDULE" in result.reason_codes


def test_no_completed_bars_cannot_be_observed_or_confirmed():
    now = ms(9, 36, 30)
    result = evaluate_bars(data(now, []), US_PUBLIC_5M_POLICY, us_schedule(now))
    assert result.observation_ok is False
    assert result.sample_count == 0
    assert result.last_bar_end_ms is None
    assert "NO_COMPLETED_BARS" in result.reason_codes


def test_fresh_bar_cannot_hide_expired_bar_age_policy():
    now = ms(9, 41, 30)
    policy = replace(US_PUBLIC_5M_POLICY, bar_age_limit_ms=80_000)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)]), policy, us_schedule(now))
    assert result.observation_ok is False
    assert "STALE_BAR" in result.reason_codes


@pytest.mark.parametrize("changes", [{"interval_ms": 0}, {"response_max_age_ms": True},
                                     {"min_completed_bars": -1}, {"publication_grace_ms": -1}])
def test_invalid_policy_never_defaults_to_success(changes):
    now = ms(9, 41, 30)
    result = evaluate_bars(data(now, [fact(9, 35), fact(9, 40)]),
                           replace(US_PUBLIC_5M_POLICY, **changes), us_schedule(now))
    assert result.observation_ok is False
    assert "INVALID_POLICY" in result.reason_codes


@pytest.mark.parametrize("event,reason", [(None, "INVALID_QUOTE_EVENT_TIMESTAMP"),
                                         (1_011_000, "FUTURE_QUOTE_EVENT")])
def test_invalid_optional_quote_event_fails_closed(event, reason):
    result = evaluate_quote(quote(event_ms=event), now_ms=1_005_000, max_age_ms=6_000)
    assert result.confirmation_ok is False
    assert reason in result.reason_codes


def test_price_too_large_for_finite_float_check_fails_closed():
    result = evaluate_quote(quote(last=10 ** 1000), now_ms=1_005_000, max_age_ms=6_000)
    assert result.observation_ok is False
    assert "INVALID_QUOTE_PRICE" in result.reason_codes


def test_snapshot_period_overflow_is_rejected_as_configuration_error():
    with pytest.raises(ValueError):
        ibkr_quote_policy(1e308)
