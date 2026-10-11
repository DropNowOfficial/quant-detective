"""Hand-derived spectrum and point-in-time contracts for research diagnostics."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import math

import numpy as np
import pytest


def api():
    """Missing capability is a failed assertion, not a collection/import error."""
    try:
        spec = importlib.util.find_spec("research_lab.factor_mining.dimension")
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, "effective-dimension research diagnostics missing"
    module = importlib.import_module("research_lab.factor_mining.dimension")
    assert hasattr(module, "DimensionStats"), "immutable DimensionStats missing"
    assert hasattr(module, "effective_dimension"), "spectrum diagnostic missing"
    assert hasattr(module, "dimension_from_returns"), "point-in-time window missing"
    return module


def window_inputs(rows=6, assets=2):
    from research_lab.factor_mining.contracts import Evidence

    rng = np.random.default_rng(20261011)
    sessions = [datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
                for i in range(rows)]
    availability = [session + timedelta(hours=22) for session in sessions]
    freeze = sessions[0] - timedelta(days=1)
    return {
        "returns": rng.normal(size=(rows, assets)),
        "columns": tuple(f"ASSET_{i}" for i in range(assets)),
        "sessions": sessions,
        "expected_sessions": list(sessions),
        "available_at": availability,
        "row_evidence": [Evidence(session, known, known, "synthetic://returns", "fixture-v1",
                                  "fixture-only", "SYNTHETIC_FIXTURE")
                         for session, known in zip(sessions, availability)],
        "universe_evidence": Evidence(freeze, freeze, freeze, "synthetic://universe",
                                      "frozen-universe-2024-v1", "fixture-only", "SYNTHETIC_FIXTURE"),
        "decision_time": sessions[-1] + timedelta(days=1, hours=9),
        "universe_version": "frozen-universe-2024-v1",
        "window": rows,
    }


# Mutations these tests catch: using matrix rank, raw singular values, or the
# number of columns in place of the probability-weighted covariance spectrum.
@pytest.mark.parametrize("size", [1, 2, 7])
def test_identity_has_all_independent_dimensions(size):
    stats = api().effective_dimension(np.eye(size))
    assert stats.n_assets == size
    assert stats.entropy_dimension == pytest.approx(size)
    assert stats.participation_dimension == pytest.approx(size)
    assert stats.normalized_entropy == pytest.approx(1)
    assert stats.normalized_participation == pytest.approx(1)
    assert stats.top1_share == pytest.approx(1 / size)


@pytest.mark.parametrize("signs", [[1, 1, 1, 1], [1, -1, 1, -1]])
def test_perfectly_dependent_series_have_one_dimension(signs):
    stats = api().effective_dimension(np.outer(signs, signs))
    assert stats.entropy_dimension == pytest.approx(1)
    assert stats.participation_dimension == pytest.approx(1)
    assert stats.normalized_entropy == pytest.approx(1 / len(signs))
    assert stats.normalized_participation == pytest.approx(1 / len(signs))
    assert stats.top1_share == pytest.approx(1)


@pytest.mark.parametrize("rho", [-0.25, 0.0, 0.25, 0.8])
def test_equicorrelation_matches_analytic_eigenvalue_probabilities(rho):
    size = 4
    eigenvalues = [1 + (size - 1) * rho] + [1 - rho] * (size - 1)
    probabilities = np.asarray(eigenvalues) / size
    expected_entropy = math.exp(-sum(p * math.log(p) for p in probabilities))
    expected_participation = 1 / sum(p * p for p in probabilities)
    matrix = (1 - rho) * np.eye(size) + rho * np.ones((size, size))
    stats = api().effective_dimension(matrix)
    assert stats.entropy_dimension == pytest.approx(expected_entropy)
    assert stats.participation_dimension == pytest.approx(expected_participation)
    assert stats.normalized_entropy == pytest.approx(expected_entropy / size)
    assert stats.normalized_participation == pytest.approx(expected_participation / size)
    assert stats.top1_share == pytest.approx(max(probabilities))
    assert stats.eigenvalues == pytest.approx(sorted(eigenvalues, reverse=True))


def test_covariance_eigenvalues_are_not_square_root_weighted():
    stats = api().effective_dimension([[1, 0.6], [0.6, 1]])
    assert stats.participation_dimension == pytest.approx(1 / (0.8**2 + 0.2**2))
    assert stats.entropy_dimension == pytest.approx(math.exp(-0.8*math.log(0.8) - 0.2*math.log(0.2)))


def test_disconnected_identical_pairs_have_two_dimensions():
    stats = api().effective_dimension(np.kron(np.eye(2), np.ones((2, 2))))
    assert stats.entropy_dimension == pytest.approx(2)
    assert stats.participation_dimension == pytest.approx(2)
    assert stats.top1_share == pytest.approx(0.5)


def test_asset_permutation_preserves_spectrum_and_inputs():
    matrix = np.array([[1, 0.4, -0.2], [0.4, 1, 0.1], [-0.2, 0.1, 1]])
    before = matrix.copy()
    stats = api().effective_dimension(matrix)
    other = api().effective_dimension(matrix[np.ix_([2, 0, 1], [2, 0, 1])])
    np.testing.assert_array_equal(matrix, before)
    assert other.entropy_dimension == pytest.approx(stats.entropy_dimension)
    assert other.participation_dimension == pytest.approx(stats.participation_dimension)


def test_statistics_and_spectrum_are_immutable():
    stats = api().effective_dimension(np.eye(3))
    assert isinstance(stats, api().DimensionStats)
    assert isinstance(stats.eigenvalues, tuple)
    with pytest.raises(FrozenInstanceError):
        stats.entropy_dimension = 999


@pytest.mark.parametrize("size", [5, 7, 21])
def test_normalized_dimensions_respect_closed_unit_bounds_despite_roundoff(size):
    stats = api().effective_dimension(np.eye(size))
    assert 1 <= stats.entropy_dimension <= stats.n_assets
    assert 1 <= stats.participation_dimension <= stats.n_assets
    assert 0 < stats.normalized_entropy <= 1
    assert 0 < stats.normalized_participation <= 1


@pytest.mark.parametrize("matrix", [
    np.array([[1, 0.2j], [-0.2j, 1]]),
    np.ma.array([[1, 0], [0, 1]], mask=[[False, True], [True, False]]),
])
def test_complex_or_masked_correlations_are_not_silently_coerced(matrix):
    with pytest.raises(ValueError, match="real|masked"):
        api().effective_dimension(matrix)


@pytest.mark.parametrize("matrix, reason", [
    ([], "square"),
    ([[1, 0, 0], [0, 1, 0]], "square"),
    ([1, 1], "square"),
    ([[[1]]], "square"),
    ([[1, float("nan")], [float("nan"), 1]], "finite"),
    ([[1, float("inf")], [float("inf"), 1]], "finite"),
    ([[1, 0.2], [0.3, 1]], "symmetric"),
    ([[2, 0], [0, 1]], "unit diagonal"),
    ([[1, -0.75, -0.75], [-0.75, 1, -0.75], [-0.75, -0.75, 1]], "positive semidefinite"),
])
def test_invalid_correlations_fail_closed(matrix, reason):
    with pytest.raises(ValueError, match=reason):
        api().effective_dimension(matrix)


def test_only_explicit_relative_roundoff_negative_eigenvalues_are_clipped():
    module = api()
    assert module.PSD_RELATIVE_TOLERANCE == 1e-12
    stats = module.effective_dimension([[1, 1 + 5e-13], [1 + 5e-13, 1]])
    assert stats.eigenvalues[-1] == 0
    assert stats.entropy_dimension == pytest.approx(1)
    with pytest.raises(ValueError, match="positive semidefinite"):
        module.effective_dimension([[1, 1 + 1e-7], [1 + 1e-7, 1]])


def test_extreme_finite_non_psd_matrix_cannot_produce_nan_statistics():
    with np.errstate(over="ignore", invalid="ignore"):
        with pytest.raises(ValueError, match="positive semidefinite"):
            api().effective_dimension([[1, 1e308], [1e308, 1]])


def test_unrepresentable_numeric_correlation_is_a_validation_error():
    with pytest.raises(ValueError, match="numeric.*square"):
        api().effective_dimension([[1, 10**1000], [10**1000, 1]])


def test_unrepresentable_numeric_returns_are_a_validation_error():
    inputs = window_inputs()
    inputs["returns"] = inputs["returns"].astype(object)
    inputs["returns"][-1, 0] = 10**1000
    with pytest.raises(ValueError, match="numeric.*two-dimensional"):
        api().dimension_from_returns(**inputs)


# Mutations these tests catch: same-day leakage, late-data filtering/backfilling,
# dropping incomplete rows, changing asset membership, and rank-limited windows.
def test_window_matches_demeaned_sample_standardized_covariance():
    inputs = window_inputs()
    data = inputs["returns"]
    standardized = (data - data.mean(axis=0)) / data.std(axis=0, ddof=1)
    correlation = standardized.T @ standardized / (len(data) - 1)
    expected = api().effective_dimension(correlation)
    stats = api().dimension_from_returns(**inputs)
    assert stats.entropy_dimension == pytest.approx(expected.entropy_dimension)
    assert stats.participation_dimension == pytest.approx(expected.participation_dimension)
    assert stats.eigenvalues == pytest.approx(expected.eigenvalues)
    assert stats.columns == inputs["columns"]
    assert stats.sessions == tuple(inputs["sessions"])
    assert stats.available_at == tuple(inputs["available_at"])
    assert stats.decision_time == inputs["decision_time"]
    assert stats.universe_version == inputs["universe_version"]
    assert stats.expected_sessions == tuple(inputs["expected_sessions"])
    assert stats.row_evidence == tuple(inputs["row_evidence"])
    assert stats.universe_evidence == inputs["universe_evidence"]
    assert stats.window == 6


def test_default_window_is_last_126_sessions_and_does_not_mutate_inputs():
    inputs = window_inputs(rows=130)
    inputs.pop("window")
    before = inputs["returns"].copy()
    stats = api().dimension_from_returns(**inputs)
    assert stats.window == 126
    assert stats.sessions == tuple(inputs["sessions"][-126:])
    np.testing.assert_array_equal(inputs["returns"], before)
    expected = api().effective_dimension(np.corrcoef(before[-126:], rowvar=False))
    assert stats.entropy_dimension == pytest.approx(expected.entropy_dimension)


def test_current_and_future_sessions_cannot_change_prior_session_diagnostic():
    inputs = window_inputs(rows=9)
    inputs["decision_time"] = inputs["sessions"][6] + timedelta(hours=23)
    inputs["window"] = 4
    stats = api().dimension_from_returns(**inputs)
    assert stats.sessions == tuple(inputs["sessions"][2:6])
    inputs["returns"][6:] = np.nan
    changed = api().dimension_from_returns(**inputs)
    assert changed == stats


def test_unselected_old_incomplete_data_is_outside_the_bounded_window():
    inputs = window_inputs(rows=8)
    inputs["window"] = 4
    expected = api().dimension_from_returns(**inputs)
    inputs["returns"][:4] = np.nan
    inputs["available_at"][:4] = [inputs["decision_time"] + timedelta(days=7)] * 4
    inputs["row_evidence"][:4] = [replace(evidence, available_at=known)
                                      for evidence, known in zip(inputs["row_evidence"][:4], inputs["available_at"][:4])]
    assert api().dimension_from_returns(**inputs) == expected


def test_timestamps_are_compared_and_returned_in_utc():
    inputs = window_inputs()
    offset = timezone(timedelta(hours=-5))
    inputs["sessions"] = [stamp.astimezone(offset) for stamp in inputs["sessions"]]
    inputs["available_at"] = [stamp.astimezone(offset).isoformat() for stamp in inputs["available_at"]]
    inputs["decision_time"] = inputs["decision_time"].astimezone(offset).isoformat()
    stats = api().dimension_from_returns(**inputs)
    assert all(stamp.tzinfo is timezone.utc for stamp in stats.sessions)
    assert all(stamp.tzinfo is timezone.utc for stamp in stats.available_at)
    assert stats.decision_time.tzinfo is timezone.utc
    assert stats.sessions[0] == datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_t_minus_one_uses_utc_dates_across_timezone_boundary():
    inputs = window_inputs(rows=7)
    inputs["decision_time"] = "2024-01-06T22:00:00-05:00"  # Jan 7 UTC
    inputs["window"] = 4
    stats = api().dimension_from_returns(**inputs)
    assert stats.sessions == tuple(inputs["sessions"][2:6])


def test_availability_at_decision_is_allowed_but_late_selected_data_is_rejected():
    inputs = window_inputs(rows=7)
    inputs["window"] = 4
    inputs["available_at"][-1] = inputs["decision_time"]
    inputs["row_evidence"][-1] = replace(inputs["row_evidence"][-1], available_at=inputs["decision_time"])
    assert api().dimension_from_returns(**inputs).window == 4
    inputs["available_at"][-1] += timedelta(microseconds=1)
    inputs["row_evidence"][-1] = replace(inputs["row_evidence"][-1], available_at=inputs["available_at"][-1])
    with pytest.raises(ValueError, match="available.*decision"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_incomplete_selected_rows_are_not_dropped_or_filled(bad):
    inputs = window_inputs(rows=7)
    inputs["window"] = 4
    inputs["returns"][-2, 1] = bad
    with pytest.raises(ValueError, match="complete.*finite"):
        api().dimension_from_returns(**inputs)


def test_zero_variance_series_is_not_dropped_from_the_universe():
    inputs = window_inputs()
    inputs["returns"][:, 1] = 0
    with pytest.raises(ValueError, match="zero variance"):
        api().dimension_from_returns(**inputs)


def test_nonzero_constant_series_is_rejected_even_if_mean_accumulation_rounds():
    inputs = window_inputs()
    inputs["returns"][:, 1] = 0.1
    with pytest.raises(ValueError, match="zero variance"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("kind", ["complex", "masked"])
def test_complex_or_masked_returns_cannot_silently_lose_information(kind):
    inputs = window_inputs()
    if kind == "complex":
        inputs["returns"] = inputs["returns"].astype(complex) + 0.25j
    else:
        inputs["returns"] = np.ma.array(inputs["returns"], mask=False)
        inputs["returns"].mask[2, 1] = True
    with pytest.raises(ValueError, match="real|masked"):
        api().dimension_from_returns(**inputs)


def test_perfectly_dependent_nonconstant_returns_are_valid():
    inputs = window_inputs()
    inputs["returns"][:, 1] = -2 * inputs["returns"][:, 0] + 0.04
    stats = api().dimension_from_returns(**inputs)
    assert stats.entropy_dimension == pytest.approx(1)
    assert stats.participation_dimension == pytest.approx(1)


@pytest.mark.parametrize("window, assets", [(2, 2), (3, 4)])
def test_rank_limited_windows_raise_explicit_diagnostic_error(window, assets):
    inputs = window_inputs(rows=6, assets=assets)
    inputs["window"] = window
    with pytest.raises(ValueError, match="rank.limit.*W.*N"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("window", [True, 0, -1, 2.5, "6"])
def test_window_must_be_a_positive_integer(window):
    inputs = window_inputs()
    inputs["window"] = window
    with pytest.raises(ValueError, match="window.*integer"):
        api().dimension_from_returns(**inputs)


def test_insufficient_prior_sessions_is_explicit():
    inputs = window_inputs()
    inputs["decision_time"] = inputs["sessions"][3]
    with pytest.raises(ValueError, match="insufficient.*prior"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("columns", [(), ("A", "A"), ("A", ""), ("A", "  "), ("A", 2), "AB"])
def test_unique_nonempty_fixed_column_names_are_required(columns):
    inputs = window_inputs()
    inputs["columns"] = columns
    with pytest.raises(ValueError, match="columns"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("version", [None, "", "   ", 42])
def test_universe_version_is_required(version):
    inputs = window_inputs()
    inputs["universe_version"] = version
    with pytest.raises(ValueError, match="universe_version"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("field", ["columns", "sessions", "available_at"])
def test_metadata_must_align_exactly_with_return_axes(field):
    inputs = window_inputs()
    inputs[field] = inputs[field][:-1]
    with pytest.raises(ValueError, match="align|columns"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("field", ["sessions", "available_at", "decision_time"])
def test_naive_timestamps_fail_closed(field):
    inputs = window_inputs()
    naive = datetime(2024, 1, 2)
    if field == "decision_time":
        inputs[field] = naive
    else:
        inputs[field][1] = naive
    with pytest.raises(ValueError, match="timezone-aware"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("duplicate", [True, False])
def test_session_dates_must_be_unique_and_strictly_increasing(duplicate):
    inputs = window_inputs()
    if duplicate:
        inputs["sessions"][2] = inputs["sessions"][1] + timedelta(hours=1)
    else:
        inputs["sessions"][1], inputs["sessions"][2] = inputs["sessions"][2], inputs["sessions"][1]
    with pytest.raises(ValueError, match="strictly increasing.*session"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("values", [[[1, 2], [2]], [1, 2, 3], [[[1, 2]]]])
def test_variable_membership_or_nonmatrix_returns_fail_closed(values):
    inputs = window_inputs()
    inputs["returns"] = values
    with pytest.raises(ValueError, match="rectangular|two-dimensional"):
        api().dimension_from_returns(**inputs)


def test_missing_selected_session_cannot_backfill_from_older_rows():
    inputs = window_inputs(rows=9)
    inputs["window"] = 4
    inputs["returns"] = np.delete(inputs["returns"], 6, axis=0)
    for field in ("sessions", "available_at", "row_evidence"):
        del inputs[field][6]
    # Eight rows remain, but the independent final four-session lattice has a gap.
    with pytest.raises(ValueError, match="expected.*session.*lattice"):
        api().dimension_from_returns(**inputs)


def test_unexpected_session_inside_window_is_not_silently_included():
    inputs = window_inputs(rows=9)
    inputs["window"] = 4
    del inputs["expected_sessions"][6]
    with pytest.raises(ValueError, match="expected.*session.*lattice"):
        api().dimension_from_returns(**inputs)


def test_expected_completion_timestamp_must_match_observed_session():
    inputs = window_inputs()
    inputs["expected_sessions"][3] += timedelta(hours=21)
    with pytest.raises(ValueError, match="expected.*session.*lattice"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("field", ["expected_sessions", "row_evidence", "universe_evidence"])
def test_independent_calendar_and_point_in_time_evidence_are_required(field):
    inputs = window_inputs()
    del inputs[field]
    with pytest.raises(TypeError, match=field):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("bad_lattice", ["naive", "duplicate", "reversed"])
def test_expected_session_lattice_requires_aware_unique_ordered_dates(bad_lattice):
    inputs = window_inputs()
    if bad_lattice == "naive":
        inputs["expected_sessions"][2] = inputs["expected_sessions"][2].replace(tzinfo=None)
    elif bad_lattice == "duplicate":
        inputs["expected_sessions"][2] = inputs["expected_sessions"][1]
    else:
        inputs["expected_sessions"].reverse()
    with pytest.raises(ValueError, match="timezone-aware|strictly increasing.*expected"):
        api().dimension_from_returns(**inputs)


def test_row_evidence_availability_must_match_legacy_timestamp():
    inputs = window_inputs()
    inputs["available_at"][-1] -= timedelta(hours=1)
    with pytest.raises(ValueError, match="available_at.*row_evidence"):
        api().dimension_from_returns(**inputs)


def test_availability_cannot_precede_the_completed_session():
    inputs = window_inputs()
    inputs["available_at"][-1] = inputs["sessions"][-1] - timedelta(microseconds=1)
    with pytest.raises(ValueError, match="availability.*completion"):
        api().dimension_from_returns(**inputs)


def test_row_evidence_event_must_be_the_exact_completed_session():
    inputs = window_inputs()
    inputs["row_evidence"][-1] = replace(inputs["row_evidence"][-1], event_time=inputs["sessions"][-1] - timedelta(hours=1))
    with pytest.raises(ValueError, match="event_time.*session"):
        api().dimension_from_returns(**inputs)


def test_late_actual_receipt_cannot_be_backdated_by_legacy_availability():
    inputs = window_inputs()
    late_receipt = inputs["decision_time"] + timedelta(microseconds=1)
    inputs["row_evidence"][-1] = replace(inputs["row_evidence"][-1],
                                       first_seen_at=late_receipt, available_at=late_receipt)
    with pytest.raises(ValueError, match="available_at.*row_evidence"):
        api().dimension_from_returns(**inputs)
    inputs["available_at"][-1] = late_receipt
    with pytest.raises(ValueError, match="available.*decision"):
        api().dimension_from_returns(**inputs)


def test_universe_must_be_known_by_start_of_first_selected_utc_date():
    inputs = window_inputs(rows=9)
    inputs["window"] = 4
    cutoff = inputs["sessions"][-4]
    inputs["universe_evidence"] = replace(inputs["universe_evidence"],
                                           available_at=cutoff, first_seen_at=cutoff)
    assert api().dimension_from_returns(**inputs).universe_evidence.available_at == cutoff
    late = cutoff + timedelta(microseconds=1)
    inputs["universe_evidence"] = replace(inputs["universe_evidence"],
                                           available_at=late, first_seen_at=late)
    with pytest.raises(ValueError, match="universe.*before.*window"):
        api().dimension_from_returns(**inputs)


@pytest.mark.parametrize("field", ["row_evidence", "universe_evidence"])
def test_evidence_is_typed_and_cannot_be_replaced_by_unverified_dicts(field):
    inputs = window_inputs()
    if field == "row_evidence":
        inputs[field][-1] = {"available_at": inputs["available_at"][-1]}
    else:
        inputs[field] = {"available_at": inputs["sessions"][0]}
    with pytest.raises(ValueError, match="Evidence"):
        api().dimension_from_returns(**inputs)


def test_row_evidence_must_align_exactly_with_all_observed_rows():
    inputs = window_inputs()
    inputs["row_evidence"].pop()
    with pytest.raises(ValueError, match="row_evidence.*align"):
        api().dimension_from_returns(**inputs)


def test_bare_matrix_diagnostic_remains_source_free_and_has_no_window_claim():
    stats = api().effective_dimension(np.eye(3))
    assert stats.expected_sessions == ()
    assert stats.row_evidence == ()
    assert stats.universe_evidence is None
