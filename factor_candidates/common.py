"""Shared PIT / missing-data helpers. Pure python, deterministic."""
from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Optional


class PITError(ValueError):
    """Input violates AGENTS.md: missing source or known_at."""


def require_provenance(source: object, known_at: object, label: str) -> None:
    if not source or not isinstance(source, str):
        raise PITError(f"{label}: source is required")
    if not isinstance(known_at, date):
        raise PITError(f"{label}: known_at (datetime.date) is required")


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
