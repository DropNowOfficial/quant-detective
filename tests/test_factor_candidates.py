"""Synthetic-data tests: correctness, PIT/leakage, missing != zero, determinism.

Synthetic data proves the CODE follows the contract. It says nothing about
whether any factor works. Tests use only the public API of factor_candidates.
"""
import dataclasses
import itertools
import random
from datetime import date, datetime, timedelta, timezone

import pytest

import factor_candidates as fc
from factor_candidates import (
    BalanceSheetFact, DailyReturn, FundamentalFact, PITError,
    cross_sectional_rank, gross_profitability, max_lottery, sloan_accruals,
)

UTC = timezone.utc
D = date


def T(y, m, d, h=0, mi=0, s=0, tz=UTC):
    return datetime(y, m, d, h, mi, s, tzinfo=tz)


def mk(cls, **kw):
    """Build cls(**kw) passing only fields the dataclass declares."""
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in kw.items() if k in names})


def error_types():
    names = ("AmbiguousVersionError", "TradingCalendarError")
    return tuple(getattr(fc, n) for n in names if hasattr(fc, n)) or (PITError,)


def ambiguous():
    return getattr(fc, "AmbiguousVersionError", PITError)


# ---------------------------------------------------------------- fixtures
GP_REQUIRED = ["security", "period_end", "known_at", "source", "period_type",
               "currency", "unit_scale", "statement_ref", "revision"]


def gp_kw(**kw):
    base = dict(security="A", period_end=D(2025, 12, 31), known_at=T(2026, 2, 20, 21, 30),
                source="synthetic", period_type="annual", currency="USD", unit_scale=1.0,
                statement_ref="synthetic:10-K:0001", revision=0,
                revenue=100.0, cogs=60.0, total_assets=200.0)
    base.update(kw)
    return base


def gp(**kw):
    return mk(FundamentalFact, **gp_kw(**kw))


ASOF = T(2026, 3, 1)


# ---------------------------------------------------------------- GP/A
def test_gp_value_is_gross_profit_over_total_assets():
    assert gross_profitability([gp()], "A", ASOF) == pytest.approx((100 - 60) / 200)


def test_gp_no_lookahead_to_the_second():
    f = gp()
    assert gross_profitability([f], "A", T(2026, 2, 20, 21, 29, 59)) is None
    assert gross_profitability([f], "A", T(2026, 2, 20, 21, 30, 0)) == pytest.approx(0.2)


def test_gp_non_utc_known_at_compared_by_instant():
    est = timezone(timedelta(hours=-5))
    f = gp(known_at=T(2026, 2, 20, 16, 30, tz=est))        # == 21:30 UTC
    assert gross_profitability([f], "A", T(2026, 2, 20, 21, 29)) is None
    assert gross_profitability([f], "A", T(2026, 2, 20, 21, 31)) == pytest.approx(0.2)


def test_gp_late_revision_not_visible_in_earlier_computation():
    orig = gp()
    rest = gp(known_at=T(2026, 6, 1, 12), revision=1, cogs=90.0)
    assert gross_profitability([orig, rest], "A", T(2026, 3, 1)) == pytest.approx(0.2)
    assert gross_profitability([orig, rest], "A", T(2026, 6, 1, 11, 59)) == pytest.approx(0.2)
    assert gross_profitability([orig, rest], "A", T(2026, 6, 1, 12)) == pytest.approx(0.05)


def test_gp_late_published_filing_not_visible_in_earlier_computation():
    old = gp(period_end=D(2024, 12, 31), known_at=T(2025, 2, 20), revenue=10.0, cogs=0.0)
    new = gp(known_at=T(2026, 4, 1))
    assert gross_profitability([old, new], "A", T(2026, 3, 1)) == pytest.approx(10.0 / 200)


def test_gp_same_known_at_higher_revision_wins_in_any_order():
    r0 = gp(revision=0, cogs=60.0)
    r1 = gp(revision=1, cogs=70.0)
    for order in itertools.permutations([r0, r1]):
        assert gross_profitability(list(order), "A", ASOF) == pytest.approx(0.15)


def test_gp_same_known_at_same_revision_conflict_raises_not_order_dependent():
    a, b = gp(cogs=60.0), gp(cogs=70.0)
    with pytest.raises(ambiguous()):
        gross_profitability([a, b], "A", ASOF)
    with pytest.raises(ambiguous()):
        gross_profitability([b, a], "A", ASOF)


def test_gp_exact_duplicate_record_is_not_a_conflict():
    assert gross_profitability([gp(), gp()], "A", ASOF) == pytest.approx(0.2)


def test_gp_conflict_hidden_if_not_yet_visible():
    a = gp(known_at=T(2026, 6, 1), cogs=60.0, revision=1)
    b = gp(known_at=T(2026, 6, 1), cogs=70.0, revision=1)
    assert gross_profitability([gp(), a, b], "A", ASOF) == pytest.approx(0.2)


def test_gp_revision_number_must_not_decrease_over_time():
    early_high = gp(known_at=T(2026, 3, 1), revision=2, cogs=70.0)
    late_low = gp(known_at=T(2026, 5, 1), revision=1, cogs=80.0)
    with pytest.raises(ambiguous()):
        gross_profitability([early_high, late_low], "A", T(2026, 6, 1))


def test_gp_period_type_selected_not_mixed():
    q = gp(period_type="quarterly", period_end=D(2026, 3, 31), known_at=T(2026, 5, 1),
           revenue=1.0, cogs=0.0, total_assets=1.0, statement_ref="synthetic:10-Q:0002")
    a = gp()
    assert gross_profitability([a, q], "A", T(2026, 6, 1)) == pytest.approx(0.2)
    assert gross_profitability([a, q], "A", T(2026, 6, 1), period_type="quarterly") == pytest.approx(1.0)


def test_gp_unit_scale_applied():
    # same economic facts, reported in thousands vs units, must agree
    f = gp(unit_scale=1000.0, revenue=100.0, cogs=60.0, total_assets=200.0)
    assert gross_profitability([f], "A", ASOF) == pytest.approx(0.2)


def test_gp_missing_is_not_zero():
    assert gross_profitability([gp(cogs=None)], "A", ASOF) is None
    assert gross_profitability([gp(revenue=float("nan"))], "A", ASOF) is None
    assert gross_profitability([gp(total_assets=0.0)], "A", ASOF) is None


def test_gp_zero_cogs_is_real_value_not_missing():
    assert gross_profitability([gp(cogs=0.0)], "A", ASOF) == pytest.approx(0.5)


def test_gp_stale_is_missing():
    assert gross_profitability([gp()], "A", T(2028, 1, 1)) is None


@pytest.mark.parametrize("field", GP_REQUIRED)
def test_gp_deleting_required_field_fails(field):
    kw = gp_kw()
    names = {f.name for f in dataclasses.fields(FundamentalFact)}
    assert field in names, f"{field} must be a declared field of FundamentalFact"
    kw.pop(field)
    with pytest.raises(TypeError):
        FundamentalFact(**kw)


@pytest.mark.parametrize("field,bad", [
    ("source", ""), ("source", None), ("statement_ref", ""), ("statement_ref", None),
    ("currency", ""), ("currency", "usd"), ("currency", None), ("period_type", "monthly"),
    ("period_type", None), ("unit_scale", 0), ("unit_scale", -1.0), ("unit_scale", None),
    ("revision", -1), ("revision", None), ("revision", 1.5), ("known_at", None),
    ("security", ""),
])
def test_gp_invalid_required_value_rejected(field, bad):
    with pytest.raises(PITError):
        gross_profitability([gp(**{field: bad})], "A", ASOF)


@pytest.mark.parametrize("bad", [D(2026, 2, 20), datetime(2026, 2, 20, 21, 30)])
def test_gp_known_at_must_be_timezone_aware_datetime(bad):
    with pytest.raises(PITError):
        gross_profitability([gp(known_at=bad)], "A", ASOF)


def test_gp_as_of_must_be_timezone_aware_datetime():
    for bad in (D(2026, 3, 1), datetime(2026, 3, 1)):
        with pytest.raises(PITError):
            gross_profitability([gp()], "A", bad)


def test_gp_period_end_must_be_a_date_not_datetime():
    with pytest.raises(PITError):
        gross_profitability([gp(period_end=T(2025, 12, 31))], "A", ASOF)


def test_gp_shuffled_input_order_does_not_change_result():
    facts = [
        gp(period_end=D(2024, 12, 31), known_at=T(2025, 2, 20), revenue=1.0),
        gp(revision=0), gp(revision=1, known_at=T(2026, 2, 25), cogs=50.0),
        gp(revision=2, known_at=T(2026, 2, 25), cogs=55.0),
        gp(revision=3, known_at=T(2026, 9, 1), cogs=99.0),
        gp(security="B", cogs=1.0),
    ]
    for as_of in (T(2026, 2, 21), T(2026, 2, 26), T(2026, 10, 1)):
        want = gross_profitability(facts, "A", as_of)
        rng = random.Random(7)
        for _ in range(25):
            shuf = facts[:]
            rng.shuffle(shuf)
            assert gross_profitability(shuf, "A", as_of) == want
    assert gross_profitability(facts, "A", T(2026, 2, 26)) == pytest.approx(0.225)


# ---------------------------------------------------------------- Sloan accruals
BS_REQUIRED = GP_REQUIRED


def bs_kw(pe, ka, **kw):
    base = dict(security="A", period_end=pe, known_at=ka, source="synthetic",
                period_type="annual", currency="USD", unit_scale=1.0,
                statement_ref=f"synthetic:10-K:{pe.year}", revision=0,
                current_assets=100.0, cash=20.0, current_liabilities=50.0,
                short_term_debt=10.0, tax_payable=5.0, depreciation_amortization=8.0,
                total_assets=400.0)
    base.update(kw)
    return base


def bs(pe, ka, **kw):
    return mk(BalanceSheetFact, **bs_kw(pe, ka, **kw))


PREV = (D(2024, 12, 31), T(2025, 2, 20))
CUR = (D(2025, 12, 31), T(2026, 2, 20))


def pair(prev_kw=None, **cur_kw):
    return [bs(*PREV, **(prev_kw or {})), bs(*CUR, **cur_kw)]


def test_accruals_value_matches_sloan_1996_formula():
    # dCA=+30, dCash=+5, dCL=+10, dSTD=+2, dTax=+1, D&A=8, avgTA=(400+440)/2
    facts = pair(current_assets=130.0, cash=25.0, current_liabilities=60.0,
                 short_term_debt=12.0, tax_payable=6.0, total_assets=440.0)
    expected = ((30 - 5) - (10 - 2 - 1) - 8) / 420.0
    assert sloan_accruals(facts, "A", ASOF) == pytest.approx(expected)


def test_accruals_depreciation_amortization_is_subtracted():
    base = sloan_accruals(pair(), "A", ASOF)
    more = sloan_accruals(pair(depreciation_amortization=18.0), "A", ASOF)
    assert more == pytest.approx(base - 10.0 / 400.0)


def test_accruals_no_lookahead():
    assert sloan_accruals(pair(), "A", T(2026, 2, 20, 21, 29)) is None


def test_accruals_prior_year_must_be_visible():
    prev = bs(PREV[0], T(2026, 5, 1))
    cur = bs(*CUR)
    assert sloan_accruals([prev, cur], "A", ASOF) is None


def test_accruals_late_revision_of_prior_year_not_in_earlier_computation():
    prev = bs(*PREV)
    prev_rev = bs(PREV[0], T(2026, 8, 1), revision=1, cash=0.0)
    cur = bs(*CUR, cash=25.0)
    early = sloan_accruals([prev, prev_rev, cur], "A", ASOF)
    assert early == pytest.approx(sloan_accruals([prev, cur], "A", ASOF))
    late = sloan_accruals([prev, prev_rev, cur], "A", T(2026, 9, 1))
    assert late != pytest.approx(early)


def test_accruals_late_revision_of_current_year_not_in_earlier_computation():
    prev, cur = bs(*PREV), bs(*CUR)
    cur_rev = bs(CUR[0], T(2026, 7, 1), revision=1, current_assets=500.0)
    assert sloan_accruals([prev, cur, cur_rev], "A", ASOF) == pytest.approx(
        sloan_accruals([prev, cur], "A", ASOF))


def test_accruals_conflict_same_known_at_same_revision_raises():
    prev, cur = bs(*PREV), bs(*CUR)
    cur2 = bs(*CUR, cash=21.0)
    for order in ([prev, cur, cur2], [cur2, cur, prev]):
        with pytest.raises(ambiguous()):
            sloan_accruals(order, "A", ASOF)


def test_accruals_same_known_at_higher_revision_wins_any_order():
    prev = bs(*PREV)
    r0, r1 = bs(*CUR, cash=20.0), bs(*CUR, cash=30.0, revision=1)
    want = sloan_accruals([prev, r1], "A", ASOF)
    for order in itertools.permutations([prev, r0, r1]):
        assert sloan_accruals(list(order), "A", ASOF) == pytest.approx(want)


def test_accruals_currency_mismatch_between_years_raises():
    with pytest.raises(PITError):
        sloan_accruals([bs(*PREV, currency="EUR"), bs(*CUR)], "A", ASOF)


def test_accruals_unit_scale_applied_across_years():
    a = pair(current_assets=130.0, cash=25.0, total_assets=440.0)
    # prior year reported in thousands, current in units: same economics
    prev_k = bs(*PREV, unit_scale=1000.0, current_assets=0.1, cash=0.02,
                current_liabilities=0.05, short_term_debt=0.01, tax_payable=0.005,
                depreciation_amortization=0.008, total_assets=0.4)
    got = sloan_accruals([prev_k, a[1]], "A", ASOF)
    assert got == pytest.approx(sloan_accruals(a, "A", ASOF))


def test_accruals_period_type_annual_only_by_default():
    q_prev = bs(D(2024, 12, 31), T(2025, 2, 20), period_type="quarterly")
    assert sloan_accruals([q_prev, bs(*CUR)], "A", ASOF) is None


@pytest.mark.parametrize("field", ["cash", "short_term_debt", "tax_payable",
                                   "current_assets", "current_liabilities",
                                   "depreciation_amortization", "total_assets"])
def test_accruals_missing_component_is_not_zero(field):
    assert sloan_accruals(pair(**{field: None}), "A", ASOF) is None


@pytest.mark.parametrize("field", ["cash", "short_term_debt", "tax_payable",
                                   "current_assets", "current_liabilities", "total_assets"])
def test_accruals_missing_prior_year_component_is_not_zero(field):
    assert sloan_accruals(pair(prev_kw={field: None}), "A", ASOF) is None


@pytest.mark.parametrize("field", BS_REQUIRED)
def test_accruals_deleting_required_field_fails(field):
    kw = bs_kw(*CUR)
    assert field in {f.name for f in dataclasses.fields(BalanceSheetFact)}
    kw.pop(field)
    with pytest.raises(TypeError):
        BalanceSheetFact(**kw)


@pytest.mark.parametrize("field,bad", [
    ("source", ""), ("statement_ref", ""), ("currency", "us"), ("period_type", "x"),
    ("unit_scale", 0), ("revision", -1), ("known_at", None),
])
def test_accruals_invalid_required_value_rejected(field, bad):
    with pytest.raises(PITError):
        sloan_accruals(pair(**{field: bad}), "A", ASOF)


@pytest.mark.parametrize("bad", [D(2026, 2, 20), datetime(2026, 2, 20, 21, 30)])
def test_accruals_known_at_must_be_timezone_aware(bad):
    with pytest.raises(PITError):
        sloan_accruals(pair(known_at=bad), "A", ASOF)


def test_accruals_shuffled_input_order_does_not_change_result():
    facts = pair(cash=25.0) + [
        bs(CUR[0], T(2026, 3, 5), revision=1, cash=26.0),
        bs(CUR[0], T(2026, 3, 5), revision=2, cash=27.0),
        bs(PREV[0], T(2026, 3, 5), revision=1, cash=19.0),
        bs(D(2023, 12, 31), T(2024, 2, 20)),
    ]
    for as_of in (T(2026, 3, 1), T(2026, 4, 1)):
        want = sloan_accruals(facts, "A", as_of)
        rng = random.Random(11)
        for _ in range(25):
            shuf = facts[:]
            rng.shuffle(shuf)
            assert sloan_accruals(shuf, "A", as_of) == want


# ---------------------------------------------------------------- MAX
def weekdays(start, n):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


CAL = weekdays(D(2025, 12, 1), 130)      # Dec 2025 .. May 2026 weekdays
JAN = [d for d in CAL if (d.year, d.month) == (2026, 1)]
BASIS = "synthetic_total_return_adjusted_close_to_close"


def dr(day, ret, ka=None, rev=0, source="synthetic", basis=BASIS, security="A"):
    ka = ka if ka is not None else datetime(day.year, day.month, day.day, 23, 0, tzinfo=UTC)
    return mk(DailyReturn, security=security, day=day, known_at=ka, source=source,
              adjustment_basis=basis, revision=rev, ret=ret)


def series(value=0.01, overrides=None, skip=(), days=CAL, **kw):
    overrides = overrides or {}
    return [dr(d, overrides.get(d, value), **kw) for d in days if d not in skip]


MAX_ASOF = T(2026, 2, 10, 23, 30)           # inside February


def test_max_primary_is_prior_calendar_month():
    ov = {D(2025, 12, 31): 0.50, JAN[5]: 0.08, D(2026, 2, 5): 0.90}
    assert max_lottery(series(overrides=ov), "A", MAX_ASOF, CAL) == pytest.approx(0.08)


def test_max_prior_month_excludes_current_and_earlier_months():
    ov = {D(2025, 12, 31): 0.60, D(2026, 2, 2): 0.70}
    assert max_lottery(series(overrides=ov), "A", MAX_ASOF, CAL) == pytest.approx(0.01)


def test_max_prior_month_across_year_boundary():
    ov = {D(2025, 12, 15): 0.33, JAN[0]: 0.9}
    got = max_lottery(series(overrides=ov), "A", T(2026, 1, 12, 23, 30), CAL)
    assert got == pytest.approx(0.33)


def test_max_fixed_21_variant_is_separate_and_labelled():
    ov = {D(2026, 2, 9): 0.07, D(2025, 12, 31): 0.50}
    rows = series(overrides=ov)
    got = max_lottery(rows, "A", MAX_ASOF, CAL, variant="fixed_21_trading_days")
    window = sorted(d for d in CAL if d <= MAX_ASOF.date())[-21:]
    assert D(2025, 12, 31) not in window and D(2026, 2, 9) in window
    assert got == pytest.approx(0.07)


def test_max_unknown_variant_raises():
    with pytest.raises(ValueError):
        max_lottery(series(), "A", MAX_ASOF, CAL, variant="whatever")


def test_max_no_lookahead_known_at_hides_late_rows():
    ov = {JAN[10]: 0.9}
    rows = [r for r in series(overrides=ov)]
    late = [dataclasses.replace(r, known_at=T(2026, 2, 28)) if r.day == JAN[10] else r for r in rows]
    assert max_lottery(late, "A", MAX_ASOF, CAL) == pytest.approx(0.01)
    assert max_lottery(late, "A", T(2026, 2, 28, 0, 1), CAL) == pytest.approx(0.9)
    assert max_lottery(rows, "A", MAX_ASOF, CAL) == pytest.approx(0.9)


def test_max_late_revision_not_visible_in_earlier_computation():
    base = series(overrides={JAN[10]: 0.05})
    rev = dr(JAN[10], 0.40, ka=T(2026, 2, 20), rev=1)
    assert max_lottery(base + [rev], "A", MAX_ASOF, CAL) == pytest.approx(0.05)
    assert max_lottery(base + [rev], "A", T(2026, 2, 21), CAL) == pytest.approx(0.40)


def test_max_same_day_same_known_at_conflict_raises_any_order():
    a = dr(JAN[3], 0.02, ka=T(2026, 1, 10), rev=0)
    b = dr(JAN[3], 0.03, ka=T(2026, 1, 10), rev=0)
    rows = [r for r in series() if r.day != JAN[3]]
    for extra in ([a, b], [b, a]):
        with pytest.raises(ambiguous()):
            max_lottery(rows + extra, "A", MAX_ASOF, CAL)


def test_max_same_known_at_higher_revision_wins_any_order():
    a = dr(JAN[3], 0.02, ka=T(2026, 1, 10), rev=0)
    b = dr(JAN[3], 0.30, ka=T(2026, 1, 10), rev=1)
    rows = [r for r in series() if r.day != JAN[3]]
    for extra in ([a, b], [b, a]):
        assert max_lottery(rows + extra, "A", MAX_ASOF, CAL) == pytest.approx(0.30)


def test_max_duplicate_trading_days_detected():
    dup = CAL + [JAN[4]]
    with pytest.raises(error_types()):
        max_lottery(series(), "A", MAX_ASOF, dup)
    with pytest.raises(error_types()):
        max_lottery(series(), "A", MAX_ASOF, dup, variant="fixed_21_trading_days")


def test_max_duplicate_trading_days_not_silently_shrinking_window():
    # a duplicated day must never change the answer silently
    ov = {JAN[0]: 0.2}
    dup = CAL + [JAN[0], JAN[0]]
    with pytest.raises(error_types()):
        max_lottery(series(overrides=ov), "A", MAX_ASOF, dup, variant="fixed_21_trading_days")


def test_max_trading_days_order_does_not_matter():
    ov = {JAN[7]: 0.12}
    rows = series(overrides=ov)
    want = max_lottery(rows, "A", MAX_ASOF, CAL)
    rng = random.Random(3)
    for _ in range(10):
        cal = CAL[:]
        rng.shuffle(cal)
        assert max_lottery(rows, "A", MAX_ASOF, cal) == want
        assert max_lottery(rows, "A", MAX_ASOF, cal, variant="fixed_21_trading_days") == \
            max_lottery(rows, "A", MAX_ASOF, CAL, variant="fixed_21_trading_days")


def test_max_trading_days_must_be_dates():
    with pytest.raises(PITError):
        max_lottery(series(), "A", MAX_ASOF, [T(2026, 1, 5)] + CAL)


def test_max_shuffled_row_order_does_not_change_result():
    rows = series(overrides={JAN[2]: 0.04, JAN[9]: 0.06}) + [
        dr(JAN[9], 0.25, ka=T(2026, 2, 3), rev=1),
        dr(JAN[9], 0.26, ka=T(2026, 2, 4), rev=2),
        dr(JAN[2], 0.99, ka=T(2026, 2, 20), rev=1),
    ]
    for as_of in (T(2026, 2, 3, 12), T(2026, 2, 10), T(2026, 2, 25)):
        want = max_lottery(rows, "A", as_of, CAL)
        rng = random.Random(5)
        for _ in range(25):
            s = rows[:]
            rng.shuffle(s)
            assert max_lottery(s, "A", as_of, CAL) == want


def test_max_missing_not_zero_negative_returns():
    skip = set(JAN[-4:])
    assert max_lottery(series(-0.02, skip=skip), "A", MAX_ASOF, CAL) == pytest.approx(-0.02)


def test_max_nan_and_none_skipped():
    ov = {JAN[-1]: None, JAN[-2]: float("nan")}
    assert max_lottery(series(-0.02, overrides=ov), "A", MAX_ASOF, CAL) == pytest.approx(-0.02)


def test_max_too_few_obs_is_missing():
    skip = set(JAN[:-5])
    assert max_lottery(series(0.01, skip=skip), "A", MAX_ASOF, CAL) is None


def test_max_without_prior_month_history_is_missing():
    assert max_lottery(series(), "A", T(2025, 12, 10), CAL) is None


def test_max_other_security_ignored():
    rows = series() + series(0.9, security="B")
    assert max_lottery(rows, "A", MAX_ASOF, CAL) == pytest.approx(0.01)


@pytest.mark.parametrize("field", ["security", "day", "known_at", "source",
                                   "adjustment_basis", "revision", "ret"])
def test_max_deleting_required_field_fails(field):
    kw = dict(security="A", day=JAN[0], known_at=T(2026, 1, 5), source="synthetic",
              adjustment_basis=BASIS, revision=0, ret=0.01)
    assert field in {f.name for f in dataclasses.fields(DailyReturn)}
    kw.pop(field)
    with pytest.raises(TypeError):
        DailyReturn(**kw)


@pytest.mark.parametrize("field,bad", [
    ("source", ""), ("source", None), ("basis", ""), ("basis", None), ("rev", -1), ("rev", None),
])
def test_max_invalid_required_value_rejected(field, bad):
    kw = {"source": "synthetic", "basis": BASIS, "rev": 0}
    kw[field] = bad
    rows = series() + [dr(JAN[0], 0.01, **kw)]
    with pytest.raises(PITError):
        max_lottery(rows, "A", MAX_ASOF, CAL)


@pytest.mark.parametrize("bad", [D(2026, 1, 5), datetime(2026, 1, 5, 12)])
def test_max_known_at_must_be_timezone_aware(bad):
    rows = series() + [mk(DailyReturn, security="A", day=JAN[0], known_at=bad,
                          source="synthetic", adjustment_basis=BASIS, revision=0, ret=0.01)]
    with pytest.raises(PITError):
        max_lottery(rows, "A", MAX_ASOF, CAL)


def test_max_as_of_must_be_timezone_aware():
    with pytest.raises(PITError):
        max_lottery(series(), "A", datetime(2026, 2, 10), CAL)


def test_max_mixed_adjustment_basis_for_same_day_raises():
    a = dr(JAN[3], 0.02, ka=T(2026, 1, 10), rev=0)
    b = dr(JAN[3], 0.02, ka=T(2026, 1, 11), rev=1, basis="price_only_unadjusted")
    rows = [r for r in series() if r.day != JAN[3]]
    with pytest.raises(PITError):
        max_lottery(rows + [a, b], "A", MAX_ASOF, CAL)


# ---------------------------------------------------------------- ranking
def test_rank_missing_stays_missing():
    r = cross_sectional_rank({"a": 1.0, "b": None, "c": 3.0, "d": float("nan"), "e": 1.0})
    assert r["b"] is None and r["d"] is None
    assert r["a"] == r["e"] == pytest.approx(1.5 / 3) and r["c"] == pytest.approx(1.0)


# ---------------------------------------------------------------- contract file
def test_candidate_contracts_are_not_validated_and_well_formed():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "contracts/factors"
    life = json.loads((root / "lifecycle_fields.json").read_text())
    c = json.loads((root / "candidates_20261011.json").read_text())
    assert len(c["candidates"]) == 3
    for e in c["candidates"]:
        assert e["CONTRACT_STATUS"] in life["contract_status"]
        assert e["DATA_STATUS"] in life["data_status"]
        assert e["VALIDATION_STATUS"] == "NOT_VALIDATED"
        assert (root.parent.parent / e["implementation"]).exists()
