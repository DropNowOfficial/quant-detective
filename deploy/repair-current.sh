#!/usr/bin/env bash
set -euo pipefail

CURRENT=/opt/quant-detective/current
STATE=/var/lib/quant-detective/state.json
SERVICE=quant-detective-live.service

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 2
fi

REAL_CURRENT="$(readlink -f "$CURRENT")"
if [ ! -d "$REAL_CURRENT/.git" ]; then
  echo "Current release is not a git checkout: $REAL_CURRENT" >&2
  exit 3
fi

echo "== capture current run identity =="
old_run_id="$(python3 - <<'PY'
import json
from pathlib import Path
p=Path("/var/lib/quant-detective/state.json")
try:
    print(json.loads(p.read_text()).get("run_id",""))
except Exception:
    print("")
PY
)"
printf 'old_run_id=%s\n' "$old_run_id"

echo "== update source as repository owner =="
sudo -u quantdetective git -C "$REAL_CURRENT" fetch --prune origin main
sudo -u quantdetective git -C "$REAL_CURRENT" reset --hard origin/main
"$CURRENT/.venv/bin/pip" install -q -e "$CURRENT"

echo "== restart service =="
systemctl daemon-reload
systemctl restart "$SERVICE"

echo "== wait for readiness =="
ready=0
for _ in $(seq 1 90); do
  if [ -f "$STATE" ]; then
    read -r run_id mode <<<"$(python3 - <<'PY'
import json
from pathlib import Path
p=Path("/var/lib/quant-detective/state.json")
try:
    d=json.loads(p.read_text())
    print((d.get("run_id") or ""), (d.get("mode") or ""))
except Exception:
    print("", "")
PY
)"
    printf 'run_id=%s mode=%s\n' "$run_id" "$mode"
    if [ -n "$run_id" ] && [ "$run_id" != "$old_run_id" ] && [ -n "$mode" ] && [ "$mode" != "BOOTSTRAPPING" ]; then
      ready=1
      break
    fi
  else
    echo "mode=NO_STATE_YET"
  fi
  sleep 2
done

echo
echo "=== SERVICE ==="
systemctl --no-pager --full status "$SERVICE" || true

echo
echo "=== HEALTH ==="
"$CURRENT/.venv/bin/python" "$CURRENT/deploy/healthcheck.py" "$STATE" 30 || true

echo
echo "=== STATE SUMMARY ==="
python3 - <<'PY'
import json
from pathlib import Path
p=Path("/var/lib/quant-detective/state.json")
if not p.exists():
    print("state.json missing")
    raise SystemExit(0)
d=json.loads(p.read_text())
print(json.dumps({
  "generated_at_utc": d.get("generated_at_utc"),
  "started_at_utc": d.get("started_at_utc"),
  "run_id": d.get("run_id"),
  "mode": d.get("mode"),
  "phase": d.get("phase"),
  "ibkr_error": d.get("ibkr_error"),
  "contracts_resolved": d.get("contracts_resolved"),
  "rows": len(d.get("rows", [])),
  "material": [
    {"symbol": r.get("symbol"), "state": r.get("state"),
     "public_change_pct": r.get("public_change_pct"),
     "d5_atr": r.get("d5_atr")}
    for r in d.get("rows", [])
    if r.get("state") in {"ENTRY_CONFIRMED","ENTRY_ARMED","LEADER_HOT_NO_CHASE","LEADER_WATCH"}
  ][:20],
}, ensure_ascii=False, indent=2))
PY

echo
echo "=== LAST LOGS ==="
journalctl -u "$SERVICE" -n 50 --no-pager || true

if [ "$ready" -ne 1 ]; then
  echo
  echo "WARNING: a new daemon run did not become ready within 180 seconds." >&2
  exit 4
fi
