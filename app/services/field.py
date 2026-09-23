"""The technician field app's reads: one person's own open work.

Ports `getWorklist`, `getJob` and the repeat-fault rule from the frontend's
operations.ts. Everything here is derived when read: the order of the
worklist, its counts, and the repeat-fault flag. Nothing is stored.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import CompletionPhoto, ServiceRequest, StageEntry, Staff, Unit
from app.models.enums import Stage
from app.schemas.field import JobOut, PhotoIn, RepeatFaultOut, WorklistCounts, WorklistOut
from app.schemas.operations import StaffOut
from app.services.errors import Conflict, Forbidden, Invalid, NotFound

# A unit that has failed the same way this many times within this window is
# flagged: the repair that keeps not holding is a different job from the one
# on the ticket. The same rule the ops portal flags a unit with.
REPEAT_FAULT_WITHIN = timedelta(days=240)
REPEAT_FAULT_OCCURRENCES = 3

# Work already started outranks work that hasn't been: a technician standing
# in the unit finishes what they're holding.
_WORK_ORDER: dict[Stage, int] = {"in-progress": 0, "assigned": 1, "submitted": 2}


def get_worklist(session: Session, technician_id: str, now: datetime) -> WorklistOut:
    technician = session.get(Staff, technician_id)
    if technician is None:
        raise NotFound(f"No technician {technician_id}")

    mine = _requests(session, ServiceRequest.assignee_id == technician_id)
    faults = _repeat_faults(session, mine, now)
    jobs = [JobOut.from_model(r, repeat_fault=faults.get(r.id)) for r in mine]

    open_jobs = sorted((j for j in jobs if j.stage != "done"), key=_work_order)
    closed = sorted((j for j in jobs if j.stage == "done"), key=_done_at, reverse=True)

    return WorklistOut(
        technician=StaffOut.model_validate(technician),
        next=open_jobs[0] if open_jobs else None,
        queued=open_jobs[1:],
        closed=closed,
        counts=WorklistCounts(
            left=len(open_jobs),
            urgent=sum(j.priority == "urgent" for j in open_jobs),
            closed=len(closed),
        ),
    )


def get_job(session: Session, job_id: str, technician_id: str, now: datetime) -> JobOut:
    """One job, only for the technician holding it. A job reassigned out from
    under someone stops resolving for them. Closed jobs stay readable."""
    if session.get(Staff, technician_id) is None:
        raise NotFound(f"No technician {technician_id}")
    found = _requests(session, ServiceRequest.id == job_id)
    if not found:
        raise NotFound(f"No job {job_id}")
    request = found[0]
    if request.assignee_id != technician_id:
        raise Forbidden(f"{job_id} isn't assigned to you")

    faults = _repeat_faults(session, [request], now)
    return JobOut.from_model(request, repeat_fault=faults.get(request.id))


def _requests(session: Session, *where) -> Sequence[ServiceRequest]:
    """Requests with everything a job shows, loaded up front: one query per
    relationship rather than one per request."""
    return session.scalars(
        select(ServiceRequest)
        .where(*where)
        .options(
            selectinload(ServiceRequest.stage_history),
            selectinload(ServiceRequest.photos),
            selectinload(ServiceRequest.completion_photos),
            selectinload(ServiceRequest.unit).selectinload(Unit.property),
            selectinload(ServiceRequest.tenant),
            selectinload(ServiceRequest.assignee),
        )
    ).all()


def _work_order(job: JobOut) -> tuple:
    """Started first, then emergencies, then oldest. A request carries no
    duration, so nothing else about the order is knowable. The id only makes
    ties come out the same way every time."""
    return (_WORK_ORDER.get(job.stage, 3), job.priority != "urgent", job.created_at, job.id)


def _done_at(job: JobOut) -> datetime:
    return next(e.at for e in job.stage_history if e.stage == "done")


def _repeat_faults(
    session: Session, requests: Sequence[ServiceRequest], now: datetime
) -> dict[str, RepeatFaultOut]:
    """The repeat fault each maintenance request's unit shows in the request's
    own category, keyed by request id. Requests without one are left out."""
    unit_ids = {r.unit_id for r in requests if r.type == "maintenance"}
    if not unit_ids:
        return {}

    # Every maintenance request on those units, oldest first: one query for
    # the whole worklist.
    history = session.execute(
        select(
            ServiceRequest.unit_id,
            ServiceRequest.category,
            ServiceRequest.created_at,
            ServiceRequest.stage,
            ServiceRequest.charge,
        )
        .where(ServiceRequest.unit_id.in_(unit_ids), ServiceRequest.type == "maintenance")
        .order_by(ServiceRequest.created_at, ServiceRequest.id)
    ).all()

    by_unit = {unit_id: _repeat_fault([h for h in history if h.unit_id == unit_id], now) for unit_id in unit_ids}
    return {
        r.id: fault
        for r in requests
        if r.type == "maintenance"
        and (fault := by_unit[r.unit_id]) is not None
        and fault.category == r.category
    }


def _repeat_fault(history: Sequence, now: datetime) -> RepeatFaultOut | None:
    """The unit's most frequent category within the window, if it has come up
    often enough. `spend` counts that category's charged work over all time."""
    cutoff = now - REPEAT_FAULT_WITHIN
    counts = Counter(h.category for h in history if h.created_at >= cutoff)
    if not counts:
        return None

    # most_common keeps first-seen order among ties; history is oldest first.
    category, count = counts.most_common(1)[0]
    if count < REPEAT_FAULT_OCCURRENCES:
        return None

    spend = sum(
        h.charge for h in history if h.category == category and h.stage == "done" and h.charge
    )
    return RepeatFaultOut(category=category, count=count, spend=float(spend))


# --- Writes ------------------------------------------------------------------
# A technician only ever touches work that's theirs, and each write checks that
# itself rather than trusting the caller. The router commits.

# How many photos close a job, and how many of those are compulsory: closing
# work with no evidence of it is the thing the finish screen exists to prevent.
REQUIRED_COMPLETION_PHOTOS = 2
MAX_COMPLETION_PHOTOS = 10


def start_job(session: Session, job_id: str, technician_id: str, now: datetime) -> None:
    """Arriving on site. Idempotent: starting a job already under way is the
    technician confirming where they are, not a second event."""
    request = _held_by(session, job_id, technician_id)
    if request.stage != "in-progress":
        _reach_stage(request, "in-progress", now)


def complete_job(
    session: Session,
    job_id: str,
    technician_id: str,
    now: datetime,
    *,
    notes: str | None,
    photos: Sequence[PhotoIn],
) -> None:
    """Closing a job. It takes photos of the work and can't set a price: a
    housekeeping booking has carried its charge since it was booked, and
    maintenance is never billed, so `charge` is deliberately untouched."""
    request = _held_by(session, job_id, technician_id)
    if request.stage != "in-progress":
        raise Conflict("Start the job before closing it")

    evidence = [p for p in photos if p.data_url]
    if len(evidence) < REQUIRED_COMPLETION_PHOTOS:
        raise Invalid(f"{REQUIRED_COMPLETION_PHOTOS} photos are needed to close a job")

    # Kept apart from `photos`, which are the fault as it was reported.
    request.completion_photos = [
        CompletionPhoto(position=i, name=p.name or "Photo", data_url=p.data_url)
        for i, p in enumerate(evidence[:MAX_COMPLETION_PHOTOS])
    ]
    request.completion_notes = (notes or "").strip() or None
    _reach_stage(request, "done", now)


def hand_back_job(
    session: Session, job_id: str, technician_id: str, now: datetime, *, reason: str | None
) -> None:
    """'Can't do it': not a refusal and not a new stage. The job goes back to
    unassigned `submitted`, which is where the ops queue reads its pressure
    from, carrying why. Its history loses everything after `submitted`, so it
    never reads as having reached a stage it's now behind."""
    request = _held_by(session, job_id, technician_id)
    reason = (reason or "").strip()
    if not reason:
        raise Invalid("Say why you can't do it")

    technician = session.get(Staff, technician_id)
    request.hand_back_reason = reason
    request.hand_back_by = technician_id
    request.hand_back_by_name = technician.name if technician else None
    request.hand_back_at = now
    request.assignee_id = None
    request.stage_history = [e for e in request.stage_history if e.stage == "submitted"]


def _held_by(session: Session, job_id: str, technician_id: str) -> ServiceRequest:
    """The job, if this technician holds it and it's still open. The row is
    locked until the transaction ends, so two taps can't both act on it."""
    if session.get(Staff, technician_id) is None:
        raise NotFound(f"No technician {technician_id}")
    request = session.scalars(
        select(ServiceRequest)
        .where(ServiceRequest.id == job_id)
        .options(selectinload(ServiceRequest.stage_history))
        .with_for_update(of=ServiceRequest)
    ).one_or_none()
    if request is None:
        raise NotFound(f"No job {job_id}")
    if request.assignee_id != technician_id:
        raise Forbidden("That job is not yours to change")
    if request.stage == "done":
        raise Conflict("That job is already closed")
    return request


def _reach_stage(request: ServiceRequest, stage: Stage, at: datetime) -> None:
    """A stage is reached once: reaching it again moves its time rather than
    adding a second entry."""
    existing = next((e for e in request.stage_history if e.stage == stage), None)
    if existing:
        existing.at = at
    else:
        request.stage_history.append(StageEntry(stage=stage, at=at))
