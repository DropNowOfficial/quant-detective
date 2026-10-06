from datetime import datetime, timedelta, timezone
import json

import pytest

from market_data import cli, github_alerts, us_watch
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
    result = classify(daily(), intra(current_price=113, change_pct=4.0, d5_atr=1.3), qqq_change=0.5)
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
    result = classify(daily(), intra(change_pct=0.9, premarket_change_pct=1.4, d5_atr=0.5))
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


def test_relative_strength_vs_qqq_can_trigger_leader():
    result = classify(daily(), intra(change_pct=0.9, premarket_change_pct=0.1, open_gap_pct=0.1,
                                     move_from_rth_open_pct=0.2, d5_atr=0.3), qqq_change=0.2)
    assert result["leader_detected"]
    assert result["relative_change_vs_qqq_pp"] > 0.5
    assert any("vs QQQ" in x for x in result["leader_reasons"])


def test_one_percent_day_move_is_not_silenced():
    result = classify(daily(), intra(change_pct=1.05, premarket_change_pct=0.0, open_gap_pct=0.0,
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
