from fastapi import FastAPI

from app.routers import health

app = FastAPI(
    title="Aqarly API",
    version="0.1.0",
    description="Backend for the Aqarly platform's apps.",
)

app.include_router(health.router)
