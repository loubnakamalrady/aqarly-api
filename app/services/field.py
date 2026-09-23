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

from app.models import ServiceRequest, Staff, Unit
from app.models.enums import Stage
from app.schemas.field import JobOut, RepeatFaultOut, WorklistCounts, WorklistOut
from app.schemas.operations import StaffOut
from app.services.errors import Forbidden, NotFound

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
