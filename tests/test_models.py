"""The schema's promises: what it computes, and what it refuses to store."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import CheckConstraint, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Photo,
    Property,
    ServiceRequest,
    StageEntry,
    Staff,
    Tenant,
    Unit,
)

SUBMITTED = datetime(2026, 8, 21, 6, 10, tzinfo=UTC)
ASSIGNED = datetime(2026, 8, 21, 7, 25, tzinfo=UTC)
STARTED = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
REASSIGNED = datetime(2026, 8, 22, 11, 30, tzinfo=UTC)


@pytest.fixture
def unit(session: Session) -> Unit:
    unit = Unit(
        id="unit-mh-0402",
        label="0402",
        status="occupied",
        bedrooms=2,
        bathrooms=2,
        property=Property(id="prop-marina-heights", name="Marina Heights", address="Marina District"),
        tenant=Tenant(id="ten-alhabsi", name="Layla Al Habsi", phone="+000 000 0001", email="layla@example.com"),
    )
    session.add(unit)
    session.add(Staff(id="stf-haddad", name="Youssef Haddad", phone="+000 000 0101", role="maintenance"))
    session.flush()
    return unit


def make_request(unit: Unit, **overrides: object) -> ServiceRequest:
    fields: dict[str, object] = {
        "id": "REQ-1",
        "unit_id": unit.id,
        "tenant_id": unit.tenant_id,
        "type": "maintenance",
        "category": "plumbing",
        "priority": "normal",
        "summary": "Leaking tap",
        "description": "",
        "stage_history": [StageEntry(stage="submitted", at=SUBMITTED)],
    }
    return ServiceRequest(**(fields | overrides))


def test_stage_and_created_at_are_read_from_history(session: Session, unit: Unit) -> None:
    request = make_request(unit, assignee_id="stf-haddad", stage_history=[
        StageEntry(stage="submitted", at=SUBMITTED),
        StageEntry(stage="in-progress", at=STARTED),
        # Handed over while in progress: `assigned` is re-dated after
        # `in-progress`, but the request has not gone backwards.
        StageEntry(stage="assigned", at=REASSIGNED),
    ])
    session.add(request)
    session.flush()
    session.expire(request)

    assert request.stage == "in-progress"
    assert request.created_at == SUBMITTED
    assert [e.stage for e in request.stage_history] == ["submitted", "assigned", "in-progress"]
    # Usable in queries too, not just on a loaded object.
    assert session.scalars(select(ServiceRequest.id).where(ServiceRequest.stage == "in-progress")).all() == ["REQ-1"]


def test_a_stage_is_reached_at_most_once(session: Session, unit: Unit) -> None:
    session.add(make_request(unit, stage_history=[
        StageEntry(stage="submitted", at=SUBMITTED),
        StageEntry(stage="submitted", at=ASSIGNED),
    ]))
    with pytest.raises(IntegrityError, match="pk_stage_history"):
        session.flush()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"type": "housekeeping", "category": "deep-clean", "priority": "urgent"}, "housekeeping_is_not_urgent"),
        ({"charge": Decimal("120")}, "maintenance_is_not_charged"),
        ({"type": "housekeeping", "category": "deep-clean", "charge": Decimal("-1")}, "charge_not_negative"),
        ({"category": "deep-clean"}, "maintenance_category_known"),
        ({"schedule_date": date(2026, 9, 24)}, "schedule_date_and_slot_together"),
        ({"hand_back_reason": "No access to the unit"}, "hand_back_reason_and_time_together"),
        ({"priority": "whenever"}, "ck_service_requests_priority"),
    ],
)
def test_refuses_what_the_rules_forbid(
    session: Session, unit: Unit, overrides: dict[str, object], constraint: str
) -> None:
    session.add(make_request(unit, **overrides))
    with pytest.raises(IntegrityError, match=constraint):
        session.flush()


def test_housekeeping_booking_keeps_its_charge_and_slot(session: Session, unit: Unit) -> None:
    session.add(make_request(
        unit,
        type="housekeeping",
        category="deep-clean",
        charge=Decimal("320"),
        schedule_date=date(2026, 9, 24),
        schedule_slot="10AM–1PM",
    ))
    session.flush()  # no constraint objects


def test_deleting_a_request_deletes_its_history_and_photos(session: Session, unit: Unit) -> None:
    request = make_request(unit, photos=[Photo(position=0, name="tap.jpg", data_url="data:image/jpeg;base64,AA==")])
    session.add(request)
    session.flush()

    session.delete(request)
    session.flush()

    assert session.scalars(select(StageEntry)).all() == []
    assert session.scalars(select(Photo)).all() == []


def test_migrations_match_the_models(session: Session) -> None:
    """Fails when a model changes without a migration to match."""
    context = MigrationContext.configure(session.connection())
    assert compare_metadata(context, Base.metadata) == []


def test_migrations_have_the_models_check_constraints(session: Session) -> None:
    """Autogenerate doesn't compare CHECK constraints, so the test above can't
    see one added or removed: a new rule on a model needs a hand-written
    `op.create_check_constraint` in its migration. This catches a forgotten one.
    """
    inspector = inspect(session.connection())
    for table in Base.metadata.sorted_tables:
        in_models = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        in_database = {c["name"] for c in inspector.get_check_constraints(table.name)}
        assert in_models == in_database, table.name
