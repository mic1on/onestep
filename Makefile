install: ## Run `uv sync`
	uv sync

lint:
	uv run ruff .
	uv run ruff check .

format: ## Formats your code with Ruff
	uv run ruff . --fix
	uv run ruff check . --fix

test:
	uv run pytest -v tests

publish:
	uv build
	uv publish

dev-install: ## Install with dev dependencies
	uv sync --extra dev

test-install: ## Install with test dependencies
	uv sync --extra test
