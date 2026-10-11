#!/usr/bin/env bash
set -euo pipefail

# Repair is the same reviewed, rollback-protected transaction as deployment.
# Never reset the live tree or run package installers with root credentials.
UPDATER=/usr/local/sbin/quant-detective-update
if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 2
fi
if [ ! -x "$UPDATER" ] || [ -L "$UPDATER" ] || [ "$(stat -c %u "$UPDATER")" != 0 ] \
  || [ "$(( 8#$(stat -c %a "$UPDATER") & 8#022 ))" -ne 0 ]; then
  echo "Install the reviewed root-owned transactional updater before repairing." >&2
  exit 3
fi
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [ ! -f "$SOURCE_DIR/update-production.sh" ] || [ -L "$SOURCE_DIR/update-production.sh" ]; then
  echo "The reviewed sibling update-production.sh is required." >&2
  exit 3
fi
expected="$(sha256sum -- "$SOURCE_DIR/update-production.sh" | awk '{print $1}')"
installed="$(sha256sum -- "$UPDATER" | awk '{print $1}')"
if [ "$expected" != "$installed" ]; then
  echo "Installed updater differs from the reviewed updater; install it separately before repairing." >&2
  exit 3
fi
exec "$UPDATER" --repair "$@"
