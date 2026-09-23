"""Units, buildings and the reports the admin portals are built on. Every
number here is derived when read. `type` picks the trade (each admin portal
manages one) and defaults to maintenance, as the frontend's reads did.
`period` scopes what was raised and spent; leave it out for all time."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.enums import RequestType
from app.schemas.common import ErrorOut
from app.schemas.operations import PropertyOut
from app.schemas.portfolio import CategoryRollupOut, DashboardOut, PropertyRollupOut, UnitRecordOut
from app.services import portfolio
from app.services.common import ReportPeriod

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
TradeParam = Annotated[RequestType, Query(alias="type")]


@router.get("/units", response_model=list[UnitRecordOut], tags=["units"])
def list_units(
    session: SessionDep,
    property_id: Annotated[str | None, Query(alias="propertyId")] = None,
    trade: TradeParam = "maintenance",
) -> list[UnitRecordOut]:
    """Units with one trade's counts and spend, busiest first: `getUnits`."""
    return portfolio.list_units(session, datetime.now(UTC), property_id=property_id, type=trade)


@router.get(
    "/units/{unit_id}", response_model=UnitRecordOut, responses={404: {"model": ErrorOut}}, tags=["units"]
)
def get_unit(unit_id: str, session: SessionDep, trade: TradeParam = "maintenance") -> UnitRecordOut:
    """One unit: `getUnitById`."""
    return portfolio.get_unit(session, unit_id, datetime.now(UTC), type=trade)


@router.get("/properties", response_model=list[PropertyOut], tags=["buildings"])
def list_properties(session: SessionDep) -> list[PropertyOut]:
    """The buildings operations manages, by name: operations.ts's
    `getProperties`. (Marketing listings are `/listings`.)"""
    return portfolio.list_properties(session)


@router.get("/reports/properties", response_model=list[PropertyRollupOut], tags=["reports"])
def property_rollups(
    session: SessionDep, period: ReportPeriod | None = None, trade: TradeParam = "maintenance"
) -> list[PropertyRollupOut]:
    """Cost and volume by building, most spend first: `getPropertyRollups`."""
    return portfolio.property_rollups(session, datetime.now(UTC), period=period, type=trade)


@router.get("/reports/categories", response_model=list[CategoryRollupOut], tags=["reports"])
def category_rollups(
    session: SessionDep, period: ReportPeriod | None = None, trade: TradeParam = "maintenance"
) -> list[CategoryRollupOut]:
    """Volume and spend by category, busiest first: `getCategoryRollups`,
    without labels."""
    return portfolio.category_rollups(session, datetime.now(UTC), period=period, type=trade)


@router.get("/reports/dashboard", response_model=DashboardOut, tags=["reports"])
def dashboard(
    session: SessionDep, period: ReportPeriod | None = None, trade: TradeParam = "maintenance"
) -> DashboardOut:
    """The dashboard's numbers: `getDashboardStats`."""
    return portfolio.dashboard(session, datetime.now(UTC), period=period, type=trade)
