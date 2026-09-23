# aqarly-api

Python backend for the Aqarly real estate platform. FastAPI + PostgreSQL 16,
SQLAlchemy 2.0 (typed `Mapped[]` style), Alembic, Pydantic v2 +
pydantic-settings, uv, pytest. Postgres runs in Docker Compose.

The user is new to backend development: explain each step briefly (what a
file or command is for, not just what to type).

## What this replaces

The frontend is a separate repo at `/Users/lsafwat/osama/aqarly`: a pnpm
monorepo of Next.js apps (marketing site, ops portal, tenant portal,
housekeeping portal, technician field app). All their data goes through
`packages/core/src`, which today reads seed JSON (`packages/core/data/
operations.json`, `properties.json`) into an in-memory store. Each app is its
own process with its own copy, so they never agree: a job assigned in ops
never reaches the field app. This API is the one shared store that fixes that.

Read the frontend for context; never modify it from this repo.

The data model is `packages/core/src/types.ts` there: Property, Unit, Tenant,
Staff, HousekeepingRate, ServiceRequest, and a request's StageEntry, Photo,
HandBack and Schedule. Model the tables on it rather than inventing a new
shape.

`packages/core` will call this API over HTTP, with its TypeScript types
generated from this API's OpenAPI schema (`openapi-typescript`). Response
shapes are therefore a contract: give every route a Pydantic
`response_model` in `app/schemas`, and name fields and enums the way
`types.ts` does.

## Rules

- **Store only real data.** Derived values (unit lifetime spend, a
  technician's load, the repeat-fault flag, dashboard rollups, worklist
  order) are computed at read time in `app/services`, never stored in a
  column.
- **No elapsed time or SLA state.** Nothing reports request age, "overdue",
  on-track/at-risk, or durations. Requests carry absolute timestamps in their
  stage history and nothing else about time. Don't add one until SLA targets
  are real and admin-configurable.
- **Guards refuse with a reason.** A rate with open bookings or a technician
  with open work refuses removal; a job is refused to a technician who doesn't
  hold it. Return a clear error message the frontend dialog can show.
- **Naming.** "Aqarly" is a placeholder codename. Never add the real company
  name anywhere in this repo.
- **Git.** Never push to the remote; the user pushes. Local commits only when
  asked.

## Layout

```
app/main.py       FastAPI app; mounts routers
app/settings.py   Settings (pydantic-settings), read from env then .env
app/db.py         engine, SessionLocal, get_session dependency
app/models/       SQLAlchemy tables; Base in models/base.py
app/schemas/      Pydantic request/response shapes
app/routers/      endpoints, one file per area
app/services/     business rules: guards and derived state
alembic/          migrations; env.py reads Settings and Base.metadata
scripts/          seed.py (Phase 3)
tests/
```

Every model module must be imported in `app/models/__init__.py`, or Alembic
autogenerate won't see its table.

## Commands

```bash
docker compose up -d --wait                  # start Postgres
uv run uvicorn app.main:app --reload         # API on :8000, docs at /docs
uv run pytest                                # tests (need Postgres up)
uv run alembic revision --autogenerate -m "…"  # after changing models
uv run alembic upgrade head                  # apply migrations
```

Use `uv add` / `uv add --dev` for dependencies. Never `pip install`.

## Roadmap

Done: Phase 0 (tooling) and Phase 1 (an empty API with `/health`, Alembic
wired, no tables).

2. Tables from `types.ts`: properties, units, tenants, staff,
   housekeeping_rates, service_requests, plus child tables stage_history,
   photos, completion_photos.
3. `scripts/seed.py`: wipe and reload from the frontend's seed JSON. This
   replaces the "Reset demo data" button.
4. First reads: `/properties`, `/properties/{slug}`,
   `/technicians/{id}/worklist` (started first, then emergency, then oldest),
   `/jobs/{id}`, with a test for each.
5. Connect the frontend: export `openapi.json`, generate types, and switch
   `getProperties`, `getWorklist` and `getJob` to `fetch`.
6. Remaining reads by area (ops, housekeeping, tenant); derived state moves
   into `services/`. Display helpers (`formatDate`, `formatCharge`,
   `stageSteps`, `tierFor`) stay in TypeScript.
7. Writes, with a test for every guard.
8. Photos to object storage (MinIO locally, R2/S3 deployed).
9. Auth: roles ops admin, housekeeping admin, technician, tenant.
10. Delete the frontend store, deploy, and add CI.
