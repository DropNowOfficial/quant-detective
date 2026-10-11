"""Audit-driven tests (user audit 2026-10-11): trading-calendar timezone for MAX,
Sloan cash + short-term-investments rule, Sloan staleness date in calendar tz.

Synthetic data only: proves the CODE follows the contract, nothing about alpha.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from factor_candidates import (
    BalanceSheetFact, DailyReturn, PITError, max_lottery, sloan_accruals,
)

UTC = timezone.utc
D = date
NY = "America/New_York"
BASIS = "synthetic_adjusted_close_to_close"


def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


CAL = weekdays(D(2025, 12, 1), 130)


def row(day, ret, ka=None):
    ka = ka or datetime(day.year, day.month, day.day, 23, 0, tzinfo=UTC)
    return DailyReturn(security="A", day=day, known_at=ka, source="synthetic",
                       adjustment_basis=BASIS, revision=0, ret=ret)


def rows(overrides=None, value=0.01):
    ov = overrides or {}
    return [row(d, ov.get(d, value)) for d in CAL]


# Distinct monthly peaks so a wrong month is visible: Jan 0.20, Feb 0.90, Mar 0.33
PEAKS = {D(2026, 1, 14): 0.20, D(2026, 2, 11): 0.90, D(2026, 3, 12): 0.33}


def mx(as_of, tz=NY, variant="prior_calendar_month", data=None):
    return max_lottery(data if data is not None else rows(PEAKS), "A", as_of, CAL,
                       variant=variant, calendar_tz=tz)


# ------------------------------------------------ 1. MAX calendar timezone
def test_max_user_case_same_instant_utc_vs_new_york_identical():
    inst_utc = datetime(2026, 3, 1, 0, 30, tzinfo=UTC)
    inst_ny = inst_utc.astimezone(ZoneInfo(NY))
    assert inst_ny.isoformat() == "2026-02-28T19:30:00-05:00"
    a, b = mx(inst_utc), mx(inst_ny)
    assert a == b == pytest.approx(0.20)          # 19:30 ET on Feb 28 -> prior month is January


@pytest.mark.parametrize("offset_h", [-8, -5, 0, 1, 5.5, 9, 14])
def test_max_same_instant_any_offset_identical(offset_h):
    inst = datetime(2026, 3, 1, 0, 30, tzinfo=UTC)
    shifted = inst.astimezone(timezone(timedelta(hours=offset_h)))
    assert mx(shifted) == mx(inst) == pytest.approx(0.20)


def test_max_calendar_tz_is_what_decides_not_the_input_zone():
    inst = datetime(2026, 3, 1, 0, 30, tzinfo=UTC)
    assert mx(inst, tz="UTC") == pytest.approx(0.90)     # in UTC it is already March
    assert mx(inst, tz=NY) == pytest.approx(0.20)


@pytest.mark.parametrize("utc_instant,want", [
    (datetime(2026, 3, 1, 4, 59, 59, tzinfo=UTC), 0.20),   # 23:59:59 EST Feb 28 -> Jan
    (datetime(2026, 3, 1, 5, 0, 0, tzinfo=UTC), 0.90),     # 00:00:00 EST Mar 1 -> Feb
    (datetime(2026, 4, 1, 3, 59, 59, tzinfo=UTC), 0.90),   # 23:59:59 EDT Mar 31 -> Feb
    (datetime(2026, 4, 1, 4, 0, 0, tzinfo=UTC), 0.33),     # 00:00:00 EDT Apr 1 -> Mar
])
def test_max_month_boundary_in_calendar_tz_incl_dst(utc_instant, want):
    assert mx(utc_instant) == pytest.approx(want)
    assert mx(utc_instant.astimezone(ZoneInfo("Asia/Tokyo"))) == pytest.approx(want)


def test_max_fixed_variant_window_end_uses_calendar_date():
    # 2026-03-03 02:00Z is 21:00 ET on Mar 2: the Mar 3 session has not happened in NY.
    big = {D(2026, 3, 3): 0.77}
    data = [row(d, big.get(d, 0.01), ka=datetime(2026, 3, 3, 1, 0, tzinfo=UTC) if d == D(2026, 3, 3) else None)
            for d in CAL]
    inst = datetime(2026, 3, 3, 2, 0, tzinfo=UTC)
    ny = max_lottery(data, "A", inst, CAL, variant="fixed_21_trading_days", calendar_tz=NY)
    ut = max_lottery(data, "A", inst, CAL, variant="fixed_21_trading_days", calendar_tz="UTC")
    assert ny == pytest.approx(0.01)
    assert ut == pytest.approx(0.77)
    assert max_lottery(data, "A", inst.astimezone(ZoneInfo(NY)), CAL,
                       variant="fixed_21_trading_days", calendar_tz=NY) == ny


def test_max_calendar_tz_is_required_explicit_parameter():
    with pytest.raises(TypeError):
        max_lottery(rows(PEAKS), "A", datetime(2026, 3, 1, 0, 30, tzinfo=UTC), CAL)


@pytest.mark.parametrize("bad", [None, "", "Not/AZone", "EST5EDT_x", 5])
def test_max_invalid_calendar_tz_rejected(bad):
    with pytest.raises(PITError):
        mx(datetime(2026, 3, 1, 0, 30, tzinfo=UTC), tz=bad)


# ------------------------------------------------ 6a. Sloan: cash + short-term investments
PREV = (D(2024, 12, 31), datetime(2025, 2, 20, tzinfo=UTC))
CUR = (D(2025, 12, 31), datetime(2026, 2, 20, 21, 30, tzinfo=UTC))
ASOF = datetime(2026, 3, 1, tzinfo=UTC)


def bs(pe, ka, **kw):
    base = dict(security="A", period_end=pe, known_at=ka, source="synthetic",
                period_type="annual", currency="USD", unit_scale=1.0,
                statement_ref=f"synthetic:10-K:{pe.year}", revision=0,
                current_assets=100.0, cash=20.0, short_term_investments=30.0,
                current_liabilities=50.0, short_term_debt=10.0, tax_payable=5.0,
                depreciation_amortization=8.0, total_assets=400.0)
    base.update(kw)
    return BalanceSheetFact(**base)


def test_sloan_dcash_includes_short_term_investments():
    prev = bs(*PREV)
    cur = bs(*CUR, current_assets=140.0, cash=25.0, short_term_investments=40.0,
             current_liabilities=60.0, short_term_debt=12.0, tax_payable=6.0, total_assets=440.0)
    dca, dcash = 40.0, (25 + 40) - (20 + 30)
    expected = ((dca - dcash) - (10 - 2 - 1) - 8) / 420.0
    assert sloan_accruals([prev, cur], "A", ASOF) == pytest.approx(expected)


def test_sloan_missing_short_term_investments_without_flag_raises_not_zero():
    prev, cur = bs(*PREV), bs(*CUR, short_term_investments=None)
    with pytest.raises(PITError):
        sloan_accruals([prev, cur], "A", ASOF)
    with pytest.raises(PITError):
        sloan_accruals([bs(*PREV, short_term_investments=float("nan")), bs(*CUR)], "A", ASOF)


def test_sloan_prior_year_missing_short_term_investments_without_flag_raises():
    with pytest.raises(PITError):
        sloan_accruals([bs(*PREV, short_term_investments=None), bs(*CUR)], "A", ASOF)


def test_sloan_explicit_no_short_term_investments_flag_uses_cash_only():
    kw = dict(short_term_investments=None, no_short_term_investments=True)
    got = sloan_accruals([bs(*PREV, **kw), bs(*CUR, cash=25.0, **kw)], "A", ASOF)
    assert got == pytest.approx(((0 - 5) - (0 - 0 - 0) - 8) / 400.0)


def test_sloan_flag_and_value_together_is_contradiction():
    with pytest.raises(PITError):
        sloan_accruals([bs(*PREV), bs(*CUR, no_short_term_investments=True)], "A", ASOF)


def test_sloan_zero_short_term_investments_value_is_a_real_value():
    a = sloan_accruals([bs(*PREV, short_term_investments=0.0), bs(*CUR, short_term_investments=0.0)], "A", ASOF)
    b = sloan_accruals([bs(*PREV, short_term_investments=None, no_short_term_investments=True),
                        bs(*CUR, short_term_investments=None, no_short_term_investments=True)], "A", ASOF)
    assert a == pytest.approx(b)


def test_sloan_late_revision_of_short_term_investments_not_in_earlier_calc():
    prev, cur = bs(*PREV), bs(*CUR)
    rev = bs(CUR[0], datetime(2026, 8, 1, tzinfo=UTC), revision=1, short_term_investments=99.0)
    assert sloan_accruals([prev, cur, rev], "A", ASOF) == pytest.approx(sloan_accruals([prev, cur], "A", ASOF))
    assert sloan_accruals([prev, cur, rev], "A", datetime(2026, 9, 1, tzinfo=UTC)) != pytest.approx(
        sloan_accruals([prev, cur], "A", ASOF))


def test_sloan_input_order_does_not_matter_with_sti():
    import itertools
    facts = [bs(*PREV), bs(*CUR, short_term_investments=44.0)]
    want = sloan_accruals(facts, "A", ASOF)
    for o in itertools.permutations(facts):
        assert sloan_accruals(list(o), "A", ASOF) == pytest.approx(want)
