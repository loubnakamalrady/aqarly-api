"""Units, buildings and the reports the admin portals are built on. Every
number here is derived when read. An admin sees their own portal's trade
(`type` may be left out, or must be that trade). `period` scopes what was
raised and spent; leave it out for all time."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.deps import AdminPrincipal, SessionDep, SignedIn, own_trade
from app.models import Unit
from app.models.enums import RequestType
from app.schemas.auth import UnitChoiceOut
from app.schemas.common import ErrorOut
from app.schemas.operations import PropertyOut
from app.schemas.portfolio import CategoryRollupOut, DashboardOut, PropertyRollupOut, UnitRecordOut
from app.services import portfolio
from app.services.common import ReportPeriod
from app.services.errors import NotFound

router = APIRouter()

TradeParam = Annotated[RequestType | None, Query(alias="type", description="Must be your portal's trade; leave it out.")]
_REFUSALS = {code: {"model": ErrorOut} for code in (401, 403)}


@router.get("/units", response_model=list[UnitRecordOut], tags=["units"], responses=_REFUSALS)
def list_units(
    who: AdminPrincipal,
    session: SessionDep,
    property_id: Annotated[str | None, Query(alias="propertyId")] = None,
    trade: TradeParam = None,
) -> list[UnitRecordOut]:
    """Units with this trade's counts and spend, busiest first: `getUnits`."""
    return portfolio.list_units(session, datetime.now(UTC), property_id=property_id, type=own_trade(who, trade))


@router.get("/units/{unit_id}", response_model=UnitRecordOut, tags=["units"], responses=_REFUSALS | {404: {"model": ErrorOut}})
def get_unit(unit_id: str, who: AdminPrincipal, session: SessionDep, trade: TradeParam = None) -> UnitRecordOut:
    """One unit: `getUnitById`."""
    return portfolio.get_unit(session, unit_id, datetime.now(UTC), type=own_trade(who, trade))


@router.get("/properties", response_model=list[PropertyOut], tags=["buildings"], responses=_REFUSALS)
def list_properties(who: SignedIn, session: SessionDep) -> list[PropertyOut]:
    """The buildings operations manages, by name: operations.ts's
    `getProperties`. (Marketing listings are `/listings`.) Anyone signed in,
    including a tenant registering."""
    return portfolio.list_properties(session)


@router.get(
    "/properties/{property_id}/units",
    response_model=list[UnitChoiceOut],
    tags=["buildings"],
    responses=_REFUSALS | {404: {"model": ErrorOut}},
)
def list_property_units(property_id: str, who: SignedIn, session: SessionDep) -> list[UnitChoiceOut]:
    """A building's units by label, to pick yours when registering."""
    units = session.scalars(select(Unit).where(Unit.property_id == property_id).order_by(Unit.label)).all()
    if not units:
        raise NotFound(f"No building {property_id}")
    return [UnitChoiceOut.model_validate(u) for u in units]


@router.get("/reports/properties", response_model=list[PropertyRollupOut], tags=["reports"], responses=_REFUSALS)
def property_rollups(
    who: AdminPrincipal, session: SessionDep, period: ReportPeriod | None = None, trade: TradeParam = None
) -> list[PropertyRollupOut]:
    """Cost and volume by building, most spend first: `getPropertyRollups`."""
    return portfolio.property_rollups(session, datetime.now(UTC), period=period, type=own_trade(who, trade))


@router.get("/reports/categories", response_model=list[CategoryRollupOut], tags=["reports"], responses=_REFUSALS)
def category_rollups(
    who: AdminPrincipal, session: SessionDep, period: ReportPeriod | None = None, trade: TradeParam = None
) -> list[CategoryRollupOut]:
    """Volume and spend by category, busiest first: `getCategoryRollups`,
    without labels."""
    return portfolio.category_rollups(session, datetime.now(UTC), period=period, type=own_trade(who, trade))


@router.get("/reports/dashboard", response_model=DashboardOut, tags=["reports"], responses=_REFUSALS)
def dashboard(
    who: AdminPrincipal, session: SessionDep, period: ReportPeriod | None = None, trade: TradeParam = None
) -> DashboardOut:
    """The dashboard's numbers: `getDashboardStats`."""
    return portfolio.dashboard(session, datetime.now(UTC), period=period, type=own_trade(who, trade))
