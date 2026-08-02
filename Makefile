.PHONY: setup test lint fmt check serve clean

setup:
	@uv sync
	@if [ -f .env ]; then echo ".env exists — leaving it alone"; else \
		cp .env.example .env; echo "wrote .env — add OPENAI_API_KEY to use the agent"; fi

test:
	uv run pytest -q

lint:
	uv run ruff check

fmt:
	uv run ruff format
	uv run ruff check --fix

check: lint test

clean:
	rm -rf .pytest_cache .ruff_cache data
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
