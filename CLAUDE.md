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

Read the frontend for context. Change it only when a phase calls for it and
the user has said go. Its own CLAUDE.md rules apply there (pnpm only, strict
TypeScript, no hard-coded hex, a disabled control with its reason rather than
one that pretends).

The frontend calls this API through `packages/core/src/api.ts`, with types
generated from `openapi.json` here. **After changing any route or schema, run
`uv run python scripts/export_openapi.py`** (`tests/test_openapi.py` fails
until you do), then `pnpm --filter @aqarly/core generate:api` in the frontend.

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
- **Staff and rates are retired, never deleted** (`retired_at`), a decision
  the user made. The guards still refuse while open work or open bookings
  exist. Retired rows leave rosters, assign panels and the rate card, but keep
  naming the work and bookings made with them. Deleting a request is real.
- The rate card's order is data: `housekeeping_rates.position`, where the
  admin listed it; a new rate goes last.
- Ties with no meaningful order are broken deterministically: buildings by
  name, requests by `created_at` then id. The frontend's old tie order was the
  seed file's array order, which the database doesn't have.

## Endpoints

- **Schemas** (`app/schemas`) extend `CamelModel`: snake_case fields,
  camelCase JSON and OpenAPI, `from_attributes` on. Shapes that `types.ts`
  nests but the tables flatten (`schedule`, `handBack`, `location`) are rebuilt
  in a `from_model` classmethod. Money is `Money` (float on the wire).
- **Services**: `portfolio.py` (the portals' reads), `admin.py` (their
  writes), `field.py` (the technician app), `common.py` (the request loader,
  `charged`, report periods, the repeat-fault rule, `reach_stage`). They hold
  derived reads and guarded writes, and
  raise `NotFound` / `Forbidden` / `Conflict` / `Invalid` from
  `services/errors.py`. `app/main.py` maps them to 404 / 403 / 409 / 400 with
  `{"detail": message}`. Messages are shown to people as written, so they
  match the frontend's wording. Writes don't commit: the router commits, then
  `expire_all()` and re-reads, because `stage` is computed from the history the
  write just changed. `get_session` rolls back a failed request explicitly,
  so a write refused halfway (a request raised with an unknown assignee)
  leaves nothing behind.
- Request ids are "REQ-" and the next number, taken under a Postgres advisory
  lock so two portals raising at once can't collide. Every refusal is declared in the
  route's `responses=` with `ErrorOut`, so it appears in the OpenAPI schema.
- A service that depends on the current time takes `now` as a parameter (the
  router passes `datetime.now(UTC)`), so tests can fix it.
- `tier` is not in any response: it's display logic, and the frontend derives
  it with `tierFor()`.
- The field app's routes carry the technician in the path
  (`/technicians/{id}/worklist`, `/technicians/{id}/jobs/{jobId}`) because
  there's no session yet. They become `/me/...` with auth.
- **Parity with the frontend.** When porting a read, compare the endpoint
  against the frontend's own function over the same seed: run
  `packages/core/src/operations.ts` under Node 24 with a loader hook that adds
  `.ts` to extensionless imports and serves `.json` as `export default …`, dump
  its output, and diff field by field. The only allowed differences are
  `tier`, and optional fields the frontend omits where the API sends
  `null`/`[]`. Phase 4's worklist and job reads matched exactly.

## Staging

Render (free) runs this API from the `Dockerfile` (`render.yaml`), Neon
(free) runs Postgres, and Vercel (free) runs the five frontends (a
`vercel.json` per app pins them to Frankfurt). A push to the `staging` branch
deploys. The API container runs `alembic upgrade head` as it starts (the free
plan has no pre-deploy step). An UptimeRobot monitor on `/health` keeps the
API awake: the user wants it always up (it's on their CV), and Render's 750
free hours a month cover one always-on service, not six, which is why the
frontends left Render. Keep `/health` open and cheap. README → "Staging" has
the setup steps.

`app/staging_lock.py` puts HTTP Basic auth in front of everything except
`/health` when `STAGING_PASSWORD` is set. The frontends are deliberately open
(the user wants shareable links; anyone with one can change demo data): they
send the credentials with every API call (`@aqarly/core/staging`) and serve
`X-Robots-Tag: noindex` when `STAGING_PASSWORD` is set. It is not user
accounts; Phase 9's sign-in sits on top of it. `DATABASE_URL` may be written `postgresql://…` as hosts give it:
settings rewrites it to the psycopg driver.

## Sign-in (Phase 9)

Everyone signs in with a phone number and a 6-digit code (`app/services/auth.py`,
routes in `app/routers/auth.py`). The same phone opens a different account per
app: `tenant` → a tenant (or a registration, or `new` to register), `field` →
staff, `ops` / `housekeeping` → an `Admin` of that trade. Staff and admins
can't register; tenants can, and ops approves (`/registrations`), which makes
them the unit's tenant, replacing whoever was there (the user chose approval).

- Codes and session tokens are stored hashed. Codes: 10 minutes, 5 wrong
  tries, 5 per phone per 15 minutes, each new one kills the last. Sessions: 30
  days, sent as the `X-Session` header (not `Authorization`, which the staging
  lock uses; the two travel together).
- `login_code_delivery = "screen"`: the API returns the code (`shownCode`) so
  the app can show it. Free, but anyone can then sign in as anyone: fine for
  dev and staging, must become a texting provider before real users.
- **Every route declares who it's for** with a dependency from `app/deps.py`
  (`SignedIn`, `AdminPrincipal`, `OpsPrincipal`, `TenantPrincipal`,
  `StaffPrincipal`). Admins work in their own trade only (`own_trade`); tenants
  see their own requests and raise them for their own home (marked `tenant`,
  unassigned); technicians only their own `/technicians/{id}/…`. Only
  `/health`, `/listings`, `/auth/code`, `/auth/verify` and `/auth/logout`
  are open. A new route must pick one.
- A tenant-portal session is worked out from its phone on every request, so
  approval takes effect without signing in again.
- Demo admins (seeded, not from the frontend JSON): ops `+000 000 0900`,
  housekeeping `+000 000 0901`. Tenants and staff sign in with their seed
  phones (e.g. Layla `+000 000 0001`, Youssef Haddad `+000 000 0101`).
- Tests sign in with the `sign_in` fixture (`sign_in("ops", admin_id=…)`;
  tenants by `phone=`), or through the real flow as `tests/test_auth.py` does.

## Seed

`app/seed.py` (run by `scripts/seed.py`) replaces the frontend's "Reset demo
data": TRUNCATE every table, then load `FRONTEND_DATA_DIR`'s JSON, in one
transaction. The JSON is validated by Pydantic models of the frontend's
camelCase shape with `extra="forbid"`, so a field the frontend adds stops the
seed until it's mapped here. After loading it compares the computed
`stage`/`created_at` against the JSON's stored ones and refuses on any
difference. When a model gains a column, map it in `app/seed.py` too.

## Tests

`tests/conftest.py` recreates `<db>_test` from the migrations on every run,
and the `session` fixture rolls each test back. Tests never touch dev data.

## Commands

```bash
docker compose up -d --wait                  # start Postgres
uv run uvicorn app.main:app --reload         # API on :8000, docs at /docs
uv run pytest                                # tests (need Postgres up)
uv run python scripts/seed.py                # wipe + reload the frontend's seed JSON
uv run alembic revision --autogenerate -m "…"  # after changing models
uv run alembic upgrade head                  # apply migrations
```

Use `uv add` / `uv add --dev` for dependencies. Never `pip install`.

## Roadmap

Done: Phase 0 (tooling), Phase 1 (an empty API with `/health`, Alembic
wired), Phase 2 (tables for every `types.ts` entity plus `listings`; first
migration applied), Phase 3 (`scripts/seed.py`), Phase 4 (`/listings`,
`/listings/{slug}`, `/technicians/{id}/worklist`,
`/technicians/{id}/jobs/{jobId}`), Phase 5 (the marketing site and the
field app on this API) and Phases 6–7 (every ops, housekeeping and tenant read
and write; the frontend's `store.ts` is deleted and all five apps share this
database). Each was checked against the frontend's own functions: the final
old-versus-new core comparison matched 906 reads and all 18 refusal messages.

8. Photos to object storage (MinIO locally, R2/S3 deployed).
9. Auth. **Done, codes shown on screen:** the API side (sign-in,
   registration, every route guarded) and all four portals' sign-in screens,
   plus ops' registrations list. Left: a texting provider in place of
   `login_code_delivery = "screen"` before real users.
10. Deploy (Neon, Railway/Render/Fly), and add CI: tests and the `openapi.json` export on every push.
