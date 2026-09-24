"""Tenants who registered themselves, for ops to confirm or decline. Ops holds
the leases, so ops is who knows whether someone lives where they say."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter
from sqlalchemy import select

from app.deps import OpsPrincipal, SessionDep
from app.models import TenantRegistration
from app.routers.auth import registration_out
from app.schemas.auth import RegistrationOut
from app.schemas.common import ErrorOut
from app.services import auth

router = APIRouter(prefix="/registrations", tags=["registrations"])

_REFUSALS = {code: {"model": ErrorOut} for code in (401, 403, 404, 409)}


@router.get("", response_model=list[RegistrationOut], responses=_REFUSALS)
def list_registrations(
    who: OpsPrincipal, session: SessionDep, status: Literal["pending", "all"] = "pending"
) -> list[RegistrationOut]:
    """Waiting for a decision (oldest first), or every registration."""
    query = select(TenantRegistration).order_by(TenantRegistration.created_at, TenantRegistration.id)
    if status == "pending":
        query = query.where(TenantRegistration.decision.is_(None))
    return [registration_out(r) for r in session.scalars(query)]


@router.post("/{registration_id}/approve", response_model=RegistrationOut, responses=_REFUSALS)
def approve(registration_id: str, who: OpsPrincipal, session: SessionDep) -> RegistrationOut:
    """Makes them a tenant and the unit's tenant, replacing whoever is there
    now (`currentTenant`). They see their home the next time they load a page."""
    registration = auth.decide(session, registration_id, who.admin_id, True, datetime.now(UTC))
    session.commit()
    return registration_out(registration)


@router.post("/{registration_id}/decline", response_model=RegistrationOut, responses=_REFUSALS)
def decline(registration_id: str, who: OpsPrincipal, session: SessionDep) -> RegistrationOut:
    """They're told their building couldn't confirm them, and can register again."""
    registration = auth.decide(session, registration_id, who.admin_id, False, datetime.now(UTC))
    session.commit()
    return registration_out(registration)
