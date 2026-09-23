"""The technician field app's shapes: `Job` and `Worklist` in operations.ts."""

from app.schemas.common import CamelModel, Money
from app.schemas.operations import EnrichedRequestOut, StaffOut


class RepeatFaultOut(CamelModel):
    """A unit that keeps failing the same way: the repair-versus-replace
    signal. Derived when read, never stored."""

    category: str
    count: int
    spend: Money


class JobOut(EnrichedRequestOut):
    """Built with `JobOut.from_model(request, repeat_fault=...)`."""

    repeat_fault: RepeatFaultOut | None


class PhotoIn(CamelModel):
    """`PhotoInput` in types.ts: anything without bytes is dropped."""

    name: str | None = None
    data_url: str | None = None


class CompleteJobIn(CamelModel):
    notes: str | None = None
    photos: list[PhotoIn] = []


class HandBackIn(CamelModel):
    reason: str | None = None


class WorklistCounts(CamelModel):
    left: int
    urgent: int
    closed: int


class WorklistOut(CamelModel):
    """One technician's own work, split the way the screen draws it: the job
    to lead with, the rest queued behind it, and what's closed."""

    technician: StaffOut
    next: JobOut | None
    queued: list[JobOut]
    closed: list[JobOut]
    counts: WorklistCounts
