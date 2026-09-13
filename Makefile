# Quant Detective — clean-clone bootstrap
.PHONY: verify install

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install: $(VENV)/bin/pytest

$(VENV)/bin/pytest:
	python3 -m venv $(VENV)
	$(PIP) install -q pytest

verify: install
	bash scripts/verify.sh
