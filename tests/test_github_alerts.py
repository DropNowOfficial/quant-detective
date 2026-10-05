from market_data import github_alerts


def _report(alerts=None):
    return {
        "generated_at_et":"2026-10-05T16:05:00-04:00",
        "rows":[
            {"symbol":"NVDA","status":"OK"},
            {"symbol":"TSM","status":"OK"},
        ],
        "alerts":list(alerts or []),
    }


def test_heartbeat_updates_even_without_material_alerts(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY","owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN","token")
    monkeypatch.setenv("GITHUB_EVENT_NAME","schedule")
    monkeypatch.setenv("GITHUB_RUN_ID","12345")
    monkeypatch.setenv("GITHUB_SHA","abcdef1234567890")
    calls=[]
    issue={"number":7,"title":"Market Watch | 2026-10-05 ET","body":"","html_url":"https://example/7"}
    heartbeat={"id":99,"body":"<!-- qd-heartbeat --> old"}

    def fake_api(method,path,*,token,body=None):
        calls.append((method,path,body))
        if path.startswith("/repos/owner/repo/issues?"):
            return [issue]
        if path=="/repos/owner/repo/issues/7/comments?per_page=100":
            return [heartbeat]
        if method=="PATCH" and path=="/repos/owner/repo/issues/comments/99":
            return {"id":99,"body":body["body"]}
        raise AssertionError((method,path,body))

    monkeypatch.setattr(github_alerts,"_api",fake_api)
    out=github_alerts.publish(_report())
    assert out["published"]==0
    assert out["heartbeat"]=="updated"
    patches=[c for c in calls if c[0]=="PATCH"]
    assert len(patches)==1
    assert "Last hosted fallback scan" in patches[0][2]["body"]
    assert "Trigger: **schedule**" in patches[0][2]["body"]
    assert "12345" in patches[0][2]["body"]
    assert "abcdef123456" in patches[0][2]["body"]
    assert "https://github.com/owner/repo/actions/runs/12345" in patches[0][2]["body"]


def test_heartbeat_created_when_missing(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY","owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN","token")
    calls=[]
    issue={"number":7,"title":"Market Watch | 2026-10-05 ET","body":"","html_url":"https://example/7"}

    def fake_api(method,path,*,token,body=None):
        calls.append((method,path,body))
        if path.startswith("/repos/owner/repo/issues?"):
            return [issue]
        if path=="/repos/owner/repo/issues/7/comments?per_page=100":
            return []
        if method=="POST" and path=="/repos/owner/repo/issues/7/comments":
            return {"id":100,"body":body["body"]}
        raise AssertionError((method,path,body))

    monkeypatch.setattr(github_alerts,"_api",fake_api)
    out=github_alerts.publish(_report())
    assert out["heartbeat"]=="created"
    posts=[c for c in calls if c[0]=="POST"]
    assert len(posts)==1
    assert github_alerts.HEARTBEAT_MARK in posts[0][2]["body"]
