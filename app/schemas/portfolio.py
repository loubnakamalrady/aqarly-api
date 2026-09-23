"""The admin portals' shapes: units, rollups, the dashboard, the roster, and
the rate card. Named after the frontend's operations.ts, which built each of
these in memory. Numbers only: category labels and other words are added by
the frontend."""

from datetime import date, datetime

from app.models.enums import Origin, Priority, Stage
from app.schemas.common import CamelModel, Money
from app.schemas.field import PhotoIn, RepeatFaultOut
from app.schemas.operations import PropertyOut, StaffOut, TenantOut, UnitOut

# --- Reads -------------------------------------------------------------------


class UnitRecordOut(UnitOut):
    """`UnitRecord`: a unit with what one trade's work there adds up to."""

    property: PropertyOut
    tenant: TenantOut | None
    request_count: int
    open_count: int
    lifetime_spend: Money
    last_serviced_at: datetime | None
    # Maintenance only: housekeeping recurring is the service working.
    repeat_fault: RepeatFaultOut | None


class PropertyRollupOut(PropertyOut):
    """Cost and volume for one building, over the report period."""

    units: int
    requests: int
    open: int
    unassigned: int
    spend: Money
    spend_per_unit: Money


class CategoryRollupOut(CamelModel):
    """`CategoryRollup` without `label`, which the frontend adds."""

    category: str
    requests: int
    spend: Money


class StageCount(CamelModel):
    stage: Stage
    count: int


class MonthTotal(CamelModel):
    # "2026-08": the month work was closed in.
    month: str
    total: Money


class DashboardOut(CamelModel):
    open: int
    unassigned: int
    in_progress: int
    closed: int
    by_stage: list[StageCount]
    raised: int
    urgent_open: int
    urgent_buildings: int
    # Both are the period's spend: the frontend has read it under either name.
    maintenance_spend: Money
    period_spend: Money
    cost_trend: list[MonthTotal]


class RosterMemberOut(StaffOut):
    """A staff member with their load, read off the requests they hold."""

    load: int
    capacity: int
    in_progress: int
    closed: int
    # Buildings they've worked in, first seen first.
    properties: list[PropertyOut]


class CandidateOut(RosterMemberOut):
    """A roster member ranked for one request's assign panel."""

    in_building: bool
    at_capacity: bool
    is_current: bool
    # Capacity outweighs familiarity; familiarity outweighs a lighter day.
    score: int


class TenantAccountOut(TenantOut):
    """A tenant with the unit they occupy."""

    unit: UnitOut | None
    property: PropertyOut | None


class RateOut(CamelModel):
    """`HousekeepingRate`, plus when it left the card, if it has."""

    service_type: str
    label: str
    price: Money
    retired_at: datetime | None


# --- Writes ------------------------------------------------------------------


class NewRequestIn(CamelModel):
    """`NewRequest` in operations.ts. `type`, `tenantId` and `charge` are not
    asked for: the category decides the trade, the unit decides the tenant, and
    the rate card decides the price."""

    unit_id: str
    category: str
    priority: Priority = "normal"
    summary: str = ""
    description: str = ""
    photos: list[PhotoIn] = []
    assignee_id: str | None = None
    scheduled_date: date | None = None
    scheduled_slot: str | None = None
    origin: Origin = "ops"


class AssignIn(CamelModel):
    ids: list[str]
    assignee_id: str


class PriorityIn(CamelModel):
    ids: list[str]
    # A plain string, checked by the service so the refusal can name it.
    priority: str


class RequestIdsIn(CamelModel):
    ids: list[str]


class RateIn(CamelModel):
    label: str | None = None
    # A form sends text; the service reads it as a number.
    price: float | str | None = None


class StaffIn(CamelModel):
    name: str | None = None
    phone: str | None = None
    # A plain string, checked by the service so the refusal can name it.
    role: str | None = None
    # A data URL. On an update, leaving it out keeps the photo they have.
    photo: str | None = None
