"""Service requests and the records that hang off them.

A request's history is the only record of where it stands and when anything
happened to it. `stage` and `created_at` are read from `stage_history` by
subqueries rather than stored, so they can never disagree with it. Nothing
here, stored or computed, reports how long anything has taken.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, case, select
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.models.base import Base
from app.models.enums import MAINTENANCE_CATEGORIES, STAGES, Origin, Priority, RequestType, Stage

if TYPE_CHECKING:
    from app.models.people import Staff, Tenant
    from app.models.property import Unit


class StageEntry(Base):
    """One stage a request has reached, and when.

    The primary key is (request, stage): a stage is reached at most once, and
    re-reaching it moves `at` rather than adding a row. A hand-back deletes
    every entry after `submitted`, so a request never reads as having reached
    a stage it is now behind.
    """

    __tablename__ = "stage_history"

    request_id: Mapped[str] = mapped_column(
        ForeignKey("service_requests.id", ondelete="CASCADE"), primary_key=True
    )
    stage: Mapped[Stage] = mapped_column(primary_key=True)
    at: Mapped[datetime]


# A stage's place in the flow: submitted 0 … done 3. Used to find the furthest
# stage reached, which is not always the latest by time: re-assigning work
# already in progress re-dates `assigned` without moving the request back.
STAGE_RANK = case({stage: rank for rank, stage in enumerate(STAGES)}, value=StageEntry.stage)


class _PhotoColumns:
    """A photo, inlined as a data URL until Phase 8 moves the bytes to object
    storage and this becomes a URL. `position` keeps the order they were
    attached in."""

    request_id: Mapped[str] = mapped_column(
        ForeignKey("service_requests.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    data_url: Mapped[str] = mapped_column(Text)


class Photo(_PhotoColumns, Base):
    """The fault as the tenant (or ops) reported it."""

    __tablename__ = "photos"


class CompletionPhoto(_PhotoColumns, Base):
    """The work as the technician left it. Kept apart from `Photo` because the
    two are read for different reasons."""

    __tablename__ = "completion_photos"


class ServiceRequest(Base):
    __tablename__ = "service_requests"
    __table_args__ = (
        # Maintenance categories are a fixed set. A housekeeping category is a
        # rate-card service type, which can later leave the card, so it is
        # only known to be text.
        CheckConstraint(
            "type = 'housekeeping' OR category IN ("
            + ", ".join(f"'{c}'" for c in MAINTENANCE_CATEGORIES)
            + ")",
            name="maintenance_category_known",
        ),
        # Housekeeping is booked into a slot rather than raced against, so it
        # has no emergency tier.
        CheckConstraint(
            "type = 'maintenance' OR priority = 'normal'",
            name="housekeeping_is_not_urgent",
        ),
        # Maintenance is the landlord's cost and is never billed on.
        CheckConstraint(
            "type = 'housekeeping' OR charge IS NULL",
            name="maintenance_is_not_charged",
        ),
        CheckConstraint("charge IS NULL OR charge >= 0", name="charge_not_negative"),
        # A date with no window, or a window with no date, isn't a booking.
        CheckConstraint(
            "(schedule_date IS NULL) = (schedule_slot IS NULL)",
            name="schedule_date_and_slot_together",
        ),
        CheckConstraint(
            "(hand_back_reason IS NULL) = (hand_back_at IS NULL)",
            name="hand_back_reason_and_time_together",
        ),
    )

    # "REQ-1058", carried over from the seed.
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    unit_id: Mapped[str] = mapped_column(ForeignKey("units.id"), index=True)
    # The unit's tenant when the request was raised. Kept, not looked up,
    # because the unit's tenant can change later.
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    type: Mapped[RequestType]
    category: Mapped[str] = mapped_column(String(64))
    priority: Mapped[Priority]
    summary: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("staff.id"), index=True)
    origin: Mapped[Origin | None]
    # A housekeeping booking's rate-card price, copied when it was made; only
    # counted as spend once the work is done (derived at read time). Always
    # null for maintenance.
    charge: Mapped[Decimal | None]
    completion_notes: Mapped[str | None] = mapped_column(Text)

    # `schedule: { date, slot }` in types.ts. The slot is "<from>–<to>", two of
    # the frontend's `visitHours`: an absolute window chosen by an admin.
    schedule_date: Mapped[date | None]
    schedule_slot: Mapped[str | None] = mapped_column(String(32))

    # `handBack: { reason, by, byName, at }` in types.ts: set when the assigned
    # technician gave the job back. `hand_back_by_name` keeps the name as it
    # was, so it still reads correctly if that staff member is later removed.
    hand_back_reason: Mapped[str | None] = mapped_column(Text)
    hand_back_by: Mapped[str | None] = mapped_column(
        ForeignKey("staff.id", ondelete="SET NULL")
    )
    hand_back_by_name: Mapped[str | None] = mapped_column(String(200))
    hand_back_at: Mapped[datetime | None]

    # --- Read from the history, never stored -------------------------------
    # These are read-only. After changing `stage_history`, flush and refresh
    # (or expire) the request before reading them again.

    # The furthest stage reached.
    stage: Mapped[Stage] = column_property(
        select(StageEntry.stage)
        .where(StageEntry.request_id == id)
        .order_by(STAGE_RANK.desc())
        .limit(1)
        .correlate_except(StageEntry)
        .scalar_subquery()
    )
    # When it was submitted. Every request has a `submitted` entry: the write
    # that creates one must add it (a database constraint can't require a
    # child row).
    created_at: Mapped[datetime] = column_property(
        select(StageEntry.at)
        .where(StageEntry.request_id == id, StageEntry.stage == "submitted")
        .correlate_except(StageEntry)
        .scalar_subquery()
    )

    # --- Relationships -----------------------------------------------------

    unit: Mapped["Unit"] = relationship()
    tenant: Mapped["Tenant | None"] = relationship()
    assignee: Mapped["Staff | None"] = relationship(foreign_keys=[assignee_id])

    stage_history: Mapped[list[StageEntry]] = relationship(
        order_by=STAGE_RANK, cascade="all, delete-orphan", passive_deletes=True
    )
    photos: Mapped[list[Photo]] = relationship(
        order_by=Photo.position, cascade="all, delete-orphan", passive_deletes=True
    )
    completion_photos: Mapped[list[CompletionPhoto]] = relationship(
        order_by=CompletionPhoto.position,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
