"""The housekeeping rate card: what tenants book against and the housekeeping
portal manages. Everyone signed in reads it (the apps name services by it);
only the housekeeping portal changes it."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.deps import AdminPrincipal, SessionDep, SignedIn
from app.schemas.common import ErrorOut
from app.schemas.portfolio import RateIn, RateOut
from app.services import admin, portfolio
from app.services.auth import Principal
from app.services.errors import Forbidden

router = APIRouter(prefix="/housekeeping-rates", tags=["rates"])

_REFUSALS = {code: {"model": ErrorOut} for code in (400, 401, 403, 404, 409)}


@router.get("", response_model=list[RateOut], responses=_REFUSALS)
def list_rates(
    who: SignedIn,
    session: SessionDep,
    include_retired: Annotated[bool, Query(alias="includeRetired")] = False,
) -> list[RateOut]:
    """The card, in its order: `getHousekeepingRates`. `includeRetired` adds
    the services that have left it, so old bookings can still be named."""
    return portfolio.list_rates(session, include_retired=include_retired)


@router.post("", response_model=RateOut, status_code=status.HTTP_201_CREATED, responses=_REFUSALS)
def add_rate(body: RateIn, who: AdminPrincipal, session: SessionDep) -> RateOut:
    """`addHousekeepingRate`: goes at the end of the card."""
    _housekeeping(who)
    rate = admin.add_rate(session, body)
    session.commit()
    return RateOut.model_validate(rate)


@router.delete("/{service_type}", response_model=RateOut, responses=_REFUSALS)
def retire_rate(service_type: str, who: AdminPrincipal, session: SessionDep) -> RateOut:
    """`removeHousekeepingRate`: takes it off the card once no open booking
    uses it. Bookings already made keep its name and their price."""
    _housekeeping(who)
    rate = admin.retire_rate(session, service_type, datetime.now(UTC))
    session.commit()
    return RateOut.model_validate(rate)


def _housekeeping(who: Principal) -> None:
    if who.trade != "housekeeping":
        raise Forbidden("The rate card belongs to the housekeeping portal.")
