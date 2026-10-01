.PHONY: install up down migrate seed run test test-pg lint fmt

install:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt

up:
	docker compose up -d db

down:
	docker compose down

migrate:
	.venv/bin/alembic upgrade head

seed:
	.venv/bin/python -m scripts.seed

run:
	.venv/bin/uvicorn app.main:app --reload

# Fast loop: in-memory SQLite, no services needed.
test:
	.venv/bin/pytest -q

# Same suite against the real engine the app ships on.
test-pg:
	TEST_DATABASE_URL=postgresql+asyncpg://subsea:subsea@localhost:5432/subsea_test .venv/bin/pytest -q

lint:
	.venv/bin/ruff check app scripts tests

fmt:
	.venv/bin/ruff format app scripts tests
