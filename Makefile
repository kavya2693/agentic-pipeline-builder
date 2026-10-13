.PHONY: data run stress check lint test

data:
	uv run python scripts/generate_data.py

run:
	uv run pipeline-builder run --raw data/raw --out generated --truth data/ground_truth

stress:
	uv run python scripts/stress_defect_rate.py

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

test:
	uv run pytest -q

check: lint test
