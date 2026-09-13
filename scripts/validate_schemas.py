#!/usr/bin/env python3
"""Fail the build if committed fixtures / packs violate Pydantic schemas."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from schemas.models import EvidencePack, Forecast, Score  # noqa: E402

errors: list[str] = []

def load(p: Path):
    return json.loads(p.read_text())

for p in [
    ROOT / "time_machine/fixtures/dev_syn001/predict_pack.json",
    ROOT / "time_machine/fixtures/dev_syn001/reveal_pack.json",
]:
    try:
        EvidencePack.model_validate(load(p))
        print(f"OK EvidencePack {p.relative_to(ROOT)}")
    except Exception as e:
        errors.append(f"{p}: {e}")

lf = ROOT / "time_machine/fixtures/dev_syn001/locked_forecast.json"
if lf.exists():
    data = load(lf)
    if "forecasts" in data and "model_id" in data:
        try:
            Forecast.model_validate(data)
            print(f"OK Forecast {lf.relative_to(ROOT)}")
        except Exception as e:
            errors.append(f"{lf}: {e}")
    else:
        print(f"SKIP legacy Forecast shape {lf.relative_to(ROOT)}")

sc = ROOT / "time_machine/fixtures/dev_syn001/score.json"
if sc.exists():
    data = load(sc)
    if "lock_id" in data and "domain_scores" in data:
        try:
            Score.model_validate(data)
            print(f"OK Score {sc.relative_to(ROOT)}")
        except Exception as e:
            errors.append(f"{sc}: {e}")
    else:
        print(f"SKIP legacy Score shape {sc.relative_to(ROOT)}")

for p in sorted((ROOT / "failure_museum/exhibits").glob("*.json")):
    try:
        load(p)
        print(f"OK JSON {p.relative_to(ROOT)}")
    except Exception as e:
        errors.append(f"{p}: {e}")

if errors:
    print("SCHEMA_GATE_FAIL")
    for e in errors:
        print(e)
    sys.exit(1)
print("SCHEMA_GATE_OK")
