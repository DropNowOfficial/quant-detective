#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q pytest
fi
PY=.venv/bin/python
$PY -c "from validation_kernel.kernel_v0 import run_kernel_v0; assert run_kernel_v0()['pass']"
$PY validation_kernel/vector_prepublish_v0.py
$PY -m pytest -q
echo "VERIFY_OK"
echo "UPSTREAM_PINS:"
$PY -c "import json; print(json.dumps(json.load(open('connectors/UPSTREAM_PINS.json'))['upstream'], indent=2))"
echo "TM_FIXTURE_HASH=$(cat time_machine/fixtures/dev_syn001/FIXTURE_HASH.txt)"
echo "LOCAL_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo NONE)"
