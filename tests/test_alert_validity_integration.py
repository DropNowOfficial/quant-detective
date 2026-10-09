"""Offline regression boundaries shared by source validation and forwarding."""
from market_data import github_alerts
from test_github_alerts import _issue, _created_comment
from test_quote_validity import moment
from test_slack_forwarding_workflow import steps, scalar


def test_missing_source_provenance_never_becomes_persisted_forwardable_event(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "DropNowOfficial/quant-detective")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "1369484548")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-test-token")
    event = {"event_key": "2026-10-07|NVDA|ENTRY_ARMED", "symbol": "NVDA",
             "state": "ENTRY_ARMED", "reason": "synthetic missing evidence"}
    report = {"generated_at_et": "2026-10-07T14:00:00-04:00", "alerts": [event]}
    saved = []

    def api(method, path, *, token, body=None):
        if "issues?" in path:
            return [_issue() | {"title": "Market Watch | 2026-10-07 ET"}]
        if method == "GET":
            return []
        saved.append(_created_comment(len(saved) + 1, body["body"]))
        return saved[-1]

    monkeypatch.setattr(github_alerts, "_api", api)
    result = github_alerts.publish(report, clock=lambda: moment("2026-10-07T14:00:00-04:00"))
    assert result["source_comments"] == []
    assert result["published"] == 0
    assert not any("<!-- qd-event:" in item["body"] for item in saved)
    assert report["publication_rejections"][0]["event_key"] == event["event_key"]
    assert report["publication_rejections"][0]["reason"] == "MISSING_CLASSIFICATION_INPUTS"


def test_scan_evidence_upload_runs_after_forwarding_and_summary_even_on_failure():
    blocks = steps()
    artifact = blocks["Preserve full scan evidence"]
    assert scalar(artifact, "if") == "always()"
    assert scalar(artifact, "uses") == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4.6.2"
    assert "path: market-watch.json" in artifact
    assert "retention-days: 30" in artifact
    assert "if-no-files-found: warn" in artifact
    names = list(blocks)
    assert names.index("Forward persisted GitHub observations to Slack") < names.index("Write compact run summary") < names.index("Preserve full scan evidence")
