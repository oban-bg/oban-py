.PHONY: format check fix test bench ci docs docs-serve docs-clean help

help:
	@echo "Available targets:"
	@echo "  check       - Check formatting and linting"
	@echo "  ci          - Run checks and tests (for CI)"
	@echo "  fix         - Fix linting issues automatically"
	@echo "  format      - Format code with ruff"
	@echo "  test        - Run tests with pytest"
	@echo "  bench       - Run benchmarks with pytest-benchmark"
	@echo "  docs        - Build HTML documentation"
	@echo "  docs-serve  - Serve documentation locally on port 8000"
	@echo "  docs-clean  - Clean built documentation"

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check

ci: check test

fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

test:
	uv run pytest

bench:
	uv run pytest -m benchmark --benchmark-only

docs:
	uv run --group docs sphinx-build -b html docs docs/_build

docs-serve: docs
	@echo "Serving documentation at http://localhost:8000"
	@cd docs/_build && python -m http.server 8000

docs-clean:
	rm -rf docs/_build
