from copy import deepcopy
from datetime import datetime, timezone

import pytest

from market_data import github_alerts, us_watch

NOW = int(datetime.fromisoformat('2026-10-05T13:41:00+00:00').timestamp()*1000)


def _quality(**changes):
    out = dict(state='VALID', reason_codes=[], observation_ok=True, confirmation_ok=True,
               evaluated_at_ms=NOW, valid_until_ms=NOW+60_000,
               expected_bar_end_ms=NOW-30_000, last_bar_end_ms=NOW-30_000, sample_count=2)
    out.update(changes)
    return out


def _event(state='ENTRY_CONFIRMED'):
    return dict(event_key=f'2026-10-05|NVDA|{state}', symbol='NVDA', state=state,
                quality=_quality(), quality_policy_id='us_public_5m_v1',
                valid_until_ms=NOW+60_000, captured_at_ms=NOW-10_000,
                known_at='2026-10-05T13:40:50+00:00', current_bar_time_utc='2026-10-05T13:35:00+00:00',
                relative_change_vs_qqq_pp=None)


def _mock_publication(monkeypatch, existing=''):
    monkeypatch.setenv('GITHUB_REPOSITORY', 'owner/repo')
    monkeypatch.setenv('GITHUB_TOKEN', 'token')
    calls=[]
    issue={'number':7, 'title':'Market Watch | 2026-10-05 ET', 'body':existing,
           'html_url':'https://example/7'}
    def fake_api(method, path, *, token, body=None):
        calls.append((method,path,deepcopy(body)))
        if path.startswith('/repos/owner/repo/issues?'): return [issue]
        if path.endswith('/comments?per_page=100'): return [{'id':99,'body':github_alerts.HEARTBEAT_MARK}]
        if method in {'POST','PATCH'}: return {'id':100,'body':body['body']}
        raise AssertionError((method,path,body))
    monkeypatch.setattr(github_alerts, '_api', fake_api)
    return calls


def _material_comments(calls):
    return [body['body'] for method,path,body in calls
            if method=='POST' and path.endswith('/comments')]



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


@pytest.mark.parametrize('delay', [60_000, 60_001])
def test_expired_confirmation_is_filtered_before_external_call(monkeypatch, delay):
    calls = _mock_publication(monkeypatch)
    event = _event()
    assert event['quality']['confirmation_ok'] is True
    out = github_alerts.publish(_report([event]), now_ms=NOW+delay)
    published_confirmation_comments = _material_comments(calls)
    assert published_confirmation_comments == []
    assert out['published'] == 0


@pytest.mark.parametrize('missing', ['quality', 'quality_policy_id', 'valid_until_ms'])
def test_missing_quality_does_not_publish_confirmation(monkeypatch, missing):
    calls = _mock_publication(monkeypatch); event = _event(); event.pop(missing)
    assert github_alerts.publish(_report([event]), now_ms=NOW)['published'] == 0
    assert _material_comments(calls) == []


@pytest.mark.parametrize('changes', [
    {'confirmation_ok':False}, {'observation_ok':False}, {'state':'UNAVAILABLE'},
    {'reason_codes':['STALE_RESPONSE']}, {'evaluated_at_ms':NOW+1},
    {'valid_until_ms':None}, {'valid_until_ms':True}, {'confirmation_ok':1},
])
def test_actual_quality_must_allow_confirmation(monkeypatch, changes):
    calls = _mock_publication(monkeypatch); event = _event(); event['quality'].update(changes)
    github_alerts.publish(_report([event]), now_ms=NOW)
    assert _material_comments(calls) == []


@pytest.mark.parametrize('valid_dependency', [False, True])
def test_populated_qqq_dependency_bounds_confirmation(monkeypatch, valid_dependency):
    calls = _mock_publication(monkeypatch); event = _event()
    event['relative_change_vs_qqq_pp'] = 0.7
    if valid_dependency:
        event['benchmark_dependency'] = {'change_pct':0.3,'reason_codes':[],
            'known_at':'2026-10-05T13:40:00+00:00', 'quality':_quality(valid_until_ms=NOW+5_000)}
    github_alerts.publish(_report([event]), now_ms=NOW+5_000)
    assert event['quality']['valid_until_ms'] > NOW+5_000
    assert _material_comments(calls) == []


def test_fresh_confirmation_has_source_evaluation_and_exclusive_expiry(monkeypatch):
    calls = _mock_publication(monkeypatch); event = _event(); original = deepcopy(event)
    out = github_alerts.publish(_report([event, event]), now_ms=NOW+59_999)
    comments = _material_comments(calls)
    assert out['published'] == 1 and len(comments) == 1
    for value in (event['known_at'], '2026-10-05T13:41:00+00:00', '2026-10-05T13:42:00+00:00'):
        assert value in comments[0]
    assert 'exclusive' in comments[0] and 'VALID' in comments[0]
    assert event == original


@pytest.mark.parametrize('state', ['ENTRY_ARMED','LEADER_HOT_NO_CHASE','LEADER_WATCH'])
def test_legacy_observation_states_are_explicitly_unvalidated(monkeypatch, state):
    calls = _mock_publication(monkeypatch); event = _event(state)
    for field in ('quality','quality_policy_id','valid_until_ms','captured_at_ms','known_at'):
        event.pop(field)
    out = github_alerts.publish(_report([event]), now_ms=NOW)
    assert out['published'] == 1
    assert 'LEGACY_UNVALIDATED' in _material_comments(calls)[0]
    assert 'confirmation_ok' not in event and 'quality' not in event


def test_local_health_incidents_do_not_become_new_external_events(monkeypatch):
    calls = _mock_publication(monkeypatch)
    events = [_event(s) for s in ('DATA_UNAVAILABLE','MARKET_CLOSED','LOCAL_HEALTH_INCIDENT')]
    assert github_alerts.publish(_report(events), now_ms=NOW)['published'] == 0
    assert _material_comments(calls) == []


def test_existing_event_marker_still_deduplicates(monkeypatch):
    event = _event(); calls = _mock_publication(monkeypatch, github_alerts._event_mark(event['event_key']))
    assert github_alerts.publish(_report([event]), now_ms=NOW)['published'] == 0
    assert _material_comments(calls) == []


def test_a2_alert_copies_original_timing_and_bounds_used_qqq_deadline(monkeypatch):
    quality = _quality(); dependency = {'change_pct':0.3, 'reason_codes':[],
        'known_at':'2026-10-05T13:40:00+00:00', 'quality':_quality(valid_until_ms=NOW+5_000)}
    row = dict(symbol='NVDA', status='OK', state='ENTRY_CONFIRMED', reason='fixture',
        quality=quality, quality_policy_id='us_public_5m_v1',
        quality_evidence={'captured_at_ms':NOW-10_000}, known_at='2026-10-05T13:40:50+00:00',
        benchmark_dependency=dependency, relative_change_vs_qqq_pp=0.7,
        intraday=dict(current_price=101, change_pct=1, premarket_change_pct=0.4,
            open_gap_pct=0.3, d5_atr=0.1, rth_vwap_approx=100.5,
            same_time_rvol=1, same_time_rvol_samples=20,
            current_bar_time_utc='2026-10-05T13:35:00+00:00'))
    monkeypatch.setattr(us_watch, '_market_context', lambda *a,**kw:{})
    monkeypatch.setattr(us_watch, '_scan_symbol', lambda *a,**kw:deepcopy(row))
    monkeypatch.setattr(us_watch, '_news', lambda *a,**kw:{'items':[]})
    report = us_watch.scan_once(('NVDA',), now=datetime.fromtimestamp(NOW/1000, timezone.utc))
    event = report['alerts'][0]
    assert event['quality'] == quality
    assert event['valid_until_ms'] == NOW+5_000
    assert event['captured_at_ms'] == NOW-10_000
    assert event['known_at'] == row['known_at']
    assert event['current_bar_time_utc'] == row['intraday']['current_bar_time_utc']
    assert event['benchmark_dependency'] == dependency


def test_live_publication_rechecks_clock_after_github_acquisition(monkeypatch):
    calls = _mock_publication(monkeypatch); base = github_alerts._api; current = [NOW]
    def delayed_api(method, path, **kwargs):
        result = base(method, path, **kwargs)
        if method == 'GET' and path.endswith('/comments?per_page=100'):
            current[0] = NOW+60_000
        return result
    monkeypatch.setattr(github_alerts, '_api', delayed_api)
    monkeypatch.setattr(github_alerts.time, 'time', lambda:current[0]/1000)
    github_alerts.publish(_report([_event()]))
    assert _material_comments(calls) == []


def test_injected_publication_clock_never_reads_wall_time(monkeypatch):
    calls = _mock_publication(monkeypatch)
    monkeypatch.setattr(github_alerts.time, 'time', lambda:pytest.fail('implicit wall-time read'))
    assert github_alerts.publish(_report([_event()]), now_ms=NOW)['published'] == 1
    assert len(_material_comments(calls)) == 1


@pytest.mark.parametrize('changes', [{'state':[]}, {'state':{}}, {'reason_codes':None}, {'sample_count':True}])
def test_malformed_quality_fails_closed_without_exception(monkeypatch, changes):
    calls = _mock_publication(monkeypatch); event = _event(); event['quality'].update(changes)
    assert github_alerts.publish(_report([event]), now_ms=NOW)['published'] == 0
    assert _material_comments(calls) == []


def test_inconsistent_benchmark_quality_cannot_enable_confirmation(monkeypatch):
    calls = _mock_publication(monkeypatch); event = _event()
    event['relative_change_vs_qqq_pp'] = 0.7
    event['benchmark_dependency'] = {'change_pct':0.3, 'reason_codes':[],
        'quality':_quality(state='UNAVAILABLE')}
    assert github_alerts.publish(_report([event]), now_ms=NOW)['published'] == 0
    assert _material_comments(calls) == []


def test_confirmation_deadline_cannot_overstate_actual_quality(monkeypatch):
    calls = _mock_publication(monkeypatch); event = _event(); event['valid_until_ms'] += 10_000
    assert github_alerts.publish(_report([event]), now_ms=NOW)['published'] == 0
    assert _material_comments(calls) == []


def test_observation_without_explicit_deadline_is_legacy_unvalidated(monkeypatch):
    calls = _mock_publication(monkeypatch); event = _event('ENTRY_ARMED'); event.pop('valid_until_ms')
    github_alerts.publish(_report([event]), now_ms=NOW)
    assert 'LEGACY_UNVALIDATED' in _material_comments(calls)[0]


def test_fresh_bounded_qqq_dependency_allows_confirmation(monkeypatch):
    calls = _mock_publication(monkeypatch); event = _event()
    event['relative_change_vs_qqq_pp'] = 0.7
    event['valid_until_ms'] = NOW+5_000
    event['benchmark_dependency'] = {'change_pct':0.3, 'reason_codes':[],
        'known_at':'2026-10-05T13:40:00+00:00', 'quality':_quality(valid_until_ms=NOW+5_000)}
    assert github_alerts.publish(_report([event]), now_ms=NOW+4_999)['published'] == 1
    assert len(_material_comments(calls)) == 1


@pytest.mark.parametrize('changes', [{'sample_count':0}, {'last_bar_end_ms':None}])
def test_confirmation_requires_actual_completed_evidence(monkeypatch, changes):
    calls = _mock_publication(monkeypatch); event = _event(); event['quality'].update(changes)
    assert github_alerts.publish(_report([event]), now_ms=NOW)['published'] == 0
    assert _material_comments(calls) == []
