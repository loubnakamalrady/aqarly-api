"""Service requests, as the ops, housekeeping and tenant portals use them.

Query parameters are camelCase (`propertyId`), like everything the frontend
sends. Bulk actions take a list of ids and return the requests they touched,
which is how the portals word their confirmations ("3 requests assigned").
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ServiceRequest
from app.models.enums import Priority, RequestType, Stage
from app.schemas.common import ErrorOut
from app.schemas.operations import EnrichedRequestOut, ServiceRequestOut
from app.schemas.portfolio import AssignIn, CandidateOut, NewRequestIn, PriorityIn, RequestIdsIn
from app.services import admin, portfolio
from app.services.common import load_requests
from app.services.portfolio import RequestSort, Tier

router = APIRouter(prefix="/requests", tags=["requests"])

SessionDep = Annotated[Session, Depends(get_session)]
_REFUSALS = {code: {"model": ErrorOut} for code in (400, 404, 409)}


@router.get("", response_model=list[EnrichedRequestOut])
def list_requests(
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
    `sort=newest`."""
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


@router.get("/{request_id}", response_model=EnrichedRequestOut, responses={404: {"model": ErrorOut}})
def get_request(request_id: str, session: SessionDep) -> EnrichedRequestOut:
    """One request with what it points at: `getRequestById`."""
    return portfolio.get_request(session, request_id)


@router.get(
    "/{request_id}/candidates", response_model=list[CandidateOut], responses={404: {"model": ErrorOut}}
)
def assignment_candidates(request_id: str, session: SessionDep) -> list[CandidateOut]:
    """Who could take this request, best first: `getAssignmentCandidates`."""
    return portfolio.assignment_candidates(session, request_id)


@router.post(
    "", response_model=EnrichedRequestOut, status_code=status.HTTP_201_CREATED, responses=_REFUSALS
)
def create_request(body: NewRequestIn, session: SessionDep) -> EnrichedRequestOut:
    """Raise a request, from ops, the housekeeping portal or a tenant:
    `createRequest`."""
    request = admin.create_request(session, body, datetime.now(UTC))
    session.commit()
    session.expire_all()
    return portfolio.get_request(session, request.id)


@router.post("/assign", response_model=list[ServiceRequestOut], responses=_REFUSALS)
def assign_requests(body: AssignIn, session: SessionDep) -> list[ServiceRequestOut]:
    """Give requests to one staff member: `assignRequests`. Closed work is
    skipped."""
    touched = admin.assign_requests(session, body.ids, body.assignee_id, datetime.now(UTC))
    return _committed(session, touched)


@router.post("/priority", response_model=list[ServiceRequestOut], responses=_REFUSALS)
def set_priority(body: PriorityIn, session: SessionDep) -> list[ServiceRequestOut]:
    """`setPriority`."""
    return _committed(session, admin.set_priority(session, body.ids, body.priority))


@router.post("/delete", response_model=list[ServiceRequestOut], responses=_REFUSALS)
def delete_requests(body: RequestIdsIn, session: SessionDep) -> list[ServiceRequestOut]:
    """Remove requests entirely: `deleteRequests`. Returns them as they were."""
    removed = admin.delete_requests(session, body.ids)
    as_they_were = [ServiceRequestOut.from_model(r) for r in removed]
    session.commit()
    return as_they_were


def _committed(session: Session, requests: list[ServiceRequest]) -> list[ServiceRequestOut]:
    """Commit, then read the touched requests back fresh, in the same order:
    `stage` is computed from the history the write may just have changed."""
    ids = [r.id for r in requests]
    session.commit()
    session.expire_all()
    fresh = {r.id: r for r in load_requests(session, ServiceRequest.id.in_(ids))}
    return [ServiceRequestOut.from_model(fresh[i]) for i in ids]
