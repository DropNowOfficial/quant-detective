#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/DropNowOfficial/quant-detective.git"
ROOT="/opt/quant-detective"
CURRENT="$ROOT/current"
RELEASES="$ROOT/releases"
LOCK="/run/lock/quant-detective-update.lock"
# Provision this reviewed tool separately; do not download an installer as root.
UV_BIN="/usr/local/bin/uv"
EXPECTED_UID=0
repair=0
if [ "${1:-}" = "--repair" ]; then repair=1; shift; fi
umask 022

if [ "$(id -u)" -ne 0 ]; then
  echo "quant-detective updater must run as root" >&2
  exit 2
fi

# These root-owned parents are the trust boundary, not an existing checkout.
for directory in "$ROOT" "$RELEASES"; do
  if [ -L "$directory" ] || [ ! -d "$directory" ] \
    || [ "$(stat -c %u "$directory")" != "$EXPECTED_UID" ] \
    || [ "$(( 8#$(stat -c %a "$directory") & 8#022 ))" -ne 0 ]; then
    echo "root-owned, non-writable deployment directories are required" >&2
    exit 2
  fi
done
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another update is already running"
  exit 0
fi

clean_git() (
  # Do not discover a repository in the invoking working directory or inherit
  # config, hooks, replacement objects, credentials/helpers from that checkout.
  cd /
  env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null GIT_ATTR_NOSYSTEM=1 GIT_NO_REPLACE_OBJECTS=1 \
    git --no-replace-objects -c core.hooksPath=/dev/null -c core.fsmonitor=false "$@"
)
remote_sha="$(clean_git ls-remote "$REPO_URL" refs/heads/main | awk 'NR==1{print $1}')"
if ! [[ "$remote_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "could not resolve main SHA" >&2
  exit 3
fi
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && { ! [[ "$1" =~ ^[0-9a-f]{40}$ ]] || [ "$1" != "$remote_sha" ]; }; }; then
  echo "requested deployment commit does not match current main" >&2
  exit 3
fi

current_target="$(readlink -f "$CURRENT" 2>/dev/null || true)"
if [ -n "$current_target" ] && { [[ "$current_target" != "$RELEASES/"* ]] \
  || [[ "${current_target#"$RELEASES/"}" = */* ]]; }; then
  echo "current release escapes the release directory" >&2
  exit 3
fi
# The path is only a hint for the no-op check, never identity evidence.
current_name="${current_target##*/}"
current_sha="${current_name%%.*}"

# A NEW protected object store is the sole source of commit identity/content.
# Existing service-owned Git config, index flags, alternates and replace refs
# are never read, even if a previous updater later chowned them to root.
trusted="$(mktemp -d "$ROOT/.source.XXXXXX")"
trap 'rm -rf -- "$trusted"' EXIT
clean_git init --bare --template= "$trusted/objects.git" >/dev/null
clean_git --git-dir "$trusted/objects.git" fetch --no-tags --depth 1 "$REPO_URL" refs/heads/main
actual="$(clean_git --git-dir "$trusted/objects.git" rev-parse FETCH_HEAD)"
if [ "$actual" != "$remote_sha" ]; then
  echo "main changed while fetching; rerun with the current reviewed commit" >&2
  exit 5
fi
clean_git --git-dir "$trusted/objects.git" show "$remote_sha:deploy/validate_release.py" > "$trusted/validate_release.py"
# Materialize raw blobs: archive/export-ignore/export-subst and checkout filters
# must not silently change the requested commit tree.
python3 -I "$trusted/validate_release.py" materialize "$trusted/objects.git" "$remote_sha" "$trusted/source"
validate_release() {
  python3 -I "$trusted/validate_release.py" verify "$trusted/source" "$1" --owner "$EXPECTED_UID"
}

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
  systemctl enable --now quant-detective-watchdog.timer
  systemctl enable --now quant-detective-github-fallback.timer
}

if [ "$repair" -eq 0 ] && [ "$current_sha" = "$remote_sha" ]; then
  if ! validate_release "$current_target"; then
    echo "current release is not an immutable exact tree; run the reviewed repair-current.sh to rebuild" >&2
    exit 5
  fi
  systemctl is-active --quiet quant-detective-live.service
  runuser -u quantdetective -- "$current_target/.venv/bin/python" "$current_target/deploy/healthcheck.py" /var/lib/quant-detective/state.json 30
  reconcile_operational_units "$trusted/source"
  echo "already current: $remote_sha"
  exit 0
fi

# Never reuse an abandoned candidate or its ignored/untracked runtime files.
release="$(mktemp -d "$RELEASES/$remote_sha.XXXXXX")"
chmod 0755 "$release"
cp -a "$trusted/source/." "$release/"
previous="$current_target"
switched=0
backup=""
managed_files=(
  /usr/local/sbin/quant-detective-update
  /etc/systemd/system/quant-detective-live.service
  /etc/systemd/system/quant-detective-update.service
  /etc/systemd/system/quant-detective-update.timer
  /etc/systemd/system/quant-detective-watchdog.service
  /etc/systemd/system/quant-detective-watchdog.timer
  /etc/systemd/system/quant-detective-github-fallback.service
  /etc/systemd/system/quant-detective-github-fallback.timer
)

restore_operational_files() {
  local target
  for target in "${managed_files[@]}"; do
    if [ -f "$backup/$(basename "$target")" ]; then
      cp -p "$backup/$(basename "$target")" "$target" || return 1
    else
      rm -f -- "$target" || return 1
    fi
  done
}

remove_failed_release() {
  # Never delete current/previous, an arbitrary path, or a symlink target.
  [[ "$release" = "$RELEASES/$remote_sha."* && "$remote_sha" =~ ^[0-9a-f]{40}$ ]] || return 1
  [ "$release" != "$previous" ] && [ ! -L "$release" ] || return 1
  rm -rf -- "$release"
}

switch_current() {
  # Rename a same-filesystem symlink into place: readers see old OR new.
  ln -sfn "$1" "$trusted/current-link" && mv -Tf "$trusted/current-link" "$CURRENT"
}

read_run_id() {
  python3 -I - <<'PYTHON'
import json
import os
import stat
try:
    fd = os.open("/var/lib/quant-detective/state.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
            raise ValueError
        raw = source.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError
    value = json.loads(raw).get("run_id", "")
    valid = isinstance(value, str) and 1 <= len(value) <= 128 and value.isascii()
    print(value if valid and all(c.isprintable() and not c.isspace() for c in value) else "")
except Exception:
    print("")
PYTHON
}

wait_for_ready() {
  local source="$1" excluded="$2" other_excluded="${3:-}" run_id
  for _ in $(seq 1 90); do
    if systemctl is-active --quiet quant-detective-live.service; then
      run_id="$(read_run_id)"
      if [ -n "$run_id" ] && [ "$run_id" != "$excluded" ] && [ "$run_id" != "$other_excluded" ] \
        && runuser -u quantdetective -- "$source/.venv/bin/python" "$release/deploy/healthcheck.py" \
          /var/lib/quant-detective/state.json 30 >/dev/null 2>&1; then
        return 0
      fi
    fi
    sleep 2
  done
  return 1
}

rollback() {
  trap - ERR INT TERM
  echo "deployment failed; rolling back" >&2
  if [ "$switched" -ne 1 ]; then
    exit 4
  fi
  failed=0
  failed_run_id="$(read_run_id)"
  restore_operational_files || failed=1
  if [ -n "$previous" ] && [ -d "$previous" ]; then
    switch_current "$previous" || failed=1
    systemctl daemon-reload || failed=1
    systemctl restart quant-detective-live.service || failed=1
    # Always use the newly verified checker, including for legacy releases.
    # Restoring a symlink or seeing systemd active is insufficient. Require a
    # healthy NEW restored run, distinct from both old and failed deployment.
    wait_for_ready "$previous" "$old_run_id" "$failed_run_id" || failed=1
  else
    rm -f -- "$CURRENT" || failed=1
    systemctl stop quant-detective-live.service || failed=1
  fi
  systemctl start quant-detective-watchdog.timer || failed=1
  if [ "$failed" -ne 0 ]; then
    echo "rollback incomplete; manual recovery required" >&2
    exit 7
  fi
  remove_failed_release || exit 7
  exit 4
}

if [ ! -x "$UV_BIN" ] || [ -L "$UV_BIN" ] || [ "$(stat -c %u "$UV_BIN")" != "$EXPECTED_UID" ] \
  || [ "$(( 8#$(stat -c %a "$UV_BIN") & 8#022 ))" -ne 0 ]; then
  echo "reviewed root-owned uv binary is required at /usr/local/bin/uv" >&2
  remove_failed_release
  exit 6
fi
# Source and metadata NEVER become writable by the build/service user. Only
# the generated virtualenv is writable. No virtualenv or candidate-derived program runs as root.
install -d -o quantdetective -g quantdetective -m 0755 "$release/.venv"
if ! runuser -u quantdetective -- env PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy \
    "$UV_BIN" sync --frozen --project "$release" \
  || ! runuser -u quantdetective -- env PYTHONDONTWRITEBYTECODE=1 \
    "$release/.venv/bin/python" -m pytest -q -p no:cacheprovider \
    "$release/tests/test_ibkr_cpg.py" "$release/tests/test_ibkr_live.py" \
    "$release/tests/test_us_watch.py" "$release/tests/test_quote_validity.py" \
    "$release/tests/test_scan_health.py" "$release/tests/test_slack_alerts.py"; then
  remove_failed_release
  exit 6
fi
chown -hR root:root "$release/.venv"
chmod -R go-w "$release/.venv"
if ! validate_release "$release"; then
  echo "candidate changed during validation" >&2
  remove_failed_release
  exit 6
fi
backup="$(mktemp -d "$ROOT/.rollback.XXXXXX")"
for target in "${managed_files[@]}"; do
  if [ -f "$target" ]; then cp -p "$target" "$backup/$(basename "$target")"; fi
done
old_run_id="$(read_run_id)"

trap 'rollback' ERR INT TERM
switched=1
systemctl stop quant-detective-watchdog.timer
switch_current "$release"
install -m 0644 "$release/deploy/systemd/quant-detective-live.service" /etc/systemd/system/quant-detective-live.service
install -m 0755 "$release/deploy/update-production.sh" /usr/local/sbin/quant-detective-update
install -m 0644 "$release/deploy/systemd/quant-detective-update.service" /etc/systemd/system/quant-detective-update.service
install -m 0644 "$release/deploy/systemd/quant-detective-update.timer" /etc/systemd/system/quant-detective-update.timer
systemctl daemon-reload
systemctl restart quant-detective-live.service

if ! wait_for_ready "$release" "$old_run_id"; then
  rollback
fi

reconcile_operational_units "$release"

systemctl try-restart quant-detective-update.timer
trap - ERR INT TERM
[[ "$backup" = "$ROOT/.rollback."* ]] && rm -rf -- "$backup"
echo "deployed $remote_sha successfully"
