"""Synthetic clocks; never an original Yahoo response replay."""
from datetime import datetime
import pytest
from market_data import quote_validity as qv


def evidence(now='2026-10-15T18:09:09-04:00', source_time='2026-10-15T16:59:59-04:00', **updates):
    now = datetime.fromisoformat(now)
    stamp = datetime.fromisoformat(source_time)
    proxy = {'symbol': 'NQ=F', 'source': 'Yahoo Finance public chart',
             'instrument_type': 'FUTURE', 'exchange': 'CME', 'currency': 'USD',
             'last_bar_utc': stamp.isoformat(), 'known_at': now.isoformat(),
             'quote_validity': qv.quote_evidence(stamp.timestamp(), now, now, equity=False)}
    proxy.update(updates)
    return qv.futures_availability(proxy, now)


def test_reopening_delay_is_limited_not_realtime():
    result = evidence()
    assert result['status'] == 'EXPECTED_DELAYED_REOPEN'
    assert result['realtime_eligible'] is False
    assert result['source_bar_end_utc'] is None


@pytest.mark.parametrize('now,status', [('2026-10-15T17:30:00-04:00','EXPECTED_MAINTENANCE'),
    ('2026-03-09T18:09:00-04:00','EXPECTED_DELAYED_REOPEN'),
    ('2026-11-02T18:09:00-05:00','EXPECTED_DELAYED_REOPEN')])
def test_normal_day_and_dst(now,status):
    source = now[:11] + '16:59:59' + now[-6:]
    assert evidence(now,source)['status'] == status


@pytest.mark.parametrize('now,source', [
    ('2026-10-15T18:10:00-04:00','2026-10-15T16:59:59-04:00'),
    ('2026-10-15T18:09:00-04:00','2026-10-15T16:40:00-04:00'),
    ('2026-10-15T18:09:00-04:00','2026-10-14T16:59:59-04:00'),
    ('2026-10-15T18:09:00-04:00','2026-10-15T17:04:00-04:00'),
    ('2026-10-16T18:09:00-04:00','2026-10-16T16:59:59-04:00'),
    ('2026-10-18T18:09:00-04:00','2026-10-16T16:59:59-04:00'),
    ('2026-11-26T18:09:00-05:00','2026-11-26T16:59:59-05:00'),
    ('2026-12-24T18:09:00-05:00','2026-12-24T16:59:59-05:00'),
])
def test_no_exception_for_old_wrong_session_or_special_calendar(now,source):
    assert evidence(now,source)['status'] == 'UNVERIFIED_OR_UNEXPECTED'


@pytest.mark.parametrize('updates', [{'known_at':None}, {'known_at':'2026-10-15T18:00:00-04:00'},
    {'source':'other'}, {'exchange':'NYQ'}, {'instrument_type':'ETF'}, {'currency':'EUR'},
    {'symbol':'QQQ'}, {'last_bar_utc':None}])
def test_identity_and_receipt_are_required(updates):
    assert evidence(**updates)['status'] == 'UNVERIFIED_OR_UNEXPECTED'


def test_futures_observation_does_not_invent_bar_completion():
    now = datetime.fromisoformat('2026-10-15T18:09:09-04:00')
    stamp = datetime.fromisoformat('2026-10-15T16:59:59-04:00')
    proof = qv.quote_evidence(stamp.timestamp(), now, now, equity=False)
    assert proof['source_bar_end_utc'] is None
    assert proof['completed_at_receipt'] is False
    assert proof['rejection_reasons'] == ['BAR_EXPIRED']
