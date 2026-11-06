"""Regression: prepublish selftest must load the real DEEP registry path."""
from pathlib import Path

from validation_kernel import vector_prepublish_v0 as pp

ROOT = Path(__file__).resolve().parents[1]


def test_deep_registry_exists_at_contract_path():
    assert (ROOT / "contracts" / "model_registry" / "DEEP_DURATION_FCFF_V1.2.json").is_file()


def test_prepublish_selftest_runs():
    pp.demo_selftest()
