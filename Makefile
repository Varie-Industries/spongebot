# SpongeBot Makefile

.PHONY: install dev test lint typecheck clean run backup compile-token-saver

PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy

# ---- Setup ----

install:
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

dev: install
	$(PIP) install -e ".[dev]"

# ---- Quality ----

test:
	$(PYTEST) tests/ -v --cov=src --cov-report=term-missing

lint:
	$(RUFF) check src/ tests/ api/

typecheck:
	$(MYPY) src/

format:
	$(RUFF) format src/ tests/ api/

# ---- Run ----

run:
	$(VENV)/bin/spongebot

run-api:
	$(VENV)/bin/uvicorn api.main:app --host 0.0.0.0 --port 8420 --reload

# ---- Token Saver (SECRET) ----

compile-token-saver:
	@echo "Compiling token saver to binary..."
	cd src/token_saver && $(PYTHON) -m pyarmor gen --enable-jit -O dist _engine.py _layers.py
	@echo "Token saver compiled. Binaries in src/token_saver/dist/"

# ---- Backup ----

backup:
	bash scripts/backup.sh

# ---- Clean ----

clean:
	rm -rf $(VENV) build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
