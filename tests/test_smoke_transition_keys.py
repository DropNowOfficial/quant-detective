"""Boundary smoke evidence supports legacy and predecessor-linked events."""
import pytest

from test_market_watch_smoke import check, ended_report_with_publication


def report_with_key(key, comment_id=123):
    report = ended_report_with_publication()
    publication = report['publication_result']
    publication['source_requests'][0].update(event_key=key, comment_id=comment_id)
    publication['source_comments'][0].update(id=comment_id, body=f'<!-- qd-event:{key} -->\nObservation only.')
    return report


@pytest.mark.parametrize('suffix', ['', '|1', '|122'])
def test_boundary_smoke_accepts_legacy_and_ordered_transition_keys(suffix):
    data = report_with_key('2026-10-05|NVDA|ENTRY_ARMED' + suffix)
    assert check(data) == 'EXPECTED_SESSION_END_WITH_PUBLICATION'


@pytest.mark.parametrize('suffix', ['|', '|0', '|-1', '|+1', '|01', '|1.0', '|bad',
                                     '|123', '|124', '|1|2', '|100000000000000000000'])
def test_boundary_smoke_rejects_invalid_transition_predecessor(suffix):
    data = report_with_key('2026-10-05|NVDA|ENTRY_ARMED' + suffix)
    with pytest.raises(ValueError):
        check(data)


def test_boundary_smoke_accepts_maximum_width_ordered_predecessor():
    data = report_with_key('2026-10-05|NVDA|ENTRY_ARMED|99999999999999999999', 10**20)
    assert check(data) == 'EXPECTED_SESSION_END_WITH_PUBLICATION'
