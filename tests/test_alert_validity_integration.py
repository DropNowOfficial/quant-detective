"""Offline regression boundaries shared by source validation and forwarding."""
from market_data import github_alerts
from test_slack_forwarding_workflow import steps, scalar


def test_missing_source_provenance_never_becomes_persisted_forwardable_event(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-test-token")
    event = {"event_key": "2026-10-07|NVDA|ENTRY_ARMED", "symbol": "NVDA",
             "state": "ENTRY_ARMED", "reason": "synthetic missing evidence"}
    report = {"generated_at_et": "2026-10-07T14:00:00-04:00", "alerts": [event]}
    saved = []

    def api(method, path, *, token, body=None):
        if "issues?" in path:
            return [{"number": 7, "title": "Market Watch | 2026-10-07 ET"}]
        if method == "GET":
            return []
        saved.append({"id": len(saved) + 1, "body": body["body"],
                      "html_url": f"https://example/7#issuecomment-{len(saved) + 1}"})
        return saved[-1]

    monkeypatch.setattr(github_alerts, "_api", api)
    result = github_alerts.publish(report)
    assert result["source_comments"] == []
    assert result["published"] == 0
    assert not any("<!-- qd-event:" in item["body"] for item in saved)
    assert report["publication_rejections"][0]["event_key"] == event["event_key"]
    assert report["publication_rejections"][0]["reason"] == "MISSING_CLASSIFICATION_INPUTS"


def test_scan_evidence_upload_runs_after_forwarding_and_summary_even_on_failure():
    blocks = steps()
    artifact = blocks["Preserve full scan evidence"]
    assert scalar(artifact, "if") == "always()"
    assert scalar(artifact, "uses") == "actions/upload-artifact@v4"
    assert "path: market-watch.json" in artifact
    assert "retention-days: 30" in artifact
    assert "if-no-files-found: warn" in artifact
    names = list(blocks)
    assert names.index("Forward persisted GitHub observations to Slack") < names.index("Write compact run summary") < names.index("Preserve full scan evidence")
