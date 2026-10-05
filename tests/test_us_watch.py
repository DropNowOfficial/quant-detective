from datetime import datetime, timedelta, timezone

from market_data.us_watch import classify, _daily_metrics, _intraday_metrics


def daily():
    return {
        "ma5": 100.0,
        "ma5_slope_1d": 1.0,
        "ma5_3point_slope": 0.8,
        "ma10_direction": "UP",
        "ma20_direction": "UP",
        "atr5": 10.0,
        "prior_close": 100.0,
    }


def intra(**changes):
    out = {
        "current_price": 101.0,
        "current_session": "RTH",
        "change_pct": 1.0,
        "premarket_change_pct": 0.4,
        "open_gap_pct": 0.3,
        "move_from_rth_open_pct": 0.7,
        "d5_atr": 0.1,
        "two_completed_5m_above_vwap_and_ma5": False,
        "same_time_rvol": 1.0,
    }
    out.update(changes)
    return out


def test_leader_detection_is_separate_from_entry_gate():
    result = classify(daily(), intra(current_price=113, change_pct=4.0, d5_atr=1.3), broad_market_change=0.5, qqq_change=0.6)
    assert result["leader_detected"]
    assert result["state"] == "LEADER_HOT_NO_CHASE"
    assert not result["standard_entry_geometry"]


def test_entry_armed_is_visible_before_full_confirmation():
    result = classify(daily(), intra(change_pct=0.2, d5_atr=0.15))
    assert result["state"] == "ENTRY_ARMED"
    assert not result["leader_detected"]


def test_entry_confirmed_requires_two_completed_bars_above_vwap_and_ma5():
    result = classify(daily(), intra(d5_atr=0.05, two_completed_5m_above_vwap_and_ma5=True, same_time_rvol=1.1))
    assert result["state"] == "ENTRY_CONFIRMED"


def test_missing_rvol_blocks_entry_confirmation():
    result = classify(daily(), intra(d5_atr=0.05, two_completed_5m_above_vwap_and_ma5=True, same_time_rvol=None))
    assert result["state"] == "ENTRY_ARMED"


def test_premarket_move_can_trigger_leader_even_before_rth():
    result = classify(daily(), intra(current_session="PRE", change_pct=0.9, premarket_change_pct=1.4, d5_atr=0.5))
    assert result["leader_detected"]
    assert any("premarket" in x for x in result["leader_reasons"])


def test_daily_metrics_exclude_current_incomplete_day():
    now = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
    rows = []
    # Enough prior completed sessions plus a deliberately absurd current-day bar.
    start = datetime(2026, 9, 1, 13, 30, tzinfo=timezone.utc)
    for i in range(24):
        close = 100 + i
        stamp = int((start + timedelta(days=i)).timestamp())
        rows.append({"t": stamp, "open": close-1, "high": close+2, "low": close-2, "close": close, "volume": 1000})
    # 2026-10-05 intraday/daily in-progress bar must not contaminate completed-day MA values.
    rows.append({"t": int(datetime(2026,10,5,13,30,tzinfo=timezone.utc).timestamp()), "open": 999, "high": 1001, "low": 998, "close": 1000, "volume": 9999})
    metrics = _daily_metrics(rows, now)
    assert metrics["ma5"] < 200
    assert metrics["prior_close"] < 200


def test_intraday_uses_premarket_but_rth_vwap_only_uses_rth():
    now = datetime(2026, 10, 5, 14, 11, tzinfo=timezone.utc)
    def bar(h, m, price, volume=100):
        t=int(datetime(2026,10,5,h,m,tzinfo=timezone.utc).timestamp())
        return {"t":t,"open":price,"high":price+0.1,"low":price-0.1,"close":price,"volume":volume}
    # UTC 08:00 = 04:00 ET premarket. UTC 13:30 = 09:30 ET RTH.
    rows=[bar(8,0,102,100000), bar(13,30,100.5,100), bar(13,35,100.6,100)]
    metrics=_intraday_metrics(rows,daily(),now)
    assert metrics["premarket_last"] == 102
    assert metrics["rth_vwap_approx"] < 101


def test_relative_strength_vs_spy_can_trigger_leader():
    result = classify(daily(), intra(change_pct=0.9, premarket_change_pct=0.1, open_gap_pct=0.1,
                                     move_from_rth_open_pct=0.2, d5_atr=0.3), broad_market_change=0.2, qqq_change=0.4)
    assert result["leader_detected"]
    assert result["relative_change_vs_spy_pp"] > 0.5
    assert any("vs SPY" in x for x in result["leader_reasons"])


def test_one_percent_day_move_is_not_silenced():
    result = classify(daily(), intra(change_pct=1.05, premarket_change_pct=0.0, open_gap_pct=0.0,
                                     move_from_rth_open_pct=0.1, d5_atr=0.6), broad_market_change=0.7, qqq_change=0.8)
    assert result["leader_detected"]
    assert result["state"] == "LEADER_HOT_NO_CHASE"


def test_old_premarket_gap_does_not_keep_failed_rth_move_as_leader():
    result = classify(
        daily(),
        intra(
            current_session="RTH",
            change_pct=-2.0,
            premarket_change_pct=1.4,
            open_gap_pct=1.1,
            move_from_rth_open_pct=-3.0,
            d5_atr=0.1,
        ),
        broad_market_change=0.4,
        qqq_change=0.5,
    )
    assert not result["leader_detected"]
    assert "premarket +1.40%" in result["event_context"]
    assert "open gap +1.10%" in result["event_context"]
    assert result["state"] == "ENTRY_ARMED"
