"""Synthetic-data tests: correctness, PIT/leakage, missing != zero."""
from datetime import date, timedelta

import pytest

from factor_candidates import (
    BalanceSheetFact, DailyReturn, FundamentalFact, PITError,
    cross_sectional_rank, gross_profitability, max_lottery, sloan_accruals,
)

D = date


def gp(**kw):
    base = dict(security="A", period_end=D(2025, 12, 31), known_at=D(2026, 2, 20),
                source="synthetic", revenue=100.0, cogs=60.0, total_assets=200.0)
    base.update(kw)
    return FundamentalFact(**base)


# ---- Gross profitability
def test_gp_value():
    assert gross_profitability([gp()], "A", D(2026, 3, 1)) == pytest.approx(0.2)

def test_gp_no_lookahead_before_known_at():
    assert gross_profitability([gp()], "A", D(2026, 2, 19)) is None

def test_gp_restatement_invisible_until_known():
    orig, rest = gp(), gp(known_at=D(2026, 6, 1), cogs=90.0)
    assert gross_profitability([orig, rest], "A", D(2026, 3, 1)) == pytest.approx(0.2)
    assert gross_profitability([orig, rest], "A", D(2026, 6, 1)) == pytest.approx(0.05)

def test_gp_missing_is_not_zero():
    assert gross_profitability([gp(cogs=None)], "A", D(2026, 3, 1)) is None
    assert gross_profitability([gp(revenue=float("nan"))], "A", D(2026, 3, 1)) is None
    assert gross_profitability([gp(total_assets=0.0)], "A", D(2026, 3, 1)) is None

def test_gp_zero_cogs_is_real_value_not_missing():
    assert gross_profitability([gp(cogs=0.0)], "A", D(2026, 3, 1)) == pytest.approx(0.5)

def test_gp_stale_is_missing():
    assert gross_profitability([gp()], "A", D(2028, 1, 1)) is None

def test_gp_requires_provenance():
    with pytest.raises(PITError):
        gross_profitability([gp(source="")], "A", D(2026, 3, 1))

def test_gp_deterministic_order_independent():
    a, b = gp(), gp(period_end=D(2024, 12, 31), known_at=D(2025, 2, 20), revenue=1.0)
    assert gross_profitability([a, b], "A", D(2026, 3, 1)) == gross_profitability([b, a], "A", D(2026, 3, 1))


# ---- Sloan accruals
def bs(pe, ka, **kw):
    base = dict(security="A", period_end=pe, known_at=ka, source="synthetic",
                current_assets=100.0, cash=20.0, current_liabilities=50.0,
                short_term_debt=10.0, tax_payable=5.0, depreciation=8.0, total_assets=400.0)
    base.update(kw)
    return BalanceSheetFact(**base)

def pair(**cur_kw):
    prev = bs(D(2024, 12, 31), D(2025, 2, 20))
    cur = bs(D(2025, 12, 31), D(2026, 2, 20), **cur_kw)
    return [prev, cur]

def test_accruals_value():
    # dCA=+30, dCash=+5, dCL=+10, dSTD=+2, dTax=+1, dep=8, avgTA=400
    facts = pair(current_assets=130.0, cash=25.0, current_liabilities=60.0,
                 short_term_debt=12.0, tax_payable=6.0)
    expected = ((30 - 5) - (10 - 2 - 1) - 8) / 400
    assert sloan_accruals(facts, "A", D(2026, 3, 1)) == pytest.approx(expected)

def test_accruals_no_lookahead():
    assert sloan_accruals(pair(), "A", D(2026, 2, 19)) is None

def test_accruals_uses_prior_year_visible_only():
    # prior-year statement not yet public => cannot compute
    prev = bs(D(2024, 12, 31), D(2026, 5, 1))
    cur = bs(D(2025, 12, 31), D(2026, 2, 20))
    assert sloan_accruals([prev, cur], "A", D(2026, 3, 1)) is None

@pytest.mark.parametrize("field", ["cash", "short_term_debt", "tax_payable",
                                   "current_assets", "current_liabilities"])
def test_accruals_missing_component_is_not_zero(field):
    assert sloan_accruals(pair(**{field: None}), "A", D(2026, 3, 1)) is None

def test_accruals_missing_depreciation_not_zero():
    assert sloan_accruals(pair(depreciation=None), "A", D(2026, 3, 1)) is None

def test_accruals_requires_provenance():
    f = pair()
    f[0] = bs(D(2024, 12, 31), D(2025, 2, 20), source="")
    with pytest.raises(PITError):
        sloan_accruals(f, "A", D(2026, 3, 1))


# ---- MAX
DAYS = [D(2026, 1, 1) + timedelta(days=i) for i in range(40)]

def rets(values, ka_lag=0, skip=()):
    out = []
    for d, v in zip(DAYS, values):
        if d in skip:
            continue
        out.append(DailyReturn("A", d, d + timedelta(days=ka_lag), "synthetic", v))
    return out

def test_max_value_and_window():
    vals = [0.01] * 40
    vals[3] = 0.50     # outside the trailing 21-day window
    vals[30] = 0.08
    assert max_lottery(rets(vals), "A", DAYS[-1], DAYS) == pytest.approx(0.08)

def test_max_no_lookahead_on_as_of():
    vals = [0.01] * 40
    vals[39] = 0.9
    assert max_lottery(rets(vals), "A", DAYS[38], DAYS) == pytest.approx(0.01)

def test_max_known_at_lag_hides_late_data():
    vals = [0.01] * 40
    vals[39] = 0.9
    assert max_lottery(rets(vals, ka_lag=0), "A", DAYS[39], DAYS) == pytest.approx(0.9)
    late = rets(vals)
    late[-1] = DailyReturn("A", DAYS[39], DAYS[39] + timedelta(days=5), "synthetic", 0.9)
    assert max_lottery(late, "A", DAYS[39], DAYS) == pytest.approx(0.01)

def test_max_missing_not_zero_negative_returns():
    # all returns negative: a zero-fill would wrongly yield MAX == 0
    vals = [-0.02] * 40
    skip = {DAYS[i] for i in range(36, 40)}
    assert max_lottery(rets(vals, skip=skip), "A", DAYS[-1], DAYS) == pytest.approx(-0.02)

def test_max_nan_and_none_skipped():
    vals = [-0.02] * 40
    vals[39] = None
    vals[38] = float("nan")
    assert max_lottery(rets(vals), "A", DAYS[-1], DAYS) == pytest.approx(-0.02)

def test_max_too_few_obs_is_missing():
    skip = {DAYS[i] for i in range(25, 40)}
    assert max_lottery(rets([0.01] * 40, skip=skip), "A", DAYS[-1], DAYS) is None

def test_max_short_history_is_missing():
    assert max_lottery(rets([0.01] * 40), "A", DAYS[10], DAYS) is None

def test_max_requires_provenance():
    r = rets([0.01] * 40)
    r[0] = DailyReturn("A", DAYS[0], DAYS[0], "", 0.01)
    with pytest.raises(PITError):
        max_lottery(r, "A", DAYS[-1], DAYS)


# ---- ranking
def test_rank_missing_stays_missing():
    r = cross_sectional_rank({"a": 1.0, "b": None, "c": 3.0, "d": float("nan"), "e": 1.0})
    assert r["b"] is None and r["d"] is None
    assert r["a"] == r["e"] == pytest.approx(1.5 / 3) and r["c"] == pytest.approx(1.0)


# ---- contract file
def test_candidate_contracts_are_not_validated_and_well_formed():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "contracts/factors"
    life = json.loads((root / "lifecycle_fields.json").read_text())
    c = json.loads((root / "candidates_20261011.json").read_text())
    ok_val = set(c["lifecycle_extension"]["validation_status"])
    assert len(c["candidates"]) == 3
    for e in c["candidates"]:
        assert e["CONTRACT_STATUS"] in life["contract_status"]
        assert e["DATA_STATUS"] in life["data_status"]
        assert e["VALIDATION_STATUS"] == "NOT_VALIDATED"
        assert (root.parent.parent / e["implementation"]).exists()
