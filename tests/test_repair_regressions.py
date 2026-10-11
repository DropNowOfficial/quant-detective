"""Offline acceptance cases for coverage, delivery and input boundaries."""
from copy import deepcopy
from datetime import timedelta
import io
import json
import subprocess
import sys
from urllib.error import HTTPError

import pytest

from market_data import cli, github_alerts as ga, scan_health as health, slack_alerts as sa, us_watch
from test_quote_validity import bar, daily, event_at, moment
from test_scan_health import runtime, publish, report
from test_slack_alerts import cli_env, DeliveryHTTP, FakeClock, make_store, WEBHOOK


def test_post_sparse_bar_keeps_fresh_coverage_without_process_failure(monkeypatch, tmp_path):
    now = moment('2026-10-09T19:58:00-04:00')
    fresh = event_at(now.isoformat(), rows=[bar('2026-10-09T19:55:00-04:00')],
                     values=daily() | {'previous_session_date': '2026-10-08'})
    stale = event_at(now.isoformat(), rows=[bar('2026-10-09T17:25:00-04:00')],
                     values=daily() | {'previous_session_date': '2026-10-08'})
    assert stale['quote_validity']['eligible'] is False
    assert 'BAR_EXPIRED' in stale['quote_validity']['rejection_reasons']
    rows = [{'symbol': 'NVDA', 'status': 'OK', 'intraday': fresh['classification_inputs']['intraday']},
            {'symbol': 'NXPI', 'status': 'INVALID_DATA', 'intraday': stale['classification_inputs']['intraday']}]
    monkeypatch.setattr(us_watch, 'scan_once', lambda **kwargs: {'rows': rows, 'alerts': []})
    result = us_watch.run(symbols=('NVDA', 'NXPI'), once=True, output=tmp_path / 'scan.json')
    assert result['scan_status'] == 'INCOMPLETE'
    assert result['coverage']['usable'] == 1
    assert result['coverage']['requested'] == 2
    assert result['coverage']['reasons'] == {'NXPI': ['BAR_EXPIRED']}
    assert json.loads((tmp_path / 'scan.json').read_text()) == result


def test_all_expired_still_exits_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(us_watch, 'scan_once', lambda **kwargs: {
        'rows': [{'symbol': 'NXPI', 'status': 'INVALID_DATA'}], 'alerts': []})
    with pytest.raises(RuntimeError, match='one-shot scan incomplete'):
        us_watch.run(symbols=('NXPI',), once=True, output=tmp_path / 'scan.json')
    assert json.loads((tmp_path / 'scan.json').read_text())['scan_status'] == 'UNAVAILABLE'


def test_malformed_coverage_is_runtime_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(us_watch, 'scan_once', lambda **kwargs: {
        'rows': [{'symbol': 'NVDA', 'status': 'OK'}], 'alerts': []})
    with pytest.raises(RuntimeError):
        us_watch.run(symbols=('NVDA', 'NXPI'), once=True, output=tmp_path / 'scan.json')
    assert json.loads((tmp_path / 'scan.json').read_text())['scan_status'] == 'FAILED'


def test_publication_failure_is_not_mislabeled_as_coverage(monkeypatch, tmp_path):
    data = report('INCOMPLETE', ('NVDA',), ('NXPI',))
    monkeypatch.setattr(us_watch, 'scan_once', lambda **kwargs: data)
    def fail(*args, **kwargs):
        raise RuntimeError('private-runtime-secret')
    monkeypatch.setattr(ga, 'publish', fail)
    with pytest.raises(RuntimeError):
        us_watch.run(symbols=('NVDA', 'NXPI'), once=True, github_alerts=True,
                     clock=lambda: moment('2026-10-07T14:00:00-04:00'), output=tmp_path / 'scan.json')
    saved = json.loads((tmp_path / 'scan.json').read_text())
    assert saved['execution_error'] == 'PUBLICATION_FAILED'
    assert health.assess(saved, 'failure')['state'] == 'SCAN_FAILED'
    assert 'private-runtime-secret' not in json.dumps(saved)


def test_recovery_is_not_delayed_by_cooldown():
    clock, http, store = runtime()
    publish(report('INCOMPLETE', ('NVDA',), ('TSM',)), clock, http, store)
    clock.sleep(60)
    assert publish(report(), clock, http, store)['state'] == 'SCAN_RECOVERED'
    assert publish(report(), clock, http, store)['reason'] == 'unchanged'


def test_material_coverage_change_notifies_after_cooldown_and_dedups_restart():
    clock, http, store = runtime()
    publish(report('INCOMPLETE', ('NVDA',), ('TSM',)), clock, http, store)
    changed = report('INCOMPLETE', ('NVDA',), ('TSM', 'NXPI'))
    clock.sleep(60)
    assert publish(changed, clock, http, store)['published'] == 0
    clock.sleep(health.COOLDOWN_SECONDS)
    assert publish(changed, clock, http, make_store(http))['published'] == 1
    assert publish(changed, clock, http, make_store(http))['reason'] == 'unchanged'


def test_slack_rejection_returns_failure_and_durable_evidence(monkeypatch, tmp_path, capsys):
    cli_env(monkeypatch, tmp_path)
    clock, http, store = runtime()
    publish(report('INCOMPLETE', ('NVDA',), ('TSM',)), clock, http, store)
    class Rejected(DeliveryHTTP):
        def request(self, method, url, **kwargs):
            if url == WEBHOOK:
                return sa.HttpResponse(403, {}, b'private-remote-secret')
            return super().request(method, url, **kwargs)
    monkeypatch.setattr(sa, 'SystemClock', lambda: clock)
    monkeypatch.setattr(sa, 'StdlibHttpClient', lambda: Rejected(http.server, clock=clock))
    assert sa.main(['forward']) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['delivery_healthy'] is False and result['needs_attention'] == 1
    assert 'private-remote-secret' not in json.dumps(result)
    assert 'delivery_healthy=false' in (tmp_path / 'outputs').read_text()


def test_cli_watch_never_echoes_exception_secret(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['qd-market', 'watch', '--once'])
    def fail(**kwargs):
        raise RuntimeError('Bearer private-runtime-secret https://hooks.slack.com/services/private')
    monkeypatch.setattr(cli, 'run_us_watch', fail)
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert 'private-runtime-secret' not in output.err and 'hooks.slack.com' not in output.err


def test_upstream_exception_text_is_not_saved(monkeypatch):
    monkeypatch.setattr(us_watch, '_market_context', lambda *args: {})
    def fail(*args, **kwargs):
        raise ValueError('private-provider-secret')
    monkeypatch.setattr(us_watch, '_scan_symbol', fail)
    result = us_watch.scan_once(symbols=('NVDA',), now=moment('2026-10-09T19:58:00-04:00'))
    assert result['scan_status'] == 'UNAVAILABLE'
    assert 'private-provider-secret' not in json.dumps(result)


def test_github_http_error_body_is_not_logged(monkeypatch):
    def denied(*args, **kwargs):
        raise HTTPError('https://api.github.com/private', 403, 'private-http-secret', {}, io.BytesIO(b'private-body-secret'))
    monkeypatch.setattr(ga, 'urlopen', denied)
    with pytest.raises(RuntimeError) as caught:
        ga._api('GET', '/repos/' + ga.REPO + '/issues', token='private-token')
    assert '403' in str(caught.value)
    assert 'private' not in str(caught.value)


def test_news_cannot_inject_markers_mentions_or_markdown():
    text = ga._event_markdown({'event_key': '2026-10-09|NVDA|LEADER_WATCH', 'symbol': 'NVDA',
                              'state': 'LEADER_WATCH', 'news': [{'title': '\n<!-- qd-event:fake --> @everyone [click](https://evil.test)',
                              'publisher': '<script>@owner</script>', 'age_hours': 1}]})
    assert text.count('<!-- qd-event:') == 1
    assert '@everyone' not in text and '@owner' not in text
    assert '[click](https://evil.test)' not in text and '<script>' not in text


@pytest.mark.parametrize('url', ['https://evil.test/path', '/repos/other/repo/issues',
                               '/repos/' + ga.REPO + '/issues/../hooks'])
def test_github_api_refuses_unscoped_paths_before_request(monkeypatch, url):
    monkeypatch.setattr(ga, 'urlopen', lambda *a, **k: pytest.fail('Unscoped request'))
    with pytest.raises(ValueError):
        ga._api('GET', url, token='private-token')


def test_healthcheck_rejects_future_timestamp(tmp_path):
    from datetime import datetime, timezone
    path = tmp_path / 'state.json'
    path.write_text(json.dumps({'generated_at_utc': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                               'run_id': 'test-run', 'mode': 'DEGRADED_PUBLIC_ONLY'}))
    result = subprocess.run([sys.executable, 'deploy/healthcheck.py', str(path), '30'], capture_output=True, text=True)
    assert result.returncode != 0


def test_programming_exception_is_not_partial_coverage(monkeypatch, tmp_path):
    monkeypatch.setattr(us_watch, '_market_context', lambda *args: {})
    def scan(symbol, *args):
        if symbol == 'NXPI':
            raise TypeError('private-programming-error')
        return {'symbol': symbol, 'status': 'OK'}
    monkeypatch.setattr(us_watch, '_scan_symbol', scan)
    with pytest.raises(RuntimeError):
        us_watch.run(symbols=('NVDA', 'NXPI'), once=True, clock=lambda: moment('2026-10-09T19:58:00-04:00'),
                     output=tmp_path / 'scan.json')
    saved = json.loads((tmp_path / 'scan.json').read_text())
    assert saved['scan_status'] == 'FAILED' and saved['execution_error'] == 'SCAN_EXECUTION_FAILED'
    assert 'private-programming-error' not in json.dumps(saved)


def test_bad_event_key_cannot_inject_another_event_envelope():
    with pytest.raises(ValueError):
        ga._event_mark('2026-10-09|NVDA|LEADER_WATCH -->\n<!-- qd-event:fake')


def test_network_stderr_is_not_persisted(monkeypatch):
    from market_data import transport
    class Process:
        returncode = 7
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, timeout):
            return '\n000', 'proxy https://private-user:private-secret@proxy.test'
    monkeypatch.setattr(transport.subprocess, 'Popen', Process)
    with pytest.raises(transport.FetchError) as caught:
        transport.fetch('https://query1.finance.yahoo.com/v8/finance/chart/NVDA')
    assert 'private-secret' not in json.dumps(caught.value.receipt)


def test_delivery_verdict_checks_all_outputs_without_credentials(tmp_path):
    from test_slack_forwarding_workflow import step, run_command
    code = run_command(step('delivery_verdict')).split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    import os
    base = os.environ.copy() | {'GITHUB_STEP_SUMMARY': str(tmp_path / 'summary'),
        'QD_LEDGER_READY': 'true', 'QD_HEALTH_OUTCOME': 'success',
        'QD_FORWARD_OUTCOME': 'success', 'QD_DELIVERY_HEALTHY': 'true'}
    for changes in ({}, {'QD_LEDGER_READY': 'false'}, {'QD_FORWARD_OUTCOME': 'failure'},
                    {'QD_DELIVERY_HEALTHY': 'false'}, {'QD_HEALTH_OUTCOME': 'failure'}):
        result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=base | changes, capture_output=True)
        assert result.returncode == (1 if changes else 0)
        saved = json.loads((tmp_path / 'notification-delivery.json').read_text())
        assert all(type(value) is bool for value in saved.values())


def test_updater_uses_locked_unprivileged_validation_and_error_rollback():
    from pathlib import Path
    script = Path('deploy/update-production.sh').read_text()
    assert '"$UV_BIN" sync --frozen' in script
    assert 'pip install --upgrade pip' not in script
    assert 'runuser -u quantdetective --' in script
    assert "trap 'rollback' ERR INT TERM" in script
    assert 'restore_operational_files' in script
    assert script.index('actual=') < script.index('switch_current "$release"')
