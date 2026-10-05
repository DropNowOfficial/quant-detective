#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/DropNowOfficial/quant-detective.git"
ROOT="/opt/quant-detective"
CURRENT="$ROOT/current"
RELEASES="$ROOT/releases"
LOCK="/run/lock/quant-detective-update.lock"

if [ "$(id -u)" -ne 0 ]; then
  echo "quant-detective updater must run as root" >&2
  exit 2
fi

mkdir -p "$RELEASES"
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another update is already running"
  exit 0
fi

remote_sha="$(git ls-remote "$REPO_URL" refs/heads/main | awk 'NR==1{print $1}')"
if ! [[ "$remote_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "could not resolve main SHA" >&2
  exit 3
fi

current_target="$(readlink -f "$CURRENT" 2>/dev/null || true)"
current_sha=""
if [ -n "$current_target" ] && [ -d "$current_target/.git" ]; then
  current_sha="$(runuser -u quantdetective -- git -C "$current_target" rev-parse HEAD 2>/dev/null || true)"
fi

reconcile_operational_units() {
  local source="$1"
  [ -n "$source" ] && [ -d "$source" ] || return 0

  if [ -f "$source/deploy/systemd/quant-detective-watchdog.service" ] \
    && [ -f "$source/deploy/systemd/quant-detective-watchdog.timer" ]; then
    install -m 0644 "$source/deploy/systemd/quant-detective-watchdog.service" /etc/systemd/system/quant-detective-watchdog.service
    install -m 0644 "$source/deploy/systemd/quant-detective-watchdog.timer" /etc/systemd/system/quant-detective-watchdog.timer
  fi

  if [ -f "$source/deploy/systemd/quant-detective-github-fallback.service" ] \
    && [ -f "$source/deploy/systemd/quant-detective-github-fallback.timer" ]; then
    install -m 0644 "$source/deploy/systemd/quant-detective-github-fallback.service" /etc/systemd/system/quant-detective-github-fallback.service
    install -m 0644 "$source/deploy/systemd/quant-detective-github-fallback.timer" /etc/systemd/system/quant-detective-github-fallback.timer
    install -d -o root -g quantdetective -m 0750 /etc/quant-detective
    if [ ! -f /etc/quant-detective/github-fallback.env ]; then
      install -o root -g quantdetective -m 0640 \
        "$source/deploy/systemd/github-fallback.env.example" \
        /etc/quant-detective/github-fallback.env
    fi
  fi

  systemctl daemon-reload
  systemctl enable --now quant-detective-watchdog.timer >/dev/null 2>&1 || true
  systemctl enable --now quant-detective-github-fallback.timer >/dev/null 2>&1 || true
}

if [ "$current_sha" = "$remote_sha" ]; then
  reconcile_operational_units "$current_target"
  echo "already current: $remote_sha"
  exit 0
fi

release="$RELEASES/$remote_sha"
previous="$current_target"

rollback() {
  echo "deployment failed; rolling back" >&2
  if [ -n "$previous" ] && [ -d "$previous" ]; then
    ln -sfn "$previous" "$CURRENT"
    install -m 0644 "$previous/deploy/systemd/quant-detective-live.service" /etc/systemd/system/quant-detective-live.service
    systemctl daemon-reload
    systemctl restart quant-detective-live.service || true
  fi
  exit 4
}

if [ ! -d "$release/.git" ]; then
  rm -rf "$release"
  git clone --depth 1 --branch main "$REPO_URL" "$release"
  actual="$(git -C "$release" rev-parse HEAD)"
  if [ "$actual" != "$remote_sha" ]; then
    echo "remote moved during clone; retry on next timer" >&2
    rm -rf "$release"
    exit 5
  fi

  if ! python3 -m venv "$release/.venv" \
    || ! "$release/.venv/bin/python" -m pip install --upgrade pip >/dev/null \
    || ! "$release/.venv/bin/pip" install -e "$release" >/dev/null \
    || ! "$release/.venv/bin/python" -m pytest -q \
      "$release/tests/test_ibkr_cpg.py" \
      "$release/tests/test_ibkr_live.py" \
      "$release/tests/test_us_watch.py"; then
    rm -rf "$release"
    exit 6
  fi
  chown -R quantdetective:quantdetective "$release"
fi
old_run_id="$(python3 - <<'PY'
import json
from pathlib import Path
try:
    print(json.loads(Path("/var/lib/quant-detective/state.json").read_text()).get("run_id",""))
except Exception:
    print("")
PY
)"

systemctl stop quant-detective-watchdog.timer >/dev/null 2>&1 || true
ln -sfn "$release" "$CURRENT"
install -m 0644 "$release/deploy/systemd/quant-detective-live.service" /etc/systemd/system/quant-detective-live.service
install -m 0755 "$release/deploy/update-production.sh" /usr/local/sbin/quant-detective-update
install -m 0644 "$release/deploy/systemd/quant-detective-update.service" /etc/systemd/system/quant-detective-update.service
install -m 0644 "$release/deploy/systemd/quant-detective-update.timer" /etc/systemd/system/quant-detective-update.timer
systemctl daemon-reload
systemctl restart quant-detective-live.service

ready=0
for _ in $(seq 1 90); do
  if systemctl is-active --quiet quant-detective-live.service && [ -f /var/lib/quant-detective/state.json ]; then
    read -r run_id mode <<<"$(python3 - <<'PY'
import json
from pathlib import Path
try:
    d=json.loads(Path("/var/lib/quant-detective/state.json").read_text())
    print((d.get("run_id") or ""), (d.get("mode") or ""))
except Exception:
    print("", "")
PY
)"
    if [ -n "$run_id" ] && [ "$run_id" != "$old_run_id" ] && [ -n "$mode" ] && [ "$mode" != "BOOTSTRAPPING" ]; then
      if "$release/.venv/bin/python" "$release/deploy/healthcheck.py" /var/lib/quant-detective/state.json 30 >/dev/null 2>&1; then
        ready=1
        break
      fi
    fi
  fi
  sleep 2
done

if [ "$ready" -ne 1 ]; then
  systemctl start quant-detective-watchdog.timer >/dev/null 2>&1 || true
  rollback
fi

reconcile_operational_units "$release"

systemctl try-restart quant-detective-update.timer || true
echo "deployed $remote_sha successfully"
