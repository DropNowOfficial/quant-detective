"""Runtime-window regressions using synthetic clocks and provider inputs only."""
from datetime import datetime, timezone
import json

import pytest

from market_data import github_alerts, us_watch


def moment(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


@pytest.mark.parametrize("value,session,reason", [
    ("2026-10-05T03:59:59.999999-04:00", "OVERNIGHT", "OUTSIDE_SESSION_HOURS"),
    ("2026-10-05T20:00:00-04:00", "OVERNIGHT", "OUTSIDE_SESSION_HOURS"),
    ("2026-10-05T20:02:00-04:00", "OVERNIGHT", "OUTSIDE_SESSION_HOURS"),
    ("2026-10-05T23:59:59-04:00", "OVERNIGHT", "OUTSIDE_SESSION_HOURS"),
    ("2026-10-04T12:00:00-04:00", "CLOSED", "NON_TRADING_DAY"),
    ("2026-07-03T12:00:00-04:00", "CLOSED", "NON_TRADING_DAY"),
    ("2026-11-26T12:00:00-05:00", "CLOSED", "NON_TRADING_DAY"),
    ("2026-03-09T07:59:59+00:00", "OVERNIGHT", "OUTSIDE_SESSION_HOURS"),
    ("2026-11-02T08:59:59+00:00", "OVERNIGHT", "OUTSIDE_SESSION_HOURS"),
])
def test_off_session_scan_skips_before_any_fetch(value, session, reason):
    calls = []

    def provider(url, **kwargs):
        calls.append(url)
        raise AssertionError("Off-session provider request")

    report = us_watch.scan_once(symbols=("aaa", "AAA", "BBB"), fetcher=provider, now=moment(value))
    assert calls == []
    assert report["scan_status"] == "SKIPPED"
    assert report["runtime_session"] == session
    assert report["skip_reason"] == reason
    assert report["symbols_requested"] == ["AAA", "BBB"]
    assert report["rows"] == report["alerts"] == report["incomplete_symbols"] == report["sources"] == []
    assert report["market_context"] == {}
    assert report["scan_started_at_utc"] == report["generated_at_utc"] == moment(value).isoformat()


@pytest.mark.parametrize("value,session", [
    ("2026-10-05T04:00:00-04:00", "PRE"),
    ("2026-10-05T09:29:59-04:00", "PRE"),
    ("2026-10-05T09:30:00-04:00", "RTH"),
    ("2026-10-05T16:00:00-04:00", "POST"),
    ("2026-10-05T19:59:59.999999-04:00", "POST"),
    ("2026-11-27T12:59:59-05:00", "RTH"),
    ("2026-11-27T13:00:00-05:00", "POST"),
    ("2026-11-27T19:59:59-05:00", "POST"),
    ("2026-03-09T08:00:00+00:00", "PRE"),
    ("2026-11-02T09:00:00+00:00", "PRE"),
    ("2026-03-09T13:30:00+00:00", "RTH"),
    ("2026-11-02T14:30:00+00:00", "RTH"),
])
def test_active_runtime_uses_calendar_and_fetches(value, session):
    calls = []

    def provider(url, **kwargs):
        calls.append(url)
        raise RuntimeError("synthetic provider unavailable")

    report = us_watch.scan_once(symbols=("GUARD_TEST",), fetcher=provider, now=moment(value))
    assert calls
    assert report["runtime_session"] == session
    assert report["scan_status"] == "UNAVAILABLE"
    assert report["incomplete_symbols"] == ["GUARD_TEST"]
    assert report["rows"][0]["status"] == "ERROR"
    assert report["alerts"] == []


@pytest.mark.parametrize("once", [True, False])
def test_run_skips_without_publication_or_failure(monkeypatch, tmp_path, capsys, once):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Skipped scans must not fetch or publish")

    ticks = iter([0, 0, 61])
    monkeypatch.setattr(github_alerts, "publish", forbidden)
    monkeypatch.setattr(us_watch.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(us_watch.time, "sleep", lambda seconds: None)
    output = tmp_path / "scan.json"
    result = us_watch.run(symbols=("AAA",), once=once, duration_minutes=1, github_alerts=True,
                          fetcher=forbidden, output=output, clock=lambda: moment("2026-10-05T20:02:00-04:00"))
    assert calls == []
    assert result["scan_status"] == "SKIPPED"
    assert json.loads(output.read_text()) == result
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == (1 if once else 2)
    assert all(line["scan_status"] == "SKIPPED" and line["runtime_session"] == "OVERNIGHT"
               and line["skip_reason"] == "OUTSIDE_SESSION_HOURS" for line in lines)


def test_continuous_run_rechecks_window_open(monkeypatch, tmp_path):
    current = [moment("2026-10-05T03:59:59-04:00")]
    calls, published, saved = [], [], []

    def provider(url, **kwargs):
        calls.append(current[0])
        raise RuntimeError("synthetic provider unavailable")

    def sleep(seconds):
        current[0] = moment("2026-10-05T04:00:00-04:00")

    original_write = us_watch._write

    def write(report, output):
        saved.append(report.copy())
        original_write(report, output)

    ticks = iter([0, 0, 61])
    monkeypatch.setattr(us_watch, "_write", write)
    monkeypatch.setattr(us_watch.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(us_watch.time, "sleep", sleep)
    monkeypatch.setattr(github_alerts, "publish", lambda report, **kwargs: published.append(report))
    result = us_watch.run(symbols=("GUARD_TEST",), duration_minutes=1, github_alerts=True,
                          fetcher=provider, output=tmp_path / "scan.json", clock=lambda: current[0])
    assert saved[0]["scan_status"] == "SKIPPED"
    assert result["scan_status"] == "UNAVAILABLE"
    assert result["runtime_session"] == "PRE"
    assert published == [result]
    assert calls and all(stamp == current[0] for stamp in calls)


def test_runtime_clock_remains_fresh_for_quote_revalidation(monkeypatch, tmp_path):
    from test_quote_validity import install_synthetic_charts

    install_synthetic_charts(monkeypatch)
    current = [moment("2026-10-05T09:42:00-04:00")]

    def delayed_news(*args):
        current[0] = moment("2026-10-05T09:44:01-04:00")
        return {"items": []}

    monkeypatch.setattr(us_watch, "_news", delayed_news)
    output = tmp_path / "scan.json"
    with pytest.raises(RuntimeError, match="one-shot scan incomplete"):
        us_watch.run(symbols=("AAA",), once=True, output=output, clock=lambda: current[0])
    report = json.loads(output.read_text())
    assert report["runtime_session"] == "RTH"
    assert report["scan_status"] == "UNAVAILABLE"
    assert report["alerts"] == []
    row = report["rows"][0]
    assert row["status"] == "INVALID_DATA"
    assert row["known_at"] == "2026-10-05T09:42:00-04:00"
    assert row["intraday"]["quote_validity"]["rejection_reasons"] == ["RECEIPT_EXPIRED"]


@pytest.mark.parametrize("boundary", ["market_context", "stock_before_news"])
def test_each_provider_request_rechecks_boundary_and_retains_acquired_rows(monkeypatch, boundary):
    current = [moment("2026-10-05T19:59:59-04:00")]
    calls = []
    us_watch._DAILY_CACHE.clear()
    us_watch._NEWS_CACHE.clear()

    def provider(url, **kwargs):
        calls.append((current[0], url))
        return {"data": {"news": []}, "receipt": {"received_at_utc": current[0].isoformat()}}

    def context(fetcher, now, clock):
        if boundary == "market_context":
            fetcher("https://synthetic.test/proxy", kind="json")
            current[0] = moment("2026-10-05T20:00:01-04:00")
        return {}

    def stock(symbol, fetcher, now, qqq_change, clock):
        fetcher("https://synthetic.test/stock", kind="json")
        current[0] = moment("2026-10-05T20:00:01-04:00")
        return {"symbol": symbol, "status": "OK", "state": "LEADER_WATCH"}

    monkeypatch.setattr(us_watch, "_market_context", context)
    if boundary == "stock_before_news":
        monkeypatch.setattr(us_watch, "_scan_symbol", stock)
    report = us_watch.scan_once(symbols=("AAA",), fetcher=provider, clock=lambda: current[0])
    assert len(calls) == 1
    assert report["scan_status"] == "SESSION_ENDED"
    assert report["runtime_session"] == "POST"
    assert report["ending_session"] == "OVERNIGHT"
    assert report["skip_reason"] == "SESSION_ENDED_DURING_SCAN"
    assert report["real_acquisition_failure"] is False
    assert report["rows"][0]["symbol"] == "AAA"
    assert report["alerts"] == []


def test_session_ended_acquisition_exits_successfully_without_publishing(monkeypatch, tmp_path):
    current = [moment("2026-10-05T19:59:59-04:00")]

    def context(fetcher, now, clock):
        current[0] = moment("2026-10-05T20:00:01-04:00")
        return {}

    monkeypatch.setattr(us_watch, "_market_context", context)
    monkeypatch.setattr(github_alerts, "publish", lambda *a, **k: pytest.fail("Off-session publication"))
    output = tmp_path / "scan.json"
    report = us_watch.run(symbols=("AAA",), once=True, output=output, github_alerts=True,
                          fetcher=lambda *a, **k: pytest.fail("Off-session fetch"), clock=lambda: current[0])
    assert report["scan_status"] == "SESSION_ENDED"
    assert json.loads(output.read_text()) == report
    from market_data.smoke_check import validate_report
    assert validate_report(report, symbols=("AAA",), scan_exit_code=0, now=current[0]) == "EXPECTED_SESSION_END"


def test_recheck_immediately_before_publisher(monkeypatch, tmp_path):
    current = [moment("2026-10-05T19:59:59-04:00")]
    report = {"scan_started_at_utc": current[0].isoformat(), "generated_at_utc": current[0].isoformat(),
              "generated_at_et": current[0].astimezone(us_watch.ET).isoformat(), "runtime_session": "POST",
              "symbols_requested": ["AAA"], "rows": [{"symbol": "AAA", "status": "OK"}],
              "alerts": [{"symbol": "AAA", "state": "LEADER_WATCH"}]}
    original_write = us_watch._write

    def write(report, output):
        original_write(report, output)
        current[0] = moment("2026-10-05T20:00:01-04:00")

    monkeypatch.setattr(us_watch, "scan_once", lambda **kwargs: report)
    monkeypatch.setattr(us_watch, "_write", write)
    monkeypatch.setattr(github_alerts, "publish", lambda *a, **k: pytest.fail("Off-session publication"))
    output = tmp_path / "scan.json"
    result = us_watch.run(symbols=("AAA",), once=True, output=output, github_alerts=True, clock=lambda: current[0])
    assert result["scan_status"] == "SESSION_ENDED"
    assert result["alerts"] == []
    assert result["rows"] == [{"symbol": "AAA", "status": "OK"}]
    assert result["ending_session"] == "OVERNIGHT"
    assert json.loads(output.read_text()) == result


def test_real_provider_failure_cannot_be_hidden_by_later_session_end(monkeypatch, tmp_path):
    current = [moment("2026-10-05T19:59:59-04:00")]
    us_watch._DAILY_CACHE.clear()

    def provider(*args, **kwargs):
        current[0] = moment("2026-10-05T20:00:01-04:00")
        raise RuntimeError("synthetic provider unavailable")

    monkeypatch.setattr(us_watch, "_market_context", lambda *args: {})
    monkeypatch.setattr(github_alerts, "publish", lambda *a, **k: pytest.fail("Off-session publication"))
    output = tmp_path / "scan.json"
    with pytest.raises(RuntimeError, match="one-shot scan incomplete"):
        us_watch.run(symbols=("AAA", "BBB"), fetcher=provider, once=True, github_alerts=True,
                     output=output, clock=lambda: current[0])
    report = json.loads(output.read_text())
    assert report["scan_status"] == "UNAVAILABLE"
    assert report["ending_session"] == "OVERNIGHT"
    assert report["real_acquisition_failure"] is True
    assert any("synthetic provider unavailable" in row.get("error", "") for row in report["rows"])
    assert report["alerts"] == []
