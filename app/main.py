from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.routers import field, health, listings, portfolio, rates, requests, staff, tenants
from app.services.errors import Conflict, Forbidden, Invalid, NotFound, ServiceError
from app.staging_lock import staging_lock

app = FastAPI(
    title="Aqarly API",
    version="0.1.0",
    description="Backend for the Aqarly platform's apps.",
)

# Closes a deployed copy behind the staging password; off locally.
app.middleware("http")(staging_lock)

app.include_router(health.router)
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
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
}


@app.exception_handler(ServiceError)
def service_error(request: Request, error: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=_STATUS.get(type(error), 400), content={"detail": error.message})
