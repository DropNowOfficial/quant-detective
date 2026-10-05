import os

from market_data import github_alerts


def report(alerts=None):
    return {
        "generated_at_et":"2026-10-05T16:05:00-04:00",
        "coverage_scope":"CORE_FALLBACK",
        "market_wide":False,
        "decision_mode":"CORE_FALLBACK_OBSERVATION_ONLY",
        "rows":[
            {"symbol":"NVDA","status":"OK"},
            {"symbol":"TSM","status":"OK"},
        ],
        "alerts":list(alerts or []),
        "market_context":{
            "SPY":{"ok":True,"stale":False},
            "NQ=F":{"ok":True,"stale":True},
        },
    }


def test_heartbeat_is_updated_even_when_no_material_alerts(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY","owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN","token")
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
    out=github_alerts.publish(report())
    assert out["published"]==0
    assert out["heartbeat"]=="updated"
    patch=[c for c in calls if c[0]=="PATCH"]
    assert len(patch)==1
    assert "Last hosted fallback scan" in patch[0][2]["body"]
    assert "NQ=F" in patch[0][2]["body"]


def test_heartbeat_comment_is_created_once(monkeypatch):
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
    out=github_alerts.publish(report())
    assert out["heartbeat"]=="created"
    posts=[c for c in calls if c[0]=="POST"]
    assert len(posts)==1
    assert github_alerts.HEARTBEAT_MARK in posts[0][2]["body"]
