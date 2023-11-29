install: ## Run `poetry install`
	poetry install --no-root

lint:
	poetry run ruff .
	poetry run ruff check .

format: ## Formasts you code with Black
	poetry run ruff . --fix
	poetry run ruff check . --fix

test:
	poetry run pytest -v tests

publish:
	poetry publish --build
