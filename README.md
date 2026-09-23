# aqarly-api

Backend for the Aqarly platform. It replaces the in-memory store in the
frontend repo's `packages/core`, so every app reads and writes one database
instead of each holding its own copy.

FastAPI · PostgreSQL 16 · SQLAlchemy 2.0 · Alembic · Pydantic v2 · uv · pytest

## Prerequisites

- [uv](https://docs.astral.sh/uv/): `brew install uv`
- Docker Desktop, running (the whale icon in the menu bar)

## First-time setup

```bash
cp .env.example .env      # local config; git ignores .env
uv sync                   # creates .venv and installs the locked dependencies
```

## Everyday commands

Start the database (Postgres 16 on localhost:5432, in the background):

```bash
docker compose up -d --wait
```

Apply migrations (a no-op until Phase 2 adds tables):

```bash
uv run alembic upgrade head
```

Run the API, reloading on code changes:

```bash
uv run uvicorn app.main:app --reload
```

Then open <http://localhost:8000/docs> for the interactive docs, or check
<http://localhost:8000/health>. It returns `{"ok": true, "database": "connected"}`,
or a 503 with `"unreachable"` if Postgres is down.

Run the tests (the database must be running):

```bash
uv run pytest
```

Stop the database (`-v` also deletes its data):

```bash
docker compose down
```

## Migrations

After changing a model in `app/models/`:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

Always read the generated file in `alembic/versions/` before applying it.
Autogenerate misses some changes (renames, for example).

## Layout

```
app/
  main.py       creates the FastAPI app and mounts the routers
  settings.py   configuration, read from the environment / .env
  db.py         database engine and the per-request session
  models/       SQLAlchemy tables
  schemas/      Pydantic request/response shapes (these become the OpenAPI schema)
  routers/      endpoints, one file per area
  services/     business rules: guards and derived state
alembic/        migrations
scripts/        one-off scripts (seed.py, from Phase 3)
tests/
```
