from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

path=Path(sys.argv[1] if len(sys.argv)>1 else "/var/lib/quant-detective/state.json")
max_age=float(sys.argv[2] if len(sys.argv)>2 else "15")
if not path.exists():
    raise SystemExit("state file missing")
data=json.loads(path.read_text(encoding="utf-8"))
stamp=datetime.fromisoformat(data["generated_at_utc"])
age=(datetime.now(timezone.utc)-stamp.astimezone(timezone.utc)).total_seconds()
if age>max_age:
    raise SystemExit(f"state stale: {age:.1f}s > {max_age:.1f}s")
mode=data.get("mode")
if mode not in {"IBKR_LIVE_PLUS_PUBLIC_STRUCTURE","DEGRADED_PUBLIC_ONLY"}:
    raise SystemExit(f"unknown mode: {mode}")
print(json.dumps({"ok":True,"age_seconds":round(age,2),"mode":mode,
                  "run_id":data.get("run_id"),
                  "started_at_utc":data.get("started_at_utc"),
                  "contracts_resolved":data.get("contracts_resolved"),
                  "ibkr_authenticated":(data.get("ibkr_auth") or {}).get("authenticated")},ensure_ascii=False))
