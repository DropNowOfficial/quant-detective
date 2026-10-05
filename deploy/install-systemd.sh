#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-$(pwd)}"
if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo deploy/install-systemd.sh /opt/quant-detective/current" >&2
  exit 2
fi

if ! id quantdetective >/dev/null 2>&1; then
  useradd --system --home /var/lib/quant-detective --shell /usr/sbin/nologin quantdetective
fi

install -d -o quantdetective -g quantdetective -m 0750 /var/lib/quant-detective
install -d -o root -g quantdetective -m 0750 /etc/quant-detective
install -m 0644 "$REPO_ROOT/deploy/systemd/quant-detective-live.service" /etc/systemd/system/quant-detective-live.service

if [ ! -f /etc/quant-detective/market-watch.env ]; then
  install -o root -g quantdetective -m 0640 "$REPO_ROOT/deploy/systemd/market-watch.env.example" /etc/quant-detective/market-watch.env
  echo "created /etc/quant-detective/market-watch.env"
fi

systemctl daemon-reload
systemctl enable quant-detective-live.service
echo "installed. Authenticate the local IBKR gateway first, then:"
echo "  sudo systemctl restart quant-detective-live"
echo "  sudo systemctl status quant-detective-live --no-pager"
