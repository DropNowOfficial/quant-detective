"""Structural validation of candidate factor contracts (JSON Schema draft 2020-12).

validate_candidate_contract(doc, root) -> list[str] of error messages (empty = valid).
Used by scripts/validate_schemas.py (verify recipe schema gate) and tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "factor_candidate_contract.schema.json"
CONTRACT_PATH = ROOT / "contracts" / "factors" / "candidates_20261011.json"


def validate_candidate_contract(doc: Any, root: Path = ROOT) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    errors = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:200]}"
              for e in sorted(v.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))]
    if isinstance(doc, dict) and isinstance(doc.get("candidates"), list):
        for i, c in enumerate(doc["candidates"]):
            impl = c.get("implementation") if isinstance(c, dict) else None
            if isinstance(impl, str) and not (root / impl).is_file():
                errors.append(f"candidates/{i}/implementation: file not found: {impl}")
            t = c.get("tests") if isinstance(c, dict) else None
            if isinstance(t, str) and not (root / t).is_file():
                errors.append(f"candidates/{i}/tests: file not found: {t}")
    return errors
