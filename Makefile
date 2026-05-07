.PHONY: help install lint format typecheck test ci frontend-lint frontend-build pre-commit

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies (Python + frontend)
	uv sync --group dev
	cd frontend && npm ci
	uv run pre-commit install

lint: ## Run Ruff linter
	uv run ruff check .

format: ## Format code with Ruff
	uv run ruff format .

typecheck: ## Run mypy type checking
	uv run mypy core/ dashboard/

test: ## Run Python tests
	uv run pytest --tb=short -q

frontend-lint: ## Lint frontend code
	cd frontend && npx eslint src/

frontend-build: ## Build frontend
	cd frontend && npm run build

pre-commit: ## Run all pre-commit hooks
	uv run pre-commit run --all-files

ci: lint typecheck test frontend-lint frontend-build ## Run full CI pipeline locally
