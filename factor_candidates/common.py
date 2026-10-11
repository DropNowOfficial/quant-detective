"""Shared PIT / missing-data / versioning helpers. Pure python, deterministic.

Rules enforced here (AGENTS.md): every fact has source + known_at; missing is
never zero; results never depend on input order.
"""
from __future__ import annotations

import dataclasses
import re
from datetime import date, datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence

PERIOD_TYPES = ("annual", "quarterly", "ttm")
_CCY = re.compile(r"^[A-Z]{3}$")
# Fields that identify *which publication* of a fact a record is, not its content.
_PUBLICATION_FIELDS = ("known_at", "source", "statement_ref")


class PITError(ValueError):
    """Input violates AGENTS.md or the filing-basis contract."""


class AmbiguousVersionError(PITError):
    """Visible records for one fact cannot be ordered unambiguously."""


class TradingCalendarError(PITError):
    """Trading-day calendar contains duplicates or non-date entries."""


def require_aware_datetime(value: object, label: str) -> datetime:
    """Full timezone-aware timestamp. Date-only and naive values are rejected."""
    if not isinstance(value, datetime):
        raise PITError(f"{label}: timezone-aware datetime required, got {type(value).__name__}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise PITError(f"{label}: naive datetime rejected; timezone is required")
    return value


def require_date(value: object, label: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise PITError(f"{label}: plain datetime.date required (not datetime)")
    return value


def require_text(value: object, label: str) -> str:
    if not value or not isinstance(value, str) or not value.strip():
        raise PITError(f"{label} is required (non-empty str)")
    return value


def require_provenance(source: object, known_at: object, label: str) -> None:
    require_text(source, f"{label}: source")
    require_aware_datetime(known_at, f"{label}: known_at")


def require_revision(revision: object, label: str) -> int:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise PITError(f"{label}: revision must be an int >= 0")
    return revision


def validate_filing_basis(f: object) -> None:
    """Validate the common basis fields carried by every fundamental fact."""
    label = f"{getattr(f, 'security', '?')}@{getattr(f, 'period_end', '?')}"
    require_text(f.security, f"{label}: security")
    require_date(f.period_end, f"{label}: period_end")
    require_provenance(f.source, f.known_at, label)
    if f.period_type not in PERIOD_TYPES:
        raise PITError(f"{label}: period_type must be one of {PERIOD_TYPES}")
    if not isinstance(f.currency, str) or not _CCY.match(f.currency):
        raise PITError(f"{label}: currency must be an ISO-4217 style upper-case code")
    s = f.unit_scale
    if isinstance(s, bool) or not isinstance(s, (int, float)) or not (s > 0) or s == float("inf"):
        raise PITError(f"{label}: unit_scale must be a positive finite number (1, 1e3, 1e6 ...)")
    require_text(f.statement_ref, f"{label}: statement_ref (accession/form/source ref)")
    require_revision(f.revision, label)


def as_of_utc(as_of: object, label: str = "as_of") -> datetime:
    return require_aware_datetime(as_of, label)


def finite_or_none(x: Optional[float]) -> Optional[float]:
    """None and NaN both mean MISSING. They are never coerced to 0."""
    if x is None:
        return None
    try:
        if x != x or x in (float("inf"), float("-inf")):
            return None
    except TypeError:
        return None
    return float(x)


def scaled(x: Optional[float], unit_scale: float) -> Optional[float]:
    v = finite_or_none(x)
    return None if v is None else v * float(unit_scale)


def _content(rec: object) -> tuple:
    out = []
    for fld in dataclasses.fields(rec):
        if fld.name in _PUBLICATION_FIELDS:
            continue
        v = getattr(rec, fld.name)
        if isinstance(v, float) and v != v:
            v = "NaN"
        out.append((fld.name, v))
    return tuple(out)


def select_version(records: Sequence[object], label: str) -> object:
    """Pick the governing version among VISIBLE records of ONE fact.

    Rule (explicit, order-independent):
      1. order by (known_at, revision);
      2. revision must not decrease as known_at increases, else AmbiguousVersionError;
      3. records with equal (known_at, revision) must have identical content
         (exact duplicates are collapsed), else AmbiguousVersionError;
      4. records sharing a revision number but published at different times must
         have identical content, else AmbiguousVersionError;
      5. the governing version is the last one in that order.
    """
    if not records:
        raise ValueError("select_version needs at least one record")
    ordered = sorted(records, key=lambda r: (r.known_at, r.revision))
    for a, b in zip(ordered, ordered[1:]):
        if b.revision < a.revision:
            raise AmbiguousVersionError(
                f"{label}: revision {b.revision} published at {b.known_at.isoformat()} "
                f"after revision {a.revision} at {a.known_at.isoformat()}")
        if b.revision == a.revision and _content(a) != _content(b):
            raise AmbiguousVersionError(
                f"{label}: conflicting content for revision {a.revision} "
                f"(known_at {a.known_at.isoformat()} vs {b.known_at.isoformat()})")
    return ordered[-1]


def clean_trading_days(trading_days: Iterable[object]) -> list[date]:
    """Sorted, duplicate-free list of plain dates. Duplicates/non-dates raise."""
    days = list(trading_days)
    for d in days:
        if isinstance(d, datetime) or not isinstance(d, date):
            raise TradingCalendarError(f"trading day must be datetime.date, got {d!r}")
    if len(set(days)) != len(days):
        seen, dups = set(), set()
        for d in days:
            (dups if d in seen else seen).add(d)
        raise TradingCalendarError(
            "duplicate trading days: " + ", ".join(sorted(x.isoformat() for x in dups)))
    return sorted(days)


def cross_sectional_rank(values: Mapping[str, Optional[float]]) -> dict[str, Optional[float]]:
    """Percentile rank in (0,1] over non-missing values; missing stays None.

    Ties get the average rank. Deterministic (sorted by value then key).
    """
    present = sorted(((v, k) for k, v in values.items() if finite_or_none(v) is not None))
    n = len(present)
    out: dict[str, Optional[float]] = {k: None for k in values}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and present[j + 1][0] == present[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for t in range(i, j + 1):
            out[present[t][1]] = avg_rank / n
        i = j + 1
    return out
