.PHONY: data run e2e stress check lint test

# The drop was extracted on 2026-02-01; date-window rules are judged against that day.
AS_OF ?= 2026-02-01

data:
	uv run python scripts/generate_data.py

run:
	uv run pipeline-builder run --raw data/raw --out generated --report outputs --truth data/ground_truth --as-of $(AS_OF)

e2e: data run

stress:
	uv run python scripts/stress_defect_rate.py

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest -q

check: lint test
