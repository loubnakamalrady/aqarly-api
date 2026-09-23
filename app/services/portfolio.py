"""The admin and tenant portals' reads: the request queue, units, rollups, the
dashboard, the roster and assignment candidates.

Each ports a read from the frontend's operations.ts and names it. Everything
is derived when read (spend, load, counts, the repeat-fault flag, scores);
nothing here is stored. Category labels and other wording stay in the
frontend.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import HousekeepingRate, Property, ServiceRequest, StageEntry, Staff, Tenant, Unit
from app.models.enums import STAGES, Priority, RequestType, Stage
from app.schemas.operations import (
    EnrichedRequestOut,
    PropertyOut,
    StaffOut,
    TenantOut,
    UnitOut,
)
from app.schemas.portfolio import (
    CandidateOut,
    CategoryRollupOut,
    DashboardOut,
    MonthTotal,
    PropertyRollupOut,
    RateOut,
    RosterMemberOut,
    StageCount,
    TenantAccountOut,
    UnitRecordOut,
)
from app.services.common import (
    STAFF_CAPACITY,
    ReportPeriod,
    charged,
    enriched_requests,
    load_requests,
    raised_since,
    repeat_fault,
)
from app.services.errors import NotFound

RequestSort = Literal["age", "newest"]
Tier = Literal["emergency", "standard"]

# --- Requests ------------------------------------------------------------------


def list_requests(
    session: Session,
    *,
    stage: Stage | None = None,
    type: RequestType | None = None,
    category: str | None = None,
    priority: Priority | None = None,
    tier: Tier | None = None,
    property_id: str | None = None,
    unit_id: str | None = None,
    assignee_id: str | None = None,
    tenant_id: str | None = None,
    open: bool = False,
    search: str | None = None,
    sort: RequestSort = "age",
) -> list[EnrichedRequestOut]:
    """`getRequests`. Every filter is optional and they narrow together.
    `assignee_id="unassigned"` means held by no one. `search` matches the
    ref, summary, unit, building, tenant and assignee, ignoring case."""
    query = enriched_requests()
    if stage:
        query = query.where(ServiceRequest.stage == stage)
    if type:
        query = query.where(ServiceRequest.type == type)
    if category:
        query = query.where(ServiceRequest.category == category)
    if priority:
        query = query.where(ServiceRequest.priority == priority)
    if tier:
        query = query.where(ServiceRequest.priority == ("urgent" if tier == "emergency" else "normal"))
    if unit_id:
        query = query.where(ServiceRequest.unit_id == unit_id)
    if assignee_id == "unassigned":
        query = query.where(ServiceRequest.assignee_id.is_(None))
    elif assignee_id:
        query = query.where(ServiceRequest.assignee_id == assignee_id)
    if tenant_id:
        query = query.where(ServiceRequest.tenant_id == tenant_id)
    if open:
        query = query.where(ServiceRequest.stage != "done")

    term = (search or "").strip().lower()
    if property_id or term:
        unit, prop = aliased(Unit), aliased(Property)
        tenant, assignee = aliased(Tenant), aliased(Staff)
        query = query.join(unit, ServiceRequest.unit_id == unit.id).join(prop, unit.property_id == prop.id)
        if property_id:
            query = query.where(prop.id == property_id)
        if term:
            query = query.outerjoin(tenant, ServiceRequest.tenant_id == tenant.id).outerjoin(
                assignee, ServiceRequest.assignee_id == assignee.id
            )
            query = query.where(
                or_(
                    *(
                        func.lower(column).contains(term, autoescape=True)
                        for column in (
                            ServiceRequest.id,
                            ServiceRequest.summary,
                            unit.label,
                            prop.name,
                            tenant.name,
                            assignee.name,
                        )
                    )
                )
            )

    if sort == "newest":
        query = query.order_by(None).order_by(ServiceRequest.created_at.desc(), ServiceRequest.id.desc())

    return [EnrichedRequestOut.from_model(r) for r in session.scalars(query)]


def get_request(session: Session, request_id: str) -> EnrichedRequestOut:
    """`getRequestById`."""
    found = load_requests(session, ServiceRequest.id == request_id)
    if not found:
        raise NotFound(f"No request {request_id}")
    return EnrichedRequestOut.from_model(found[0])


# --- Units -------------------------------------------------------------------------


def list_units(
    session: Session, now: datetime, *, property_id: str | None = None, type: RequestType = "maintenance"
) -> list[UnitRecordOut]:
    """`getUnits`: busiest first, then by building and label. Counts and
    spend read off one trade's work, since each portal manages one."""
    query = select(Unit).join(Unit.property)
    if property_id:
        query = query.where(Unit.property_id == property_id)
    units = session.scalars(query).all()
    records = _unit_records(session, units, now, type)
    return sorted(records, key=lambda u: (-u.open_count, u.property.name, u.label))


def get_unit(session: Session, unit_id: str, now: datetime, *, type: RequestType = "maintenance") -> UnitRecordOut:
    """`getUnitById`."""
    unit = session.get(Unit, unit_id)
    if unit is None:
        raise NotFound(f"No unit {unit_id}")
    return _unit_records(session, [unit], now, type)[0]


def _unit_records(
    session: Session, units: Sequence[Unit], now: datetime, type: RequestType
) -> list[UnitRecordOut]:
    unit_ids = [u.id for u in units]
    history = session.execute(
        select(
            ServiceRequest.unit_id,
            ServiceRequest.category,
            ServiceRequest.created_at,
            ServiceRequest.stage,
            ServiceRequest.charge,
            _done_at(),
        )
        .where(ServiceRequest.unit_id.in_(unit_ids), ServiceRequest.type == type)
        .order_by(ServiceRequest.created_at, ServiceRequest.id)
    ).all()
    by_unit: dict[str, list] = defaultdict(list)
    for h in history:
        by_unit[h.unit_id].append(h)

    records = []
    for unit in units:
        requests = by_unit[unit.id]
        done_times = [h.done_at for h in requests if h.stage == "done" and h.done_at]
        records.append(
            UnitRecordOut.model_validate(
                {
                    **UnitOut.model_validate(unit).model_dump(),
                    "property": unit.property,
                    "tenant": unit.tenant,
                    "request_count": len(requests),
                    "open_count": sum(h.stage != "done" for h in requests),
                    "lifetime_spend": float(sum(charged(h.stage, h.charge) for h in requests)),
                    "last_serviced_at": max(done_times, default=None),
                    "repeat_fault": repeat_fault(requests, now) if type == "maintenance" else None,
                }
            )
        )
    return records


def _done_at():
    """When a request was closed, as a column: a subquery on its history."""
    return (
        select(StageEntry.at)
        .where(StageEntry.request_id == ServiceRequest.id, StageEntry.stage == "done")
        .correlate(ServiceRequest)
        .scalar_subquery()
        .label("done_at")
    )


# --- Buildings and reports ---------------------------------------------------------


def list_properties(session: Session) -> list[PropertyOut]:
    """`getProperties` (buildings, not listings), by name."""
    return [PropertyOut.model_validate(p) for p in session.scalars(select(Property).order_by(Property.name))]


def property_rollups(
    session: Session, now: datetime, *, period: ReportPeriod | None = None, type: RequestType = "maintenance"
) -> list[PropertyRollupOut]:
    """`getPropertyRollups`: cost and volume by building, most spend first.
    `requests` and `spend` count what was raised in the period; `open` and
    `unassigned` count open work whenever it came in."""
    since = raised_since(period, now)
    unit_counts = dict(
        session.execute(select(Unit.property_id, func.count()).group_by(Unit.property_id)).tuples().all()
    )
    rows = session.execute(
        select(
            Unit.property_id,
            ServiceRequest.stage,
            ServiceRequest.assignee_id,
            ServiceRequest.created_at,
            ServiceRequest.charge,
        )
        .join(Unit, ServiceRequest.unit_id == Unit.id)
        .where(ServiceRequest.type == type)
    ).all()
    by_property: dict[str, list] = defaultdict(list)
    for row in rows:
        by_property[row.property_id].append(row)

    rollups = []
    for prop in session.scalars(select(Property)):
        requests = by_property[prop.id]
        open_ = [r for r in requests if r.stage != "done"]
        raised = [r for r in requests if since is None or r.created_at >= since]
        spend = sum(charged(r.stage, r.charge) for r in raised)
        units = unit_counts.get(prop.id, 0)
        rollups.append(
            PropertyRollupOut(
                **PropertyOut.model_validate(prop).model_dump(),
                units=units,
                requests=len(raised),
                open=len(open_),
                unassigned=sum(r.assignee_id is None for r in open_),
                spend=float(spend),
                spend_per_unit=float(spend / units) if units else 0.0,
            )
        )
    # Ties go to the building's name, so they come out the same way every time.
    return sorted(rollups, key=lambda r: (-r.spend, -r.requests, r.name))


def category_rollups(
    session: Session, now: datetime, *, period: ReportPeriod | None = None, type: RequestType = "maintenance"
) -> list[CategoryRollupOut]:
    """`getCategoryRollups` without labels: volume and spend per category
    raised in the period, busiest first, then first raised first."""
    since = raised_since(period, now)
    query = (
        select(ServiceRequest.category, ServiceRequest.stage, ServiceRequest.charge)
        .where(ServiceRequest.type == type)
        .order_by(ServiceRequest.created_at, ServiceRequest.id)
    )
    if since is not None:
        query = query.where(ServiceRequest.created_at >= since)

    totals: dict[str, list[Decimal | int]] = {}
    for row in session.execute(query):
        entry = totals.setdefault(row.category, [0, Decimal(0)])
        entry[0] += 1
        entry[1] += charged(row.stage, row.charge)
    ordered = sorted(totals.items(), key=lambda item: -item[1][0])  # stable: first seen first
    return [CategoryRollupOut(category=c, requests=int(n), spend=float(s)) for c, (n, s) in ordered]


def dashboard(
    session: Session, now: datetime, *, period: ReportPeriod | None = None, type: RequestType = "maintenance"
) -> DashboardOut:
    """`getDashboardStats`. One trade at a time: maintenance spend is the
    landlord's and housekeeping is billed on, so they never share a
    dashboard."""
    since = raised_since(period, now)
    rows = session.execute(
        select(
            ServiceRequest.stage,
            ServiceRequest.priority,
            ServiceRequest.assignee_id,
            ServiceRequest.created_at,
            ServiceRequest.charge,
            Unit.property_id,
            _done_at(),
        )
        .join(Unit, ServiceRequest.unit_id == Unit.id)
        .where(ServiceRequest.type == type)
    ).all()
    open_ = [r for r in rows if r.stage != "done"]
    closed = [r for r in rows if r.stage == "done"]
    raised = [r for r in rows if since is None or r.created_at >= since]
    spend = float(sum(charged(r.stage, r.charge) for r in raised))
    urgent_open = [r for r in open_ if r.priority == "urgent"]

    # Charges by the month the work was closed in, over all time: the cost
    # trend.
    months: dict[str, Decimal] = defaultdict(Decimal)
    for r in closed:
        if r.charge and r.done_at:
            months[r.done_at.strftime("%Y-%m")] += r.charge

    return DashboardOut(
        open=len(open_),
        unassigned=sum(r.assignee_id is None for r in open_),
        in_progress=sum(r.stage == "in-progress" for r in open_),
        closed=len(closed),
        by_stage=[StageCount(stage=s, count=sum(r.stage == s for r in rows)) for s in STAGES],
        raised=len(raised),
        urgent_open=len(urgent_open),
        urgent_buildings=len({r.property_id for r in urgent_open}),
        maintenance_spend=spend,
        period_spend=spend,
        cost_trend=[MonthTotal(month=m, total=float(t)) for m, t in sorted(months.items())],
    )


# --- Staff -------------------------------------------------------------------------


def roster(session: Session, *, type: RequestType = "maintenance") -> list[RosterMemberOut]:
    """`getStaffRoster`: one trade's current staff with the load they hold,
    heaviest first, then by name. Retired staff are not on it."""
    members = session.scalars(
        select(Staff).where(Staff.role == type, Staff.retired_at.is_(None))
    ).all()
    held = session.execute(
        select(ServiceRequest.assignee_id, ServiceRequest.stage, Property)
        .join(Unit, ServiceRequest.unit_id == Unit.id)
        .join(Property, Unit.property_id == Property.id)
        .where(ServiceRequest.assignee_id.in_([m.id for m in members]))
        .order_by(ServiceRequest.created_at, ServiceRequest.id)
    ).all()

    result = []
    for member in members:
        mine = [h for h in held if h.assignee_id == member.id]
        open_ = [h for h in mine if h.stage != "done"]
        buildings: dict[str, Property] = {}
        for h in mine:
            buildings.setdefault(h.Property.id, h.Property)
        result.append(
            RosterMemberOut(
                **StaffOut.model_validate(member).model_dump(),
                load=len(open_),
                capacity=STAFF_CAPACITY,
                in_progress=sum(h.stage == "in-progress" for h in open_),
                closed=sum(h.stage == "done" for h in mine),
                properties=[PropertyOut.model_validate(p) for p in buildings.values()],
            )
        )
    return sorted(result, key=lambda m: (-m.load, m.name))


def assignment_candidates(session: Session, request_id: str) -> list[CandidateOut]:
    """`getAssignmentCandidates`: who could take this request. The trade has
    to match; then anyone at capacity drops, anyone already working the
    building rises, and a lighter day breaks the rest."""
    request = get_request(session, request_id)
    candidates = []
    for member in roster(session, type=request.type):
        in_building = any(p.id == request.property.id for p in member.properties)
        at_capacity = member.load >= member.capacity
        candidates.append(
            CandidateOut(
                **member.model_dump(),
                in_building=in_building,
                at_capacity=at_capacity,
                is_current=member.id == request.assignee_id,
                score=(-100 if at_capacity else 0) + (10 if in_building else 0) - member.load,
            )
        )
    return sorted(candidates, key=lambda c: -c.score)  # stable: roster order breaks ties


def get_staff(session: Session, staff_id: str) -> StaffOut:
    member = session.get(Staff, staff_id)
    if member is None:
        raise NotFound(f"No staff member {staff_id}")
    return StaffOut.model_validate(member)


# --- Tenants and the rate card ------------------------------------------------------


def get_tenant(session: Session, tenant_id: str) -> TenantAccountOut:
    """`getTenantById`: a tenant with the unit they occupy, if any."""
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound(f"No tenant {tenant_id}")
    unit = session.scalars(select(Unit).where(Unit.tenant_id == tenant_id).order_by(Unit.id)).first()
    return TenantAccountOut(
        **TenantOut.model_validate(tenant).model_dump(),
        unit=UnitOut.model_validate(unit) if unit else None,
        property=PropertyOut.model_validate(unit.property) if unit else None,
    )


def list_rates(session: Session, *, include_retired: bool = False) -> list[RateOut]:
    """`getHousekeepingRates`: the card in the order it's listed. With
    `include_retired`, also the services that have left it, so old bookings
    can still be named."""
    query = select(HousekeepingRate).order_by(HousekeepingRate.position)
    if not include_retired:
        query = query.where(HousekeepingRate.retired_at.is_(None))
    return [RateOut.model_validate(r) for r in session.scalars(query)]

