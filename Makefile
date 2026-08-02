.PHONY: setup test lint fmt check seed serve clean

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

seed:
	uv run python scripts/seed_demo.py --db demo.sqlite3 --fresh

# No default PERSON. A server bound to whoever happens to be row 1 is the same
# mistake _resolve_person refuses to make.
serve:
	@if [ -z "$(PERSON)" ]; then \
		echo "which sponsor's household? e.g.  make serve PERSON=1"; exit 1; fi
	STEWARD_DB=$${STEWARD_DB:-demo.sqlite3} \
	PAY_WARDEN_POLICY=$${PAY_WARDEN_POLICY:-demo-household.yaml} \
	uv run python -m steward serve --person $(PERSON)

clean:
	rm -rf .pytest_cache .ruff_cache data
	rm -f demo.sqlite3 demo.sqlite3-wal demo.sqlite3-shm demo-household.yaml
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
