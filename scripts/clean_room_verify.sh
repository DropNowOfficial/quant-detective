#!/usr/bin/env bash
# Clone into a fresh temp dir; fresh env; no PYTHONPATH into old workspace.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
export QD_FORBIDDEN_SRC="$SRC"
TMP="$(mktemp -d /tmp/qd-cleanroom-XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
echo "CLEAN_ROOM_TMP=$TMP"
git clone -- "$SRC" "$TMP/quant-detective"
cd "$TMP/quant-detective"
unset PYTHONPATH
HASH_BEFORE="$(sha256sum uv.lock | awk '{print $1}')"
uv sync --frozen
HASH_AFTER="$(sha256sum uv.lock | awk '{print $1}')"
if [[ "$HASH_BEFORE" != "$HASH_AFTER" ]]; then
  echo "LOCKFILE_MUTATED"
  exit 1
fi
test -d .venv
test "$(realpath .venv)" != "$(realpath "$SRC/.venv")"
bash scripts/verify.sh
uv run python - <<'PY'
import os, sys, pathlib
src = pathlib.Path(os.environ["QD_FORBIDDEN_SRC"]).resolve()
clone = pathlib.Path.cwd().resolve()
for p in sys.path:
    try:
        rp = pathlib.Path(p).resolve()
    except Exception:
        continue
    if rp == src:
        raise SystemExit(f"SRC_LEAK: {rp}")
print("NO_SRC_LEAK")
print("CLEAN_ROOM_OK")
PY
