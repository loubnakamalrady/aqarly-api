"""The technician field app.

The technician is in the path because there is no sign-in yet: the frontend
passes its stub `getSignedInTechnician()`'s id, as it passes it to `getJob`
today. Once auth exists (Phase 9) the id comes from the session instead, and
these become `/me/...`.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.common import ErrorOut
from app.schemas.field import JobOut, WorklistOut
from app.services import field

router = APIRouter(prefix="/technicians/{technician_id}", tags=["field"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/worklist", response_model=WorklistOut, responses={404: {"model": ErrorOut}})
def get_worklist(technician_id: str, session: SessionDep) -> WorklistOut:
    """Everything assigned to this technician. Open work is ordered started
    first, then emergencies, then oldest; closed work, most recently closed
    first."""
    return field.get_worklist(session, technician_id, now=datetime.now(UTC))


@router.get(
    "/jobs/{job_id}",
    response_model=JobOut,
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
def get_job(technician_id: str, job_id: str, session: SessionDep) -> JobOut:
    """One job. Refused (403) if this technician doesn't hold it."""
    return field.get_job(session, job_id, technician_id, now=datetime.now(UTC))
