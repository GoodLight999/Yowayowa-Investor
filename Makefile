# Shared tool venv + per-tree src direct reference.
#
# Verification never relies on an editable install of yowayowa-investor:
# `uv sync` writes an editable .pth pointing at the current tree's src/, so
# concurrent worktrees sharing one venv overwrite each other's path (hotspot
# task t_9803f6b1). Rules below call venv tools directly and resolve the
# project package via PYTHONPATH=<this tree>/src, which takes precedence over
# site-packages. See "Shared tool venv rules" in docs/AGENT_ENVIRONMENT.md.
#
# V is the shared tool venv (the main checkout's .venv). From a worktree,
# pass the main checkout's path explicitly: make V=<main>/.venv <target>.
# A fresh clone has no .venv yet; the order-only `bootstrap` prerequisite
# creates it on first use (uv's environment lock makes concurrent bootstraps
# idempotent). Bootstrap and install sync dependencies only
# (--no-install-project), so no editable install of this project ever exists.
V := $(CURDIR)/.venv
export PYTHONPATH := $(CURDIR)/src

.PHONY: install bootstrap dev test lint typecheck openapi verify

# One-time tool-venv construction (initial setup only; do not re-run before
# every verification). Never installs this project itself. Re-run this once
# after uv.lock changes to refresh dependencies.
install:
	UV_PROJECT_ENVIRONMENT=$(V) uv sync --extra dev --extra operator-ir --no-install-project

# First-use bootstrap for fresh clones/worktrees: creates $(V) if missing.
bootstrap:
	@if [ ! -x "$(V)/bin/python" ]; then \
		echo "==> Bootstrapping shared tool venv at $(V) (first use in this tree)"; \
		UV_PROJECT_ENVIRONMENT=$(V) uv sync --extra dev --extra operator-ir --no-install-project; \
	fi

dev: | bootstrap
	$(V)/bin/python -m uvicorn yowayowa.api.app:app --reload

test: | bootstrap
	$(V)/bin/pytest --cov=yowayowa --cov-report=term-missing

lint: | bootstrap
	$(V)/bin/ruff check src tests
	$(V)/bin/ruff format --check --diff src tests

typecheck: | bootstrap
	$(V)/bin/mypy src/yowayowa

openapi: | bootstrap
	$(V)/bin/python -m yowayowa.cli_shim export-openapi

verify: lint typecheck test openapi
