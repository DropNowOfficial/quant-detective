"""Pure availability decisions, independent of factors and lifecycle governance.

Facts preserve completion at capture. Fill-forward rows never advance a real
watermark. VALID permits confirmation; OBSERVATION_ONLY is fresh evidence with
insufficient confirmation history; UNAVAILABLE is rejected for current use.
Every successful decision has a bounded, exclusive ``valid_until_ms``.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .session_clock import SessionSchedule


@dataclass(frozen=True)
class BarFact:
    open_ms: int
    end_ms: int
    closed_at_capture: bool
    is_fill_forward: bool = False


@dataclass(frozen=True)
class QualityPolicy:
    policy_id: str
    interval_ms: int
    response_max_age_ms: int
    publication_grace_ms: int
    future_clock_tolerance_ms: int
    min_completed_bars: int
    min_rvol_sessions: int
    bar_age_limit_ms: int | None


@dataclass(frozen=True)
class BarQualityInput:
    now_ms: int
    captured_at_ms: int
    bars: tuple[BarFact, ...]
    invalid_rows: int
    complete_rvol_sessions: int
    last_daily_session: str | None


@dataclass(frozen=True)
class QualityResult:
    state: str
    reason_codes: tuple[str, ...]
    observation_ok: bool
    confirmation_ok: bool
    evaluated_at_ms: int
    valid_until_ms: int | None
    expected_bar_end_ms: int | None
    last_bar_end_ms: int | None
    sample_count: int


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _finite_positive(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _policy_valid(policy):
    return (isinstance(policy.policy_id, str) and bool(policy.policy_id)
            and _integer(policy.interval_ms, 1)
            and _integer(policy.response_max_age_ms, 1)
            and _integer(policy.publication_grace_ms)
            and _integer(policy.future_clock_tolerance_ms)
            and _integer(policy.min_completed_bars)
            and _integer(policy.min_rvol_sessions)
            and (policy.bar_age_limit_ms is None or _integer(policy.bar_age_limit_ms, 1)))


def _result(now, reasons, blockers, expiry, expected, last, count):
    observation_ok = not blockers
    confirmation_ok = observation_ok and not reasons
    return QualityResult(
        "VALID" if confirmation_ok else "OBSERVATION_ONLY" if observation_ok else "UNAVAILABLE",
        tuple(dict.fromkeys(reasons)), observation_ok, confirmation_ok, now,
        expiry if observation_ok else None, expected, last, count,
    )


def evaluate_bars(data: BarQualityInput, policy: QualityPolicy,
                  schedule: SessionSchedule) -> QualityResult:
    """Evaluate captured facts using supplied policy, schedule, and clock only.

    Adapter-reported invalid rows are a veto, even if valid rows remain. The
    core defensively rejects invalid facts too. A single fresh US bar can be
    observed, but completed-bar/RVOL minima are separate confirmation gates.
    """
    reasons, blockers = [], []

    def reject(reason):
        reasons.append(reason)
        blockers.append(reason)

    now = data.now_ms if _integer(data.now_ms) else 0
    if (not _integer(data.now_ms) or not _integer(data.captured_at_ms)
            or not _integer(data.invalid_rows) or not _integer(data.complete_rvol_sessions)
            or not isinstance(data.bars, tuple)):
        reject("INVALID_INPUT")
        return _result(now, reasons, blockers, None, None, None, 0)
    if not _policy_valid(policy):
        reject("INVALID_POLICY")
        return _result(now, reasons, blockers, None, None, None, 0)
    if schedule.phase == "CALENDAR_ERROR":
        reject("CALENDAR_UNAVAILABLE")
    elif schedule.phase not in {"REGULAR", "CONTINUOUS"}:
        reject("SESSION_NOT_OPEN")
    if data.invalid_rows:
        reject("INVALID_ROWS")
    if data.captured_at_ms > now + policy.future_clock_tolerance_ms:
        reject("FUTURE_CAPTURE")
    response_expiry = data.captured_at_ms + policy.response_max_age_ms
    if now >= response_expiry:
        reject("STALE_RESPONSE")

    expected_ends = schedule.expected_completed_ends
    if ((schedule.open_ms is not None and not _integer(schedule.open_ms))
            or (schedule.close_ms is not None and not _integer(schedule.close_ms, 1))
            or (schedule.phase == "CONTINUOUS" and (
                schedule.open_ms is not None or schedule.close_ms is not None))
            or not isinstance(expected_ends, tuple)
            or any(not _integer(end, 1) for end in expected_ends)
            or any(a >= b for a, b in zip(expected_ends, expected_ends[1:]))
            or any(end + policy.publication_grace_ms > now for end in expected_ends)
            or (schedule.phase == "REGULAR" and (
                not _integer(schedule.open_ms) or not _integer(schedule.close_ms, 1)
                or not schedule.open_ms <= now < schedule.close_ms))):
        reject("INVALID_SCHEDULE")
        return _result(now, reasons, blockers, None, None, None, 0)
    expected = expected_ends[-1] if expected_ends else None
    selected = {}
    for bar in data.bars:
        if (not isinstance(bar, BarFact) or not _integer(bar.open_ms)
                or not _integer(bar.end_ms, 1) or bar.end_ms <= bar.open_ms
                or type(bar.closed_at_capture) is not bool or type(bar.is_fill_forward) is not bool):
            reject("INVALID_BAR")
            continue
        partial_close = (schedule.close_ms == bar.end_ms
                         and bar.end_ms - bar.open_ms < policy.interval_ms)
        if bar.end_ms - bar.open_ms != policy.interval_ms and not partial_close:
            reject("INVALID_BAR")
            continue
        if bar.open_ms > now + policy.future_clock_tolerance_ms:
            reject("FUTURE_BAR")
        if bar.closed_at_capture and bar.end_ms > now + policy.future_clock_tolerance_ms:
            reject("FUTURE_BAR")
        if bar.closed_at_capture and bar.end_ms > data.captured_at_ms:
            reject("BAR_AFTER_CAPTURE")
        if (not bar.closed_at_capture or bar.is_fill_forward or bar.end_ms > now
                or bar.end_ms > data.captured_at_ms):
            continue
        if schedule.open_ms is not None and bar.open_ms < schedule.open_ms:
            continue
        if schedule.close_ms is not None and bar.end_ms > schedule.close_ms:
            continue
        if bar.end_ms in selected:
            reject("DUPLICATE_BAR")
        selected[bar.end_ms] = bar
    ends = sorted(selected)
    last = ends[-1] if ends else None
    if last is None:
        reject("NO_COMPLETED_BARS")
    elif expected is not None and last < expected:
        reject("STALE_BAR")
    if expected_ends and any(end not in selected for end in expected_ends):
        reject("BAR_GAP")
    # The continuous minute calculator needs only its trailing 61 bars, whereas
    # XNYS cumulative metrics also need every expected bar from the RTH open.
    required = ends[-max(1, policy.min_completed_bars):]
    if any(selected[b].open_ms != a for a, b in zip(required, required[1:])):
        reject("BAR_GAP")
    if len(ends) < policy.min_completed_bars:
        reasons.append("INSUFFICIENT_COMPLETED_BARS")
    if data.complete_rvol_sessions < policy.min_rvol_sessions:
        reasons.append("INSUFFICIENT_RVOL_HISTORY")
    if policy.min_rvol_sessions and (schedule.previous_session is None
                                    or data.last_daily_session != schedule.previous_session):
        reject("STALE_DAILY_SESSION")

    deadlines = [response_expiry]
    if schedule.close_ms is not None and schedule.phase == "REGULAR":
        deadlines.append(schedule.close_ms)
    if last is not None:
        if policy.bar_age_limit_ms is not None:
            deadlines.append(last + policy.bar_age_limit_ms)
            if now >= last + policy.bar_age_limit_ms:
                reject("STALE_BAR")
        # A new source receipt never extends the next source-bar deadline.
        next_end = last + policy.interval_ms
        if schedule.close_ms is not None:
            next_end = min(next_end, schedule.close_ms)
        deadlines.append(next_end + policy.publication_grace_ms)
    expiry = min(deadlines)
    if now >= expiry and not blockers:
        reject("STALE_BAR")
    return _result(now, reasons, blockers, expiry, expected, last, len(ends))


def evaluate_quote(quote: dict, *, now_ms: int, max_age_ms: int,
                   future_clock_tolerance_ms: int = 5000) -> QualityResult:
    """Require an explicit real-time TRADE and a positive source updated_ms.

    Optional provider ``event_ms`` is checked independently when present. The
    current IBKR adapter does not supply it; receipt times are never substitutes.
    """
    reasons = []
    now = now_ms if _integer(now_ms) else 0
    if (not isinstance(quote, dict) or not _integer(now_ms)
            or not _integer(max_age_ms, 1) or not _integer(future_clock_tolerance_ms)):
        return _result(now, ["INVALID_INPUT"], ["INVALID_INPUT"], None, None, None, 0)
    price = quote.get("last")
    if not _finite_positive(price):
        reasons.append("INVALID_QUOTE_PRICE")
    if quote.get("last_status") != "TRADE":
        reasons.append("QUOTE_NOT_TRADE")
    availability = quote.get("market_data_availability")
    if not isinstance(availability, str) or not availability.startswith("R"):
        reasons.append("QUOTE_NOT_REALTIME")
    updated = quote.get("updated_ms")
    deadlines = []
    if not _integer(updated, 1):
        reasons.append("INVALID_QUOTE_TIMESTAMP")
        updated = None
    else:
        deadlines.append(updated + max_age_ms)
        if updated > now + future_clock_tolerance_ms:
            reasons.append("FUTURE_QUOTE")
        if now >= updated + max_age_ms:
            reasons.append("STALE_QUOTE")
    if "event_ms" in quote:
        event = quote["event_ms"]
        if not _integer(event, 1):
            reasons.append("INVALID_QUOTE_EVENT_TIMESTAMP")
        else:
            deadlines.append(event + max_age_ms)
            if event > now + future_clock_tolerance_ms:
                reasons.append("FUTURE_QUOTE_EVENT")
            if now >= event + max_age_ms:
                reasons.append("STALE_QUOTE_EVENT")
    return _result(now, reasons, reasons, min(deadlines) if deadlines else None,
                   None, updated, 1 if updated is not None else 0)
