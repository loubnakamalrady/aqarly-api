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
| `GET /requests?type=&stage=&open=&search=&sort=…` | `getRequests()` |
| `GET /requests/{id}` | `getRequestById()` |
| `GET /requests/{id}/candidates` | `getAssignmentCandidates()` |
| `POST /requests` | `createRequest()` |
| `POST /requests/assign` · `/priority` · `/delete` | `assignRequests()` · `setPriority()` · `deleteRequests()` |
| `GET /units?propertyId=&type=` · `GET /units/{id}` | `getUnits()` · `getUnitById()` |
| `GET /properties` | `getProperties()` (buildings) |
| `GET /reports/properties` · `/categories` · `/dashboard` | `getPropertyRollups()` · `getCategoryRollups()` · `getDashboardStats()` |
| `GET /staff/roster` · `GET /staff/{id}` | `getStaffRoster()` · `getSignedInTechnician()` |
| `POST /staff` · `PUT /staff/{id}` · `DELETE /staff/{id}` | `addStaff()` · `updateStaff()` · `removeStaff()` (retires) |
| `GET /housekeeping-rates?includeRetired=` | `getHousekeepingRates()` |
| `POST /housekeeping-rates` · `DELETE /housekeeping-rates/{serviceType}` | `addHousekeepingRate()` · `removeHousekeepingRate()` (retires) |
| `GET /tenants/{id}` | `getTenantById()` · `getSignedInTenant()` |

Reports take `period=month|quarter|year` (leave it out for all time) and
`type=maintenance|housekeeping` (defaults to maintenance).

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

## Staging

A shared copy of everything, online, for free: the API on **Render** (in a
Docker container), the five frontends on **Render**, and Postgres on **Neon**.
Both are free plans with no credit card. Each repo has a `render.yaml` that
describes its services; every push to the `staging` branch redeploys.

What free costs: an app that gets no traffic for 15 minutes goes to sleep, and
the next visit takes about a minute to wake it (the frontend, then the API),
then it's fast. The free plan includes 750 running hours and 500 build minutes
a month across all the services; light staging use fits.

**Staging is locked.** There is no sign-in yet (roadmap Phase 9), so every
app and the API ask for one shared username and password (`staging` and the
password you choose). The browser shows its own login prompt; the frontends
send the password to the API themselves. Locally nothing is locked, because
`STAGING_PASSWORD` isn't set.

### One-time setup

1. **Pick the staging password** and keep it in a password manager:

   ```bash
   openssl rand -base64 24
   ```

2. **Database (Neon).** Sign up at <https://neon.com> with GitHub, create a
   project in **AWS Europe Central (Frankfurt)**, and copy its connection
   string (`postgresql://…neon.tech/…?sslmode=require`). Use it as it is.

3. **Branches.** In each repo, create a `staging` branch from `main` and push
   it (`git switch -c staging && git push -u origin staging`). Staging deploys
   from this branch only, so `main` can move without touching it.

4. **The API (Render).** Sign up at <https://render.com> with GitHub, and give
   it access to this repo. Then **New → Blueprint**, pick this repo and the
   `staging` branch. Render reads `render.yaml` and asks for:
   - `DATABASE_URL`: the Neon connection string
   - `STAGING_PASSWORD`: the password from step 1

   The first deploy builds the container, which runs the migrations
   (`alembic upgrade head`) as it starts, then serves. (Render's free plan
   has no pre-deploy step, so the container does it.) Note its address, e.g.
   `https://aqarly-api-stg.onrender.com`.

5. **Load the demo data** into staging, from your laptop (the frontend repo
   must be next to this one, as locally):

   ```bash
   DATABASE_URL='<the Neon connection string>' uv run python scripts/seed.py
   ```

   Run it again any time to reset staging, just like locally.

6. **The frontends (Render).** Give Render access to the frontend repo too,
   then **New → Blueprint** on it, `staging` branch. It creates five services
   and asks, for each, for:
   - `API_URL`: the API's address from step 4 (no trailing slash)
   - `STAGING_PASSWORD`: the same password

### Addresses

| What | Where |
|---|---|
| Swagger | `https://aqarly-api-stg.onrender.com/docs` |
| Marketing site | `https://aqarly-web-stg.onrender.com` |
| Ops portal | `https://aqarly-ops-stg.onrender.com` |
| Tenant portal | `https://aqarly-tenant-stg.onrender.com` |
| Housekeeping portal | `https://aqarly-housekeeping-stg.onrender.com` |
| Field app | `https://aqarly-field-stg.onrender.com` |

Render adds a suffix if a name is taken; the dashboard shows the real one.
A custom domain (`dev.aqarlystg.…`) can be added per service later under
**Settings → Custom Domains**: one DNS record each, HTTPS included.

### Deploying

Merge into `staging` and push. Render rebuilds only what changed: the API on
any push to this repo; a frontend when its app or the shared packages change.
Watch progress and logs in the Render dashboard. The API applies its
migrations as it starts; if one fails, the previous version keeps running.

### Trying it locally, the way staging runs

```bash
docker build -t aqarly-api .
docker run -p 8010:10000 -e PORT=10000 -e STAGING_PASSWORD=try \
  -e DATABASE_URL=postgresql://aqarly:aqarly@host.docker.internal:5432/aqarly aqarly-api
curl -u staging:try localhost:8010/staff/roster
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
