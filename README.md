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

Load the demo data: wipes every table and reloads the frontend's seed JSON
(`../aqarly/packages/core/data`, or `FRONTEND_DATA_DIR` in `.env`). Run it
again any time to get back to a known state. It's all or nothing: bad data
is refused and the database is left as it was.

```bash
uv run python scripts/seed.py
```

Run the API, reloading on code changes:

```bash
uv run uvicorn app.main:app --reload
```

Then open <http://localhost:8000/docs> for the interactive docs, or check
<http://localhost:8000/health>. It returns `{"ok": true, "database": "connected"}`,
or a 503 with `"unreachable"` if Postgres is down.

Endpoints so far:

| Route | Frontend function it replaces |
|---|---|
| `GET /health` | (none) |
| `GET /listings?purpose=&type=&featured=` | `getProperties()` |
| `GET /listings/{slug}` | `getPropertyBySlug()` |
| `GET /technicians/{id}/worklist` | `getWorklist()` |
| `GET /technicians/{id}/jobs/{jobId}` | `getJob()` |
| `POST /technicians/{id}/jobs/{jobId}/start` | `startRequest()` |
| `POST /technicians/{id}/jobs/{jobId}/complete` | `completeRequest()` |
| `POST /technicians/{id}/jobs/{jobId}/hand-back` | `handBackRequest()` |

After changing any route or schema, re-export the OpenAPI schema. The
frontend generates its TypeScript types from this file, and
`tests/test_openapi.py` fails until it's current:

```bash
uv run python scripts/export_openapi.py
```

Then, in the frontend repo: `pnpm --filter @aqarly/core generate:api`.

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
Autogenerate misses some changes: renames, and **any CHECK constraint added
to or removed from a model**. Write those into the migration yourself
(`op.create_check_constraint` / `op.drop_constraint`);
`test_migrations_have_the_models_check_constraints` fails if you forget.

## Tests

`uv run pytest` never touches your dev data. It drops and recreates a
separate `aqarly_test` database, builds it by running the migrations, and
rolls back each test's changes when the test ends (see `tests/conftest.py`).

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
