.PHONY: bench check ci fix format precommit test
.PHONY: db-reset db-setup db-teardown
.PHONY: docs docs-clean docs-publish docs-serve
.PHONY: help release 

API_BASE ?= https://oban.pro
DSN_BASE ?= postgresql://postgres@localhost
TEST_DB = oban_py_test
TEST_DSN = $(DSN_BASE)/$(TEST_DB)

help:
	@echo "Available targets:"
	@echo "  bench        - Run benchmarks with pytest-benchmark"
	@echo "  check        - Check formatting and linting"
	@echo "  ci           - Run checks and tests (for CI)"
	@echo "  db-reset     - Drop and recreate test database"
	@echo "  db-setup     - Create test database and install schema"
	@echo "  db-teardown  - Uninstall schema and drop test database"
	@echo "  docs         - Build HTML documentation"
	@echo "  docs-clean   - Clean built documentation"
	@echo "  docs-publish - Publish documentation"
	@echo "  docs-serve   - Serve documentation locally on port 8000"
	@echo "  fix          - Fix linting issues automatically"
	@echo "  format       - Format code with ruff"
	@echo "  precommit    - Run pre-commit checks (alias for check)"
	@echo "  release      - Build and publish a release (requires VERSION=x.y.z)"
	@echo "  test         - Run tests with pytest"

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check

precommit: check

ci: check test

fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

release: ci
ifndef VERSION
	$(error VERSION is required. Usage: make release VERSION=x.y.z)
endif
	rm -rf dist
	uv version $(VERSION)
	git add pyproject.toml uv.lock CHANGELOG.md
	git commit -m "Release v$(VERSION)"
	git tag v$(VERSION)
	uv build
	uv publish
	git push origin main --tags
	$(MAKE) docs-publish

test:
	uv run pytest -s

bench:
	uv run pytest -m benchmark --benchmark-only

docs:
	uv run --group docs sphinx-build -b html docs docs/_build

docs-serve:
	DOCS_SWITCHER_URL="_static/switcher.json" uv run --group docs sphinx-build -b html docs docs/_build
	@echo "Serving documentation at http://localhost:8000"
	@cd docs/_build && uv run python -m http.server 8000

docs-clean:
	rm -rf docs/_build

docs-publish: docs
	$(eval VERSION := $(shell grep -m1 '^version' pyproject.toml | sed 's/.*"\(.*\)"/\1/'))
	@echo "Publishing docs for oban-py v$(VERSION)..."
	@(cd docs/_build && COPYFILE_DISABLE=1 tar -czf /tmp/oban-py-docs.tar.gz --exclude='locales' --exclude='*.map' --exclude='*.ttf' *)
	@curl -s -X POST "$(API_BASE)/releases" \
		-H "Authorization: Bearer $$LYS_API_KEY" \
		-F "package=py" \
		-F "version=$(VERSION)" \
		-F "docs_tar=@/tmp/oban-py-docs.tar.gz" \
		-F "notes=Documentation update"
	@rm -f /tmp/oban-py-docs.tar.gz

db-setup:
	@psql $(DSN_BASE)/postgres -c "CREATE DATABASE $(TEST_DB)" 2>/dev/null || true
	@uv run oban install --dsn $(TEST_DSN)

db-reset:
	@psql $(DSN_BASE)/postgres -c "DROP DATABASE IF EXISTS $(TEST_DB)"
	@$(MAKE) db-setup

db-teardown:
	@uv run oban uninstall --dsn $(TEST_DSN)
	@psql $(DSN_BASE)/postgres -c "DROP DATABASE IF EXISTS $(TEST_DB)"
