"""Hand-derived spectrum and point-in-time contracts for research diagnostics."""

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import importlib
import importlib.util
import math

import numpy as np
import pytest


def api():
    spec = importlib.util.find_spec("research_lab.factor_mining.dimension")
    assert spec is not None, "effective-dimension research diagnostics missing"
    module = importlib.import_module("research_lab.factor_mining.dimension")
    assert hasattr(module, "DimensionStats")
    assert hasattr(module, "effective_dimension")
    assert hasattr(module, "dimension_from_returns")
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
    assert stats.top1_share == pytest.approx(max(probabilities))


def test_statistics_and_spectrum_are_immutable():
    stats = api().effective_dimension(np.eye(3))
    with pytest.raises(FrozenInstanceError):
        stats.entropy_dimension = 999


@pytest.mark.parametrize("matrix, reason", [
    ([], "square"),
    ([[1, 0, 0], [0, 1, 0]], "square"),
    ([[1, float("nan")], [float("nan"), 1]], "finite"),
    ([[1, 0.2], [0.3, 1]], "symmetric"),
    ([[2, 0], [0, 1]], "unit diagonal"),
    ([[1, -0.75, -0.75], [-0.75, 1, -0.75], [-0.75, -0.75, 1]], "positive semidefinite"),
])
def test_invalid_correlations_fail_closed(matrix, reason):
    with pytest.raises(ValueError, match=reason):
        api().effective_dimension(matrix)


def test_window_matches_demeaned_sample_standardized_covariance():
    inputs = window_inputs()
    data = inputs["returns"]
    standardized = (data - data.mean(axis=0)) / data.std(axis=0, ddof=1)
    correlation = standardized.T @ standardized / (len(data) - 1)
    expected = api().effective_dimension(correlation)
    stats = api().dimension_from_returns(**inputs)
    assert stats.entropy_dimension == pytest.approx(expected.entropy_dimension)
    assert stats.participation_dimension == pytest.approx(expected.participation_dimension)


def test_current_and_future_sessions_cannot_change_prior_session_diagnostic():
    inputs = window_inputs(rows=9)
    inputs["decision_time"] = inputs["sessions"][6] + timedelta(hours=23)
    inputs["window"] = 4
    stats = api().dimension_from_returns(**inputs)
    inputs["returns"][6:] = np.nan
    assert api().dimension_from_returns(**inputs) == stats


def test_late_selected_data_is_rejected():
    inputs = window_inputs(rows=7)
    inputs["window"] = 4
    late = inputs["decision_time"] + timedelta(microseconds=1)
    inputs["available_at"][-1] = late
    inputs["row_evidence"][-1] = replace(inputs["row_evidence"][-1], available_at=late, first_seen_at=late)
    with pytest.raises(ValueError, match="available.*decision"):
        api().dimension_from_returns(**inputs)
