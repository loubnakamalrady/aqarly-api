"""Reads and rules shared by more than one area: loading requests with what
they point at, what a request has actually cost, report periods, and the
repeat-fault rule. Everything here is derived when read; nothing is stored."""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.models import ServiceRequest, StageEntry, Unit
from app.models.enums import Stage
from app.schemas.field import RepeatFaultOut

# A unit that has failed the same way this many times within this window is
# flagged: the repair that keeps not holding is a different job from the one
# on the ticket.
REPEAT_FAULT_WITHIN = timedelta(days=240)
REPEAT_FAULT_OCCURRENCES = 3

# How much open work one person can hold before they read as full. Fixed for
# now; Ops PRD §7 wants it admin-configurable later.
STAFF_CAPACITY = 7

# The dashboard reports over a window. Anything counted as "raised" or
# "spent" is period-scoped; open work is open whenever it came in.
ReportPeriod = Literal["month", "quarter", "year"]
REPORT_PERIOD_DAYS: dict[ReportPeriod, int] = {"month": 30, "quarter": 90, "year": 365}


def raised_since(period: ReportPeriod | None, now: datetime) -> datetime | None:
    """The start of a report period, or None for all time."""
    return now - timedelta(days=REPORT_PERIOD_DAYS[period]) if period else None


def charged(stage: Stage, charge: Decimal | None) -> Decimal:
    """What a request has actually cost. A housekeeping booking carries its
    price from the moment it's made, but nothing is charged until the work is
    done, so every spend total reads through this rather than `charge`."""
    return (charge or Decimal(0)) if stage == "done" else Decimal(0)


def enriched_requests() -> Select[tuple[ServiceRequest]]:
    """A query for requests with everything the portals show about them,
    loaded up front: one query per relationship, not one per request. The
    queue's order: oldest first, id breaking ties."""
    return (
        select(ServiceRequest)
        .options(
            selectinload(ServiceRequest.stage_history),
            selectinload(ServiceRequest.photos),
            selectinload(ServiceRequest.completion_photos),
            selectinload(ServiceRequest.unit).selectinload(Unit.property),
            selectinload(ServiceRequest.tenant),
            selectinload(ServiceRequest.assignee),
        )
        .order_by(ServiceRequest.created_at, ServiceRequest.id)
    )


def load_requests(session: Session, *where) -> Sequence[ServiceRequest]:
    return session.scalars(enriched_requests().where(*where)).all()


def stage_at(request: ServiceRequest, stage: Stage) -> datetime | None:
    return next((e.at for e in request.stage_history if e.stage == stage), None)


def repeat_fault(history: Sequence, now: datetime) -> RepeatFaultOut | None:
    """The most frequent category among a unit's requests within the window,
    if it has come up often enough. `history` is that unit's requests of one
    trade, oldest first, each with `category`, `created_at`, `stage` and
    `charge`. `spend` counts that category's charged work over all time."""
    cutoff = now - REPEAT_FAULT_WITHIN
    counts = Counter(h.category for h in history if h.created_at >= cutoff)
    if not counts:
        return None

    # most_common keeps first-seen order among ties; history is oldest first.
    category, count = counts.most_common(1)[0]
    if count < REPEAT_FAULT_OCCURRENCES:
        return None

    spend = sum(charged(h.stage, h.charge) for h in history if h.category == category)
    return RepeatFaultOut(category=category, count=count, spend=float(spend))


def reach_stage(request: ServiceRequest, stage: Stage, at: datetime) -> None:
    """A stage is reached once: reaching it again moves its time rather than
    adding a second entry. Needs `stage_history` loaded."""
    existing = next((e for e in request.stage_history if e.stage == stage), None)
    if existing:
        existing.at = at
    else:
        request.stage_history.append(StageEntry(stage=stage, at=at))
