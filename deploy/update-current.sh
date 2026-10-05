#!/usr/bin/env bash
set -euo pipefail

CURRENT=/opt/quant-detective/current
STATE_DIR=/var/lib/quant-detective
STATUS="$STATE_DIR/deploy-status.json"
SERVICE=quant-detective-live.service

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root" >&2
  exit 2
fi

REAL_CURRENT="$(readlink -f "$CURRENT")"
if [ ! -d "$REAL_CURRENT/.git" ]; then
  echo "current release is not a git checkout: $REAL_CURRENT" >&2
  exit 3
fi

install -d -o quantdetective -g quantdetective -m 0750 "$STATE_DIR"

old_sha="$(sudo -u quantdetective git -C "$REAL_CURRENT" rev-parse HEAD)"
sudo -u quantdetective git -C "$REAL_CURRENT" fetch --quiet --prune origin main
new_sha="$(sudo -u quantdetective git -C "$REAL_CURRENT" rev-parse origin/main)"

if [ "$old_sha" = "$new_sha" ]; then
  python3 - "$STATUS" "$old_sha" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
path=Path(sys.argv[1]); sha=sys.argv[2]
path.write_text(json.dumps({
  "checked_at_utc":datetime.now(timezone.utc).isoformat(),
  "updated":False,
  "sha":sha,
},indent=2)+"\n")
PY
  chown quantdetective:quantdetective "$STATUS"
  exit 0
fi

echo "Updating $old_sha -> $new_sha"
sudo -u quantdetective git -C "$REAL_CURRENT" reset --hard origin/main
"$CURRENT/.venv/bin/pip" install -q -e "$CURRENT"
install -m 0644 "$CURRENT/deploy/systemd/quant-detective-live.service" /etc/systemd/system/quant-detective-live.service
install -m 0644 "$CURRENT/deploy/systemd/quant-detective-update.service" /etc/systemd/system/quant-detective-update.service
install -m 0644 "$CURRENT/deploy/systemd/quant-detective-update.timer" /etc/systemd/system/quant-detective-update.timer
systemctl daemon-reload
systemctl restart "$SERVICE"

python3 - "$STATUS" "$old_sha" "$new_sha" <<'PY'
import json,sys
from datetime import datetime,timezone
from pathlib import Path
path=Path(sys.argv[1]); old=sys.argv[2]; new=sys.argv[3]
path.write_text(json.dumps({
  "checked_at_utc":datetime.now(timezone.utc).isoformat(),
  "updated":True,
  "from_sha":old,
  "sha":new,
},indent=2)+"\n")
PY
chown quantdetective:quantdetective "$STATUS"
