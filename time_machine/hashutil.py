
import hashlib, json
from pathlib import Path
from typing import Any

def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def lock_hash(*, evidence: dict, model: dict, forecast: dict, code_commit: str, manifest: dict) -> str:
    blob = {
        "evidence_pack": evidence,
        "model_contract": model,
        "forecast": {k: v for k, v in forecast.items() if k != "lock_id"},
        "code_commit": code_commit,
        "experiment_manifest": {k: v for k, v in manifest.items() if k != "lock_id"},
    }
    return sha256_text(canonical_json(blob))
