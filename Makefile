.PHONY: help install check lint format test migrations-check matrix test-django

help:  ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install:  ## Install with development dependencies (the core from ../ponyglot)
	uv sync

check: lint migrations-check test  ## Everything CI would run

lint:  ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

format:  ## Apply ruff fixes and formatting
	uv run ruff check --fix .
	uv run ruff format .

migrations-check:  ## Fail if models lack migrations
	uv run python -m django makemigrations djangocms_ponyglot --check --dry-run --settings=tests.settings

test:  ## Run the tests (SQLite, no services needed)
	uv run pytest

# Supported combinations: Django 5.2 on Python 3.10–3.14, Django 6.0 on 3.12–3.14
# (oldest and newest Python of each). Each runs in a throwaway environment, not the lockfile's;
# the core comes from CORE (default: the checkout next to this repository).
MATRIX := 3.10:5.2 3.14:5.2 3.12:6.0 3.14:6.0
CORE ?= ../ponyglot

matrix:  ## Run the tests on every supported Python × Django combination
	@set -e; for combo in $(MATRIX); do \
		$(MAKE) --no-print-directory test-django PY=$${combo%%:*} DJANGO=$${combo##*:}; \
	done

test-django:  ## Run the tests on one combination: make test-django PY=3.10 DJANGO=5.2
	@echo "--- Python $(PY), Django $(DJANGO)"
	uv run --no-project --isolated --python $(PY) --with-editable . --with-editable $(CORE) \
		--with "django==$(DJANGO).*" --with pytest --with pytest-django \
		--with djangocms-versioning --with djangocms-text --with djangocms-frontend \
		python -m pytest -q -p no:cacheprovider
