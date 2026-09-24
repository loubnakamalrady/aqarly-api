"""The technician field app: one technician's own work.

The technician is in the path, and every route here refuses anyone but that
technician, signed in to the field app (403 otherwise, 401 if nobody is).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import SessionDep, StaffPrincipal
from app.models import ServiceRequest
from app.schemas.common import ErrorOut
from app.schemas.field import CompleteJobIn, HandBackIn, JobOut, WorklistOut
from app.schemas.operations import ServiceRequestOut
from app.services import field
from app.services.errors import Forbidden


def _only_themselves(technician_id: str, who: StaffPrincipal) -> None:
    if who.staff_id != technician_id:
        raise Forbidden("You can only see your own work.")


router = APIRouter(
    prefix="/technicians/{technician_id}",
    tags=["field"],
    dependencies=[Depends(_only_themselves)],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)


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


# --- Writes ------------------------------------------------------------------
# Each commits the change, then reads the job back fresh: `stage` is computed
# from the history the write just changed.

_WRITE_ERRORS = {
    400: {"model": ErrorOut},
    403: {"model": ErrorOut},
    404: {"model": ErrorOut},
    409: {"model": ErrorOut},
}


@router.post("/jobs/{job_id}/start", response_model=JobOut, responses=_WRITE_ERRORS)
def start_job(technician_id: str, job_id: str, session: SessionDep) -> JobOut:
    """Arriving on site: moves the job to in-progress. Starting a job already
    under way changes nothing."""
    now = datetime.now(UTC)
    field.start_job(session, job_id, technician_id, now)
    return _committed_job(session, job_id, technician_id, now)


@router.post("/jobs/{job_id}/complete", response_model=JobOut, responses=_WRITE_ERRORS)
def complete_job(
    technician_id: str, job_id: str, body: CompleteJobIn, session: SessionDep
) -> JobOut:
    """Closes a started job. Needs at least two photos of the work."""
    now = datetime.now(UTC)
    field.complete_job(
        session, job_id, technician_id, now, notes=body.notes, photos=body.photos
    )
    return _committed_job(session, job_id, technician_id, now)


@router.post(
    "/jobs/{job_id}/hand-back", response_model=ServiceRequestOut, responses=_WRITE_ERRORS
)
def hand_back_job(
    technician_id: str, job_id: str, body: HandBackIn, session: SessionDep
) -> ServiceRequestOut:
    """'Can't do it': the job goes back to the office unassigned, with the
    reason. It's no longer this technician's, so the request comes back
    rather than a job."""
    field.hand_back_job(session, job_id, technician_id, datetime.now(UTC), reason=body.reason)
    session.commit()
    session.expire_all()
    return ServiceRequestOut.from_model(session.get_one(ServiceRequest, job_id))


def _committed_job(session: Session, job_id: str, technician_id: str, now: datetime) -> JobOut:
    session.commit()
    session.expire_all()
    return field.get_job(session, job_id, technician_id, now)
