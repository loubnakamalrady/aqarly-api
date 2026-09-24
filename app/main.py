from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.routers import auth, field, health, listings, portfolio, rates, registrations, requests, staff, tenants
from app.services.errors import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    ServiceError,
    TooMany,
    Unauthenticated,
)
from app.staging_lock import staging_lock

app = FastAPI(
    title="Aqarly API",
    version="0.1.0",
    description="Backend for the Aqarly platform's apps.",
)

# Closes a deployed copy behind the staging password; off locally.
app.middleware("http")(staging_lock)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(registrations.router)
app.include_router(listings.router)
app.include_router(field.router)
app.include_router(requests.router)
app.include_router(portfolio.router)
app.include_router(staff.router)
app.include_router(rates.router)
app.include_router(tenants.router)

# A service's refusal becomes an HTTP status here, with its message as
# `detail` so the frontend can show it as it is.
_STATUS: dict[type[ServiceError], int] = {
    Invalid: 400,
    Unauthenticated: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
    TooMany: 429,
}


@app.exception_handler(ServiceError)
def service_error(request: Request, error: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=_STATUS.get(type(error), 400), content={"detail": error.message})
