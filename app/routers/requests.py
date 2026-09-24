"""Service requests, as the ops, housekeeping and tenant portals use them.

Who may do what: an admin works in their own portal's trade only; a tenant
sees their own requests and raises them for their own home. Query parameters
are camelCase (`propertyId`), like everything the frontend sends. Bulk actions
take a list of ids and return the requests they touched, which is how the
portals word their confirmations ("3 requests assigned").
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import AdminPrincipal, SessionDep, SignedIn, own_trade
from app.models import ServiceRequest, Staff
from app.models.enums import MAINTENANCE_CATEGORIES, Priority, RequestType, Stage
from app.schemas.common import ErrorOut
from app.schemas.operations import EnrichedRequestOut, ServiceRequestOut
from app.schemas.portfolio import AssignIn, CandidateOut, NewRequestIn, PriorityIn, RequestIdsIn
from app.services import admin, portfolio
from app.services.auth import Principal
from app.services.common import load_requests
from app.services.errors import Forbidden, Invalid
from app.services.portfolio import RequestSort, Tier

router = APIRouter(prefix="/requests", tags=["requests"])

_REFUSALS = {code: {"model": ErrorOut} for code in (400, 401, 403, 404, 409)}


@router.get("", response_model=list[EnrichedRequestOut], responses=_REFUSALS)
def list_requests(
    who: SignedIn,
    session: SessionDep,
    stage: Stage | None = None,
    request_type: Annotated[RequestType | None, Query(alias="type")] = None,
    category: str | None = None,
    priority: Priority | None = None,
    tier: Tier | None = None,
    property_id: Annotated[str | None, Query(alias="propertyId")] = None,
    unit_id: Annotated[str | None, Query(alias="unitId")] = None,
    assignee_id: Annotated[str | None, Query(alias="assigneeId", description='A staff id, or "unassigned".')] = None,
    tenant_id: Annotated[str | None, Query(alias="tenantId")] = None,
    open: bool = False,
    search: str | None = None,
    sort: RequestSort = "age",
) -> list[EnrichedRequestOut]:
    """The queue: `getRequests`. Filters narrow together; oldest first unless
    `sort=newest`. An admin gets their own trade; a tenant, their own
    requests."""
    if _admin_or_tenant(who) == "admin":
        request_type = own_trade(who, request_type)  # type: ignore[assignment]
    else:
        if tenant_id is not None and tenant_id != who.tenant_id:
            raise Forbidden("You can only see your own requests.")
        tenant_id = who.tenant_id
    return portfolio.list_requests(
        session,
        stage=stage,
        type=request_type,
        category=category,
        priority=priority,
        tier=tier,
        property_id=property_id,
        unit_id=unit_id,
        assignee_id=assignee_id,
        tenant_id=tenant_id,
        open=open,
        search=search,
        sort=sort,
    )


@router.get("/{request_id}", response_model=EnrichedRequestOut, responses=_REFUSALS)
def get_request(request_id: str, who: SignedIn, session: SessionDep) -> EnrichedRequestOut:
    """One request with what it points at: `getRequestById`."""
    request = portfolio.get_request(session, request_id)
    _may_see(who, request)
    return request


@router.get("/{request_id}/candidates", response_model=list[CandidateOut], responses=_REFUSALS)
def assignment_candidates(request_id: str, who: AdminPrincipal, session: SessionDep) -> list[CandidateOut]:
    """Who could take this request, best first: `getAssignmentCandidates`."""
    _may_see(who, portfolio.get_request(session, request_id))
    return portfolio.assignment_candidates(session, request_id)


@router.post("", response_model=EnrichedRequestOut, status_code=status.HTTP_201_CREATED, responses=_REFUSALS)
def create_request(body: NewRequestIn, who: SignedIn, session: SessionDep) -> EnrichedRequestOut:
    """Raise a request: `createRequest`. An admin raises their own trade's; a
    tenant raises for their own home, unassigned and marked as theirs."""
    if _admin_or_tenant(who) == "admin":
        trade = "maintenance" if body.category in MAINTENANCE_CATEGORIES else "housekeeping"
        if trade != who.trade:
            raise Forbidden(f"This portal raises {who.trade} requests only.")
    else:
        home = portfolio.get_tenant(session, who.tenant_id).unit  # type: ignore[arg-type]
        if home is None or body.unit_id != home.id:
            raise Forbidden("You can only raise requests for your own home.")
        body = body.model_copy(update={"origin": "tenant", "assignee_id": None})

    request = admin.create_request(session, body, datetime.now(UTC))
    session.commit()
    session.expire_all()
    return portfolio.get_request(session, request.id)


@router.post("/assign", response_model=list[ServiceRequestOut], responses=_REFUSALS)
def assign_requests(body: AssignIn, who: AdminPrincipal, session: SessionDep) -> list[ServiceRequestOut]:
    """Give requests to one staff member of this portal's trade:
    `assignRequests`. Closed work is skipped."""
    _own_trade_only(session, who, body.ids)
    member = session.get(Staff, body.assignee_id)
    if member is not None and member.role != who.trade:
        raise Invalid(f"{member.name} works in {member.role}, not {who.trade}.")
    touched = admin.assign_requests(session, body.ids, body.assignee_id, datetime.now(UTC))
    return _committed(session, touched)


@router.post("/priority", response_model=list[ServiceRequestOut], responses=_REFUSALS)
def set_priority(body: PriorityIn, who: AdminPrincipal, session: SessionDep) -> list[ServiceRequestOut]:
    """`setPriority`."""
    _own_trade_only(session, who, body.ids)
    return _committed(session, admin.set_priority(session, body.ids, body.priority))


@router.post("/delete", response_model=list[ServiceRequestOut], responses=_REFUSALS)
def delete_requests(body: RequestIdsIn, who: AdminPrincipal, session: SessionDep) -> list[ServiceRequestOut]:
    """Remove requests entirely: `deleteRequests`. Returns them as they were."""
    _own_trade_only(session, who, body.ids)
    removed = admin.delete_requests(session, body.ids)
    as_they_were = [ServiceRequestOut.from_model(r) for r in removed]
    session.commit()
    return as_they_were


# --- Who may ---------------------------------------------------------------------


def _admin_or_tenant(who: Principal) -> str:
    if who.kind in ("admin", "tenant"):
        return who.kind
    if who.kind == "registration":
        raise Forbidden("Your building hasn't confirmed you yet.")
    raise Forbidden("This is for the admin portals and tenants.")


def _may_see(who: Principal, request: EnrichedRequestOut) -> None:
    if _admin_or_tenant(who) == "admin":
        if request.type != who.trade:
            raise Forbidden("That request belongs to the other portal.")
    elif request.tenant_id != who.tenant_id:
        raise Forbidden("That request isn't yours.")


def _own_trade_only(session: Session, who: Principal, ids: Sequence[str]) -> None:
    types = session.scalars(select(ServiceRequest.type).where(ServiceRequest.id.in_(ids)))
    if any(t != who.trade for t in types):
        raise Forbidden("Some of those requests belong to the other portal.")


def _committed(session: Session, requests: list[ServiceRequest]) -> list[ServiceRequestOut]:
    """Commit, then read the touched requests back fresh, in the same order:
    `stage` is computed from the history the write may just have changed."""
    ids = [r.id for r in requests]
    session.commit()
    session.expire_all()
    fresh = {r.id: r for r in load_requests(session, ServiceRequest.id.in_(ids))}
    return [ServiceRequestOut.from_model(fresh[i]) for i in ids]
