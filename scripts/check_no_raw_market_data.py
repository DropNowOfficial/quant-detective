#!/usr/bin/env python3
"""AGENTS.md rule 6 gate: fail if raw market datasets are tracked in Git (manifests/hashes only).
Flags tracked paths: daily_*.csv price files, anything under raw/, _raw/, _out/, market_cache/ ,
and any tracked .csv whose header looks like a price series (date + close column).
Run via scripts/validate_schemas.py (the schema gate) and tests."""
from __future__ import annotations
import fnmatch, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR_PARTS = {"raw", "_raw", "_out", "market_cache"}
NAME_PATTERNS = ["daily_*.csv", "yahoo_*.json", "nasdaq_*.json", "stooq_*.csv"]
# self-documenting evidence snapshots may hold hashes only; they are scanned like everything else.


def looks_like_price_csv(path) -> bool:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.readline().strip().lower()
    except OSError:
        return False
    cols = [c.strip() for c in head.split(",")]
    return bool(cols) and cols[0] in ("date", "timestamp", "day") and any(re.fullmatch(r"(raw_|adj_)?close|adj close", c) for c in cols)


def violations(paths, root: Path | None = None) -> list[str]:
    out = []
    for p in paths:
        parts = p.split("/")
        name = parts[-1]
        if DIR_PARTS & set(parts[:-1]) or any(fnmatch.fnmatch(name, pat) for pat in NAME_PATTERNS):
            out.append(p)
        elif root is not None and name.endswith(".csv") and looks_like_price_csv(root / p):
            out.append(p)
    return out


def tracked(root: Path = ROOT) -> list[str]:
    return subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()


def main() -> int:
    bad = violations(tracked(), ROOT)
    for p in bad:
        print(f"RAW_MARKET_DATA_TRACKED {p}")
    print("NO_RAW_MARKET_DATA_OK" if not bad else "NO_RAW_MARKET_DATA_FAIL")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
