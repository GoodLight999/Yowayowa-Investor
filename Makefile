.PHONY: install dev test lint typecheck verify openapi

install:
	uv sync --extra dev

dev:
	uv run uvicorn yowayowa.api.app:app --reload

test:
	uv run pytest --cov=yowayowa --cov-report=term-missing

lint:
	uv run ruff check src tests
	uv run ruff format --check --diff src tests

typecheck:
	uv run mypy src/yowayowa

openapi:
	uv run yowayowa export-openapi

verify: lint typecheck test openapi
