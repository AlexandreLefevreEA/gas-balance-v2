# Gas Balance v2 — single entrypoint. Targets activate as subsystems are implemented.
# Windows: run under WSL/Git-Bash, or call the underlying uv/npm commands directly.
.DEFAULT_GOAL := help
.PHONY: help setup fmt lint test run-etl run-ce run-api dev

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-10s %s\n", $$1, $$2}'

setup: ## uv env + workspace install; web deps
	uv sync --all-packages
	cd web && npm install

fmt: ## Auto-format + autofix (ruff)
	uv run ruff format .
	uv run ruff check --fix .

lint: ## Lint + type-check (python + web)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	cd web && npm run lint && npx tsc -b --noEmit

test: ## Run all tests
	uv run pytest
	cd web && npm test

run-etl: ## Run the ETL CLI (see etl/CLAUDE.md)
	uv run etl run all

run-ce: ## Run just the Commodity Essentials connector
	uv run etl run ce

run-api: ## Run the API locally
	uv run uvicorn gasbalance_api.main:app --reload

dev: ## Local dev stack: postgres + api + web
	docker compose -f infra/docker-compose.yml up
