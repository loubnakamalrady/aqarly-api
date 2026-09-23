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

## Schema

- **String ids from the seed** (`prop-marina-heights`, `REQ-1058`) are the
  primary keys, so the frontend's URLs keep working.
- **Vocabularies** (`Stage`, `RequestType`, …) are `Literal`s in
  `app/models/enums.py`, stored as VARCHAR + a named CHECK (not native Postgres
  enums). Pydantic schemas reuse the same `Literal`s, so the generated
  TypeScript unions match `types.ts`.
- **`stage` and `created_at` are not columns.** They're `column_property`
  subqueries over `stage_history`: the furthest stage reached (by rank, not
  by time) and the `submitted` entry's time. Read-only; after changing the
  history, flush and expire the request before reading them. Every write that
  creates a request must add its `submitted` entry.
- Columns are snake_case (`unit_id`); `types.ts` is camelCase (`unitId`). The
  response schemas map one to the other with aliases, so the JSON matches
  `types.ts`. Nested shapes (`schedule`, `handBack`, `location`) are flattened
  into prefixed columns and rebuilt in the schemas.
- Frontend rules are CHECK constraints on `service_requests` (housekeeping is
  never urgent, maintenance is never charged, schedule date and slot together,
  maintenance categories fixed).
- **Autogenerate does not see CHECK constraint changes.** Add or drop them by
  hand in the migration; `test_migrations_have_the_models_check_constraints`
  catches a forgotten one.
- Money is `Numeric(12, 2)` (Python `Decimal`). Declare it as a number in
  response schemas, because Pydantic serializes `Decimal` as a JSON string by
  default.
- `listings` holds the marketing site's `Listing`s; `properties` holds the
  buildings operations manages. The frontend's `getProperties()` returns
  listings, so its API route should be `/listings`, not `/properties`.
- `service_requests.assignee_id` is a plain (RESTRICT) foreign key, so staff
  with closed work can't be deleted yet, although the frontend allows that.
  Decide in Phase 7 (soft-remove staff, or null the assignee).

## Tests

`tests/conftest.py` recreates `<db>_test` from the migrations on every run,
and the `session` fixture rolls each test back. Tests never touch dev data.

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

Done: Phase 0 (tooling), Phase 1 (an empty API with `/health`, Alembic
wired) and Phase 2 (tables for every `types.ts` entity plus `listings`; first
migration applied).

3. `scripts/seed.py`: wipe and reload from the frontend's seed JSON. This
   replaces the "Reset demo data" button.
4. First reads: listings (`/listings`, `/listings/{slug}`),
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
