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

A shared copy of everything, online, for free, and awake all the time:

| Part | Host | Why there |
|---|---|---|
| The API | **Render**, from this repo's `Dockerfile` | Runs a container; kept awake by a ping (below) |
| Postgres | **Neon** | Pauses when idle but wakes in under a second |
| The five frontends | **Vercel** | Made for Next.js; the free plan never sleeps |

All three are free plans. Every push to the `staging` branch of either repo
redeploys what changed.

**Keeping the API awake.** Render's free plan puts a service to sleep after
15 minutes without traffic, and waking it takes a minute or more. An uptime
monitor (UptimeRobot) calls `/health` every 5 minutes, so it never gets the
chance. That's allowed on the free plan, but only for one service: the plan
has 750 running hours a month across all services, and one service awake all
month uses ~730. That's why the frontends are on Vercel, not Render.

**The API is locked; the apps are not.** The API and Swagger ask for one
shared username and password (`staging` and the password you choose), on top
of the apps' own sign-in (see "Signing in"). The five apps are open to anyone
with the link, so you can share them: they send the password to the API
themselves, server-side, and ask search engines not to list them. Anyone with
an app link can sign in with the demo numbers and change the demo data; reset
it with the seed script. Locally nothing is locked, because
`STAGING_PASSWORD` isn't set.

**One limit on Vercel's free plan:** a form can post at most 4.5 MB. Photos
still travel inside the form (until Phase 8 moves them to file storage), so a
job closed with several full-size phone photos can be refused there.

### One-time setup

1. **Pick the staging password** and keep it in a password manager:

   ```bash
   openssl rand -base64 24
   ```

2. **Database (Neon).** Sign up at <https://neon.com> with GitHub, create a
   project in **AWS Europe Central (Frankfurt)**, and copy its connection
   string (`postgresql://…neon.tech/…?sslmode=require`, the one *without*
   `-pooler`). Use it as it is.

3. **Branches.** In each repo, create a `staging` branch from `main` and push
   it (`git switch -c staging && git push -u origin staging`). Staging deploys
   from this branch only, so `main` can move without touching it.

4. **The API (Render).** Sign up at <https://render.com> with GitHub, and give
   it access to this repo. Then **New → Web Service**, pick this repo, branch
   `staging`, runtime **Docker**, region **Frankfurt**, plan **Free**, health
   check path `/health` (these match `render.yaml`), and add:
   - `DATABASE_URL`: the Neon connection string
   - `STAGING_PASSWORD`: the password from step 1

   The first deploy builds the container, which runs the migrations
   (`alembic upgrade head`) as it starts, then serves. (Render's free plan
   has no pre-deploy step, so the container does it.) Note its address, e.g.
   `https://aqarly-api-stg.onrender.com`.

5. **Load the demo data** into staging, from your laptop, in this folder (the
   frontend repo must be next to this one, as locally). Put the connection
   string in `.neon-url` (git-ignored) rather than typing it into a command:

   ```bash
   touch .neon-url && open -e .neon-url     # paste it, save, close
   DATABASE_URL="$(cat .neon-url)" uv run python scripts/seed.py
   ```

   Run the second line again any time to reset staging, just like locally.
   It also creates the demo admins, so ops and housekeeping can sign in.

6. **Keep the API awake (UptimeRobot).** Sign up at <https://uptimerobot.com>
   (free), **New monitor** → type **HTTP(s)**, URL
   `https://aqarly-api-stg.onrender.com/health`, interval **5 minutes**. It
   also emails you if the API goes down.

7. **The frontends (Vercel).** Sign up at <https://vercel.com> with GitHub
   (the free **Hobby** plan) and give it access to the frontend repo. Then,
   once per app (ops, tenant, housekeeping, field, and web if you want the
   marketing site): **Add New → Project**, import the frontend repo, and:
   - **Project name**: e.g. `aqarly-ops-stg`
   - **Root Directory**: **Edit** → `apps/ops` (the app's folder). Vercel
     sees the pnpm workspace and installs from the repo root itself; leave
     the build and install commands as they are.
   - **Environment Variables**:
     - `API_URL`: the API's address from step 4 (no trailing slash)
     - `STAGING_PASSWORD`: the same password (the apps use it to reach the
       API; visitors never need it)
   - **Deploy.** Then, in the project's **Settings → Environments →
     Production**, set the branch to `staging`, so that branch is what the
     main address shows. Redeploy once from **Deployments** after changing it.

   Each app's `vercel.json` runs its server code in Frankfurt (`fra1`), next
   to the API and the database; Vercel's default is Washington, which would
   add an ocean to every API call.

### Addresses

| What | Where |
|---|---|
| Swagger | `https://aqarly-api-stg.onrender.com/docs` |
| Ops portal | `https://aqarly-ops-stg.vercel.app` |
| Tenant portal | `https://aqarly-tenant-stg.vercel.app` |
| Housekeeping portal | `https://aqarly-housekeeping-stg.vercel.app` |
| Field app | `https://aqarly-field-stg.vercel.app` |
| Marketing site | `https://aqarly-web-stg.vercel.app` (if created) |

Vercel adds a suffix if a name is taken; each project's page shows the real
address. A custom domain can be added per project later under **Settings →
Domains**, HTTPS included.

### Deploying

Merge into `staging` and push. Render redeploys the API on any push to this
repo, and applies its migrations as it starts; if one fails, the previous
version keeps running. Vercel builds each frontend project on a push to the
frontend repo's `staging` branch; other branches get preview deployments at
their own addresses. Before pushing the frontend, run `pnpm --filter <app>
build` for the apps you touched: `next dev` doesn't catch everything the
production build refuses.

### Trying it locally, the way staging runs

```bash
docker build -t aqarly-api .
docker run -p 8010:10000 -e PORT=10000 -e STAGING_PASSWORD=try \
  -e DATABASE_URL=postgresql://aqarly:aqarly@host.docker.internal:5432/aqarly aqarly-api
curl -u staging:try localhost:8010/staff/roster
```

## Signing in

Every app signs in with a phone number and a 6-digit code. For now the code
isn't texted: `POST /auth/code` returns it (`shownCode`) and the app shows it.
In Swagger: call `/auth/code`, then `/auth/verify` with that code, copy the
`token`, click **Authorize** and paste it as `X-Session`.

Demo accounts (after `scripts/seed.py`):

| App (`app`) | Phone |
|---|---|
| `ops` | `+000 000 0900` |
| `housekeeping` | `+000 000 0901` |
| `field` | any staff member's, e.g. `+000 000 0101` (Youssef Haddad) |
| `tenant` | any tenant's, e.g. `+000 000 0001` (Layla Al Habsi); a new number can register |

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
