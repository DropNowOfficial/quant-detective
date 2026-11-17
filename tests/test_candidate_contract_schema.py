"""Structural validation of contracts/factors/candidates_20261011.json.

Mutation tests: delete / mistype each required piece and the validator MUST fail.
"""
import copy
import json
from pathlib import Path

import pytest

from schemas.factor_contracts import (
    CONTRACT_PATH, SCHEMA_PATH, validate_candidate_contract,
)

ROOT = Path(__file__).resolve().parents[1]
DOC = json.loads(CONTRACT_PATH.read_text())
CAND_KEYS = sorted(DOC["candidates"][0].keys())
TOP_KEYS = sorted(DOC.keys())


def fresh():
    return copy.deepcopy(DOC)


def test_committed_contract_is_valid():
    assert SCHEMA_PATH.exists()
    assert validate_candidate_contract(DOC, ROOT) == []


@pytest.mark.parametrize("key", TOP_KEYS)
def test_delete_each_top_level_key_fails(key):
    d = fresh(); d.pop(key)
    assert validate_candidate_contract(d, ROOT)


@pytest.mark.parametrize("idx", [0, 1, 2])
@pytest.mark.parametrize("key", CAND_KEYS)
def test_delete_each_candidate_key_fails(idx, key):
    d = fresh(); d["candidates"][idx].pop(key)
    assert validate_candidate_contract(d, ROOT), f"deleting {key} on candidate {idx} still validates"


@pytest.mark.parametrize("idx", [0, 1, 2])
@pytest.mark.parametrize("key", ["field", "source", "known_at"])
def test_delete_each_input_subkey_fails(idx, key):
    d = fresh(); d["candidates"][idx]["inputs"][0].pop(key)
    assert validate_candidate_contract(d, ROOT)


@pytest.mark.parametrize("idx", [0, 1, 2])
def test_empty_inputs_or_formula_fails(idx):
    d = fresh(); d["candidates"][idx]["inputs"] = []
    assert validate_candidate_contract(d, ROOT)
    d = fresh(); d["candidates"][idx]["formula"] = ""
    assert validate_candidate_contract(d, ROOT)
    d = fresh(); d["candidates"][idx]["formula"] = 5
    assert validate_candidate_contract(d, ROOT)


@pytest.mark.parametrize("idx", [0, 1, 2])
def test_required_record_fields_must_keep_provenance(idx):
    for must in ("security", "known_at", "source", "revision"):
        d = fresh()
        d["candidates"][idx]["required_record_fields"].remove(must)
        assert validate_candidate_contract(d, ROOT), must
    d = fresh(); d["candidates"][idx]["required_record_fields"] = []
    assert validate_candidate_contract(d, ROOT)
    d = fresh(); d["candidates"][idx]["required_record_fields"] = "security"
    assert validate_candidate_contract(d, ROOT)


@pytest.mark.parametrize("field,bad", [
    ("CONTRACT_STATUS", "MAYBE"), ("DATA_STATUS", "GREAT"), ("VALIDATION_STATUS", "VALIDATED"),
    ("VALIDATION_STATUS", None), ("scoring_usable", True), ("scoring_usable", "no"),
    ("rank", "1"), ("rank", 0), ("id", "other_factor"), ("pit_rule", ""),
])
def test_mistyped_or_forbidden_lifecycle_values_fail(field, bad):
    for idx in (0, 1, 2):
        d = fresh(); d["candidates"][idx][field] = bad
        assert validate_candidate_contract(d, ROOT), (field, idx)


def test_unknown_extra_key_rejected():
    d = fresh(); d["candidates"][0]["formulla"] = "typo"
    assert validate_candidate_contract(d, ROOT)


def test_known_at_must_be_declared_timezone_aware():
    for idx in (0, 1, 2):
        d = fresh(); d["candidates"][idx]["provenance"]["known_at_timezone_aware"] = False
        assert validate_candidate_contract(d, ROOT)
        d = fresh(); d["candidates"][idx]["provenance"].pop("source_required")
        assert validate_candidate_contract(d, ROOT)


def test_variant_info_required_and_checked():
    d = fresh(); d["candidates"][2]["variants"] = []
    assert validate_candidate_contract(d, ROOT)
    d = fresh(); d["candidates"][2]["variants"] = [v for v in d["candidates"][2]["variants"]
                                                    if v["name"] != "prior_calendar_month"]
    assert validate_candidate_contract(d, ROOT)
    d = fresh(); d["candidates"][2]["variants"][0].pop("role")
    assert validate_candidate_contract(d, ROOT)
    d = fresh(); d["candidates"][2]["calendar_timezone"] = "Not/AZone_fmt!"
    assert validate_candidate_contract(d, ROOT)
    d = fresh(); d["candidates"][2].pop("calendar_timezone")
    assert validate_candidate_contract(d, ROOT)


def test_sloan_contract_requires_short_term_investment_inputs_and_blocker():
    d = fresh(); s = d["candidates"][1]
    s["required_record_fields"] = [f for f in s["required_record_fields"]]
    assert "short_term_investments" in json.dumps(s["inputs"])
    d["candidates"][1]["scoring_blocker"] = ""
    assert validate_candidate_contract(d, ROOT)


def test_implementation_file_must_exist():
    d = fresh(); d["candidates"][0]["implementation"] = "factor_candidates/does_not_exist.py"
    assert validate_candidate_contract(d, ROOT)
