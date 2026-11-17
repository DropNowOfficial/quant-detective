"""PIT tests for GP/A quality, MAX lottery demand, and CLV accumulation."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import pytest

from research_lab.factor_mining import (
    DailyReturnObs,
    Evidence,
    FactKey,
    OHLCVDay,
    TimedFact,
    clv_accumulation_pit,
    gross_profitability_pit,
    max_lottery_pit,
)


def _ev(t: datetime, lag_minutes: int = 1, vintage: str = 'v1') -> Evidence:
    avail = t + timedelta(minutes=lag_minutes)
    return Evidence(
        event_time=t,
        available_at=avail,
        first_seen_at=avail,
        source_uri='synthetic://sec-or-ohlcv',
        vintage_id=vintage,
        license_id='synthetic-generated',
        availability_basis='SYNTHETIC_FIXTURE',
    )


def _gp_bundle(filing_time: datetime, *, rev=500.0, cogs=200.0, assets=1000.0, vintage='10k-v1'):
    ev = _ev(filing_time, lag_minutes=5, vintage=vintage)
    out = []
    for metric, val in [('revenue', rev), ('cogs', cogs), ('total_assets', assets)]:
        k = FactKey('ACME', metric, date(2025, 1, 1), date(2025, 12, 31), 'FY2025', 'million', 'USD', 'GAAP', 'consolidated')
        out.append(TimedFact(k, val, ev, f'acc-{vintage}'))
    return out


def test_gpa_deduplicates_same_filing_evidence_and_respects_pit_restatement():
    t_orig = datetime(2026, 2, 15, 21, tzinfo=timezone.utc)
    t_rest = datetime(2026, 6, 1, 21, tzinfo=timezone.utc)
    facts = _gp_bundle(t_orig, rev=500.0, cogs=200.0, assets=1000.0, vintage='v1') + _gp_bundle(
        t_rest, rev=500.0, cogs=350.0, assets=1000.0, vintage='v2'
    )
    # Before original filing availability -> MISSING
    before = gross_profitability_pit(
        facts, entity='ACME', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        fiscal_period='FY2025', decision_time=t_orig,
    )
    assert before.status == 'MISSING' and before.value is None

    # After original filing, before restatement -> 0.30, single deduplicated Evidence
    mid = gross_profitability_pit(
        facts, entity='ACME', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        fiscal_period='FY2025', decision_time=t_orig + timedelta(days=10),
    )
    assert mid.status == 'VALID' and mid.value == pytest.approx(0.30)
    assert len(mid.evidence) == 1
    assert mid.research_state == 'NEEDS BACKTEST'

    # After restatement -> 0.15
    after = gross_profitability_pit(
        facts, entity='ACME', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        fiscal_period='FY2025', decision_time=t_rest + timedelta(hours=1),
    )
    assert after.status == 'VALID' and after.value == pytest.approx(0.15)


def test_gpa_missing_cogs_is_not_zero_while_reported_zero_cogs_is_valid():
    t = datetime(2026, 2, 15, 21, tzinfo=timezone.utc)
    decision = t + timedelta(hours=1)
    missing_cogs = _gp_bundle(t, cogs=None)
    res_missing = gross_profitability_pit(
        missing_cogs, entity='ACME', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        fiscal_period='FY2025', decision_time=decision,
    )
    assert res_missing.status == 'MISSING' and res_missing.value is None

    zero_cogs = _gp_bundle(t, rev=400.0, cogs=0.0, assets=1000.0)
    res_zero = gross_profitability_pit(
        zero_cogs, entity='ACME', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        fiscal_period='FY2025', decision_time=decision,
    )
    assert res_zero.status == 'VALID' and res_zero.value == pytest.approx(0.40)


def test_gpa_stale_filing_and_nonpositive_assets():
    t = datetime(2026, 2, 15, 21, tzinfo=timezone.utc)
    stale = gross_profitability_pit(
        _gp_bundle(t), entity='ACME', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        fiscal_period='FY2025', decision_time=datetime(2027, 8, 1, 21, tzinfo=timezone.utc),
    )
    assert stale.status == 'MISSING' and stale.reason == 'STALE_FUNDAMENTAL_FILING'

    bad_assets = gross_profitability_pit(
        _gp_bundle(t, assets=0.0), entity='ACME', period_start=date(2025, 1, 1), period_end=date(2025, 12, 31),
        fiscal_period='FY2025', decision_time=t + timedelta(hours=1),
    )
    assert bad_assets.status == 'INVALID' and bad_assets.reason == 'NONPOSITIVE_TOTAL_ASSETS'


def test_max_lottery_missing_days_never_become_zero_in_negative_month():
    sessions = [datetime(2026, 3, 1, 20, tzinfo=timezone.utc) + timedelta(days=i) for i in range(21)]
    rows = [
        DailyReturnObs('ACME', s, -0.025, _ev(s, vintage=f'd{i}'))
        for i, s in enumerate(sessions[:18])
    ]
    obs = max_lottery_pit(rows, entity='ACME', sessions=sessions, decision_time=sessions[-1] + timedelta(minutes=5))
    assert obs.status == 'VALID'
    assert obs.value == pytest.approx(-0.025)
    assert 'MISSING_SESSION' in obs.quality_flags

    # Too few observations -> MISSING
    too_few = max_lottery_pit(rows[:14], entity='ACME', sessions=sessions, decision_time=sessions[-1] + timedelta(minutes=5))
    assert too_few.status == 'MISSING' and too_few.value is None

    # Future evidence -> BLOCKED
    late_rows = list(rows)
    late_rows[0] = replace(late_rows[0], evidence=_ev(sessions[0], lag_minutes=60 * 24 * 30, vintage='late'))
    blocked = max_lottery_pit(late_rows, entity='ACME', sessions=sessions, decision_time=sessions[-1] + timedelta(minutes=5))
    assert blocked.status == 'BLOCKED' and blocked.reason == 'NOT_YET_AVAILABLE'


def test_clv_accumulation_distinguishes_institutional_close_from_fade():
    sessions = [datetime(2026, 4, 1, 20, tzinfo=timezone.utc) + timedelta(days=i) for i in range(20)]
    strong_rows = [
        OHLCVDay('ACME', s, 100.0, 110.0, 100.0, 109.0, 1000.0, _ev(s, vintage=f'b{i}'))
        for i, s in enumerate(sessions)
    ]
    fade_rows = [
        OHLCVDay('ACME', s, 109.0, 110.0, 100.0, 101.0, 1000.0, _ev(s, vintage=f'f{i}'))
        for i, s in enumerate(sessions)
    ]
    strong = clv_accumulation_pit(strong_rows, entity='ACME', sessions=sessions, decision_time=sessions[-1] + timedelta(minutes=5))
    faded = clv_accumulation_pit(fade_rows, entity='ACME', sessions=sessions, decision_time=sessions[-1] + timedelta(minutes=5))
    assert strong.status == 'VALID' and strong.value == pytest.approx(0.80)
    assert faded.status == 'VALID' and faded.value == pytest.approx(-0.80)

    # Contradictory OHLC (close > high) is rejected, never silently clamped
    bad_rows = [replace(r, close=120.0) for r in strong_rows]
    bad = clv_accumulation_pit(bad_rows, entity='ACME', sessions=sessions, decision_time=sessions[-1] + timedelta(minutes=5))
    assert bad.status == 'MISSING' and bad.value is None
