"""Independent offline regressions found during repair review."""
import pytest
from market_data import github_alerts as ga, scan_health as health
from test_github_alerts import _runtime, _report, _event
from test_quote_validity import moment
from test_scan_health import report


@pytest.mark.parametrize('ref,event', [('refs/heads/topic', 'workflow_dispatch'),
    ('refs/heads/main', 'pull_request_target'), ('', 'schedule'), ('refs/heads/main', '')])
def test_publisher_rejects_untrusted_ref_and_event_before_network(monkeypatch, ref, event):
    _runtime(monkeypatch)
    monkeypatch.setenv('GITHUB_REF', ref)
    monkeypatch.setenv('GITHUB_EVENT_NAME', event)
    monkeypatch.setattr(ga, '_api', lambda *args, **kwargs: pytest.fail('untrusted publication'))
    with pytest.raises(RuntimeError, match='context'):
        ga.publish(_report([_event()]), clock=lambda: moment('2026-10-05T16:05:00-04:00'))


def test_failed_process_with_partial_artifact_is_failed_not_degraded():
    partial = report('INCOMPLETE', ('NVDA',), ('TSM',))
    assert health.assess(partial, 'success')['state'] == 'SCAN_DEGRADED'
    assert health.assess(partial, 'failure')['state'] == 'SCAN_FAILED'


def test_hosted_scan_job_has_main_repository_event_guard():
    from pathlib import Path
    text = Path('.github/workflows/market-watch.yml').read_text()
    job = text.split('  scan:\n', 1)[1].split('    steps:', 1)[0]
    assert "github.ref == 'refs/heads/main'" in job
    assert "github.repository_id == '1369484548'" in job
    assert "github.repository == 'DropNowOfficial/quant-detective'" in job
    assert 'github.event_name' in job


def test_market_state_return_transition_publishes_once_and_remains_slack_compatible(monkeypatch):
    from market_data import slack_alerts as sa
    from test_github_alerts import _server
    from test_quote_validity import bar, event_at
    from test_slack_alerts import make_config, make_entry
    state = _server(monkeypatch)
    publications = []
    for value, price in [('2026-10-05T16:05:00-04:00', 101.25),
                         ('2026-10-05T16:10:00-04:00', 120),
                         ('2026-10-05T16:15:00-04:00', 101.25),
                         ('2026-10-05T16:16:00-04:00', 101.25)]:
        candle = bar(value, close=price)
        candle.update(open=price, high=price + 0.2, low=price - 0.2)
        event = event_at(value, rows=[candle])
        data = _report([event]) | {'generated_at_et': value}
        publications.append(ga.publish(data, clock=lambda: moment(value))['published'])
    assert publications == [1, 1, 1, 0]
    parsed = [sa.parse_source(c, state['issues'][0], make_config()) for c in state['comments']
              if c['body'].startswith('<!-- qd-event:')]
    assert len(parsed) == 3 and all(parsed)
    assert len({source.event_key for source in parsed}) == 3
    for source in parsed:
        assert sa.format_payload(make_entry(source), moment('2026-10-05T16:17:00-04:00'))
    # Restart against persisted remote comments must not emit the same state.
    _server(monkeypatch, issues=state['issues'], comments=state['comments'])
    assert ga.publish(data, clock=lambda: moment(value))['published'] == 0


@pytest.mark.parametrize('suffix', ['|0', '|-1', '|bad', '|1|2'])
def test_market_transition_marker_rejects_invalid_predecessor(suffix):
    from market_data import slack_alerts as sa
    from test_github_alerts import _created_comment, _issue
    from test_slack_alerts import make_config
    key = '2026-10-05|AAA|ENTRY_ARMED' + suffix
    comment = _created_comment(102, '<!-- qd-event:' + key + ' -->\n### AAA — ENTRY_ARMED')
    assert sa.parse_source(comment, _issue(), make_config()) is None


def test_transition_predecessor_must_be_earlier_than_source_comment():
    from market_data import slack_alerts as sa
    from test_github_alerts import _created_comment, _issue
    from test_slack_alerts import make_config
    for predecessor in (102, 103):
        key = f'2026-10-05|AAA|ENTRY_ARMED|{predecessor}'
        comment = _created_comment(102, '<!-- qd-event:' + key + ' -->\n### AAA — ENTRY_ARMED')
        assert sa.parse_source(comment, _issue(), make_config()) is None


@pytest.mark.parametrize('status,ok,missing', [
    ('UNKNOWN', ('NVDA',), ('TSM',)), ('COMPLETE', ('NVDA',), ('TSM',)),
    ('UNAVAILABLE', ('NVDA',), ('TSM',)), ('INCOMPLETE', ('NVDA', 'TSM'), ()),
    ('INCOMPLETE', (), ('NVDA', 'TSM')),
])
def test_health_rejects_status_coverage_contradictions(status, ok, missing):
    assert health.assess(report(status, ok, missing), 'success')['state'] == 'SCAN_FAILED'
