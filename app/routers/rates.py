"""The housekeeping rate card: what tenants book against and the housekeeping
portal manages."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas.common import ErrorOut
from app.schemas.portfolio import RateIn, RateOut
from app.services import admin, portfolio

router = APIRouter(prefix="/housekeeping-rates", tags=["rates"])

SessionDep = Annotated[Session, Depends(get_session)]
_REFUSALS = {code: {"model": ErrorOut} for code in (400, 404, 409)}


@router.get("", response_model=list[RateOut])
def list_rates(
    session: SessionDep,
    include_retired: Annotated[bool, Query(alias="includeRetired")] = False,
) -> list[RateOut]:
    """The card, in its order: `getHousekeepingRates`. `includeRetired` adds
    the services that have left it, so old bookings can still be named."""
    return portfolio.list_rates(session, include_retired=include_retired)


@router.post("", response_model=RateOut, status_code=status.HTTP_201_CREATED, responses=_REFUSALS)
def add_rate(body: RateIn, session: SessionDep) -> RateOut:
    """`addHousekeepingRate`: goes at the end of the card."""
    rate = admin.add_rate(session, body)
    session.commit()
    return RateOut.model_validate(rate)


@router.delete("/{service_type}", response_model=RateOut, responses=_REFUSALS)
def retire_rate(service_type: str, session: SessionDep) -> RateOut:
    """`removeHousekeepingRate`: takes it off the card once no open booking
    uses it. Bookings already made keep its name and their price."""
    rate = admin.retire_rate(session, service_type, datetime.now(UTC))
    session.commit()
    return RateOut.model_validate(rate)
