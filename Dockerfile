# The API as a container: what Render (or any host) runs.
#   docker build -t aqarly-api .
#   docker run -p 8000:8000 -e DATABASE_URL=… aqarly-api

FROM python:3.12-slim

# uv, the same version the repo is developed with.
COPY --from=ghcr.io/astral-sh/uv:0.12.18 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

# Dependencies first, from the lockfile, so a code-only change reuses this
# layer instead of reinstalling everything.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

# The virtualenv's tools (uvicorn, alembic) on the PATH, so the host's
# pre-deploy command can be plain `alembic upgrade head`.
ENV PATH="/app/.venv/bin:$PATH"

# Production mode: no --reload. Hosts say which port to use in $PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
