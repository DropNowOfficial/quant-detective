"""Safe failure evidence; no remote I/O and no change to discovery/queue policy."""
from dataclasses import asdict
import json
from urllib.parse import urlsplit
import pytest
from market_data import slack_alerts as sa, slack_drain as sd
from test_slack_alerts import BASE, delivery_fixture, forward_run, restored
from test_slack_drain import execute, forward

SECRET='https://secret.invalid/token?private=raw-response'


def failed_discovery(result):
    def setup(http, store):
        original=http.request
        def request(method,url,**kwargs):
            if urlsplit(url).path == BASE+'/issues/comments':
                if isinstance(result,BaseException): raise result
                return result
            return original(method,url,**kwargs)
        http.request=request
    return forward_run(delivery_fixture(queued=False),setup=setup)


@pytest.mark.parametrize('status',[403,404,429,500,503])
def test_discovery_http_failure_retains_only_safe_evidence(status):
    summary,http=failed_discovery(sa.HttpResponse(status,{},SECRET.encode()))
    assert summary.paused_reason=='discovery_unavailable'
    assert not summary.discovery_complete and not http.server['slack_posts']
    assert summary.discovery_failure=={'stage':'list_comments','reason':'http_status','http_status':status,'page':1}
    assert SECRET not in json.dumps(asdict(summary))


@pytest.mark.parametrize('error,reason',[(TimeoutError(SECRET),'transport_timeout'),(OSError(SECRET),'transport_failure'),(sa.TransportFailure(SECRET),'transport_failure')])
def test_transport_failure_is_classified_without_exception_text(error,reason):
    summary,_=failed_discovery(error)
    assert summary.discovery_failure=={'stage':'list_comments','reason':reason,'http_status':None,'page':1}
    assert SECRET not in json.dumps(asdict(summary))


def test_invalid_json_records_response_status_without_body():
    summary,_=failed_discovery(sa.HttpResponse(200,{},SECRET.encode()))
    assert summary.discovery_failure['reason']=='invalid_json'
    assert summary.discovery_failure['http_status']==200


def test_duplicate_page_records_are_distinct_from_http_error():
    summary,_=failed_discovery(sa.HttpResponse(200,{'Date':'Wed, 07 Oct 2026 18:00:02 GMT'},b'[{"id":1},{"id":1}]'))
    assert summary.discovery_failure['reason']=='pagination_invalid'
    assert summary.discovery_failure['page']==1
    assert summary.discovery_failure['http_status']==200


def test_drain_retains_only_allowlisted_discovery_evidence(monkeypatch,capsys):
    evidence={'stage':'list_comments','reason':'http_status','http_status':503,'page':2,'body':SECRET}
    _,report,_=execute(sd,monkeypatch,capsys,[forward(delivered=0,paused_reason='discovery_unavailable',discovery_complete=False,discovery_failure=evidence)])
    assert report['discovery_failure']=={k:v for k,v in evidence.items() if k!='body'}
    assert SECRET not in json.dumps(report)


def test_drain_drops_arbitrary_diagnostic_values(monkeypatch,capsys):
    evidence={'stage':SECRET,'reason':SECRET,'http_status':True,'page':SECRET}
    _,report,_=execute(sd,monkeypatch,capsys,[forward(delivered=0,paused_reason='discovery_unavailable',discovery_failure=evidence)])
    assert report['discovery_failure']=={'stage':'unclassified','reason':'ledger_unavailable','http_status':None,'page':None}
    assert SECRET not in json.dumps(report)


def test_bad_server_date_is_diagnosed_without_header_content():
    summary,_=failed_discovery(sa.HttpResponse(200,{'Date':SECRET},b'[]'))
    assert summary.discovery_failure=={'stage':'list_comments','reason':'invalid_server_date','http_status':200,'page':1}


def test_cursor_write_failure_keeps_specific_discovery_stage_and_status():
    def setup(http,store):
        original=store.update_manifest
        def fail(value):
            raise sa.LedgerUnavailable(SECRET,reason='http_status',http_status=503)
        store.update_manifest=fail
    summary,http=forward_run(delivery_fixture(queued=False),setup=setup)
    assert summary.discovery_failure['stage']=='advance_cursor'
    assert summary.discovery_failure['http_status']==503
    assert SECRET not in json.dumps(asdict(summary))


def test_diagnostic_does_not_block_existing_safe_queue_progress():
    def setup(http,store):
        http.fail('GET',BASE+'/issues/comments',error=TimeoutError(SECRET))
    summary,http=forward_run(delivery_fixture(),setup=setup)
    assert summary.delivered==1 and summary.paused_reason=='discovery_unavailable'
    assert summary.discovery_failure['reason']=='transport_timeout'
    assert restored(http).entries[101].status=='delivered'


def test_second_page_failure_preserves_page_number():
    from test_slack_alerts import make_store,FakeHTTP,FakeClock,FakeBudget,NOW,make_config
    from urllib.parse import parse_qs
    prior=delivery_fixture(queued=False)
    def setup(http,store):
        original=http.request
        def request(method,url,**kwargs):
            if urlsplit(url).path==BASE+'/issues/comments':
                page=int(parse_qs(urlsplit(url).query)['page'][0])
                if page==1:
                    return sa.HttpResponse(200,{'Date':'Wed, 07 Oct 2026 18:00:02 GMT'},json.dumps([{'id':i} for i in range(1,101)]).encode())
                return sa.HttpResponse(502,{},SECRET.encode())
            return original(method,url,**kwargs)
        http.request=request
    summary,http=forward_run(prior,setup=setup)
    assert summary.discovery_failure=={'stage':'list_comments','reason':'http_status','http_status':502,'page':2}
    assert not summary.discovery_complete and not http.server['slack_posts']
