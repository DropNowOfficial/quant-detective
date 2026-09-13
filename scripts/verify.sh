#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen
uv run python scripts/validate_schemas.py
uv run python -m validation_kernel.kernel_v0
uv run pytest -q
uv run python -m time_machine.cli run-fixture
echo VERIFY_OK
