"""The admin and tenant portals' writes: raising, assigning, prioritising and
removing requests, and managing the rate card and the roster.

Each ports a write from the frontend's operations.ts, with its checks in the
same order and its messages word for word, because the portals show them as
they are. Staff and rates are retired rather than deleted, so closed work
still says who did it and what the service was called. Nothing here commits:
the router does.
"""

import math
import re
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import HousekeepingRate, Photo, ServiceRequest, StageEntry, Staff, Unit
from app.models.enums import MAINTENANCE_CATEGORIES, RequestType
from app.schemas.portfolio import NewRequestIn, RateIn, StaffIn
from app.services.common import reach_stage
from app.services.errors import Conflict, Invalid, NotFound

# How many photos one request carries. They're inlined as data URLs until
# Phase 8 gives them a file store, so the cap keeps rows a sensible size.
MAX_REQUEST_PHOTOS = 4

# The hour marks a visit's window can start or end on. A schedule is an
# absolute from–to pair an admin chose, not a duration.
VISIT_HOURS = ["8AM", "9AM", "10AM", "11AM", "12PM", "1PM", "2PM", "3PM", "4PM", "5PM", "6PM", "7PM", "8PM"]

# Serialises request numbering: two requests raised at once would otherwise
# both read the same highest number. Any fixed number works as long as
# nothing else uses it.
_REQUEST_ID_LOCK = 7_210_001


def is_valid_slot(slot: str) -> bool:
    """A slot is "<from>–<to>", both visit hours, starting before it ends."""
    parts = slot.split("–")
    start, end = parts[0], parts[1] if len(parts) > 1 else ""
    return start in VISIT_HOURS and end in VISIT_HOURS and VISIT_HOURS.index(start) < VISIT_HOURS.index(end)


# --- Requests ------------------------------------------------------------------


def create_request(session: Session, body: NewRequestIn, now: datetime) -> ServiceRequest:
    """`createRequest`. The category decides the trade; a housekeeping
    booking takes the rate card's current price and keeps it, and has no
    emergency tier."""
    unit = session.get(Unit, body.unit_id)
    if unit is None:
        raise Invalid(f"Unknown unit {body.unit_id}")
    if not body.summary.strip():
        raise Invalid("A request needs a summary")

    maintenance = body.category in MAINTENANCE_CATEGORIES
    rate = None if maintenance else session.get(HousekeepingRate, body.category)
    if not maintenance and rate is None:
        raise Invalid(f"Unknown category {body.category}")
    if body.scheduled_slot and not is_valid_slot(body.scheduled_slot):
        raise Invalid(f"Unknown time slot {body.scheduled_slot}")
    if rate is not None and rate.retired_at is not None:
        raise Invalid(f"{rate.label} is no longer on the rate card")

    type: RequestType = "maintenance" if maintenance else "housekeeping"
    photos = [p for p in body.photos if p.data_url][:MAX_REQUEST_PHOTOS]
    scheduled = body.scheduled_date is not None and bool(body.scheduled_slot)

    request = ServiceRequest(
        id=_next_request_id(session),
        unit_id=unit.id,
        tenant_id=unit.tenant_id,
        type=type,
        category=body.category,
        priority="normal" if type == "housekeeping" else body.priority,
        summary=body.summary.strip(),
        description=body.description.strip(),
        assignee_id=None,
        origin=body.origin,
        charge=rate.price if rate else None,
        completion_notes=None,
        schedule_date=body.scheduled_date if scheduled else None,
        schedule_slot=body.scheduled_slot if scheduled else None,
        stage_history=[StageEntry(stage="submitted", at=now)],
        photos=[Photo(position=i, name=p.name or "Photo", data_url=p.data_url) for i, p in enumerate(photos)],
    )
    session.add(request)
    session.flush()

    # Assigning at creation is the same move as assigning from the queue.
    if body.assignee_id:
        assign_requests(session, [request.id], body.assignee_id, now)
    return request


def _next_request_id(session: Session) -> str:
    """'REQ-' and one more than the highest number any request id carries."""
    session.execute(select(func.pg_advisory_xact_lock(_REQUEST_ID_LOCK)))
    ids = session.scalars(select(ServiceRequest.id)).all()
    highest = max((int(d) for i in ids if (d := re.sub(r"\D", "", i))), default=0)
    return f"REQ-{highest + 1}"


def assign_requests(
    session: Session, ids: Sequence[str], assignee_id: str, now: datetime
) -> list[ServiceRequest]:
    """`assignRequests`: the one action that also moves a request forward.
    Unassigned work is "submitted"; the moment someone holds it, it's
    "assigned". Work already in flight is handed over, not sent backwards.
    Closed work and unknown ids are skipped; what was touched comes back."""
    member = session.get(Staff, assignee_id)
    if member is None or member.retired_at is not None:
        raise Invalid(f"Unknown staff member {assignee_id}")

    touched = []
    for request in _locked(session, ids):
        if request.stage == "done":
            continue
        request.assignee_id = assignee_id
        if request.stage == "submitted":
            reach_stage(request, "assigned", now)
        else:
            assigned = next((e for e in request.stage_history if e.stage == "assigned"), None)
            if assigned:
                assigned.at = now
        touched.append(request)
    return touched


def set_priority(session: Session, ids: Sequence[str], priority: str) -> list[ServiceRequest]:
    """`setPriority`. Unknown ids are skipped."""
    if priority not in ("urgent", "normal"):
        raise Invalid(f"Unknown priority {priority}")

    requests = _locked(session, ids)
    if priority == "urgent" and any(r.type == "housekeeping" for r in requests):
        # The frontend never checked this; the database refuses it.
        raise Invalid("Housekeeping is booked into a slot and has no emergency tier")
    for request in requests:
        request.priority = priority
    return list(requests)


def delete_requests(session: Session, ids: Sequence[str]) -> list[ServiceRequest]:
    """`deleteRequests`: out of the queue entirely, history and photos with
    it, and its charges leave the rollups. Returns what was removed, as it
    was."""
    removed = list(_locked(session, [i for i in ids if i]))
    for request in removed:
        session.delete(request)
    return removed


def _locked(session: Session, ids: Sequence[str]) -> Sequence[ServiceRequest]:
    """The requests with these ids, in the order given, locked until the
    transaction ends."""
    found = {
        r.id: r
        for r in session.scalars(
            select(ServiceRequest)
            .where(ServiceRequest.id.in_(ids))
            .options(selectinload(ServiceRequest.stage_history))
            .with_for_update(of=ServiceRequest)
        )
    }
    return [found[i] for i in dict.fromkeys(ids) if i in found]


# --- The rate card -------------------------------------------------------------


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.strip().lower()).strip("-")


def add_rate(session: Session, body: RateIn) -> HousekeepingRate:
    """`addHousekeepingRate`. A service retired earlier under the same name
    comes back with the new price rather than being refused."""
    label = (body.label or "").strip()
    service_type = _slug(label)
    if not service_type:
        raise Invalid("A rate needs a service name")

    amount = _number(body.price)
    if not math.isfinite(amount) or amount <= 0:
        raise Invalid("A rate needs a price above zero")

    existing = session.get(HousekeepingRate, service_type)
    if (existing is not None and existing.retired_at is None) or service_type in MAINTENANCE_CATEGORIES:
        raise Invalid(f"{label} is already a service")

    last = session.scalar(select(func.max(HousekeepingRate.position)))
    position = 0 if last is None else last + 1
    price = math.floor(amount + 0.5)  # Math.round: halves go up
    if existing is not None:
        existing.label, existing.price, existing.position, existing.retired_at = label, price, position, None
        return existing
    rate = HousekeepingRate(service_type=service_type, label=label, price=price, position=position)
    session.add(rate)
    return rate


def _number(value: float | str | None) -> float:
    """JavaScript's `Number()`: blank is 0, anything unreadable is NaN."""
    if value is None:
        return 0.0
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return 0.0
        try:
            return float(value)
        except ValueError:
            return math.nan
    return float(value)


def retire_rate(session: Session, service_type: str, now: datetime) -> HousekeepingRate:
    """`removeHousekeepingRate`: only once nothing open is priced against it.
    A tenant can't be left mid-service with a rate the card no longer lists."""
    rate = session.get(HousekeepingRate, service_type)
    if rate is None or rate.retired_at is not None:
        raise NotFound(f"Unknown service {service_type}")

    open_bookings = session.scalar(
        select(func.count())
        .select_from(ServiceRequest)
        .where(ServiceRequest.category == service_type, ServiceRequest.stage != "done")
    ) or 0
    if open_bookings:
        uses = "booking uses" if open_bookings == 1 else "bookings use"
        raise Conflict(f"{open_bookings} open {uses} this service — close them first")

    rate.retired_at = now
    return rate


# --- The roster --------------------------------------------------------------------

_ROLES: tuple[RequestType, ...] = ("maintenance", "housekeeping")
_DIALLABLE = re.compile(r"^\+?[0-9\s()-]+$", re.ASCII)


def _clean_phone(phone: str | None) -> str:
    """Loose on purpose: numbers arrive in local and international formats,
    so this only refuses what can't be dialled at all."""
    trimmed = (phone or "").strip()
    digits = re.sub(r"[^0-9]", "", trimmed)
    if not _DIALLABLE.match(trimmed) or not 7 <= len(digits) <= 15:
        raise Invalid("Enter a valid mobile number")
    return trimmed


def _checked(body: StaffIn) -> tuple[str, RequestType, str]:
    name = (body.name or "").strip()
    if not name:
        raise Invalid("A staff member needs a name")
    if body.role not in _ROLES:
        raise Invalid(f"Unknown trade {body.role}")
    return name, body.role, _clean_phone(body.phone)  # type: ignore[return-value]


def add_staff(session: Session, body: StaffIn) -> Staff:
    """`addStaff`."""
    name, role, phone = _checked(body)
    member = Staff(id=_staff_id(session, name), name=name, phone=phone, role=role, photo=body.photo)
    session.add(member)
    return member


def _staff_id(session: Session, name: str) -> str:
    """'stf-' and their last name, numbered if taken (retired staff keep
    theirs, so their ids are taken too)."""
    base = next((part for part in reversed(_slug(name).split("-")) if part), "member")
    taken = set(session.scalars(select(Staff.id).where(Staff.id.like(f"stf-{base}%"))))
    candidate, suffix = f"stf-{base}", 2
    while candidate in taken:
        candidate, suffix = f"stf-{base}-{suffix}", suffix + 1
    return candidate


def update_staff(session: Session, staff_id: str, body: StaffIn) -> Staff:
    """`updateStaff`. Leaving `photo` out keeps the one they have."""
    member = _current_member(session, staff_id)
    member.name, member.role, member.phone = _checked(body)
    if body.photo:
        member.photo = body.photo
    return member


def retire_staff(session: Session, staff_id: str, now: datetime) -> Staff:
    """`removeStaff`: only once they hold no open work, which has to be
    somewhere. Closed work keeps them as its assignee."""
    member = _current_member(session, staff_id)
    held = session.scalar(
        select(func.count())
        .select_from(ServiceRequest)
        .where(ServiceRequest.assignee_id == staff_id, ServiceRequest.stage != "done")
    ) or 0
    if held:
        raise Conflict(
            "1 open request is still assigned — reassign it first"
            if held == 1
            else f"{held} open requests are still assigned — reassign them first"
        )
    member.retired_at = now
    return member


def _current_member(session: Session, staff_id: str) -> Staff:
    member = session.get(Staff, staff_id)
    if member is None or member.retired_at is not None:
        raise NotFound(f"Unknown staff member {staff_id}")
    return member
