#!/usr/bin/env bash
set -euo pipefail

CURRENT=/opt/quant-detective/current
ENV_DIR=/etc/quant-detective
ENV_FILE="$ENV_DIR/github-fallback.env"
SERVICE=quant-detective-github-fallback.service
TIMER=quant-detective-github-fallback.timer
STATUS=/var/lib/quant-detective/github-fallback-dispatch.json

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 2
fi

if [ ! -d "$CURRENT" ]; then
  echo "Quant Detective current release not found." >&2
  exit 3
fi

printf 'Paste the fine-grained GitHub token (input hidden): ' >/dev/tty
IFS= read -r -s TOKEN </dev/tty
printf '\n' >/dev/tty

if [ -z "$TOKEN" ] || [[ "$TOKEN" =~ [[:space:]] ]]; then
  echo "Token is empty or contains whitespace." >&2
  unset TOKEN
  exit 4
fi

install -d -o root -g quantdetective -m 0750 "$ENV_DIR"
tmp="$(mktemp)"
trap 'rm -f "$tmp"; unset TOKEN' EXIT
printf 'QD_GITHUB_FALLBACK_TOKEN=%s\n' "$TOKEN" > "$tmp"
install -o root -g quantdetective -m 0640 "$tmp" "$ENV_FILE"
unset TOKEN

install -m 0644 "$CURRENT/deploy/systemd/quant-detective-github-fallback.service"   /etc/systemd/system/quant-detective-github-fallback.service
install -m 0644 "$CURRENT/deploy/systemd/quant-detective-github-fallback.timer"   /etc/systemd/system/quant-detective-github-fallback.timer

systemctl daemon-reload
systemctl enable --now "$TIMER"
systemctl start "$SERVICE"

echo
echo "=== external GitHub fallback status ==="
if [ -f "$STATUS" ]; then
  python3 -m json.tool "$STATUS"
else
  echo "status file not created; inspect:"
  echo "  journalctl -u $SERVICE -n 50 --no-pager"
  exit 5
fi
