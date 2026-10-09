"""Synthetic temporal regressions; these are not actual market signals."""
from datetime import datetime, timedelta, timezone

import pytest

from market_data import us_watch


def moment(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def bar(value, close=101.0, volume=100):
    return {"t": int(moment(value).timestamp()), "open": 100.0, "high": 101.2,
            "low": 99.8, "close": close, "volume": volume}


def daily():
    return {"ma5": 100.0, "atr5": 10.0, "prior_close": 100.0,
            "ma5_slope_1d": 1.0, "ma5_3point_slope": 0.8,
            "previous_session_date": "2026-10-02"}


def rth_pair():
    return [bar("2026-10-05T09:30:00-04:00", 100.6),
            bar("2026-10-05T09:35:00-04:00", 101.0)]


def history_pair():
    return [bar("2026-10-02T09:30:00-04:00"), bar("2026-10-02T09:35:00-04:00")]


def test_fresh_post_quote_cannot_reuse_old_rth_confirmation():
    now = moment("2026-10-05T16:07:00-04:00")
    rows = rth_pair() + [bar("2026-10-05T16:05:00-04:00")]
    metrics = us_watch._intraday_metrics(rows, daily(), now, history_rows=history_pair(), received_at=now.isoformat())
    assert us_watch.classify(daily(), metrics)["state"] != "ENTRY_CONFIRMED"


def test_ten_hour_old_quote_is_diagnostic_only():
    now = moment("2026-10-05T19:40:00-04:00")
    metrics = us_watch._intraday_metrics(rth_pair(), daily(), now, history_rows=history_pair(), received_at=now.isoformat())
    assert us_watch.classify(daily(), metrics)["state"] == "DATA_INVALID"


def metrics_at(now, rows=None, received=None, daily_values=None):
    return us_watch._intraday_metrics(rows or rth_pair(), daily_values or daily(), moment(now),
        history_rows=history_pair(), received_at=received or now)


@pytest.mark.parametrize("now,allowed", [
    ("2026-10-05T10:07:00-04:00", True),
    ("2026-10-05T10:10:59-04:00", True),
    ("2026-10-05T10:11:00-04:00", False),
])
def test_latest_completed_bar_uses_next_bar_plus_sixty_second_grace(now, allowed):
    rows = [bar("2026-10-05T09:55:00-04:00", 100.6), bar("2026-10-05T10:00:00-04:00")]
    metrics = metrics_at(now, rows)
    assert metrics["quote_validity"]["eligible"] is allowed
    assert metrics["confirmation_validity"]["eligible"] is allowed


@pytest.mark.parametrize("age,allowed", [(120, True), (120.001, False), (-5, True), (-5.001, False)])
def test_original_receipt_age_and_clock_skew(age, allowed):
    now = moment("2026-10-05T09:42:00-04:00")
    metrics = metrics_at(now.isoformat(), received=(now - timedelta(seconds=age)).isoformat())
    assert metrics["quote_validity"]["eligible"] is allowed


def test_unfinished_bar_is_latest_price_without_becoming_confirmation_later():
    receipt = "2026-10-05T09:36:00-04:00"
    metrics = metrics_at("2026-10-05T09:41:00-04:00", received=receipt)
    assert metrics["current_price"] == 101.0
    assert metrics["rth_completed_bars"] == 1
    assert not metrics["two_completed_5m_above_vwap_and_ma5"]
    assert not metrics["quote_validity"]["eligible"]
    fresh = metrics_at(receipt, received=receipt)
    assert fresh["quote_validity"]["eligible"]
    assert not fresh["quote_validity"]["completed_at_receipt"]
    assert not fresh["confirmation_validity"]["eligible"]


@pytest.mark.parametrize("time,session", [
    ("2026-10-05T08:07:00-04:00", "PRE"),
    ("2026-10-05T16:07:00-04:00", "POST"),
])
def test_fresh_extended_hours_observations_remain_available(time, session):
    start = moment(time) - timedelta(minutes=2)
    rows = [r for r in rth_pair() if r["t"] <= moment(time).timestamp()]
    metrics = metrics_at(time, rows + [bar(start.isoformat())])
    state = us_watch.classify(daily(), metrics)
    assert metrics["current_session"] == session
    assert metrics["quote_validity"]["eligible"]
    assert state["state"] == "ENTRY_ARMED"


@pytest.mark.parametrize("time,session", [
    ("2026-11-27T12:59:00-05:00", "RTH"),
    ("2026-11-27T13:00:00-05:00", "POST"),
    ("2026-07-03T10:00:00-04:00", "CLOSED"),
    ("2026-10-04T10:00:00-04:00", "CLOSED"),
    ("2026-03-09T13:30:00+00:00", "RTH"),
    ("2026-11-02T14:30:00+00:00", "RTH"),
])
def test_real_exchange_calendar_handles_early_close_holidays_and_dst(time, session):
    assert us_watch._session_name(moment(time).timestamp()) == session


@pytest.mark.parametrize("received", [None, "", "invalid", "2026-10-05T09:42:00", "2026-10-05T10:42:00-04:00"])
def test_missing_malformed_naive_or_future_receipt_is_diagnostic(received):
    metrics = us_watch._intraday_metrics(rth_pair(), daily(), moment("2026-10-05T09:42:00-04:00"), received_at=received)
    assert us_watch.classify(daily(), metrics)["state"] == "DATA_INVALID"
    assert metrics["quote_validity"]["rejection_reasons"]


def test_future_source_bar_is_diagnostic():
    metrics = metrics_at("2026-10-05T10:07:00-04:00", [bar("2026-10-05T10:10:00-04:00")])
    assert not metrics["quote_validity"]["eligible"]


def test_gap_between_confirmation_bars_does_not_confirm():
    rows = [bar("2026-10-05T09:50:00-04:00", 100.6), bar("2026-10-05T10:00:00-04:00")]
    metrics = metrics_at("2026-10-05T10:07:00-04:00", rows)
    assert not metrics["confirmation_validity"]["eligible"]
    assert us_watch.classify(daily(), metrics)["state"] == "ENTRY_ARMED"


def test_daily_source_must_be_previous_actual_session():
    values = daily() | {"previous_session_date": "2026-10-01"}
    metrics = metrics_at("2026-10-05T09:42:00-04:00", daily_values=values)
    assert not metrics["quote_validity"]["eligible"]
    assert "DAILY_SESSION_MISMATCH" in metrics["quote_validity"]["rejection_reasons"]


def event_at(value="2026-10-05T09:42:00-04:00", rows=None, values=None):
    values = values or daily()
    intra = metrics_at(value, rows, daily_values=values)
    state = us_watch.classify(values, intra)
    return {"event_key": f"2026-10-05|AAA|{state['state']}", "symbol": "AAA",
            **state, **{k: v for k, v in intra.items() if k not in {"quote_validity", "confirmation_validity"}},
            "quote_validity": intra["quote_validity"], "confirmation_validity": intra["confirmation_validity"],
            "classification_inputs": {"daily": values, "intraday": intra}, "market_context": {}}


def test_publish_expiry_uses_original_receipt_not_report_generation():
    from market_data.quote_validity import publication_event
    event = event_at()
    allowed, reason = publication_event(event, now=moment("2026-10-05T09:44:00-04:00"))
    assert allowed is not None and reason is None
    rejected, reason = publication_event(event, now=moment("2026-10-05T09:44:01-04:00"))
    assert rejected is None
    assert "RECEIPT_EXPIRED" in reason
    assert event["quote_validity"]["received_at_utc"] == "2026-10-05T09:42:00-04:00"


def test_confirmation_cannot_be_published_after_market_close():
    from market_data.quote_validity import publication_event
    rows = [bar("2026-10-05T15:45:00-04:00", 100.6), bar("2026-10-05T15:50:00-04:00")]
    event = event_at("2026-10-05T15:59:00-04:00", rows)
    # Existing same-time RVOL formula has a valid, deliberately small synthetic history.
    assert event["state"] == "ENTRY_CONFIRMED"
    published, reason = publication_event(event, now=moment("2026-10-05T16:00:00-04:00"))
    assert published is None
    assert "NOT_CURRENT_RTH" in reason


def test_publication_missing_timing_metadata_fails_closed():
    from market_data.quote_validity import publication_event
    event = event_at()
    event["quote_validity"].pop("source_bar_end_utc")
    assert publication_event(event, now=moment("2026-10-05T09:42:00-04:00"))[0] is None


def test_stale_qqq_drops_relative_strength_not_independent_stock_leader():
    from market_data.quote_validity import publication_event, quote_evidence
    now = moment("2026-10-05T09:42:00-04:00")
    values = daily() | {"ma5_slope_1d": -1}
    event = event_at(now.isoformat(), values=values)
    event["market_context"] = {"QQQ": {"ok": True, "change_pct": 0.0, "price": 100,
        "quote_validity": quote_evidence(moment("2026-10-05T09:35:00-04:00").timestamp(),
            "2026-10-05T09:39:59-04:00", now)},
        "NQ=F": {"ok": True, "price": 20000, "change_pct": 2.0}}
    allowed, reason = publication_event(event, now=now)
    assert reason is None
    assert allowed["state"] == "LEADER_WATCH"
    assert allowed["relative_change_vs_qqq_pp"] is None
    assert any(s.startswith("day +") for s in allowed["leader_reasons"])
    assert not any(s.startswith("vs QQQ") for s in allowed["leader_reasons"])
    assert allowed["market_context"]["NQ=F"]["change_pct"] is None


def test_stale_qqq_only_leader_does_not_publish():
    from market_data.quote_validity import publication_event, quote_evidence
    now = moment("2026-10-05T09:42:00-04:00")
    values = daily() | {"ma5_slope_1d": -1}
    event = event_at(now.isoformat(), values=values)
    intra = event["classification_inputs"]["intraday"]
    intra.update(change_pct=0.7, premarket_change_pct=0.0, open_gap_pct=0.0, move_from_rth_open_pct=0.0)
    event.update(us_watch.classify(values, intra, qqq_change=0.0))
    event["market_context"] = {"QQQ": {"ok": True, "change_pct": 0.0,
        "quote_validity": quote_evidence(moment("2026-10-05T09:35:00-04:00").timestamp(),
            "2026-10-05T09:39:59-04:00", now)}}
    assert publication_event(event, now=now)[0] is None


def install_synthetic_charts(monkeypatch, *, receipt="2026-10-05T09:42:00-04:00", stale_proxy=False):
    from market_data.quote_validity import _calendar
    us_watch._DAILY_CACHE.clear()
    us_watch._VOLUME_PROFILE_CACHE.clear()
    us_watch._NEWS_CACHE.clear()
    sessions = _calendar(2026).sessions_in_range("2026-08-25", "2026-10-02")[-25:]
    daily_rows = []
    for i, day in enumerate(sessions):
        close = 98 + i * .1
        daily_rows.append({"t": int(day.timestamp()) + 14 * 3600,
                           "open": close, "high": close + .5, "low": close - .5,
                           "close": close, "volume": 1000})
    rows = [bar("2026-10-05T09:30:00-04:00", 100.25), bar("2026-10-05T09:35:00-04:00", 100.3)]
    for row in rows:
        row.update(high=100.35, low=99.95)

    def chart(fetcher, symbol, *, range_value, interval, **kwargs):
        known = receipt
        if stale_proxy and symbol in {"QQQ", "NQ=F", "ES=F"}:
            known = "2026-10-05T09:39:00-04:00"
        result = daily_rows if interval == "1d" else history_pair() if range_value == "1mo" else rows
        return result, {"previousClose": 100.0}, {"received_at_utc": known}

    monkeypatch.setattr(us_watch, "_chart", chart)
    monkeypatch.setattr(us_watch, "_news", lambda *args: {"items": []})


def test_scan_preserves_source_receipt_and_full_confirmation_evidence(monkeypatch):
    install_synthetic_charts(monkeypatch)
    report = us_watch.scan_once(symbols=("AAA",), now=moment("2026-10-05T09:42:00-04:00"))
    assert report["rows"][0]["state"] == "ENTRY_CONFIRMED"
    event = report["alerts"][0]
    assert event["quote_validity"]["received_at_utc"] == "2026-10-05T09:42:00-04:00"
    assert event["quote_validity"]["source_bar_start_utc"] == "2026-10-05T13:35:00+00:00"
    assert len(event["confirmation_validity"]["bars"]) == 2
    assert event["classification_inputs"]["daily"]["previous_session_date"] == "2026-10-02"


def test_scan_rechecks_after_news_delay_without_refreshing_receipt(monkeypatch):
    install_synthetic_charts(monkeypatch)
    current = [moment("2026-10-05T09:42:00-04:00")]
    def news(*args):
        current[0] = moment("2026-10-05T09:44:01-04:00")
        return {"items": []}
    monkeypatch.setattr(us_watch, "_news", news)
    report = us_watch.scan_once(symbols=("AAA",), clock=lambda: current[0])
    assert not report["alerts"]
    assert report["rows"][0]["state"] == "DATA_INVALID"
    assert report["rows"][0]["known_at"] == "2026-10-05T09:42:00-04:00"
    assert report["rows"][0]["intraday"]["quote_validity"]["rejection_reasons"] == ["RECEIPT_EXPIRED"]


def test_scan_excludes_stale_proxy_values_without_killing_valid_stock(monkeypatch):
    install_synthetic_charts(monkeypatch, stale_proxy=True)
    report = us_watch.scan_once(symbols=("AAA",), now=moment("2026-10-05T09:42:00-04:00"))
    assert report["alerts"]
    assert report["alerts"][0]["relative_change_vs_qqq_pp"] is None
    for value in report["market_context"].values():
        assert not value["ok"]
        assert value["change_pct"] is None
        assert value["observed_price"] is not None


def test_each_github_publication_revalidates_without_false_event_markers(monkeypatch):
    from market_data import github_alerts
    monkeypatch.setenv("GITHUB_REPOSITORY", "DropNowOfficial/quant-detective")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1369484548")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-test-token")
    first = event_at()
    second = event_at() | {"symbol": "BBB", "event_key": "2026-10-05|BBB|ENTRY_CONFIRMED"}
    report = {"generated_at_et": "2026-10-05T09:42:00-04:00", "alerts": [first, second]}
    posts = []
    current = [moment("2026-10-05T09:42:00-04:00")]
    def api(method, path, *, token, body=None):
        if "issues?" in path:
            return [{"number": 7, "title": "Market Watch | 2026-10-05 ET",
                     "user": {"id": 41898282, "type": "Bot"},
                     "url": "https://api.github.com/repos/DropNowOfficial/quant-detective/issues/7",
                     "html_url": "https://github.com/DropNowOfficial/quant-detective/issues/7"}]
        if method == "GET":
            return []
        if method == "POST":
            posts.append(body["body"])
            if "<!-- qd-event:" in body["body"]:
                current[0] = moment("2026-10-05T09:44:01-04:00")
            return {"id": len(posts), "body": body["body"], "user": {"id": 41898282, "type": "Bot"},
                    "issue_url": "https://api.github.com/repos/DropNowOfficial/quant-detective/issues/7",
                    "url": f"https://api.github.com/repos/DropNowOfficial/quant-detective/issues/comments/{len(posts)}",
                    "html_url": f"https://github.com/DropNowOfficial/quant-detective/issues/7#issuecomment-{len(posts)}"}
        raise AssertionError(method)
    monkeypatch.setattr(github_alerts, "_api", api)
    result = github_alerts.publish(report, clock=lambda: current[0])
    assert result["published"] == 1
    material = [body for body in posts if "<!-- qd-event:" in body]
    assert len(material) == 1
    assert "BBB" not in material[0]
    assert "Source bar" in material[0] and "HTTP receipt" in material[0]
    assert report["publication_rejections"][0]["event_key"] == second["event_key"]
    assert "RECEIPT_EXPIRED" in report["publication_rejections"][0]["reason"]


def test_publisher_failure_still_persists_full_report_and_diagnostics(monkeypatch, tmp_path):
    import json
    from market_data import github_alerts
    report = {"rows": [{"symbol": "AAA", "status": "OK"}], "alerts": [event_at()]}
    monkeypatch.setattr(us_watch, "scan_once", lambda **kwargs: report)
    def publish(value):
        value["publication_rejections"] = [{"event_key": "key", "reason": "RECEIPT_EXPIRED"}]
        raise RuntimeError("synthetic publication failure")
    monkeypatch.setattr(github_alerts, "publish", publish)
    output = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="synthetic publication failure"):
        us_watch.run(symbols=("AAA",), once=True, github_alerts=True, output=output)
    assert json.loads(output.read_text()) == report


def test_workflow_always_retains_full_scan_artifact():
    from pathlib import Path
    text = Path(".github/workflows/market-watch.yml").read_text()
    assert "uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in text
    step = text.split("uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02")[0].split("- name:")[-1]
    assert "if: always()" in step
    artifact = text.split("uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02", 1)[1]
    assert "path: market-watch.json" in artifact
    assert "retention-days: 30" in artifact


def test_new_incomplete_price_does_not_revive_expired_confirmation():
    rows = [bar("2026-10-05T09:55:00-04:00", 100.6), bar("2026-10-05T10:00:00-04:00"),
            bar("2026-10-05T10:10:00-04:00", 101.2)]
    metrics = metrics_at("2026-10-05T10:12:00-04:00", rows)
    assert metrics["current_price"] == 101.2
    assert metrics["quote_validity"]["eligible"]
    assert not metrics["quote_validity"]["completed_at_receipt"]
    assert not metrics["confirmation_validity"]["eligible"]
    assert us_watch.classify(daily(), metrics)["state"] == "ENTRY_ARMED"


def test_zero_volume_latest_completed_bar_cannot_be_skipped_for_confirmation():
    rows = [bar("2026-10-05T09:50:00-04:00", 100.6), bar("2026-10-05T09:55:00-04:00"),
            bar("2026-10-05T10:00:00-04:00", 101.2, volume=0)]
    metrics = metrics_at("2026-10-05T10:07:00-04:00", rows)
    assert not metrics["two_completed_5m_above_vwap_and_ma5"]
    assert us_watch.classify(daily(), metrics)["state"] == "ENTRY_ARMED"


def test_daily_previous_session_skips_holiday_weekend():
    from market_data.quote_validity import previous_session_date
    assert previous_session_date(moment("2026-07-06T10:00:00-04:00")) == "2026-07-02"


def test_temporal_gate_does_not_rewrite_historical_vwap_math():
    rows = rth_pair() + [bar("2026-10-05T16:05:00-04:00")]
    metrics = metrics_at("2026-10-05T16:07:00-04:00", rows)
    assert metrics["two_completed_5m_above_vwap_and_ma5"]
    assert not metrics["confirmation_validity"]["eligible"]
    assert us_watch.classify(daily(), metrics)["state"] == "ENTRY_ARMED"


def test_stale_daily_response_is_not_cached_against_later_recovery(monkeypatch):
    install_synthetic_charts(monkeypatch)
    original_chart = us_watch._chart
    daily_calls = []
    def chart(fetcher, symbol, *, interval, **kwargs):
        rows, meta, receipt = original_chart(fetcher, symbol, interval=interval, **kwargs)
        if interval == "1d":
            daily_calls.append(symbol)
            if len(daily_calls) == 1:
                rows = rows[:-1]
        return rows, meta, receipt
    monkeypatch.setattr(us_watch, "_chart", chart)
    now = moment("2026-10-05T09:42:00-04:00")
    first = us_watch._scan_symbol("AAA", None, now)
    second = us_watch._scan_symbol("AAA", None, now)
    assert first["status"] == "INVALID_DATA"
    assert second["status"] == "OK"
    assert daily_calls == ["AAA", "AAA"]


@pytest.mark.parametrize("proof", [{"bars": [None]}, {"bars": "bad"}, ["bad"]])
def test_malformed_confirmation_is_rejected_without_aborting_later_publications(monkeypatch, proof):
    from market_data import github_alerts
    monkeypatch.setenv("GITHUB_REPOSITORY", "DropNowOfficial/quant-detective")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1369484548")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-test-token")
    invalid = event_at() | {"confirmation_validity": proof}
    valid = event_at() | {"symbol": "BBB", "event_key": "2026-10-05|BBB|ENTRY_CONFIRMED"}
    report = {"generated_at_et": "2026-10-05T09:42:00-04:00", "alerts": [invalid, valid]}
    posts = []
    def api(method, path, *, token, body=None):
        if "issues?" in path:
            return [{"number": 7, "title": "Market Watch | 2026-10-05 ET",
                     "user": {"id": 41898282, "type": "Bot"},
                     "url": "https://api.github.com/repos/DropNowOfficial/quant-detective/issues/7",
                     "html_url": "https://github.com/DropNowOfficial/quant-detective/issues/7"}]
        if method == "GET":
            return []
        posts.append(body["body"])
        return {"id": len(posts), "body": body["body"], "user": {"id": 41898282, "type": "Bot"},
                    "issue_url": "https://api.github.com/repos/DropNowOfficial/quant-detective/issues/7",
                    "url": f"https://api.github.com/repos/DropNowOfficial/quant-detective/issues/comments/{len(posts)}",
                    "html_url": f"https://github.com/DropNowOfficial/quant-detective/issues/7#issuecomment-{len(posts)}"}
    monkeypatch.setattr(github_alerts, "_api", api)
    result = github_alerts.publish(report, clock=lambda: moment("2026-10-05T09:42:00-04:00"))
    assert result["published"] == 1
    assert len(report["publication_rejections"]) == 1
    material = [body for body in posts if "<!-- qd-event:" in body]
    assert len(material) == 1 and "BBB" in material[0]


def test_rejected_future_receipt_cannot_age_into_an_eligible_snapshot():
    from market_data.quote_validity import refresh_intraday
    metrics = metrics_at("2026-10-05T09:39:40-04:00", received="2026-10-05T09:40:00-04:00")
    assert us_watch.classify(daily(), metrics)["state"] == "DATA_INVALID"
    later = refresh_intraday(metrics, moment("2026-10-05T09:40:00-04:00"))
    assert us_watch.classify(daily(), later)["state"] == "DATA_INVALID"
    assert "RECEIPT_IN_FUTURE" in later["quote_validity"]["rejection_reasons"]


def test_final_2002_scan_can_observe_fresh_post_bar_without_confirming():
    metrics = metrics_at("2026-10-05T20:02:00-04:00", [bar("2026-10-05T19:55:00-04:00")])
    assert metrics["quote_validity"]["eligible"]
    assert metrics["current_session"] == "POST"
    assert metrics["quote_validity"]["evaluation_session"] == "OVERNIGHT"
    assert us_watch.classify(daily(), metrics)["state"] == "ENTRY_ARMED"


def test_alert_displays_separate_proxy_source_and_receipt_times():
    from market_data.github_alerts import _event_markdown
    from market_data.quote_validity import quote_evidence
    event = event_at()
    event["market_context"] = {"NQ=F": {"change_pct": 1.0, "quote_validity": quote_evidence(
        moment("2026-10-05T09:40:00-04:00").timestamp(), "2026-10-05T09:41:53-04:00",
        moment("2026-10-05T09:42:00-04:00"), equity=False)}}
    text = _event_markdown(event)
    assert "NQ=F source bar" in text
    assert "2026-10-05T13:45:00+00:00" in text
    assert "2026-10-05T09:41:53-04:00" in text


def test_post_quote_cannot_publish_at_runtime_boundary():
    from market_data.quote_validity import publication_event
    event = event_at("2026-10-05T19:59:59-04:00", rows=[bar("2026-10-05T19:55:00-04:00")])
    assert event["quote_validity"]["eligible"]
    assert publication_event(event, now=moment("2026-10-05T19:59:59-04:00"))[0] is not None
    checked, reason = publication_event(event, now=moment("2026-10-05T20:00:00-04:00"))
    assert checked is None
    assert reason == "OUTSIDE_SUPPORTED_RUNTIME_SESSION"
