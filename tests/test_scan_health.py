"""Offline incident journal, trusted-source and real durable forwarding tests."""
from copy import deepcopy
import json
import os
import subprocess
import sys

import pytest
from market_data import scan_health as health, slack_alerts as sa
from test_slack_alerts import (NOW, LEDGER_ISSUE, REPO, BASE, AUTHOR, WEBHOOK, FakeClock,
                               FakeBudget, FakeHTTP, DeliveryHTTP, make_config, make_store, cli_env)


def report(status='COMPLETE', ok=('NVDA', 'TSM'), missing=()):
    return {'generated_at_et': NOW.astimezone(sa.ET).isoformat(), 'scan_status': status,
            'symbols_requested': list(ok + missing), 'runtime_session': 'RTH', 'alerts': [],
            'rows': [{'symbol': s, 'status': 'OK'} for s in ok]
                    + [{'symbol': s, 'status': 'ERROR', 'error': 'BAR_EXPIRED'} for s in missing]}


class HealthHTTP(DeliveryHTTP):
    def request(self, method, url, *, headers, body, timeout):
        if method == 'POST' and body and json.loads(body).get('body', '').startswith('<!-- qd-event:'):
            return FakeHTTP.request(self, method, url, headers=headers, body=body, timeout=timeout)
        return super().request(method, url, headers=headers, body=body, timeout=timeout)

    def add_comment(self, comment_id, body, **kwargs):
        return super().add_comment(comment_id, body, **({'created_at': self.clock.now()} | kwargs))


def runtime():
    clock = FakeClock()
    http = HealthHTTP(clock=clock)
    store = make_store(http)
    store.initialize()
    return clock, http, store


def publish(data, clock, http, store, outcome='success', test=False):
    http.date = clock.now()
    return health.publish_health(data, outcome=outcome, github=store.github, store=store,
                                 clock=clock, delivery_test=test, run_id='999')


def test_coverage_and_missing_evidence_are_distinct_without_raw_errors():
    partial = report('INCOMPLETE', ('NVDA',), ('TSM',))
    assert health.assess(partial, 'failure')['state'] == 'SCAN_DEGRADED'
    assert health.assess(report('UNAVAILABLE', (), ('NVDA', 'TSM')), 'failure')['state'] == 'SCAN_UNAVAILABLE'
    assert health.assess(None, 'failure')['state'] == 'SCAN_FAILED'
    assert health.assess(report(), 'failure')['state'] == 'SCAN_FAILED'
    partial['rows'][1]['error'] = 'private-token-not-for-notifications'
    assert 'private-token' not in json.dumps(health.assess(partial, 'failure'))


@pytest.mark.parametrize('status', ['SKIPPED', 'SESSION_ENDED'])
def test_expected_session_stops_quiet_but_failed_process_not_quiet(status):
    stopped = report(status, ('NVDA',), ('TSM',))
    assert health.assess(stopped, 'success') is None
    assert health.assess(stopped, 'failure') is not None
    stopped.update(real_acquisition_failure=True, ending_session='OVERNIGHT')
    assessment = health.assess(stopped, 'failure')
    assert assessment is not None and 'actual acquisition errors' in assessment['action']


@pytest.mark.parametrize('rows', [[{'symbol': [], 'status': 'OK'}],
                                  [{'symbol': 'NVDA', 'status': []}],
                                  [{'symbol': 'NVDA', 'status': 'OK'}] * 2,
                                  [{'symbol': 'NVDA', 'status': 'OK'}, {'symbol': 'TSM', 'status': 'SURPRISE'}],
                                  [{'symbol': 'NVDA', 'status': 'OK'}, {'symbol': 'FAKE', 'status': 'OK'}]])
def test_malformed_rows_fail_closed_without_hashing_untrusted_values(rows):
    assert health.assess(report() | {'rows': rows}, 'success')['state'] == 'SCAN_FAILED'


def test_healthy_start_and_offhours_do_not_manufacture_incidents():
    clock, http, store = runtime()
    assert publish(report(), clock, http, store)['reason'] == 'healthy_no_incident'
    assert publish({'scan_status': 'SKIPPED'}, clock, http, store)['reason'] == 'session_skipped'
    assert not any(c['body'].startswith('<!-- qd-event:') for c in http.server['comments'].values())


def test_restart_date_rollover_dedup_and_recovery_cooldown():
    clock, http, store = runtime()
    partial = report('INCOMPLETE', ('NVDA',), ('TSM',))
    assert publish(partial, clock, http, store, 'failure')['published'] == 1
    clock.sleep(300)
    fresh_http = HealthHTTP(http.server, clock=clock)
    fresh = make_store(fresh_http)
    assert publish(partial, clock, fresh_http, fresh, 'failure')['reason'] == 'unchanged'
    assert publish(report(), clock, fresh_http, fresh)['reason'] == 'cooldown'
    clock.sleep(86400)
    assert publish(partial, clock, fresh_http, fresh, 'failure')['reason'] == 'unchanged'
    assert publish(report(), clock, fresh_http, fresh)['state'] == 'SCAN_RECOVERED'
    assert publish(report(), clock, fresh_http, fresh)['reason'] == 'unchanged'
    assert len([c for c in http.server['comments'].values() if c['body'].startswith('<!-- qd-event:')]) == 2


def test_severity_escalation_is_immediate_symbol_churn_is_not_spam():
    clock, http, store = runtime()
    publish(report('INCOMPLETE', ('NVDA',), ('TSM',)), clock, http, store, 'failure')
    clock.sleep(60)
    assert publish(report('INCOMPLETE', ('TSM',), ('NVDA',)), clock, http, store, 'failure')['reason'] == 'unchanged'
    assert publish(report('UNAVAILABLE', (), ('NVDA', 'TSM')), clock, http, store, 'failure')['state'] == 'SCAN_UNAVAILABLE'
    assert publish(report(), clock, http, store)['reason'] == 'cooldown'


def test_post_response_loss_readback_and_next_process_do_not_duplicate():
    clock, http, store = runtime()
    http.fail('POST', f'{BASE}/issues/{LEDGER_ISSUE}/comments', after=True)
    assert publish(None, clock, http, store, 'failure')['published'] == 1
    fresh_http = HealthHTTP(http.server, clock=clock)
    assert publish(None, clock, fresh_http, make_store(fresh_http), 'failure')['reason'] == 'unchanged'


def test_existing_durable_forwarder_delivers_nontrade_framed_health():
    clock, http, store = runtime()
    publish(report('INCOMPLETE', ('NVDA',), ('TSM',)), clock, http, store, 'failure')
    summary = sa.forward(make_config(), store=make_store(http), github=store.github,
                         webhook_url=WEBHOOK, http=http, clock=clock, budget=FakeBudget(clock))
    assert summary.delivered == 1
    payload = http.server['slack_posts'][0][1]
    assert '系统健康' in payload['text'] and '非交易信号' in payload['text']
    text = ''.join(b['text']['text'] for b in payload['blocks'])
    assert '1 / 2' in text and 'TSM' in text and 'No orders' in text and 'VPS' in text
    assert f'https://github.com/{REPO}/actions/runs/999' in text
    assert payload.get('channel') is None


def test_edited_live_source_does_not_override_accepted_immutable_snapshot():
    clock, http, store = runtime()
    outage = report('UNAVAILABLE', (), ('NVDA', 'TSM'))
    first = publish(outage, clock, http, store, 'failure')
    assert sa.forward(make_config(), store=make_store(http), github=store.github,
                      webhook_url=WEBHOOK, http=http, clock=clock, budget=FakeBudget(clock)).delivered == 1
    source = http.server['comments'][first['source_comment_id']]
    source['body'] = source['body'].replace('SCAN_UNAVAILABLE', 'SCAN_RECOVERED')
    clock.sleep(60)
    assert publish(outage, clock, http, make_store(http), 'failure')['reason'] == 'unchanged'


def test_health_sources_only_from_configured_ledger_and_trusted_writer():
    clock, http, store = runtime()
    publish(None, clock, http, store, 'failure')
    source = next(c for c in http.server['comments'].values() if c['body'].startswith('<!-- qd-event:'))
    issue = http.server['issues'][LEDGER_ISSUE]
    assert sa.parse_source(source, issue, make_config()) is not None
    assert sa.parse_source(source | {'user': {'id': AUTHOR + 1, 'type': 'Bot'}}, issue, make_config()) is None
    assert sa.parse_source(source | {'user': {'id': AUTHOR, 'type': 'User'}}, issue, make_config()) is None
    other = deepcopy(source)
    for key in ['issue_url', 'html_url', 'url']:
        if key in other:
            other[key] = other[key].replace(f'/issues/{LEDGER_ISSUE}', '/issues/7')
    daily = {'number': 7, 'title': 'Market Watch | 2026-10-07 ET',
             'html_url': f'https://github.com/{REPO}/issues/7', 'url': f'https://api.github.com{BASE}/issues/7',
             'user': {'id': AUTHOR, 'type': 'Bot'}}
    assert sa.parse_source(other, daily, make_config()) is None


def test_delivery_test_is_labeled_idempotent_and_does_not_recover_incident():
    clock, http, store = runtime()
    assert publish(None, clock, http, store, test=True)['state'] == 'SCAN_DELIVERY_TEST'
    assert publish(None, clock, http, store, test=True)['reason'] == 'test_already_persisted'
    assert publish(report(), clock, http, store)['reason'] == 'healthy_no_incident'
    body = next(c['body'] for c in http.server['comments'].values() if c['body'].startswith('<!-- qd-event:'))
    assert 'HEALTH / DELIVERY TEST' in body and 'No market data or signal' in body


@pytest.mark.parametrize('changes', [{'QD_SLACK_ENABLED': None}, {'GITHUB_REF': 'refs/heads/topic'},
                                    {'GITHUB_REPOSITORY': 'fork/repo'}, {'GITHUB_EVENT_NAME': 'pull_request'},
                                    {'GITHUB_REPOSITORY_ID': None}, {'GITHUB_REPOSITORY_ID': '999999'}])
def test_cli_trust_guards_before_network(monkeypatch, tmp_path, capsys, changes):
    cli_env(monkeypatch, tmp_path, **changes)
    http = HealthHTTP()
    monkeypatch.setattr(sa, 'StdlibHttpClient', lambda: http)
    assert health.main(['--outcome', 'failure']) == 0
    assert http.requests == []


def test_cli_missing_artifact_persists_diagnostic(monkeypatch, tmp_path, capsys):
    cli_env(monkeypatch, tmp_path)
    clock, http, _ = runtime()
    monkeypatch.setattr(sa, 'SystemClock', lambda: clock)
    monkeypatch.setattr(sa, 'StdlibHttpClient', lambda: http)
    assert health.main(['--report', str(tmp_path / 'absent'), '--outcome', 'failure']) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'SCAN_FAILED'


def test_cli_failed_persistence_does_not_expose_exception_or_secret(monkeypatch, tmp_path, capsys):
    cli_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sa, 'StdlibHttpClient', lambda: HealthHTTP())
    assert health.main(['--report', str(tmp_path / 'absent'), '--outcome', 'failure']) == 1
    result = capsys.readouterr().out
    assert json.loads(result)['reason'] == 'health_persistence_unavailable'
    assert 'fake-token' not in result and 'hooks.slack.com' not in result


def test_stdlib_only_health_can_run_after_dependency_failure():
    result = subprocess.run([sys.executable, '-S', '-m', 'market_data.scan_health', '--outcome', 'failure'],
                            env={'PATH': os.environ.get('PATH', '')}, capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)['reason'] == 'disabled'
