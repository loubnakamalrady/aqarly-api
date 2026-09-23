"""Replace the database's contents with the frontend's seed data.

This is what the frontend's "Reset demo data" button did to its in-memory
store: every run empties the tables and loads `operations.json` and
`properties.json` again. `scripts/seed.py` runs it from the command line.

The JSON is validated against the shapes below before anything is written,
and the result is checked against it afterwards. The caller commits: if
`seed()` raises, rolling back leaves the database exactly as it was.
"""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import (
    Base,
    CompletionPhoto,
    HousekeepingRate,
    Listing,
    Photo,
    Property,
    ServiceRequest,
    StageEntry,
    Staff,
    Tenant,
    Unit,
)
from app.models.enums import Origin, Priority, RequestType, Stage, UnitStatus


class SeedError(Exception):
    """The seed data can't be loaded as it is."""


# --- The JSON, as the frontend writes it -----------------------------------
# camelCase in the file, snake_case here. `extra="forbid"`: a field this
# script doesn't know about stops the seed instead of being silently dropped.


class _Json(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid", frozen=True)


class _Property(_Json):
    id: str
    name: str
    address: str


class _Unit(_Json):
    id: str
    property_id: str
    label: str
    status: UnitStatus
    tenant_id: str | None
    bedrooms: int
    bathrooms: int


class _Tenant(_Json):
    id: str
    name: str
    phone: str
    email: str


class _Staff(_Json):
    id: str
    name: str
    phone: str
    role: RequestType
    photo: str | None = None


class _Rate(_Json):
    service_type: str
    label: str
    price: Decimal


class _Photo(_Json):
    name: str
    data_url: str


class _StageEntry(_Json):
    stage: Stage
    at: datetime


class _Schedule(_Json):
    date: date
    slot: str


class _HandBack(_Json):
    reason: str
    by: str
    by_name: str | None
    at: datetime


class _Request(_Json):
    id: str
    unit_id: str
    tenant_id: str | None
    type: RequestType
    category: str
    priority: Priority
    summary: str
    description: str
    assignee_id: str | None
    origin: Origin | None = None
    charge: Decimal | None
    completion_notes: str | None
    stage_history: list[_StageEntry]
    photos: list[_Photo] = []
    completion_photos: list[_Photo] = []
    schedule: _Schedule | None = None
    hand_back: _HandBack | None = None
    # Stored by the frontend, computed by this database. Read only to check
    # that the two agree.
    stage: Stage
    created_at: datetime


class _Operations(_Json):
    properties: list[_Property]
    units: list[_Unit]
    tenants: list[_Tenant]
    staff: list[_Staff]
    housekeeping_rates: list[_Rate]
    requests: list[_Request]


class _Location(_Json):
    city: str
    area: str


class _Listing(_Json):
    slug: str
    title: str
    type: str
    purpose: str
    price: Decimal
    currency: str
    bedrooms: int
    bathrooms: int
    area_sqft: int
    location: _Location
    images: list[str]
    description: str
    featured: bool


# properties.json is a bare list, so it's validated with an adapter rather
# than a model.
_LISTINGS = TypeAdapter(list[_Listing])


# --- Loading ---------------------------------------------------------------


def read_seed(data_dir: Path) -> tuple[_Operations, list[_Listing]]:
    """Read and validate both seed files, before touching the database."""
    try:
        operations = _Operations.model_validate_json((data_dir / "operations.json").read_bytes())
        listings = _LISTINGS.validate_json((data_dir / "properties.json").read_bytes())
    except FileNotFoundError as e:
        raise SeedError(f"Seed file not found: {e.filename}. Set FRONTEND_DATA_DIR in .env.") from e
    except ValidationError as e:
        raise SeedError(f"Seed data doesn't match the model:\n{e}") from e
    return operations, listings


def seed(session: Session, data_dir: Path) -> dict[str, int]:
    """Empty every table and load the seed. Returns the row count per table.

    Raises `SeedError` if the data doesn't validate, or if what the database
    computes from it disagrees with what the frontend stored.
    """
    operations, listings = read_seed(data_dir)

    # One statement for all tables. TRUNCATE is transactional in Postgres,
    # so a failure later on puts every row back when the caller rolls back.
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables}"))

    # Parents before children, flushing between, so every foreign key
    # already has its row when the child is inserted.
    session.add_all(Property(**p.model_dump()) for p in operations.properties)
    session.add_all(Tenant(**t.model_dump()) for t in operations.tenants)
    session.add_all(Staff(**s.model_dump()) for s in operations.staff)
    # The card's order is the order the file lists it in.
    session.add_all(
        HousekeepingRate(**r.model_dump(), position=i)
        for i, r in enumerate(operations.housekeeping_rates)
    )
    session.add_all(_listing(item) for item in listings)
    session.flush()
    session.add_all(Unit(**u.model_dump()) for u in operations.units)
    session.flush()
    session.add_all(_request(r) for r in operations.requests)
    session.flush()

    _check_computed_fields(session, operations.requests)
    return _row_counts(session)


def _listing(item: _Listing) -> Listing:
    return Listing(
        **item.model_dump(exclude={"location"}),
        city=item.location.city,
        area=item.location.area,
    )


def _request(r: _Request) -> ServiceRequest:
    return ServiceRequest(
        **r.model_dump(
            exclude={
                "stage_history", "photos", "completion_photos",
                "schedule", "hand_back", "stage", "created_at",
            }
        ),
        stage_history=[StageEntry(stage=e.stage, at=e.at) for e in r.stage_history],
        photos=[
            Photo(position=i, name=p.name, data_url=p.data_url) for i, p in enumerate(r.photos)
        ],
        completion_photos=[
            CompletionPhoto(position=i, name=p.name, data_url=p.data_url)
            for i, p in enumerate(r.completion_photos)
        ],
        schedule_date=r.schedule.date if r.schedule else None,
        schedule_slot=r.schedule.slot if r.schedule else None,
        hand_back_reason=r.hand_back.reason if r.hand_back else None,
        hand_back_by=r.hand_back.by if r.hand_back else None,
        hand_back_by_name=r.hand_back.by_name if r.hand_back else None,
        hand_back_at=r.hand_back.at if r.hand_back else None,
    )


def _check_computed_fields(session: Session, requests: list[_Request]) -> None:
    """The frontend stores `stage` and `createdAt`; this database computes
    them from the history. Refuse the seed if the two ever disagree."""
    computed = {
        row.id: (row.stage, row.created_at)
        for row in session.execute(
            select(ServiceRequest.id, ServiceRequest.stage, ServiceRequest.created_at)
        )
    }
    problems = []
    for r in requests:
        stage, created_at = computed[r.id]
        if (stage, created_at) != (r.stage, r.created_at):
            problems.append(
                f"{r.id}: seed says {r.stage} at {r.created_at.isoformat()}, "
                f"history gives {stage} at {created_at.isoformat() if created_at else '(no submitted entry)'}"
            )
    if problems:
        raise SeedError("Stage history disagrees with the seed:\n  " + "\n  ".join(problems))


def _row_counts(session: Session) -> dict[str, int]:
    return {
        table.name: session.scalar(select(func.count()).select_from(table)) or 0
        for table in Base.metadata.sorted_tables
    }
