#!/usr/bin/env bash
set -euo pipefail

BASE="https://raw.githubusercontent.com/DropNowOfficial/quant-detective/main"

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 2
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL "$BASE/deploy/update-production.sh" -o "$tmp/update-production.sh"
curl -fsSL "$BASE/deploy/systemd/quant-detective-update.service" -o "$tmp/quant-detective-update.service"
curl -fsSL "$BASE/deploy/systemd/quant-detective-update.timer" -o "$tmp/quant-detective-update.timer"

bash -n "$tmp/update-production.sh"
install -m 0755 "$tmp/update-production.sh" /usr/local/sbin/quant-detective-update
install -m 0644 "$tmp/quant-detective-update.service" /etc/systemd/system/quant-detective-update.service
install -m 0644 "$tmp/quant-detective-update.timer" /etc/systemd/system/quant-detective-update.timer

systemctl daemon-reload
systemctl enable --now quant-detective-update.timer
systemctl start quant-detective-update.service

echo
echo "=== UPDATE TIMER ==="
systemctl --no-pager --full status quant-detective-update.timer || true
echo
echo "=== WATCHER ==="
systemctl --no-pager --full status quant-detective-live.service || true
echo
echo "=== HEALTH ==="
current="$(readlink -f /opt/quant-detective/current)"
"$current/.venv/bin/python" "$current/deploy/healthcheck.py" /var/lib/quant-detective/state.json 30
