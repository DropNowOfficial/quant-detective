from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

STATE=Path("/var/lib/quant-detective/state.json")
STATUS=Path("/var/lib/quant-detective/watchdog.json")
SERVICE="quant-detective-live.service"
MAX_AGE=float(sys.argv[1] if len(sys.argv)>1 else "180")
RESTART_GRACE=float(sys.argv[2] if len(sys.argv)>2 else "45")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_state():
    if not STATE.exists():
        return None, None
    try:
        data=json.loads(STATE.read_text(encoding="utf-8"))
        stamp=datetime.fromisoformat(data["generated_at_utc"]).astimezone(timezone.utc)
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        return data, age
    except Exception:
        return None, None


def write_status(payload):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    payload={"checked_at_utc":now_iso(), **payload}
    STATUS.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


data,age=read_state()
active=subprocess.run(["systemctl","is-active","--quiet",SERVICE]).returncode==0
healthy=bool(active and data and age is not None and age<=MAX_AGE)

if healthy:
    write_status({
        "ok":True,
        "action":"none",
        "service_active":True,
        "state_age_seconds":round(age,2),
        "run_id":data.get("run_id"),
        "mode":data.get("mode"),
    })
    raise SystemExit(0)

before_run=(data or {}).get("run_id")
reason=[]
if not active:
    reason.append("service_inactive")
if data is None:
    reason.append("state_missing_or_invalid")
elif age is None or age>MAX_AGE:
    reason.append("state_stale")

subprocess.run(["systemctl","restart",SERVICE],check=False)
deadline=time.monotonic()+RESTART_GRACE
recovered=None
while time.monotonic()<deadline:
    time.sleep(2)
    fresh,fresh_age=read_state()
    is_active=subprocess.run(["systemctl","is-active","--quiet",SERVICE]).returncode==0
    if is_active and fresh and fresh_age is not None and fresh_age<=MAX_AGE and fresh.get("run_id")!=before_run:
        recovered=(fresh,fresh_age)
        break

if recovered:
    fresh,fresh_age=recovered
    write_status({
        "ok":True,
        "action":"restart_recovered",
        "reason":reason,
        "service_active":True,
        "state_age_seconds":round(fresh_age,2),
        "previous_run_id":before_run,
        "run_id":fresh.get("run_id"),
        "mode":fresh.get("mode"),
    })
    raise SystemExit(0)

fresh,fresh_age=read_state()
write_status({
    "ok":False,
    "action":"restart_failed",
    "reason":reason,
    "service_active":subprocess.run(["systemctl","is-active","--quiet",SERVICE]).returncode==0,
    "state_age_seconds":round(fresh_age,2) if fresh_age is not None else None,
    "previous_run_id":before_run,
    "run_id":(fresh or {}).get("run_id"),
    "mode":(fresh or {}).get("mode"),
})
raise SystemExit(2)
