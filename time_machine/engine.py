
"""PREDICT → LOCK → REVEAL → SCORE state machine."""
from __future__ import annotations
import json
import shutil
from pathlib import Path

from schemas.models import EvidencePack, Forecast, ExperimentManifest, Score, ModelContract
from time_machine.hashutil import lock_hash
from time_machine.seal import seal_path, unseal_path, assert_sealed_unreadable

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_SRC = ROOT / "time_machine" / "fixtures" / "dev_syn001"

DEFAULT_MODEL = {
    "model_id": "TM_FIXTURE_POINT_v0",
    "version": "0.1.0",
    "inputs": ["predict_pack"],
    "units": {"currency": "USD", "scale": "raw"},
    "as_of": "2024-06-28",
    "equations": ["point_next_eps = 1.04 * ttm_eps"],
    "invariants": ["cite pack_object_ids", "no reveal access during predict"],
    "allowed_domains": ["fundamental"],
    "missing_data_policy": "UNAVAILABLE",
    "failure_conditions": ["post-lock mutation", "truth leak"],
    "validation_tests": ["hash_stable", "sealed_truth"],
    "output_interpretation": "synthetic fixture score only",
}


class TimeMachineRun:
    def __init__(self, workdir: Path, code_commit: str = "UNKNOWN"):
        self.workdir = Path(workdir)
        self.code_commit = code_commit
        self.run_dir = self.workdir / "run"
        self.predict_dir = self.run_dir / "predict"
        self.sealed_dir = self.run_dir / "sealed_truth"
        self.lock_dir = self.run_dir / "lock"
        self.reveal_dir = self.run_dir / "reveal"
        self.score_path = self.run_dir / "score.json"
        self.state_path = self.run_dir / "state.json"

    def _set_state(self, phase: str, **extra):
        self.state_path.write_text(json.dumps({"phase": phase, **extra}, indent=2) + "\n")

    def init_from_fixture(self) -> None:
        if self.run_dir.exists():
            # unseal before delete (mode 000 blocks rmtree)
            sealed = self.run_dir / "sealed_truth"
            if sealed.exists():
                try:
                    from time_machine.seal import unseal_path
                    unseal_path(sealed)
                except Exception:
                    pass
            shutil.rmtree(self.run_dir)
        self.predict_dir.mkdir(parents=True)
        self.sealed_dir.mkdir(parents=True)
        self.lock_dir.mkdir(parents=True)
        self.reveal_dir.mkdir(parents=True)
        shutil.copy(FIXTURE_SRC / "predict_pack.json", self.predict_dir / "evidence_pack.json")
        shutil.copy(FIXTURE_SRC / "reveal_pack.json", self.sealed_dir / "truth_pack.json")
        seal_path(self.sealed_dir)
        assert_sealed_unreadable(self.sealed_dir)
        self._set_state("initialized", sealed=str(self.sealed_dir))

    def predict(self) -> dict:
        assert_sealed_unreadable(self.sealed_dir)
        pack = json.loads((self.predict_dir / "evidence_pack.json").read_text())
        EvidencePack.model_validate(pack)
        eps_obj = next(o for o in pack["objects"] if o["metric"] == "ttm_eps")
        point = float(eps_obj["value"]) * 1.04
        forecast = {
            "model_id": DEFAULT_MODEL["model_id"],
            "version": DEFAULT_MODEL["version"],
            "as_of": pack["simulation_date"],
            "pack_id": pack["pack_id"],
            "forecasts": [{
                "quantity": "next_period_eps",
                "domain": "fundamental",
                "point": point,
                "units": {"currency": "USD", "scale": "raw"},
                "as_of": pack["simulation_date"],
                "pack_object_ids": [eps_obj["object_id"]],
                "falsifier_ids": ["fal_eps_below_5"],
                "kernel_prepublish_ok": True,
            }],
            "assumptions": ["point = 1.04 * ttm_eps"],
            "falsifiers": [{"id": "fal_eps_below_5", "test": "realized_eps < 5.0"}],
            "known_at_rule": pack["known_at_rule"],
        }
        Forecast.model_validate(forecast)
        (self.predict_dir / "forecast.json").write_text(json.dumps(forecast, indent=2) + "\n")
        (self.predict_dir / "model_contract.json").write_text(json.dumps(DEFAULT_MODEL, indent=2) + "\n")
        self._set_state("predicted")
        return forecast

    def lock(self) -> str:
        pack = json.loads((self.predict_dir / "evidence_pack.json").read_text())
        forecast = json.loads((self.predict_dir / "forecast.json").read_text())
        model = json.loads((self.predict_dir / "model_contract.json").read_text())
        manifest = {
            "experiment_id": "dev_syn001",
            "simulation_date": pack["simulation_date"],
            "pack_id": pack["pack_id"],
            "model_id": model["model_id"],
            "model_version": model["version"],
            "code_commit": self.code_commit,
            "phase": "locked",
            # paths are run-local only — omitted from lock hash payload via relative labels
            "truth_seal_label": "sealed_truth",
            "predict_pack_label": "predict/evidence_pack.json",
        }
        ExperimentManifest.model_validate(manifest)
        ModelContract.model_validate(model)
        h = lock_hash(
            evidence=pack, model=model, forecast=forecast,
            code_commit=self.code_commit, manifest=manifest,
        )
        forecast["lock_id"] = h
        (self.lock_dir / "evidence_pack.json").write_text(json.dumps(pack, indent=2) + "\n")
        (self.lock_dir / "model_contract.json").write_text(json.dumps(model, indent=2) + "\n")
        (self.lock_dir / "forecast.json").write_text(json.dumps(forecast, indent=2) + "\n")
        (self.lock_dir / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (self.lock_dir / "LOCK_HASH.txt").write_text(h + "\n")
        self._set_state("locked", lock_id=h)
        return h

    def reveal(self) -> dict:
        unseal_path(self.sealed_dir)
        truth = json.loads((self.sealed_dir / "truth_pack.json").read_text())
        (self.reveal_dir / "truth_pack.json").write_text(json.dumps(truth, indent=2) + "\n")
        self._set_state("revealed")
        return truth

    def _verify_lock_integrity(self) -> None:
        pack = json.loads((self.lock_dir / "evidence_pack.json").read_text())
        forecast = json.loads((self.lock_dir / "forecast.json").read_text())
        model = json.loads((self.lock_dir / "model_contract.json").read_text())
        manifest = json.loads((self.lock_dir / "experiment_manifest.json").read_text())
        expected = (self.lock_dir / "LOCK_HASH.txt").read_text().strip()
        h = lock_hash(
            evidence=pack, model=model, forecast=forecast,
            code_commit=manifest["code_commit"], manifest=manifest,
        )
        if h != expected:
            raise ValueError(f"LOCK_INVALIDATED: recomputed {h} != {expected}")

    def score(self) -> dict:
        self._verify_lock_integrity()
        forecast = json.loads((self.lock_dir / "forecast.json").read_text())
        truth = json.loads((self.reveal_dir / "truth_pack.json").read_text())
        realized = next(o["value"] for o in truth["objects"] if o["metric"] == "realized_eps_next")
        point = forecast["forecasts"][0]["point"]
        err = abs(point - realized)
        score = {
            "lock_id": forecast["lock_id"],
            "domain_scores": {
                "fundamental": {"abs_error": err, "point": point, "realized": realized},
                "valuation_expectation": None,
                "event_assimilation": None,
                "risk": None,
                "data_model_invariant": "PASS",
            },
            "error_taxonomy": [] if err < 1.0 else ["MODEL_MISS"],
            "note": "synthetic fixture — not stock analysis",
        }
        Score.model_validate(score)
        self.score_path.write_text(json.dumps(score, indent=2) + "\n")
        self._set_state("scored", lock_id=forecast["lock_id"])
        return score
