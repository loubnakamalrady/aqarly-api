"""The roster each admin portal manages for its own trade."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.deps import AdminPrincipal, SessionDep, SignedIn, own_trade
from app.models import Staff
from app.models.enums import RequestType
from app.schemas.common import ErrorOut
from app.schemas.operations import StaffOut
from app.schemas.portfolio import RosterMemberOut, StaffIn
from app.services import admin, portfolio
from app.services.auth import Principal
from app.services.errors import Forbidden

router = APIRouter(prefix="/staff", tags=["staff"])

_REFUSALS = {code: {"model": ErrorOut} for code in (400, 401, 403, 404, 409)}


@router.get("/roster", response_model=list[RosterMemberOut], responses=_REFUSALS)
def roster(
    who: AdminPrincipal,
    session: SessionDep,
    trade: Annotated[RequestType | None, Query(alias="type", description="Must be your portal's trade; leave it out.")] = None,
) -> list[RosterMemberOut]:
    """This portal's current staff with the load they hold: `getStaffRoster`."""
    return portfolio.roster(session, type=own_trade(who, trade))


@router.get("/{staff_id}", response_model=StaffOut, responses=_REFUSALS)
def get_staff(staff_id: str, who: SignedIn, session: SessionDep) -> StaffOut:
    """One staff member, retired or not: themselves, or an admin of their trade."""
    member = portfolio.get_staff(session, staff_id)
    if who.staff_id != staff_id and not (who.kind == "admin" and who.trade == member.role):
        raise Forbidden("That isn't your team.")
    return member


@router.post("", response_model=StaffOut, status_code=status.HTTP_201_CREATED, responses=_REFUSALS)
def add_staff(body: StaffIn, who: AdminPrincipal, session: SessionDep) -> StaffOut:
    """`addStaff`, into this portal's trade."""
    _this_trade(who, body.role)
    member = admin.add_staff(session, body)
    session.commit()
    return StaffOut.model_validate(member)


@router.put("/{staff_id}", response_model=StaffOut, responses=_REFUSALS)
def update_staff(staff_id: str, body: StaffIn, who: AdminPrincipal, session: SessionDep) -> StaffOut:
    """`updateStaff`. Leaving `photo` out keeps the one they have."""
    _manages(session, who, staff_id)
    _this_trade(who, body.role)
    member = admin.update_staff(session, staff_id, body)
    session.commit()
    return StaffOut.model_validate(member)


@router.delete("/{staff_id}", response_model=StaffOut, responses=_REFUSALS)
def retire_staff(staff_id: str, who: AdminPrincipal, session: SessionDep) -> StaffOut:
    """`removeStaff`: takes them off the roster once they hold no open work.
    Their closed work still names them."""
    _manages(session, who, staff_id)
    member = admin.retire_staff(session, staff_id, datetime.now(UTC))
    session.commit()
    return StaffOut.model_validate(member)


def _this_trade(who: Principal, role: str | None) -> None:
    """A valid trade other than this portal's is refused; an unknown one is
    left for the service's own message."""
    if role in ("maintenance", "housekeeping") and role != who.trade:
        raise Forbidden(f"This portal manages {who.trade} staff only.")


def _manages(session: SessionDep, who: Principal, staff_id: str) -> None:
    member = session.get(Staff, staff_id)
    if member is not None and member.role != who.trade:
        raise Forbidden("That isn't your team.")
