"""CI-only acceptance rules; synthetic evidence, never actual market signals."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path

import pytest

from market_data.quote_validity import ET, previous_session_date, quote_evidence

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("NVDA", "TSM", "MSFT")


def moment(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def report_at(now="2026-10-07T23:18:00-04:00", source="2026-10-07T19:59:41-04:00"):
    now, source = moment(now), moment(source)
    rows = []
    for symbol in SYMBOLS:
        proof = quote_evidence(source.timestamp(), now.isoformat(), now,
                               daily_date=previous_session_date(now), require_daily=True)
        valid = proof["eligible"]
        rows.append({"symbol": symbol, "status": "OK" if valid else "INVALID_DATA",
                     "state": "WATCH" if valid else "DATA_INVALID", "leader_detected": False,
                     "source": "Yahoo Finance public chart", "known_at": now.isoformat(),
                     "daily": {"previous_session_date": previous_session_date(now)},
                     "intraday": {"current_price": 100.0, "current_bar_time_utc": source.isoformat(),
                                  "current_session": proof["source_session"], "quote_validity": proof}})
    incomplete = sorted(r["symbol"] for r in rows if r["status"] != "OK")
    context = {}
    for symbol in ("NQ=F", "ES=F", "QQQ"):
        start = source if symbol == "QQQ" else now - timedelta(minutes=2)
        proof = quote_evidence(start.timestamp(), now.isoformat(), now, equity=symbol == "QQQ")
        valid = proof["eligible"]
        context[symbol] = {"symbol": symbol, "ok": valid, "quote_validity": proof,
                           "known_at": now.isoformat(), "last_bar_utc": start.isoformat(),
                           "price": 100.0 if valid else None, "change_pct": 1.0 if valid else None,
                           "observed_price": 100.0, "observed_change_pct": 1.0}
    return {"scan_started_at_utc": (now - timedelta(seconds=10)).isoformat(),
            "generated_at_utc": now.isoformat(), "generated_at_et": now.astimezone(ET).isoformat(),
            "mode": "PUBLIC_HEADLESS_OBSERVATION", "trading_enabled": False,
            "symbols_requested": list(SYMBOLS), "rows": rows, "alerts": [], "market_context": context,
            "scan_status": "UNAVAILABLE" if len(incomplete) == len(rows) else "INCOMPLETE" if incomplete else "COMPLETE",
            "incomplete_symbols": incomplete}


def check(report, exit_code=None, now=None):
    module = importlib.import_module("market_data.smoke_check")
    return module.validate_report(report, symbols=SYMBOLS,
                                  scan_exit_code=(2 if report["scan_status"] in {"INCOMPLETE", "UNAVAILABLE"} else 0)
                                  if exit_code is None else exit_code,
                                  now=now or moment(report["generated_at_utc"]))


def test_closed_session_requires_proven_recent_close_and_safe_rejection():
    assert check(report_at()) == "EXPECTED_CLOSED_SESSION_REJECTION"


@pytest.mark.parametrize("now,source", [
    ("2026-10-05T08:07:00-04:00", "2026-10-05T08:05:00-04:00"),
    ("2026-10-05T10:07:00-04:00", "2026-10-05T10:05:00-04:00"),
    ("2026-10-05T16:07:00-04:00", "2026-10-05T16:05:00-04:00"),
    ("2026-10-05T20:02:00-04:00", "2026-10-05T19:59:41-04:00"),
    ("2026-10-05T20:10:40-04:00", "2026-10-05T19:59:41-04:00"),
    ("2026-11-27T13:07:00-05:00", "2026-11-27T13:05:00-05:00"),
    ("2026-03-09T13:37:00+00:00", "2026-03-09T13:35:00+00:00"),
    ("2026-11-02T14:37:00+00:00", "2026-11-02T14:35:00+00:00"),
])
def test_fresh_data_pass_in_supported_sessions_and_end_grace(now, source):
    assert check(report_at(now, source)) == "FRESH_DATA_PASS"


@pytest.mark.parametrize("now,source", [
    ("2026-10-05T20:11:00-04:00", "2026-10-05T19:59:41-04:00"),
    ("2026-10-05T20:06:00-04:00", "2026-10-05T19:55:00-04:00"),
    ("2026-10-05T20:10:41-04:00", "2026-10-05T19:59:41-04:00"),
    ("2026-10-06T03:59:59-04:00", "2026-10-05T19:55:00-04:00"),
    ("2026-10-04T10:00:00-04:00", "2026-10-02T19:59:41-04:00"),
    ("2026-10-05T03:00:00-04:00", "2026-10-02T19:59:41-04:00"),
    ("2026-07-03T10:00:00-04:00", "2026-07-02T19:59:41-04:00"),
    ("2026-07-06T03:00:00-04:00", "2026-07-02T19:59:41-04:00"),
    ("2026-11-27T23:00:00-05:00", "2026-11-27T19:59:41-05:00"),
    ("2026-03-08T10:00:00-04:00", "2026-03-06T19:59:41-05:00"),
    ("2026-11-01T10:00:00-05:00", "2026-10-30T19:59:41-04:00"),
])
def test_prior_source_session_uses_exchange_calendar_not_elapsed_day_limit(now, source):
    assert check(report_at(now, source)) == "EXPECTED_CLOSED_SESSION_REJECTION"


@pytest.mark.parametrize("now,source", [
    ("2026-10-05T04:00:00-04:00", "2026-10-02T19:59:41-04:00"),
    ("2026-10-05T08:07:00-04:00", "2026-10-05T07:30:00-04:00"),
    ("2026-10-05T10:07:00-04:00", "2026-10-05T09:30:00-04:00"),
    ("2026-11-27T14:07:00-05:00", "2026-11-27T12:55:00-05:00"),
])
def test_invalid_quotes_fail_in_live_windows_including_early_close_post_and_grace(now, source):
    with pytest.raises(ValueError, match="live window"):
        check(report_at(now, source))


@pytest.mark.parametrize("now,source", [
    ("2026-10-05T20:02:00-04:00", "2026-10-05T19:50:00-04:00"),
    ("2026-10-07T23:00:00-04:00", "2026-10-07T09:30:00-04:00"),
    ("2026-10-07T23:00:00-04:00", "2026-10-07T19:54:59-04:00"),
    ("2026-10-07T23:00:00-04:00", "2026-10-06T19:59:41-04:00"),
    ("2026-10-04T10:00:00-04:00", "2026-10-01T19:59:41-04:00"),
    ("2026-07-06T03:00:00-04:00", "2026-07-01T19:59:41-04:00"),
    ("2026-07-03T10:00:00-04:00", "2026-07-03T09:55:00-04:00"),
])
def test_closed_session_does_not_excuse_arbitrary_stale_data(now, source):
    with pytest.raises(ValueError):
        check(report_at(now, source))


@pytest.mark.parametrize("mutate", [
    lambda p: p["rows"].pop(),
    lambda p: p["rows"].append(deepcopy(p["rows"][0])),
    lambda p: p["rows"][0].update(symbol="AAPL"),
    lambda p: p.update(symbols_requested=["AAPL"]),
    lambda p: p.update(scan_status="COMPLETE"),
    lambda p: p.update(incomplete_symbols=[]),
    lambda p: p.update(trading_enabled=True),
    lambda p: p.update(mode="UNKNOWN"),
    lambda p: p.update(errors=["transport failed"]),
    lambda p: p.update(error="application error"),
    lambda p: p["rows"][0].update(status="ERROR", error="ValueError: no bars for current ET date"),
    lambda p: p["rows"][0].update(error="fetch failed"),
    lambda p: p["rows"][0].update(state="LEADER_WATCH"),
    lambda p: p["rows"][0].update(leader_detected=True),
    lambda p: p.update(alerts=[{"symbol": "NVDA", "state": "LEADER_WATCH"}]),
    lambda p: p.pop("alerts"),
    lambda p: p["rows"][0]["intraday"].update(current_price=None),
    lambda p: p["rows"][0]["intraday"].update(current_price=float("nan")),
    lambda p: p["rows"][0]["intraday"].update(current_price=True),
    lambda p: p["rows"][0]["daily"].update(previous_session_date="2026-10-01"),
    lambda p: p["rows"][0]["intraday"].update(current_bar_time_utc="bad"),
    lambda p: p["rows"][0].update(known_at="bad"),
    lambda p: p["market_context"]["QQQ"].update(error="fetch failed"),
    lambda p: p["market_context"]["QQQ"].update(price=100),
    lambda p: p["market_context"]["QQQ"].update(ok=True),
    lambda p: p["market_context"]["QQQ"].update(symbol="SPY"),
    lambda p: p["market_context"].pop("QQQ"),
    lambda p: p["market_context"]["NQ=F"].update(error="HTTP 429"),
    lambda p: p.update(generated_at_utc="bad"),
    lambda p: p.update(generated_at_et="2026-10-01T23:18:00-04:00"),
    lambda p: p.update(scan_started_at_utc="2026-10-08T05:18:00+00:00"),
])
def test_unhealthy_or_malformed_reports_fail_closed(mutate):
    report = report_at()
    mutate(report)
    with pytest.raises(ValueError):
        check(report)


@pytest.mark.parametrize("field,value", [
    ("source_bar_start_utc", None), ("source_bar_start_utc", "bad"),
    ("source_bar_start_utc", "2026-10-08T05:00:00+00:00"),
    ("source_bar_end_utc", "2026-10-08T01:00:00+00:00"),
    ("received_at_utc", "2026-10-08T03:15:00+00:00"),
    ("received_at_utc", "2026-10-08T03:19:00+00:00"),
    ("received_at_utc", "2026-10-08T03:18:00"),
    ("version", 2), ("eligible", True), ("completed_at_receipt", False),
    ("source_session", "RTH"), ("evaluation_session", "POST"),
    ("evaluated_at_utc", "2026-10-07T03:18:00+00:00"),
    ("bar_expires_at_utc", None), ("receipt_valid_until_utc", None),
    ("daily_session_date", "2026-10-01"), ("expected_daily_session_date", "2026-10-01"),
    ("rejection_reasons", []), ("rejection_reasons", ["RECEIPT_EXPIRED"]),
    ("rejection_reasons", ["BAR_EXPIRED", "MISSING_OR_INCONSISTENT_PROVENANCE"]),
])
def test_untrusted_provenance_is_not_closed_session_success(field, value):
    report = report_at()
    report["rows"][0]["intraday"]["quote_validity"][field] = value
    with pytest.raises(ValueError):
        check(report)


@pytest.mark.parametrize("exit_code", [0, 1, 3, 124, 137])
def test_closed_rejection_requires_expected_watch_exit_code(exit_code):
    with pytest.raises(ValueError, match="exit"):
        check(report_at(), exit_code=exit_code)


def test_partial_expiry_after_close_fails_explicitly():
    report = report_at("2026-10-05T20:07:00-04:00", "2026-10-05T19:55:00-04:00")
    fresh = report_at("2026-10-05T20:07:00-04:00", "2026-10-05T19:59:41-04:00")
    report["rows"][0] = fresh["rows"][0]
    report["incomplete_symbols"].remove("NVDA")
    report["scan_status"] = "INCOMPLETE"
    with pytest.raises(ValueError, match="mixed"):
        check(report)


def test_fresh_report_does_not_mask_process_failure():
    with pytest.raises(ValueError, match="exit"):
        check(report_at("2026-10-05T10:07:00-04:00", "2026-10-05T10:05:00-04:00"), exit_code=2)


@pytest.mark.parametrize("seconds", [121, -6])
def test_old_or_future_report_cannot_be_reused(seconds):
    report = report_at()
    with pytest.raises(ValueError, match="report time"):
        check(report, now=moment(report["generated_at_utc"]) + timedelta(seconds=seconds))


def test_cli_prints_exact_reasons_and_persists_summary_even_on_failed_scan(tmp_path, monkeypatch, capsys):
    module = importlib.import_module("market_data.smoke_check")
    report = report_at()
    report["rows"][0]["status"] = "ERROR"
    report["rows"][0]["error"] = "fetch failed"
    path, summary = tmp_path / "report.json", tmp_path / "summary.md"
    path.write_text(json.dumps(report))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(module, "current_time", lambda: moment(report["generated_at_utc"]))
    assert module.main([str(path), "--scan-exit-code", "2"]) == 1
    output = capsys.readouterr().out
    assert "SMOKE_FAIL" in output and "fetch failed" in output and "BAR_EXPIRED" in output
    assert summary.read_text() == output


def test_cli_success_label_distinguishes_closed_observation(tmp_path, monkeypatch, capsys):
    module = importlib.import_module("market_data.smoke_check")
    report = report_at()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    monkeypatch.setattr(module, "current_time", lambda: moment(report["generated_at_utc"]))
    assert module.main([str(path), "--scan-exit-code", "2"]) == 0
    assert "EXPECTED_CLOSED_SESSION_REJECTION" in capsys.readouterr().out


@pytest.mark.parametrize("payload", [None, "not json", "[]", "null"])
def test_cli_missing_or_invalid_report_fails_with_diagnostic(tmp_path, capsys, payload):
    module = importlib.import_module("market_data.smoke_check")
    path = tmp_path / "report.json"
    if payload is not None:
        path.write_text(payload)
    assert module.main([str(path), "--scan-exit-code", "2"]) == 1
    assert "SMOKE_FAIL" in capsys.readouterr().out


def test_workflow_preserves_scan_status_runs_validation_and_always_uploads_evidence():
    workflow = (ROOT / ".github/workflows/market-watch-smoke.yml").read_text()
    assert "'tests/test_market_watch_smoke.py'" in workflow
    assert "id: scan" in workflow and "continue-on-error: true" in workflow
    assert 'echo "exit_code=$status" >> "$GITHUB_OUTPUT"' in workflow
    assert 'exit "$status"' in workflow
    assert "python -m market_data.smoke_check" in workflow
    assert '"${{ steps.scan.outputs.exit_code }}"' in workflow
    assert workflow.count("if: always()") >= 2
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "path: market-watch-smoke.json" in workflow
    assert "--github-alerts" not in workflow and "slack_alerts" not in workflow


@pytest.mark.parametrize("now,source,label", [
    ("2026-10-05T09:42:00-04:00", "2026-10-05T09:40:00-04:00", "FRESH_DATA_PASS"),
    ("2026-10-05T20:02:00-04:00", "2026-10-05T19:59:41-04:00", "EXPECTED_SESSION_SKIP"),
    ("2026-10-05T20:06:00-04:00", "2026-10-05T19:55:00-04:00", "EXPECTED_SESSION_SKIP"),
    ("2026-10-05T23:18:00-04:00", "2026-10-05T19:59:41-04:00", "EXPECTED_SESSION_SKIP"),
])
def test_real_scanner_and_one_shot_output_contract(monkeypatch, tmp_path, now, source, label):
    from market_data import us_watch
    from test_quote_validity import bar, install_synthetic_charts

    install_synthetic_charts(monkeypatch, receipt=now)
    original_chart = us_watch._chart

    def chart(fetcher, symbol, **kwargs):
        rows, meta, receipt = original_chart(fetcher, symbol, **kwargs)
        if kwargs["interval"] == "5m" and kwargs["range_value"] == "5d":
            stamp = (moment(now) - timedelta(minutes=2)).isoformat() if symbol in {"NQ=F", "ES=F"} else source
            rows = [bar(stamp)]
        return rows, meta, receipt

    monkeypatch.setattr(us_watch, "_chart", chart)
    output = tmp_path / "scan.json"
    us_watch.run(symbols=SYMBOLS, once=True, output=output, clock=lambda: moment(now))
    assert check(json.loads(output.read_text())) == label


@pytest.mark.parametrize("status", [0, 2, 124])
def test_actual_workflow_shell_records_and_preserves_exit_code(tmp_path, status):
    import re
    import subprocess

    workflow = (ROOT / ".github/workflows/market-watch-smoke.yml").read_text()
    scan_block = workflow.split("      - name: Scan NVDA", 1)[1].split("      - name:", 1)[0]
    run = re.search(r"        run: \|\n((?:          .*\n)+)", scan_block).group(1)
    script = "\n".join(line[10:] for line in run.splitlines())
    output = tmp_path / "step-output"
    result = subprocess.run(["bash", "-eo", "pipefail", "-c",
                             f'uv() {{ return {status}; }}; export GITHUB_OUTPUT="{output}";\n{script}'],
                            capture_output=True, text=True)
    assert result.returncode == status
    assert output.read_text() == f"exit_code={status}\n"


def test_qqq_only_expiry_after_close_is_safe_context_not_mixed_stock_failure():
    report = report_at("2026-10-05T20:07:00-04:00", "2026-10-05T19:59:41-04:00")
    rejected = report_at("2026-10-05T20:07:00-04:00", "2026-10-05T19:55:00-04:00")
    report["market_context"]["QQQ"] = rejected["market_context"]["QQQ"]
    assert check(report) == "FRESH_DATA_PASS"


def test_qqq_only_expiry_during_live_window_still_fails():
    report = report_at("2026-10-05T19:57:00-04:00", "2026-10-05T19:55:00-04:00")
    rejected = report_at("2026-10-05T19:57:00-04:00", "2026-10-05T19:40:00-04:00")
    report["market_context"]["QQQ"] = rejected["market_context"]["QQQ"]
    with pytest.raises(ValueError, match="invalid live proxy"):
        check(report)


def test_snapshot_receipt_fresh_at_scan_generation_and_report_age_bounded_separately():
    report = report_at()
    generated = moment(report["generated_at_utc"])
    row = report["rows"][0]
    source = moment(row["intraday"]["current_bar_time_utc"])
    receipt = generated - timedelta(seconds=120)
    row["known_at"] = receipt.isoformat()
    row["intraday"]["quote_validity"] = quote_evidence(
        source.timestamp(), receipt.isoformat(), generated,
        daily_date=previous_session_date(generated), require_daily=True)
    assert check(report, now=generated + timedelta(seconds=120)) == "EXPECTED_CLOSED_SESSION_REJECTION"
    with pytest.raises(ValueError, match="report time"):
        check(report, now=generated + timedelta(seconds=121))


def skipped_report(value="2026-10-05T20:02:00-04:00"):
    from market_data.quote_validity import session_name
    now = moment(value)
    session = session_name(now.timestamp())
    return {"scan_started_at_utc": now.isoformat(), "generated_at_utc": now.isoformat(),
            "generated_at_et": now.astimezone(ET).isoformat(), "runtime_session": session,
            "mode": "PUBLIC_HEADLESS_OBSERVATION", "trading_enabled": False,
            "symbols_requested": list(SYMBOLS), "rows": [], "alerts": [], "market_context": {},
            "sources": [], "scan_status": "SKIPPED", "incomplete_symbols": [],
            "skip_reason": "NON_TRADING_DAY" if session == "CLOSED" else "OUTSIDE_SESSION_HOURS"}


@pytest.mark.parametrize("value", [
    "2026-10-05T03:59:59-04:00", "2026-10-05T20:00:00-04:00",
    "2026-10-04T12:00:00-04:00", "2026-07-03T12:00:00-04:00",
    "2026-11-27T20:00:00-05:00", "2026-03-09T07:59:59+00:00", "2026-11-02T08:59:59+00:00",
])
def test_smoke_validates_calendar_backed_empty_skip(value):
    assert check(skipped_report(value)) == "EXPECTED_SESSION_SKIP"


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(runtime_session="CLOSED"),
    lambda p: p.update(skip_reason="NON_TRADING_DAY"),
    lambda p: p.update(scan_started_at_utc="2026-10-05T23:59:59+00:00"),
    lambda p: p.update(rows=[{"symbol": "NVDA", "status": "OK"}]),
    lambda p: p.update(alerts=[{"symbol": "NVDA"}]),
    lambda p: p.update(market_context={"QQQ": {"price": 100}}),
    lambda p: p.update(sources=["Yahoo Finance public chart"]),
    lambda p: p.update(incomplete_symbols=["NVDA"]),
    lambda p: p.update(publication_rejections=[{"reason": "stale"}]),
    lambda p: p.update(error="provider unavailable"),
    lambda p: p.update(symbols_requested=["AAPL"]),
    lambda p: p.update(trading_enabled=True),
    lambda p: p.pop("skip_reason"), lambda p: p.pop("runtime_session"),
    lambda p: p.pop("rows"), lambda p: p.pop("alerts"),
    lambda p: p.pop("market_context"), lambda p: p.pop("sources"), lambda p: p.pop("incomplete_symbols"),
])
def test_skip_cannot_hide_observations_or_spoof_session(mutate):
    report = skipped_report()
    mutate(report)
    with pytest.raises(ValueError):
        check(report)


@pytest.mark.parametrize("value", [
    "2026-10-05T04:00:00-04:00", "2026-10-05T10:00:00-04:00", "2026-10-05T19:59:59-04:00",
    "2026-11-27T13:00:00-05:00", "2026-03-09T08:00:00+00:00", "2026-11-02T09:00:00+00:00",
])
def test_skip_is_rejected_in_active_window(value):
    report = skipped_report(value)
    report["runtime_session"] = "OVERNIGHT"
    with pytest.raises(ValueError):
        check(report)


@pytest.mark.parametrize("exit_code", [1, 2, 124, 137])
def test_skip_requires_successful_exit(exit_code):
    with pytest.raises(ValueError, match="exit"):
        check(skipped_report(), exit_code=exit_code)


@pytest.mark.parametrize("seconds", [121, -6])
def test_skip_report_must_be_current(seconds):
    report = skipped_report()
    with pytest.raises(ValueError, match="report time"):
        check(report, now=moment(report["generated_at_utc"]) + timedelta(seconds=seconds))


def test_skip_cli_labels_no_acquisition_distinctly(tmp_path, monkeypatch, capsys):
    module = importlib.import_module("market_data.smoke_check")
    report = skipped_report()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    monkeypatch.setattr(module, "current_time", lambda: moment(report["generated_at_utc"]))
    assert module.main([str(path), "--scan-exit-code", "0"]) == 0
    output = capsys.readouterr().out
    assert "EXPECTED_SESSION_SKIP" in output
    assert "no market data was fetched" in output


def ended_report():
    report = skipped_report("2026-10-05T20:00:01-04:00")
    report.update(scan_status="SESSION_ENDED", scan_started_at_utc="2026-10-05T23:59:59+00:00",
                  runtime_session="POST", ending_session="OVERNIGHT", skip_reason="SESSION_ENDED_DURING_SCAN",
                  real_acquisition_failure=False, sources=["Yahoo Finance public chart"],
                  incomplete_symbols=sorted(SYMBOLS),
                  rows=[{"symbol": "NVDA", "status": "ERROR", "error": "RuntimeError: SESSION_ENDED_DURING_SCAN"}])
    return report


def test_smoke_validates_session_end_without_claiming_data_success():
    assert check(ended_report()) == "EXPECTED_SESSION_END"


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(scan_started_at_utc="2026-10-05T23:00:00+00:00", runtime_session="RTH"),
    lambda p: p.update(scan_started_at_utc="2026-10-06T00:00:00+00:00", runtime_session="OVERNIGHT"),
    lambda p: p.update(generated_at_utc="2026-10-05T23:59:59+00:00", generated_at_et="2026-10-05T19:59:59-04:00"),
    lambda p: p.update(ending_session="POST"),
    lambda p: p.update(skip_reason="OUTSIDE_SESSION_HOURS"),
    lambda p: p.update(alerts=[{"symbol": "NVDA"}]),
    lambda p: p.update(publication_rejections=[{"reason": "bad quote"}]),
    lambda p: p.update(source_comments=[{"id": 123}]),
    lambda p: p.update(publication_result={"published": 1, "source_comments": [{"id": 123}]}),
    lambda p: p.update(publication_result={"published": 0, "source_comments": [{"id": 123}]}),
    lambda p: p.update(incomplete_symbols=[]),
    lambda p: p["rows"][0].update(error="RuntimeError: provider failed"),
    lambda p: p["rows"][0].update(symbol="UNREQUESTED"),
    lambda p: p["rows"].append(deepcopy(p["rows"][0])),
    lambda p: p.pop("market_context"), lambda p: p.pop("ending_session"),
    lambda p: p.update(real_acquisition_failure=True),
    lambda p: p.update(real_acquisition_failure="false"), lambda p: p.pop("real_acquisition_failure"),
])
def test_session_ended_report_cannot_hide_bad_boundaries_or_publication(mutate):
    report = ended_report()
    mutate(report)
    with pytest.raises(ValueError):
        check(report)


@pytest.mark.parametrize("exit_code", [1, 2, 124, 137])
def test_session_end_requires_successful_exit(exit_code):
    with pytest.raises(ValueError, match="exit"):
        check(ended_report(), exit_code=exit_code)


def ended_report_with_publication():
    report = ended_report()
    report["publication_result"] = {"published": 1, "source_comments": [
        {"id": 123, "body": "<!-- qd-event:2026-10-05|NVDA|ENTRY_ARMED -->\nObservation only."}],
        "source_requests": [{"event_key": "2026-10-05|NVDA|ENTRY_ARMED", "comment_id": 123,
                             "requested_at_utc": "2026-10-05T23:59:59+00:00"}]}
    return report


def test_boundary_smoke_labels_confirmed_preclose_publication_separately():
    assert check(ended_report_with_publication()) == "EXPECTED_SESSION_END_WITH_PUBLICATION"


@pytest.mark.parametrize("mutate", [
    lambda p: p["publication_result"].update(published=True),
    lambda p: p["publication_result"].update(published=2),
    lambda p: p["publication_result"].update(source_requests=[]),
    lambda p: p["publication_result"]["source_requests"][0].update(requested_at_utc="2026-10-06T00:00:00+00:00"),
    lambda p: p["publication_result"]["source_requests"][0].update(requested_at_utc="2026-10-05T23:59:58+00:00"),
    lambda p: p["publication_result"]["source_requests"][0].update(comment_id=124),
    lambda p: p["publication_result"]["source_requests"][0].update(event_key="2026-10-05|AAPL|ENTRY_ARMED"),
    lambda p: p["publication_result"]["source_requests"][0].update(event_key="2026-10-04|NVDA|ENTRY_ARMED"),
    lambda p: p["publication_result"]["source_comments"][0].update(body="Unrelated comment"),
    lambda p: p["publication_result"]["source_comments"][0].update(body=""),
    lambda p: (p["publication_result"].update(published=2),
               p["publication_result"]["source_comments"].append(deepcopy(p["publication_result"]["source_comments"][0])),
               p["publication_result"]["source_requests"].append(deepcopy(p["publication_result"]["source_requests"][0]))),
])
def test_boundary_publication_needs_coherent_preclose_request_evidence(mutate):
    report = ended_report_with_publication()
    mutate(report)
    with pytest.raises(ValueError):
        check(report)
