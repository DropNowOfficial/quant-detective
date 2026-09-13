
"""Filesystem truth isolation: sealed dir/files are mode 000 until reveal.

Predictor must not open sealed files. Isolation is by filesystem permissions,
not an if-phase guard in the predictor.
"""
from __future__ import annotations
import os
from pathlib import Path

SEALED_MODE = 0o000
OPEN_MODE = 0o644
DIR_OPEN = 0o755


def seal_path(path: Path) -> None:
    path = Path(path)
    if path.is_dir():
        for p in sorted(path.rglob("*"), reverse=True):
            if p.is_file():
                os.chmod(p, SEALED_MODE)
            elif p.is_dir():
                os.chmod(p, SEALED_MODE)
        os.chmod(path, SEALED_MODE)
    else:
        os.chmod(path, SEALED_MODE)


def unseal_path(path: Path) -> None:
    path = Path(path)
    if path.is_dir():
        os.chmod(path, DIR_OPEN)
        for p in path.rglob("*"):
            if p.is_dir():
                os.chmod(p, DIR_OPEN)
            else:
                os.chmod(p, OPEN_MODE)
    else:
        os.chmod(path, OPEN_MODE)


def assert_sealed_unreadable(path: Path) -> None:
    path = Path(path)
    try:
        if path.is_dir():
            # listing or reading children must fail
            list(path.iterdir())
            for child in path.iterdir():
                if child.is_file():
                    child.read_bytes()
        else:
            path.read_bytes()
    except (PermissionError, OSError):
        return
    raise AssertionError(f"sealed path still readable: {path}")
