.PHONY: help install dev-db migrate test lint format check run seed clean

PY := .venv/bin/python

help:
	@awk -F':.*##' '/^[a-z-]+:.*##/ {printf "  %-10s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## create the venv and install the project
	uv venv --python 3.11 && uv pip install -e ".[dev]"

dev-db:  ## create the local development and test databases
	./scripts/create_dev_db.sh

migrate:  ## apply migrations to the development database
	$(PY) -m alembic upgrade head

test:  ## run the whole test suite
	$(PY) -m pytest

lint:  ## static checks
	$(PY) -m ruff check app tests alembic
	$(PY) -m alembic check

format:  ## auto-format and auto-fix
	$(PY) -m ruff format app tests alembic scripts
	$(PY) -m ruff check --fix app tests alembic

check:  ## verify configuration and database connectivity
	$(PY) -m app.cli check

run:  ## start the API on :8000
	.venv/bin/uvicorn app.main:app --reload --port 8000

seed:  ## load demo mail through the real pipeline
	$(PY) scripts/demo_seed.py --reset

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
