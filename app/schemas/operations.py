"""The operations model's shapes, named and nested the way `types.ts` has them."""

from datetime import date, datetime
from typing import Self

from app.models import ServiceRequest
from app.models.enums import Origin, Priority, RequestType, Stage, UnitStatus
from app.schemas.common import CamelModel, Money


class PropertyOut(CamelModel):
    id: str
    name: str
    address: str


class UnitOut(CamelModel):
    id: str
    property_id: str
    label: str
    status: UnitStatus
    tenant_id: str | None
    bedrooms: int
    bathrooms: int


class TenantOut(CamelModel):
    id: str
    name: str
    phone: str
    email: str


class StaffOut(CamelModel):
    id: str
    name: str
    phone: str
    role: RequestType
    photo: str | None
    # Set once they've left the roster. Retired staff still appear as the
    # assignee of work they closed.
    retired_at: datetime | None


class StageEntryOut(CamelModel):
    stage: Stage
    at: datetime


class PhotoOut(CamelModel):
    name: str
    data_url: str


class ScheduleOut(CamelModel):
    date: date
    slot: str


class HandBackOut(CamelModel):
    reason: str
    by: str | None
    by_name: str | None
    at: datetime


class ServiceRequestOut(CamelModel):
    """`ServiceRequest` in types.ts. `stage` and `createdAt` are read from the
    history (see the model); `schedule` and `handBack` are rebuilt from their
    columns."""

    id: str
    unit_id: str
    tenant_id: str | None
    type: RequestType
    category: str
    priority: Priority
    summary: str
    description: str
    stage: Stage
    assignee_id: str | None
    origin: Origin | None
    created_at: datetime
    stage_history: list[StageEntryOut]
    photos: list[PhotoOut]
    charge: Money | None
    completion_notes: str | None
    schedule: ScheduleOut | None
    completion_photos: list[PhotoOut]
    hand_back: HandBackOut | None

    @classmethod
    def _fields_of(cls, request: ServiceRequest) -> dict[str, object]:
        rebuilt = {"schedule", "hand_back"}
        fields: dict[str, object] = {
            name: getattr(request, name)
            for name in ServiceRequestOut.model_fields
            if name not in rebuilt
        }
        fields["schedule"] = (
            ScheduleOut(date=request.schedule_date, slot=request.schedule_slot)
            if request.schedule_date and request.schedule_slot
            else None
        )
        fields["hand_back"] = (
            HandBackOut(
                reason=request.hand_back_reason,
                by=request.hand_back_by,
                by_name=request.hand_back_by_name,
                at=request.hand_back_at,
            )
            if request.hand_back_reason and request.hand_back_at
            else None
        )
        return fields

    @classmethod
    def from_model(cls, request: ServiceRequest, **derived: object) -> Self:
        """Build from a request; `derived` fills fields a subclass computes
        (a job's `repeat_fault`)."""
        return cls.model_validate({**cls._fields_of(request), **derived})


class EnrichedRequestOut(ServiceRequestOut):
    """`EnrichedRequest` in the frontend's operations.ts: a request with the
    records it points at. The frontend adds `tier` itself with `tierFor()`."""

    unit: UnitOut
    property: PropertyOut
    tenant: TenantOut | None
    assignee: StaffOut | None

    @classmethod
    def _fields_of(cls, request: ServiceRequest) -> dict[str, object]:
        return {
            **super()._fields_of(request),
            "unit": request.unit,
            "property": request.unit.property,
            "tenant": request.tenant,
            "assignee": request.assignee,
        }
