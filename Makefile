.PHONY: help install lint format typecheck test ci frontend-lint frontend-test frontend-build rust-fmt rust-test rust-build pre-commit

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

frontend-test: ## Run frontend unit tests (node:test)
	cd frontend && node --test "src/**/*.test.js"

frontend-build: ## Build frontend
	cd frontend && npm run build

rust-fmt: ## Check Rust formatting
	cd atri-host && cargo fmt --all -- --check

rust-test: ## Run Rust workspace tests
	cd atri-host && cargo test --workspace

rust-build: ## Build atri-host binary
	cd atri-host && cargo build -p atri-host

pre-commit: ## Run all pre-commit hooks
	uv run pre-commit run --all-files

ci: lint typecheck test frontend-lint frontend-test frontend-build rust-fmt rust-test rust-build ## Run full CI pipeline locally
