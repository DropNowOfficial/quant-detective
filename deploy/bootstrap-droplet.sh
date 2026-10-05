#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/DropNowOfficial/quant-detective.git"
ROOT="/opt/quant-detective"
RELEASE="$ROOT/releases/bootstrap-$(date -u +%Y%m%dT%H%M%SZ)"
CURRENT="$ROOT/current"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this bootstrap as root." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends   ca-certificates curl git python3 python3-venv python3-pip rsync openssh-server

systemctl enable --now ssh

# This droplet has 1 GiB RAM. Add swap once to reduce OOM risk during Python
# dependency installs and future gateway/runtime work.
if ! swapon --show=NAME --noheadings | grep -q .; then
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! id quantdetective >/dev/null 2>&1; then
  useradd --system --home /var/lib/quant-detective --shell /usr/sbin/nologin quantdetective
fi

install -d -o root -g root -m 0755 "$ROOT/releases"
install -d -o quantdetective -g quantdetective -m 0750 /var/lib/quant-detective
install -d -o root -g quantdetective -m 0750 /etc/quant-detective

git clone --depth 1 --branch main "$REPO_URL" "$RELEASE"
python3 -m venv "$RELEASE/.venv"
"$RELEASE/.venv/bin/python" -m pip install --upgrade pip
"$RELEASE/.venv/bin/pip" install -e "$RELEASE"
chown -R quantdetective:quantdetective "$RELEASE"

ln -sfn "$RELEASE" "$CURRENT"
install -m 0644 "$CURRENT/deploy/systemd/quant-detective-live.service"   /etc/systemd/system/quant-detective-live.service
if [ ! -f /etc/quant-detective/market-watch.env ]; then
  install -o root -g quantdetective -m 0640     "$CURRENT/deploy/systemd/market-watch.env.example"     /etc/quant-detective/market-watch.env
fi

systemctl daemon-reload
systemctl enable --now quant-detective-live.service

sleep 8
echo
echo "=== service ==="
systemctl --no-pager --full status quant-detective-live.service || true
echo
echo "=== health ==="
"$CURRENT/.venv/bin/python" "$CURRENT/deploy/healthcheck.py"   /var/lib/quant-detective/state.json 30 || true
echo
echo "=== latest state ==="
python3 - <<'PY'
import json
from pathlib import Path
p=Path("/var/lib/quant-detective/state.json")
if p.exists():
    d=json.loads(p.read_text())
    print(json.dumps({
        "generated_at_utc":d.get("generated_at_utc"),
        "mode":d.get("mode"),
        "ibkr_error":d.get("ibkr_error"),
        "contracts_resolved":d.get("contracts_resolved"),
        "rows":len(d.get("rows",[])),
    }, indent=2))
else:
    print("state.json not created yet")
PY
