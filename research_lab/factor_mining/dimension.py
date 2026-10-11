"""Offline effective-dimension research diagnostics, never trading signals.

The probability weights are covariance/correlation eigenvalues divided by their
sum. Equivalently, an SVD of standardized returns would require *squared*
singular values. These sample diagnostics do not establish predictive value or
remove finite-sample bias, universe-selection bias, or estimation uncertainty.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from numbers import Integral
from typing import Sequence

import numpy as np

from .contracts import Evidence


# An eigenvalue may be clipped only when its negative magnitude is no greater
# than this fraction of the largest absolute eigenvalue. No statistical
# shrinkage, eigenvalue cleaning, or material PSD repair is performed.
PSD_RELATIVE_TOLERANCE = 1e-12
CORRELATION_ABSOLUTE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class DimensionStats:
    """Immutable sample-spectrum statistics and optional window provenance.

    ``eigenvalues`` are descending, after numerical-roundoff clipping. A pure
    correlation calculation has empty window provenance. For a return window,
    ``available_at`` is one timestamp per row and must match typed row evidence
    recording completion and actual first receipt. ``expected_sessions`` is the
    selected slice of an independently supplied completion-time calendar.
    """

    n_assets: int
    entropy_dimension: float
    participation_dimension: float
    normalized_entropy: float
    normalized_participation: float
    top1_share: float
    eigenvalues: tuple[float, ...]
    columns: tuple[str, ...] = ()
    sessions: tuple[datetime, ...] = ()
    available_at: tuple[datetime, ...] = ()
    decision_time: datetime | None = None
    universe_version: str | None = None
    window: int | None = None
    expected_sessions: tuple[datetime, ...] = ()
    row_evidence: tuple[Evidence, ...] = ()
    universe_evidence: Evidence | None = None


def effective_dimension(correlation) -> DimensionStats:
    """Measure entropy and participation dimensions of a correlation matrix.

    Requires a finite, nonempty, square, symmetric, unit-diagonal, positive
    semidefinite matrix. Symmetry and unit-diagonal roundoff use the explicit
    absolute tolerance above. PSD roundoff uses the relative spectral tolerance
    above. Invalid matrices raise ``ValueError`` instead of being repaired.
    """
    if np.ma.isMaskedArray(correlation) and np.ma.getmaskarray(correlation).any():
        raise ValueError("correlation must not contain masked values")
    try:
        matrix = np.asarray(correlation)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("correlation must be a finite numeric square matrix") from exc
    if np.iscomplexobj(matrix):
        raise ValueError("correlation must contain real numbers")
    try:
        matrix = matrix.astype(float, copy=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("correlation must be a finite numeric square matrix") from exc
    if matrix.ndim != 2 or not matrix.shape[0] or matrix.shape[0] != matrix.shape[1]:
        raise ValueError("correlation must be a nonempty square matrix")
    if not np.isfinite(matrix).all():
        raise ValueError("correlation must contain only finite values")
    if not np.allclose(matrix, matrix.T, rtol=0, atol=CORRELATION_ABSOLUTE_TOLERANCE):
        raise ValueError("correlation must be symmetric")
    if not np.allclose(np.diag(matrix), 1, rtol=0, atol=CORRELATION_ABSOLUTE_TOLERANCE):
        raise ValueError("correlation must have unit diagonal")

    # Symmetrization is restricted to already-validated numerical roundoff.
    try:
        eigenvalues = np.linalg.eigvalsh(matrix / 2 + matrix.T / 2)
    except np.linalg.LinAlgError as exc:
        raise ValueError("positive semidefinite correlation spectrum could not be determined") from exc
    if not np.isfinite(eigenvalues).all():
        raise ValueError("positive semidefinite correlation spectrum must be finite")
    tolerance = PSD_RELATIVE_TOLERANCE * float(np.max(np.abs(eigenvalues)))
    if eigenvalues[0] < -tolerance:
        raise ValueError(
            "correlation must be positive semidefinite; negative eigenvalue "
            f"{eigenvalues[0]:.8g} exceeds relative numerical tolerance {tolerance:.8g}"
        )
    eigenvalues = np.maximum(eigenvalues, 0)[::-1]
    probabilities = eigenvalues / eigenvalues.sum()
    positive = probabilities[probabilities > 0]
    entropy = float(np.exp(-np.sum(positive * np.log(positive))))
    participation = float(1 / np.sum(probabilities**2))
    size = len(eigenvalues)
    # Valid probability spectra imply 1 <= dimension <= N exactly. Correct
    # only explicit relative floating-point roundoff at those closed bounds.
    bound_tolerance = PSD_RELATIVE_TOLERANCE * size
    if any(value < 1 - bound_tolerance or value > size + bound_tolerance
           for value in (entropy, participation)):
        raise ValueError("effective dimension exceeds numerical probability bounds")
    entropy = min(max(entropy, 1.0), float(size))
    participation = min(max(participation, 1.0), float(size))
    return DimensionStats(
        n_assets=size,
        entropy_dimension=entropy,
        participation_dimension=participation,
        normalized_entropy=entropy / size,
        normalized_participation=participation / size,
        top1_share=float(probabilities[0]),
        eigenvalues=tuple(float(value) for value in eigenvalues),
    )


def _utc_timestamp(value, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} requires timezone-aware timestamps") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} requires timezone-aware timestamps")
    return value.astimezone(timezone.utc)


def _timestamps(values, field: str, rows: int | None = None) -> tuple[datetime, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{field} must align one-to-one with return rows")
    try:
        result = tuple(_utc_timestamp(value, field) for value in values)
    except TypeError as exc:
        raise ValueError(f"{field} must align one-to-one with return rows") from exc
    if rows is not None and len(result) != rows:
        raise ValueError(f"{field} must align one-to-one with return rows")
    return result


def dimension_from_returns(
    returns,
    *,
    columns: Sequence[str],
    sessions: Sequence[datetime | str],
    expected_sessions: Sequence[datetime | str],
    available_at: Sequence[datetime | str],
    row_evidence: Sequence[Evidence],
    decision_time: datetime | str,
    universe_version: str,
    universe_evidence: Evidence,
    window: int = 126,
) -> DimensionStats:
    """Use the final ``window`` prior UTC sessions of a fixed return universe.

    Inputs are a row-by-asset numeric matrix with exactly aligned row metadata.
    ``sessions`` are completed-session instants, not session-start labels.
    Session timestamps must be aware and strictly increasing by UTC date. The
    t-minus-one convention excludes the entire UTC decision date and later
    dates, even if a same-day timestamp is earlier than the decision time.

    Select the last W prior sessions from the required independent
    ``expected_sessions`` calendar, which callers must not derive by filtering
    observed rows. Observations must exactly cover that calendar slice, with no
    missing, extra, or mismatched completion timestamps. Selected returns must
    be finite and available by the decision instant. A bad row is never dropped,
    filled, or replaced by an older row. Unselected return values do not affect
    the result.

    Each row's typed ``Evidence`` records the completed event, actual first
    receipt, source, vintage, and license for the full fixed-universe row. The
    compatibility ``available_at`` values must exactly match row evidence, and
    availability cannot precede either completion or first receipt. The frozen
    universe's evidence must be available by 00:00 UTC on the first selected
    date, conservatively before the window begins. These checks validate
    supplied provenance; callers remain responsible for its truth and coverage.

    Each selected series is demeaned and standardized using sample deviation
    (ddof=1). W must exceed N because demeaning bounds covariance rank by W-1;
    even W>N does not eliminate finite-sample estimation noise. Constant series
    are rejected without changing membership. Universe labels are provenance,
    not proof that a supplied universe is free of survivorship bias.
    """
    if isinstance(window, bool) or not isinstance(window, Integral) or window <= 0:
        raise ValueError("window must be a positive integer")
    window = int(window)
    if not isinstance(universe_version, str) or not universe_version.strip():
        raise ValueError("universe_version must be a nonempty string")
    if isinstance(columns, (str, bytes)):
        raise ValueError("columns must contain unique nonempty asset names")
    try:
        names = tuple(columns)
    except TypeError as exc:
        raise ValueError("columns must contain unique nonempty asset names") from exc
    if (not names or any(not isinstance(name, str) or not name.strip() for name in names)
            or len(set(names)) != len(names)):
        raise ValueError("columns must contain unique nonempty asset names")

    try:
        data = np.asarray(returns)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("returns must be a rectangular numeric two-dimensional matrix") from exc
    if np.iscomplexobj(data):
        raise ValueError("returns must contain real numbers")
    try:
        data = data.astype(float, copy=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("returns must be a rectangular numeric two-dimensional matrix") from exc
    if data.ndim != 2:
        raise ValueError("returns must be a rectangular numeric two-dimensional matrix")
    rows, size = data.shape
    if size != len(names):
        raise ValueError("columns must align exactly with the fixed return matrix universe")
    if window <= size:
        raise ValueError(f"rank-limited diagnostic: require W > N; got W={window}, N={size}")
    stamps = _timestamps(sessions, "sessions", rows)
    availability = _timestamps(available_at, "available_at", rows)
    decision = _utc_timestamp(decision_time, "decision_time")
    dates = tuple(stamp.date() for stamp in stamps)
    if any(later <= earlier for earlier, later in zip(dates, dates[1:])):
        raise ValueError("strictly increasing unique UTC session dates required")
    expected = _timestamps(expected_sessions, "expected_sessions")
    expected_dates = tuple(stamp.date() for stamp in expected)
    if any(later <= earlier for earlier, later in zip(expected_dates, expected_dates[1:])):
        raise ValueError("strictly increasing unique UTC expected session dates required")
    expected_prior = tuple(stamp for stamp in expected if stamp.date() < decision.date())
    if len(expected_prior) < window:
        raise ValueError(f"insufficient prior expected sessions: require {window}, got {len(expected_prior)}")
    lattice = expected_prior[-window:]
    selected = [index for index, session_date in enumerate(dates)
                if lattice[0].date() <= session_date < decision.date()]
    if tuple(stamps[index] for index in selected) != lattice:
        raise ValueError("observed rows must exactly cover the expected session lattice; no backfilling")

    if not isinstance(universe_evidence, Evidence):
        raise ValueError("universe_evidence must be typed Evidence")
    freeze_deadline = datetime.combine(lattice[0].date(), datetime.min.time(), tzinfo=timezone.utc)
    if universe_evidence.available_at > freeze_deadline:
        raise ValueError("universe must be known before the window starts at 00:00 UTC")
    try:
        evidence = tuple(row_evidence)
    except TypeError as exc:
        raise ValueError("row_evidence must align one-to-one with return rows") from exc
    if len(evidence) != rows:
        raise ValueError("row_evidence must align one-to-one with return rows")
    if any(not isinstance(item, Evidence) for item in evidence):
        raise ValueError("row_evidence must contain typed Evidence")
    for stamp, known, item in zip(stamps, availability, evidence):
        if known < stamp:
            raise ValueError("row availability precedes completion")
        if known != item.available_at:
            raise ValueError("available_at must equal row_evidence.available_at")
        if known < item.first_seen_at:
            raise ValueError("row availability precedes actual first receipt")
        if item.event_time != stamp:
            raise ValueError("row_evidence.event_time must equal the completed session")
    if any(availability[index] > decision for index in selected):
        raise ValueError("selected returns must be available_at or before decision_time")
    if np.ma.isMaskedArray(returns) and np.ma.getmaskarray(returns)[selected].any():
        raise ValueError("selected returns must not contain masked values")
    sample = data[selected]
    if not np.isfinite(sample).all():
        raise ValueError("selected return rows must be complete and finite; no dropping or filling")
    if np.any(np.all(sample == sample[0], axis=0)):
        raise ValueError("zero variance series in fixed universe")
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            centered = sample - sample.mean(axis=0)
            deviations = sample.std(axis=0, ddof=1)
            if np.any(deviations == 0):
                raise ValueError("zero variance series in fixed universe")
            standardized = centered / deviations
            correlation = standardized.T @ standardized / (window - 1)
    except FloatingPointError as exc:
        raise ValueError("return standardization is not numerically finite") from exc
    stats = effective_dimension(correlation)
    return replace(
        stats,
        columns=names,
        sessions=tuple(stamps[index] for index in selected),
        available_at=tuple(availability[index] for index in selected),
        decision_time=decision,
        universe_version=universe_version,
        window=window,
        expected_sessions=lattice,
        row_evidence=tuple(evidence[index] for index in selected),
        universe_evidence=universe_evidence,
    )
