"""The roster each admin portal manages for its own trade."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.enums import RequestType
from app.schemas.common import ErrorOut
from app.schemas.operations import StaffOut
from app.schemas.portfolio import RosterMemberOut, StaffIn
from app.services import admin, portfolio

router = APIRouter(prefix="/staff", tags=["staff"])

SessionDep = Annotated[Session, Depends(get_session)]
_REFUSALS = {code: {"model": ErrorOut} for code in (400, 404, 409)}


@router.get("/roster", response_model=list[RosterMemberOut])
def roster(
    session: SessionDep, trade: Annotated[RequestType, Query(alias="type")] = "maintenance"
) -> list[RosterMemberOut]:
    """One trade's current staff with the load they hold: `getStaffRoster`."""
    return portfolio.roster(session, type=trade)


@router.get("/{staff_id}", response_model=StaffOut, responses={404: {"model": ErrorOut}})
def get_staff(staff_id: str, session: SessionDep) -> StaffOut:
    """One staff member, retired or not."""
    return portfolio.get_staff(session, staff_id)


@router.post("", response_model=StaffOut, status_code=status.HTTP_201_CREATED, responses=_REFUSALS)
def add_staff(body: StaffIn, session: SessionDep) -> StaffOut:
    """`addStaff`."""
    member = admin.add_staff(session, body)
    session.commit()
    return StaffOut.model_validate(member)


@router.put("/{staff_id}", response_model=StaffOut, responses=_REFUSALS)
def update_staff(staff_id: str, body: StaffIn, session: SessionDep) -> StaffOut:
    """`updateStaff`. Leaving `photo` out keeps the one they have."""
    member = admin.update_staff(session, staff_id, body)
    session.commit()
    return StaffOut.model_validate(member)


@router.delete("/{staff_id}", response_model=StaffOut, responses=_REFUSALS)
def retire_staff(staff_id: str, session: SessionDep) -> StaffOut:
    """`removeStaff`: takes them off the roster once they hold no open work.
    Their closed work still names them."""
    member = admin.retire_staff(session, staff_id, datetime.now(UTC))
    session.commit()
    return StaffOut.model_validate(member)
