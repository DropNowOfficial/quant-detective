# Quant Detective — clean-clone bootstrap
.PHONY: verify sync tm-fixture clean-room-check

sync:
	uv sync --frozen

verify: sync
	uv run python scripts/validate_schemas.py
	uv run python -m validation_kernel.kernel_v0
	uv run pytest -q
	uv run python -m time_machine.cli run-fixture
	@echo VERIFY_OK

tm-fixture:
	uv run python -m time_machine.cli run-fixture

clean-room-check:
	bash scripts/clean_room_verify.sh
