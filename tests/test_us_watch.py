from datetime import datetime, timedelta, timezone
from functools import lru_cache
import json

import pytest

from market_data import cli, github_alerts, us_watch
from market_data.us_watch import classify, _daily_metrics, _intraday_metrics
from market_data.quality import QualityResult
from market_data.session_clock import schedule_at


def valid_quality():
    return QualityResult("VALID", (), True, True, 1791223860000, 1791223980000,
                         1791223800000, 1791223800000, 2)


def classified(*args, **kwargs):
    return classify(*args, quality=valid_quality(), **kwargs)


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
    result = classified(daily(), intra(current_price=113, change_pct=4.0, d5_atr=1.3), qqq_change=0.5)
    assert result["leader_detected"]
    assert result["state"] == "LEADER_HOT_NO_CHASE"
    assert not result["standard_entry_geometry"]


def test_entry_armed_is_visible_before_full_confirmation():
    result = classified(daily(), intra(change_pct=0.2, d5_atr=0.15))
    assert result["state"] == "ENTRY_ARMED"
    assert not result["leader_detected"]


def test_entry_confirmed_requires_two_completed_bars_above_vwap_and_ma5():
    result = classified(daily(), intra(d5_atr=0.05, two_completed_5m_above_vwap_and_ma5=True, same_time_rvol=1.1))
    assert result["state"] == "ENTRY_CONFIRMED"


def test_missing_rvol_blocks_entry_confirmation():
    result = classified(daily(), intra(d5_atr=0.05, two_completed_5m_above_vwap_and_ma5=True, same_time_rvol=None))
    assert result["state"] == "ENTRY_ARMED"


def test_captured_premarket_move_remains_independent_leader_condition():
    result = classified(daily(), intra(change_pct=0.9, premarket_change_pct=1.4, d5_atr=0.5))
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
    metrics=_intraday_metrics(rows,daily(),now,captured_at_ms=int(now.timestamp()*1000))
    assert metrics["premarket_last"] == 102
    assert metrics["rth_vwap_approx"] < 101


def test_relative_strength_vs_qqq_can_trigger_leader():
    result = classified(daily(), intra(change_pct=0.9, premarket_change_pct=0.1, open_gap_pct=0.1,
                                     move_from_rth_open_pct=0.2, d5_atr=0.3), qqq_change=0.2)
    assert result["leader_detected"]
    assert result["relative_change_vs_qqq_pp"] > 0.5
    assert any("vs QQQ" in x for x in result["leader_reasons"])


def test_one_percent_day_move_is_not_silenced():
    result = classified(daily(), intra(change_pct=1.05, premarket_change_pct=0.0, open_gap_pct=0.0,
                                     move_from_rth_open_pct=0.1, d5_atr=0.6), qqq_change=0.7)
    assert result["leader_detected"]
    assert result["state"] == "LEADER_HOT_NO_CHASE"


@pytest.mark.parametrize("rows,incomplete", [
    ([], ["AAA", "BBB"]),
    ([{"symbol": "AAA", "status": "ERROR"}, {"symbol": "BBB", "status": "ERROR"}], ["AAA", "BBB"]),
    ([{"symbol": "AAA", "status": "OK"}, {"symbol": "BBB", "status": "ERROR"}], ["BBB"]),
    ([{"symbol": "AAA", "status": "OK"}], ["BBB"]),
])
def test_incomplete_one_shot_preserves_report_and_publication_before_failing(monkeypatch, tmp_path, rows, incomplete):
    output = tmp_path / "report.json"
    report = {"generated_at_et": "2026-10-05T16:05:00-04:00", "rows": rows,
              "alerts": [{"symbol": "AAA", "state": "LEADER_WATCH"}]}
    monkeypatch.setattr(us_watch, "scan_once", lambda **kwargs: report)
    published = []

    def publish(value):
        assert json.loads(output.read_text()) == value
        published.append(value.copy())

    monkeypatch.setattr(github_alerts, "publish", publish)
    with pytest.raises(RuntimeError, match="one-shot scan incomplete"):
        us_watch.run(symbols=("AAA", "BBB"), once=True, github_alerts=True, output=output)
    saved = json.loads(output.read_text())
    assert saved["rows"] == rows
    assert saved["alerts"] == report["alerts"]
    assert saved["scan_status"] == "INCOMPLETE"
    assert saved["incomplete_symbols"] == incomplete
    assert published == [saved]


def test_complete_one_shot_without_alerts_succeeds(monkeypatch, tmp_path):
    report = {"rows": [{"symbol": "AAA", "status": "OK"}], "alerts": []}
    monkeypatch.setattr(us_watch, "scan_once", lambda **kwargs: report)
    result = us_watch.run(symbols=("AAA",), once=True, output=tmp_path / "report.json")
    assert result["scan_status"] == "COMPLETE"
    assert result["incomplete_symbols"] == []


def test_continuous_scan_retries_after_incomplete_results(monkeypatch):
    failed = {"rows": [{"symbol": "AAA", "status": "ERROR"}], "alerts": []}
    healthy = {"rows": [{"symbol": "AAA", "status": "OK"}], "alerts": []}
    reports = iter([failed, healthy])
    ticks = iter([0, 0, 61])
    monkeypatch.setattr(us_watch, "scan_once", lambda **kwargs: next(reports))
    monkeypatch.setattr(us_watch.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(us_watch.time, "sleep", lambda seconds: None)
    assert us_watch.run(symbols=("AAA",), duration_minutes=1) is healthy


def test_watch_cli_reports_incomplete_one_shot_as_failure(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["qd-market", "watch", "--once", "--symbols", "AAA"])

    def failed_watch(**kwargs):
        raise RuntimeError("one-shot scan incomplete: AAA")

    monkeypatch.setattr(cli, "run_us_watch", failed_watch)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "watch failed: one-shot scan incomplete" in capsys.readouterr().err


# All fixtures are synthetic. The exchange calendar supplies sessions only.
def at(day="2026-10-05", hour=9, minute=41, second=30):
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, second=second, tzinfo=us_watch.ET)


@lru_cache(maxsize=512)
def schedule(now):
    return schedule_at(int(now.timestamp() * 1000), calendar_name="XNYS",
                       interval_ms=300_000, grace_ms=60_000)


def previous_dates(now, count):
    dates = []
    day = now - timedelta(days=1)
    while len(dates) < count:
        if schedule(day.replace(hour=12)).open_ms is not None:
            dates.append(day.date().isoformat())
        day -= timedelta(days=1)
    return list(reversed(dates))


def bars_for(day, count=2, volume=100):
    start = schedule(at(day, 12)).open_ms // 1000
    return [{"t": start + i*300, "open": 100.5 + i*0.1,
             "high": 100.7 + i*0.1, "low": 100.3 + i*0.1,
             "close": 100.6 + i*0.1, "volume": volume}
            for i in range(count)]


def current_rows(now, count=2, volume=100):
    return [dict(r, session="RTH", completed=True) for r in bars_for(now.date().isoformat(), count, volume)]


def daily_rows(now, count=24):
    rows = []
    for i, day in enumerate(previous_dates(now, count)):
        close = 100 - (count-1-i)*0.1
        rows.append({"t": int(at(day, 9, 30, 0).timestamp()), "open": close-1,
                     "high": close+2, "low": close-2, "close": close, "volume": 1000})
    return rows


def yahoo(rows, now, symbol="AAA"):
    return {"data": {"chart": {"result": [{"meta": {
        "symbol": symbol, "instrumentType": "EQUITY", "exchangeTimezoneName": "America/New_York",
        "currency": "USD", "regularMarketPreviousClose": 100},
        "timestamp": [r["t"] for r in rows], "indicators": {"quote": [{
            k: [r[k] for r in rows] for k in ("open", "high", "low", "close", "volume")}]}}]}},
        "receipt": {"received_at_utc": now.astimezone(timezone.utc).isoformat()}}


def scan_fixture(monkeypatch, now, rows=None, history=None, daily_source=None,
                 intraday_capture=None, qqq_context=None, daily_capture=None, history_capture=None):
    monkeypatch.setattr(us_watch, "_DAILY_CACHE", {})
    monkeypatch.setattr(us_watch, "_VOLUME_PROFILE_CACHE", {})
    rows = bars_for(now.date().isoformat()) if rows is None else rows
    history = [r for d in previous_dates(now, 20) for r in bars_for(d)] if history is None else history
    sources = {"1d": yahoo(daily_rows(now) if daily_source is None else daily_source, daily_capture or now),
               "1mo": yahoo(history, history_capture or now), "5d": yahoo(rows, intraday_capture or now)}
    calls = []
    def fetcher(url, kind):
        calls.append(url)
        return sources["1d" if "interval=1d" in url else "1mo" if "range=1mo" in url else "5d"]
    fetcher.sources = sources
    fetcher.calls = calls
    return us_watch._scan_symbol("AAA", fetcher, now, qqq_context=qqq_context), fetcher


def test_1500_with_0935_bars_and_one_rvol_day_cannot_confirm(monkeypatch):
    now = datetime(2026, 10, 5, 19, tzinfo=timezone.utc)
    history = bars_for("2026-10-02")
    result, _ = scan_fixture(monkeypatch, now, history=history)
    assert result["state"] != "ENTRY_CONFIRMED"
    assert result["state"] == "DATA_UNAVAILABLE"
    assert {"STALE_BAR", "INSUFFICIENT_RVOL_HISTORY"} <= set(result["quality"]["reason_codes"])


def test_complete_twenty_session_rvol_uses_median():
    now = at()
    volumes = [10*i for i in range(1, 20)] + [100000]
    history = [r for d, volume in zip(previous_dates(now, 20), volumes) for r in bars_for(d, volume=volume)]
    rvol, samples = us_watch._same_time_rvol(current_rows(now, volume=105), history, now,
                                           schedule_provider=schedule_at)
    assert samples == 20
    # Current 210 / median cumulative [20,40,...,380,200000] = 210 / 210.
    assert rvol == 1.0


def test_partial_historical_day_does_not_count():
    now = at()
    history = [r for d in previous_dates(now, 20) for r in bars_for(d)]
    history.pop(1)
    rvol, samples = us_watch._same_time_rvol(current_rows(now), history, now,
                                           schedule_provider=schedule_at)
    assert samples == 19
    assert rvol is None


def test_half_day_not_comparable_to_later_window():
    now = at("2026-11-30", 14, 1)
    current = current_rows(now, 54)
    # Black Friday closes at 13:00 ET, before the required 14:00 target.
    history = bars_for("2026-11-27", 42)
    rvol, samples = us_watch._same_time_rvol(current, history, now, schedule_provider=schedule_at)
    assert rvol is None
    assert samples == 0


def test_zero_volume_not_missing_but_zero_baseline_undefined():
    now = at()
    history = [r for d in previous_dates(now, 20) for r in bars_for(d, volume=0)]
    rvol, samples = us_watch._same_time_rvol(current_rows(now, volume=0), history, now,
                                           schedule_provider=schedule_at)
    assert samples == 20
    assert rvol is None


@pytest.mark.parametrize("d5", [-0.10, 0.20])
def test_fresh_inputs_preserve_existing_entry_geometry(d5):
    result = classify(daily(), intra(d5_atr=d5, two_completed_5m_above_vwap_and_ma5=True,
                                    same_time_rvol=0.8), quality=valid_quality())
    assert result["state"] == "ENTRY_CONFIRMED"


def test_classify_requires_quality():
    with pytest.raises(TypeError, match="quality"):
        classify(daily(), intra())


def test_observation_only_quality_cannot_confirm():
    quality = QualityResult("OBSERVATION_ONLY", ("INSUFFICIENT_RVOL_HISTORY",), True, False,
                            1, 2, 1, 1, 2)
    result = classify(daily(), intra(two_completed_5m_above_vwap_and_ma5=True), quality=quality)
    assert result["state"] == "ENTRY_ARMED"


def test_stale_benchmark_cannot_trigger_relative_leader(monkeypatch):
    now = at()
    # Relative strength would fire (0.7-0.1), without any independent leader condition.
    stale = {"change_pct": 0.1, "quality": {"observation_ok": True,
        "valid_until_ms": int(now.timestamp()*1000)-1}, "current_bar_time_utc": at(minute=35).isoformat()}
    result, _ = scan_fixture(monkeypatch, now, qqq_context=stale)
    assert result["relative_change_vs_qqq_pp"] is None
    assert not any("vs QQQ" in r for r in result["leader_reasons"])
    assert result["quality"]["observation_ok"]


def test_invalid_yahoo_rows_not_silently_certified(monkeypatch):
    now = at()
    rows = bars_for(now.date().isoformat())
    rows[0]["close"] = None
    result, fetcher = scan_fixture(monkeypatch, now, rows=rows)
    _, _, receipt = us_watch._chart(fetcher, "AAA", range_value="5d", interval="5m", include_prepost=True)
    assert receipt["invalid_rows"] == 1
    assert receipt["invalid_row_evidence"][0]["t"] == rows[0]["t"]
    assert result["state"] == "DATA_UNAVAILABLE"
    assert "INVALID_ROWS" in result["quality"]["reason_codes"]


def test_invalid_rows_outside_dependency_windows_are_diagnostic(monkeypatch):
    now = at()
    rows = bars_for("2026-10-02") + bars_for(now.date().isoformat())
    rows[0]["close"] = None
    daily_source = daily_rows(now, 25)
    daily_source[0]["close"] = None
    result, _ = scan_fixture(monkeypatch, now, rows=rows, daily_source=daily_source)
    assert result["quality"]["confirmation_ok"]
    assert result["quality_evidence"]["intraday"]["invalid_rows"] == 0
    assert result["quality_evidence"]["intraday"]["receipt"]["invalid_rows"] == 1
    assert result["quality_evidence"]["daily"]["invalid_rows"] == 0


def test_invalid_daily_dependency_cannot_be_replaced_by_older_valid_bar(monkeypatch):
    now = at()
    rows = daily_rows(now, 25)
    rows[-10]["close"] = None
    result, _ = scan_fixture(monkeypatch, now, daily_source=rows)
    assert result["state"] == "DATA_UNAVAILABLE"
    assert "INVALID_ROWS" in result["quality"]["reason_codes"]


def test_duplicate_conflicting_yahoo_bars_are_evidence_and_block_required_window(monkeypatch):
    now = at()
    rows = bars_for(now.date().isoformat())
    duplicate = dict(rows[0], close=100.55)
    result, _ = scan_fixture(monkeypatch, now, rows=rows+[duplicate])
    receipt = result["quality_evidence"]["intraday"]["receipt"]
    assert receipt["duplicate_conflicts"] == 1
    assert result["state"] == "DATA_UNAVAILABLE"


def test_capture_completion_and_cache_receipts_never_refresh(monkeypatch):
    now = at(minute=40, second=30)
    capture = at(minute=39, second=59)
    result, fetcher = scan_fixture(monkeypatch, now, intraday_capture=capture)
    assert result["intraday"]["rth_completed_bars"] == 1
    evidence = result["quality_evidence"]
    assert evidence["intraday"]["bars"][-1]["closed_at_capture"] is False
    later = now + timedelta(minutes=1)
    refreshed = us_watch._scan_symbol("AAA", fetcher, later)
    assert refreshed["daily_known_at"] == result["daily_known_at"]
    assert refreshed["volume_profile_known_at"] == result["volume_profile_known_at"]
    assert refreshed["quality_evidence"]["daily"]["captured_at_ms"] == evidence["daily"]["captured_at_ms"]
    assert refreshed["quality_evidence"]["history"]["captured_at_ms"] == evidence["history"]["captured_at_ms"]
    assert refreshed["intraday"]["rth_completed_bars"] == 1


def test_market_closed_quality_cannot_retain_confirmation(monkeypatch):
    now = at(hour=16, minute=1)
    result, _ = scan_fixture(monkeypatch, now)
    assert result["state"] == "MARKET_CLOSED"
    assert not result["quality"]["confirmation_ok"]


def test_missing_daily_session_inside_window_is_unavailable(monkeypatch):
    now = at()
    rows = daily_rows(now, 25)
    rows.pop(-10)
    result, _ = scan_fixture(monkeypatch, now, daily_source=rows)
    assert result["state"] == "DATA_UNAVAILABLE"
    assert result["quality_evidence"]["daily"]["missing_sessions"]


def test_invalid_only_22_daily_rows_returns_quality_not_exception(monkeypatch):
    now = at()
    rows = daily_rows(now, 22)
    rows[-1]["close"] = None
    result, _ = scan_fixture(monkeypatch, now, daily_source=rows)
    assert result["state"] == "DATA_UNAVAILABLE"
    assert not result["quality"]["observation_ok"]


def test_no_current_session_bars_is_quality_unavailable(monkeypatch):
    now = at()
    result, _ = scan_fixture(monkeypatch, now, rows=bars_for("2026-10-02"))
    assert result["state"] == "DATA_UNAVAILABLE"
    assert "NO_COMPLETED_BARS" in result["quality"]["reason_codes"]


def test_invalid_history_after_target_window_does_not_discard_session(monkeypatch):
    now = at()
    history = [r for d in previous_dates(now, 20) for r in bars_for(d)]
    old_bad = bars_for(previous_dates(now, 20)[0], 3)[-1]
    old_bad["close"] = None
    result, _ = scan_fixture(monkeypatch, now, history=history+[old_bad])
    assert result["intraday"]["same_time_rvol_samples"] == 20
    assert result["quality"]["confirmation_ok"]


def test_stale_benchmark_preserves_independent_valid_leader(monkeypatch):
    now = at()
    rows = bars_for(now.date().isoformat())
    for r in rows:
        r.update(open=105, high=106, low=104, close=105)
    stale = {"change_pct": -10, "quality": {"observation_ok": False, "valid_until_ms": None}}
    result, _ = scan_fixture(monkeypatch, now, rows=rows, qqq_context=stale)
    assert result["leader_detected"]
    assert any(r.startswith("day +") for r in result["leader_reasons"])
    assert result["relative_change_vs_qqq_pp"] is None


def test_misaligned_benchmark_cannot_trigger_relative_leader(monkeypatch):
    now = at()
    qqq = {"change_pct": 0.1, "current_bar_time_utc": at(minute=30).astimezone(timezone.utc).isoformat(),
           "quality": {"observation_ok": True, "valid_until_ms": int(now.timestamp()*1000)+1000,
                       "last_bar_end_ms": int(at(minute=35, second=0).timestamp()*1000)}}
    result, _ = scan_fixture(monkeypatch, now, qqq_context=qqq)
    assert result["relative_change_vs_qqq_pp"] is None
    assert result["benchmark_dependency"]["reason_codes"] == ["MISALIGNED_BENCHMARK"]
    assert result["quality"]["observation_ok"]


def test_fresh_aligned_benchmark_keeps_relative_leader(monkeypatch):
    now = at()
    qqq = {"change_pct": 0.1, "current_bar_time_utc": at(minute=35, second=0).astimezone(timezone.utc).isoformat(),
           "quality": {"observation_ok": True, "valid_until_ms": int(now.timestamp()*1000)+1000,
                       "last_bar_end_ms": int(at(minute=40, second=0).timestamp()*1000)}}
    result, _ = scan_fixture(monkeypatch, now, qqq_context=qqq)
    assert result["relative_change_vs_qqq_pp"] == pytest.approx(0.6)
    assert any("vs QQQ" in r for r in result["leader_reasons"])


def test_market_proxy_qqq_quality_rejects_stale_bars():
    now = at(hour=15, minute=0)
    def fetcher(url, kind):
        return yahoo(bars_for("2026-10-05"), now, symbol="QQQ")
    qqq = us_watch._market_proxy(fetcher, "QQQ", now)
    assert qqq["ok"]
    assert not qqq["quality"]["observation_ok"]
    assert "STALE_BAR" in qqq["quality"]["reason_codes"]


def test_invalid_history_inside_window_reduces_samples(monkeypatch):
    now = at()
    history = [r for d in previous_dates(now, 20) for r in bars_for(d)]
    history[1]["close"] = None
    result, _ = scan_fixture(monkeypatch, now, history=history)
    assert result["intraday"]["same_time_rvol_samples"] == 19
    assert result["intraday"]["same_time_rvol"] is None
    assert result["quality"]["state"] == "OBSERVATION_ONLY"


def test_quality_evidence_reconstructs_original_input_after_wait(monkeypatch):
    from market_data.quality import BarFact, BarQualityInput, evaluate_bars
    from market_data.quality_profiles import US_PUBLIC_5M_POLICY
    now = at()
    result, _ = scan_fixture(monkeypatch, now)
    evidence = json.loads(json.dumps(result["quality_evidence"]))
    later = now + timedelta(minutes=3)
    evaluated = evaluate_bars(BarQualityInput(
        int(later.timestamp()*1000), evidence["captured_at_ms"],
        tuple(BarFact(**bar) for bar in evidence["bars"]), evidence["invalid_rows"],
        evidence["complete_rvol_sessions"], evidence["last_daily_session"]),
        US_PUBLIC_5M_POLICY, schedule(later))
    assert not evaluated.confirmation_ok
    assert "STALE_RESPONSE" in evaluated.reason_codes
    assert evaluated.last_bar_end_ms == result["quality"]["last_bar_end_ms"]



def test_future_daily_capture_is_not_qualified(monkeypatch):
    now = at()
    result, _ = scan_fixture(monkeypatch, now, daily_capture=now+timedelta(seconds=6))
    assert result["state"] == "DATA_UNAVAILABLE"
    assert result["quality_evidence"]["daily"]["capture_valid"] is False


def test_future_history_capture_only_disables_rvol_dependency(monkeypatch):
    now = at()
    result, _ = scan_fixture(monkeypatch, now, history_capture=now+timedelta(seconds=6))
    assert result["quality"]["state"] == "OBSERVATION_ONLY"
    assert result["intraday"]["same_time_rvol"] is None
    assert result["quality_evidence"]["history"]["capture_valid"] is False


def test_stale_intraday_receipt_rejects_fresh_looking_bars(monkeypatch):
    now = at()
    capture = now-timedelta(seconds=120)
    result, _ = scan_fixture(monkeypatch, now, intraday_capture=capture)
    assert result["state"] == "DATA_UNAVAILABLE"
    assert "STALE_RESPONSE" in result["quality"]["reason_codes"]


def test_missing_current_window_bar_rejects_fresh_watermark(monkeypatch):
    now = at(minute=46)
    rows = bars_for(now.date().isoformat(), 3)
    rows.pop(1)
    result, _ = scan_fixture(monkeypatch, now, rows=rows)
    assert result["state"] == "DATA_UNAVAILABLE"
    assert "BAR_GAP" in result["quality"]["reason_codes"]


def test_twenty_old_history_days_with_one_non_session_row_remain_comparable():
    now = at()
    history = [r for d in previous_dates(now, 20) for r in bars_for(d)]
    history.append({"t": int(at("2026-10-03", 9, 30).timestamp()), "volume": 10})
    _, samples = us_watch._same_time_rvol(current_rows(now), history, now, schedule_provider=schedule_at)
    assert samples == 20



@pytest.mark.parametrize("bad_capture", [None, 12, True, "not a timestamp"])
def test_malformed_receipt_never_claims_capture_completion(bad_capture):
    now = at()
    wrapped = yahoo(bars_for(now.date().isoformat()), now)
    wrapped["receipt"]["received_at_utc"] = bad_capture
    rows, _, receipt = us_watch._chart(lambda url, kind: wrapped, "AAA", range_value="5d",
                                       interval="5m", include_prepost=True)
    assert receipt["captured_at_ms"] == -1
    assert not any(r["closed_at_capture"] for r in rows)



def test_required_invalid_cached_snapshots_retry_only_on_next_ordinary_scan(monkeypatch):
    now = at()
    daily_source = daily_rows(now)
    daily_source[-3]["close"] = None
    history = [r for d in previous_dates(now, 20) for r in bars_for(d)]
    history[1]["close"] = None
    first, fetcher = scan_fixture(monkeypatch, now, daily_source=daily_source, history=history)
    assert first["state"] == "DATA_UNAVAILABLE"
    assert len(fetcher.calls) == 3  # No immediate retries.
    later = now + timedelta(minutes=1)
    fetcher.sources["1d"] = yahoo(daily_rows(later), later)
    fetcher.sources["1mo"] = yahoo([r for d in previous_dates(later, 20) for r in bars_for(d)], later)
    fetcher.sources["5d"] = yahoo(bars_for(later.date().isoformat()), later)
    repaired = us_watch._scan_symbol("AAA", fetcher, later)
    assert repaired["quality"]["confirmation_ok"]
    assert repaired["daily_known_at"] != first["daily_known_at"]
    assert repaired["volume_profile_known_at"] != first["volume_profile_known_at"]
    assert len(fetcher.calls) == 6


def test_short_history_alone_stays_cached_without_extra_fetches(monkeypatch):
    now = at()
    history = [r for d in previous_dates(now, 19) for r in bars_for(d)]
    first, fetcher = scan_fixture(monkeypatch, now, history=history)
    later = now + timedelta(minutes=1)
    second = us_watch._scan_symbol("AAA", fetcher, later)
    assert first["intraday"]["same_time_rvol_samples"] == second["intraday"]["same_time_rvol_samples"] == 19
    assert len(fetcher.calls) == 4
    assert first["volume_profile_known_at"] == second["volume_profile_known_at"]


def test_outside_window_invalid_diagnostics_do_not_evict_caches(monkeypatch):
    now = at()
    history = [r for d in previous_dates(now, 20) for r in bars_for(d)]
    bad = bars_for(previous_dates(now, 20)[0], 3)[-1]
    bad["close"] = None
    daily_source = daily_rows(now, 25)
    daily_source[0]["close"] = None
    first, fetcher = scan_fixture(monkeypatch, now, history=history+[bad], daily_source=daily_source)
    second = us_watch._scan_symbol("AAA", fetcher, now+timedelta(minutes=1))
    assert first["quality"]["confirmation_ok"] and second["quality"]["confirmation_ok"]
    assert len(fetcher.calls) == 4


def grace_rows(now):
    rows = bars_for(now.date().isoformat(), 3)
    for i, row in enumerate(rows):
        row.update(open=100.3+i*0.1, high=100.4+i*0.1, low=100.2+i*0.1,
                   close=100.39+i*0.1)
    return rows


def test_consumed_stock_conflict_inside_publication_grace_cannot_confirm(monkeypatch):
    now = at(minute=45, second=30)
    rows = grace_rows(now)
    duplicate = dict(rows[-1], close=100.58)
    history = [r for d in previous_dates(now, 20) for r in bars_for(d, 3)]
    result, _ = scan_fixture(monkeypatch, now, rows=rows+[duplicate], history=history)
    assert result["intraday"]["two_completed_5m_above_vwap_and_ma5"]
    assert result["intraday"]["same_time_rvol"] == 1.0
    assert result["state"] == "DATA_UNAVAILABLE"
    assert result["quality_evidence"]["intraday"]["invalid_rows"] == 1
    assert "INVALID_ROWS" in result["quality"]["reason_codes"]


def test_consumed_qqq_conflict_inside_publication_grace_is_unavailable():
    now = at(minute=45, second=30)
    rows = grace_rows(now)
    duplicate = dict(rows[-1], close=100.58)
    qqq = us_watch._market_proxy(lambda url, kind: yahoo(rows+[duplicate], now, symbol="QQQ"), "QQQ", now)
    assert qqq["ok"]
    assert not qqq["quality"]["observation_ok"]
    assert "INVALID_ROWS" in qqq["quality"]["reason_codes"]


@pytest.mark.parametrize("symbol", ["AAA", "QQQ"])
def test_invalid_current_live_bar_is_a_consumed_price_dependency(monkeypatch, symbol):
    now = at(minute=44, second=30)
    rows = grace_rows(now)
    # 09:40 is still open at capture but supplies the current price.
    duplicate = dict(rows[-1], close=100.58)
    if symbol == "AAA":
        result, _ = scan_fixture(monkeypatch, now, rows=rows+[duplicate])
    else:
        result = us_watch._market_proxy(lambda url, kind: yahoo(rows+[duplicate], now, symbol="QQQ"), "QQQ", now)
    assert not result["quality"]["observation_ok"]
    assert "INVALID_ROWS" in result["quality"]["reason_codes"]


def timed_feed(start, captured):
    history = [row for day in previous_dates(start, 20) for row in bars_for(day)]
    def fetcher(url, kind):
        if "/v1/finance/search?" in url:
            return {"data": {"news": []}, "receipt": {"received_at_utc": captured.isoformat()}}
        symbol = "AAA" if "/chart/AAA?" in url else "QQQ" if "/chart/QQQ?" in url else "NQ=F" if "NQ%3DF" in url else "ES=F"
        rows = daily_rows(start) if "interval=1d" in url else history if "range=1mo" in url else bars_for(start.date().isoformat())
        return yahoo(rows, captured, symbol=symbol)
    return fetcher


def test_default_live_scan_refreshes_evaluation_after_delayed_acquisition(monkeypatch):
    start = at()
    received = start + timedelta(seconds=6)
    wall = {"now": start}
    class ProcessDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return wall["now"].astimezone(tz) if tz else wall["now"]
    monkeypatch.setattr(us_watch, "datetime", ProcessDateTime)
    monkeypatch.setattr(us_watch, "_DAILY_CACHE", {})
    monkeypatch.setattr(us_watch, "_VOLUME_PROFILE_CACHE", {})
    monkeypatch.setattr(us_watch, "_NEWS_CACHE", {})
    source = timed_feed(start, received)
    def delayed(url, kind):
        response = source(url, kind)
        wall["now"] = received
        return response
    report = us_watch.scan_once(symbols=("AAA",), fetcher=delayed, workers=1)
    row = report["rows"][0]
    assert row["quality"]["state"] == "VALID"
    assert row["quality"]["evaluated_at_ms"] == int(received.timestamp()*1000)
    assert row["known_at"] == received.astimezone(timezone.utc).isoformat()
    assert row["quality_evidence"]["daily"]["capture_valid"]
    assert row["quality_evidence"]["history"]["capture_valid"]
    assert report["market_context"]["QQQ"]["quality"]["state"] == "VALID"
    assert ("AAA", start.date().isoformat()) in us_watch._DAILY_CACHE
    assert ("AAA", start.date().isoformat()) in us_watch._VOLUME_PROFILE_CACHE


def test_injected_clock_refreshes_queued_symbol_and_qqq_after_acquisition(monkeypatch):
    start = at()
    received = start + timedelta(seconds=6)
    monkeypatch.setattr(us_watch, "_DAILY_CACHE", {})
    monkeypatch.setattr(us_watch, "_VOLUME_PROFILE_CACHE", {})
    feed = timed_feed(start, received)
    clock = lambda: received
    row = us_watch._scan_symbol("AAA", feed, start, clock=clock)
    qqq = us_watch._market_proxy(feed, "QQQ", start, clock=clock)
    assert row["quality"]["confirmation_ok"]
    assert qqq["quality"]["observation_ok"]
    assert row["quality_evidence"]["captured_at_ms"] == int(received.timestamp()*1000)
    assert row["quality"]["evaluated_at_ms"] == int(received.timestamp()*1000)


def test_explicit_now_replay_remains_fixed_despite_later_receipts(monkeypatch):
    start = at()
    received = start + timedelta(seconds=6)
    class ForbiddenClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("explicit-now replay must not read wall time")
    monkeypatch.setattr(us_watch, "datetime", ForbiddenClockDateTime)
    monkeypatch.setattr(us_watch, "_DAILY_CACHE", {})
    monkeypatch.setattr(us_watch, "_VOLUME_PROFILE_CACHE", {})
    report = us_watch.scan_once(symbols=("AAA",), fetcher=timed_feed(start, received), now=start, workers=1)
    row = report["rows"][0]
    assert row["state"] == "DATA_UNAVAILABLE"
    assert "FUTURE_CAPTURE" in row["quality"]["reason_codes"]
    assert row["quality"]["evaluated_at_ms"] == int(start.timestamp()*1000)


def test_live_clock_does_not_make_genuinely_future_receipts_valid(monkeypatch):
    start = at()
    evaluated = start + timedelta(seconds=6)
    received = evaluated + timedelta(seconds=6)
    monkeypatch.setattr(us_watch, "_DAILY_CACHE", {})
    monkeypatch.setattr(us_watch, "_VOLUME_PROFILE_CACHE", {})
    row = us_watch._scan_symbol("AAA", timed_feed(start, received), start, clock=lambda: evaluated)
    assert row["state"] == "DATA_UNAVAILABLE"
    assert "FUTURE_CAPTURE" in row["quality"]["reason_codes"]


def test_live_evaluation_never_matures_bars_open_at_receipt(monkeypatch):
    start = at(minute=39, second=55)
    received = at(minute=39, second=59)
    evaluated = at(minute=41, second=30)
    monkeypatch.setattr(us_watch, "_DAILY_CACHE", {})
    monkeypatch.setattr(us_watch, "_VOLUME_PROFILE_CACHE", {})
    row = us_watch._scan_symbol("AAA", timed_feed(start, received), start, clock=lambda: evaluated)
    assert row["quality_evidence"]["bars"][-1]["closed_at_capture"] is False
    assert row["intraday"]["rth_completed_bars"] == 1
    assert "STALE_BAR" in row["quality"]["reason_codes"]


def test_scan_once_propagates_injected_live_clock_with_explicit_start(monkeypatch):
    start = at()
    received = start + timedelta(seconds=6)
    class ForbiddenWallDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("an injected live clock must not sample implicit wall time")
    monkeypatch.setattr(us_watch, "datetime", ForbiddenWallDateTime)
    monkeypatch.setattr(us_watch, "_DAILY_CACHE", {})
    monkeypatch.setattr(us_watch, "_VOLUME_PROFILE_CACHE", {})
    report = us_watch.scan_once(symbols=("AAA",), fetcher=timed_feed(start, received),
                                now=start, workers=1, clock=lambda: received)
    assert report["rows"][0]["quality"]["confirmation_ok"]
    assert report["market_context"]["QQQ"]["quality"]["observation_ok"]
    assert report["generated_at_utc"] == received.isoformat()
