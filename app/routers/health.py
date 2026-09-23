from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.health import Health

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=Health,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Health}},
)
def health(
    response: Response, session: Annotated[Session, Depends(get_session)]
) -> Health:
    """Whether the API is up and can reach its database."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(ok=False, database="unreachable")
    return Health(ok=True, database="connected")
