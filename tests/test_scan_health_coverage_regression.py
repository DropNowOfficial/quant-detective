"""Offline coverage-worsening contract; only the existing in-memory HTTP fake."""
import pytest
from types import SimpleNamespace
from market_data import scan_health as health, slack_alerts as sa
from test_scan_health import report, runtime, publish, HealthHTTP, make_store
from test_slack_alerts import make_config, WEBHOOK, FakeBudget


UNIVERSE = tuple(f'T{i:02d}' for i in range(36)) + ('CEG', 'ETN', 'MCHP', 'MPWR', 'NXPI')


def coverage(missing):
    missing = tuple(missing)
    return report('INCOMPLETE', tuple(s for s in UNIVERSE if s not in missing), missing)


@pytest.mark.parametrize('elapsed', [60, 1800, 86400])
def test_same_state_coverage_worsening_publishes_once(elapsed):
    clock, http, store = runtime()
    first = publish(coverage(('ETN',)), clock, http, store, 'failure')
    assert first['state'] == 'SCAN_DEGRADED'
    clock.sleep(elapsed)
    fresh_http = HealthHTTP(http.server, clock=clock)
    fresh_store = make_store(fresh_http)
    worse = coverage(('CEG', 'ETN', 'MCHP', 'MPWR', 'NXPI'))
    result = publish(worse, clock, fresh_http, fresh_store, 'failure')
    assert result['published'] == 1, result
    assert result['state'] == 'SCAN_DEGRADED'
    body = http.server['comments'][result['source_comment_id']]['body']
    assert '36 / 41' in body
    assert 'CEG, ETN, MCHP, MPWR, NXPI' in body
    assert publish(worse, clock, fresh_http, make_store(fresh_http), 'failure')['reason'] == 'unchanged'
    assert not http.server['slack_posts']


def test_improvement_and_same_count_churn_remain_quiet():
    clock, http, store = runtime()
    publish(coverage(('CEG', 'ETN', 'MCHP', 'MPWR', 'NXPI')), clock, http, store, 'failure')
    clock.sleep(60)
    assert publish(coverage(('ETN',)), clock, http, store, 'failure')['reason'] == 'unchanged'
    churn = coverage(('T00', 'ETN', 'MCHP', 'MPWR', 'NXPI'))
    assert publish(churn, clock, http, store, 'failure')['reason'] == 'unchanged'


def test_oscillation_compares_to_last_notified_low_water_mark():
    clock, http, store = runtime()
    forty = coverage(('ETN',))
    thirty_nine = coverage(('ETN', 'CEG'))
    assert publish(forty, clock, http, store, 'failure')['published'] == 1
    clock.sleep(60)
    assert publish(thirty_nine, clock, http, store, 'failure')['published'] == 1
    for data in (forty, thirty_nine, forty, thirty_nine):
        clock.sleep(60)
        fresh_http = HealthHTTP(http.server, clock=clock)
        assert publish(data, clock, fresh_http, make_store(fresh_http), 'failure')['reason'] == 'unchanged'


@pytest.mark.parametrize('status', ['SKIPPED', 'SESSION_ENDED'])
def test_expected_session_end_still_quiet(status):
    clock, http, store = runtime()
    publish(coverage(('ETN',)), clock, http, store, 'failure')
    worse = coverage(('CEG', 'ETN', 'MCHP', 'MPWR', 'NXPI')) | {'scan_status': status}
    assert publish(worse, clock, http, store)['reason'] == 'session_skipped'


def test_accepted_snapshot_beats_edited_live_coverage():
    clock, http, store = runtime()
    first = publish(coverage(('ETN',)), clock, http, store, 'failure')
    assert sa.forward(make_config(), store=make_store(http), github=store.github,
                      webhook_url=WEBHOOK, http=http, clock=clock, budget=FakeBudget(clock)).delivered == 1
    comment = http.server['comments'][first['source_comment_id']]
    comment['body'] = comment['body'].replace('40 / 41', '1 / 41')
    clock.sleep(60)
    worse = coverage(('CEG', 'ETN', 'MCHP', 'MPWR', 'NXPI'))
    assert publish(worse, clock, http, make_store(http), 'failure')['published'] == 1


@pytest.mark.parametrize('body', [None, '', '- Usable / requested symbols: unknown',
    '- Usable / requested symbols: 0 / 41', '- Usable / requested symbols: 41 / 41',
    '- Usable / requested symbols: 40 / 42', '- Usable / requested symbols: 40 / 99',
    '- Usable / requested symbols: 40 / 41\n- Usable / requested symbols: 39 / 41'])
def test_ambiguous_or_incomparable_previous_coverage_does_not_escalate(body):
    previous = SimpleNamespace(state='SCAN_DEGRADED', body=body)
    assessment = health.assess(coverage(('CEG', 'ETN', 'MCHP', 'MPWR', 'NXPI')), 'failure')
    assert not health._coverage_worsened(assessment, previous)


def test_real_acquisition_failure_at_session_end_still_reports_worsening():
    clock, http, store = runtime()
    publish(coverage(('ETN',)), clock, http, store, 'failure')
    clock.sleep(60)
    worse = coverage(('CEG', 'ETN', 'MCHP', 'MPWR', 'NXPI'))
    worse.update(scan_status='SESSION_ENDED', real_acquisition_failure=True, ending_session='OVERNIGHT')
    assert publish(worse, clock, http, store, 'failure')['published'] == 1

