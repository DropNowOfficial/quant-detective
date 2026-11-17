#!/usr/bin/env bash
# Hand-run of the Makefile `verify` recipe (make is not installed on the box). Reverts the rewritten
# validation_kernel report json afterwards. Usage: bash docs/research/evidence/run_verify_recipe.sh
cd "$(git rev-parse --show-toplevel)" || exit 1
rc=0
echo "HEAD $(git rev-parse HEAD)"; echo "date_utc $(date -u +%FT%TZ)"
run() { echo; echo "\$ $*"; "$@"; r=$?; echo "[exit $r]"; [ $r -eq 0 ] || rc=1; }
run uv sync --frozen
run uv run python scripts/validate_schemas.py
run uv run python -m validation_kernel.kernel_v0
run uv run --extra research pytest -q -p no:cacheprovider
run uv run --extra research --directory legacy/trade-monitor python -m unittest discover -s tests -q
run uv run python -m time_machine.cli run-fixture
git checkout -- validation_kernel 2>/dev/null
git status --short -- validation_kernel
[ $rc -eq 0 ] && echo VERIFY_OK || echo VERIFY_FAILED
exit $rc
