"""The technician field app: worklist order, the repeat-fault flag, and who
may read a job."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Property, ServiceRequest, StageEntry, Staff, Unit
from app.models.enums import Stage
from app.services import field

DAY_ONE = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def day(n: int) -> datetime:
    return DAY_ONE + timedelta(days=n)


def job(
    id: str,
    stages: dict[Stage, datetime],
    *,
    assignee: str | None = "stf-haddad",
    unit: str = "unit-a",
    priority: str = "normal",
    category: str = "plumbing",
) -> ServiceRequest:
    return ServiceRequest(
        id=id,
        unit_id=unit,
        tenant_id=None,
        type="maintenance",
        category=category,
        priority=priority,
        summary=f"{category} job {id}",
        description="",
        assignee_id=assignee,
        stage_history=[StageEntry(stage=s, at=at) for s, at in stages.items()],
    )


@pytest.fixture(autouse=True)
def people_and_places(session: Session) -> None:
    property = Property(id="prop-1", name="Marina Heights", address="Marina District")
    session.add_all([
        Unit(id="unit-a", label="0101", status="occupied", bedrooms=1, bathrooms=1, property=property),
        Unit(id="unit-b", label="0102", status="occupied", bedrooms=1, bathrooms=1, property=property),
        Staff(id="stf-haddad", name="Youssef Haddad", phone="+000 000 0101", role="maintenance"),
        Staff(id="stf-kimani", name="Grace Kimani", phone="+000 000 0102", role="maintenance"),
    ])
    session.flush()


# --- Worklist ------------------------------------------------------------------


def test_worklist_order_started_then_emergency_then_oldest(session: Session, client: TestClient) -> None:
    session.add_all([
        job("REQ-A", {"submitted": day(1), "assigned": day(1)}),
        job("REQ-B", {"submitted": day(3), "assigned": day(3)}, priority="urgent"),
        job("REQ-C", {"submitted": day(4), "assigned": day(4), "in-progress": day(5)}),
        job("REQ-D", {"submitted": day(2), "assigned": day(2)}),
        job("REQ-E", {"submitted": day(0), "assigned": day(0), "in-progress": day(1), "done": day(2)}),
        job("REQ-F", {"submitted": day(0), "assigned": day(0), "in-progress": day(1), "done": day(6)}),
        job("REQ-G", {"submitted": day(0), "assigned": day(0)}, assignee="stf-kimani"),
    ])
    session.flush()

    worklist = client.get("/technicians/stf-haddad/worklist").json()

    # Started work leads even though it's the newest; the emergency is next
    # even though it's newer than the two standard jobs; those go oldest first.
    assert worklist["next"]["id"] == "REQ-C"
    assert [j["id"] for j in worklist["queued"]] == ["REQ-B", "REQ-A", "REQ-D"]
    # Most recently closed first. Kimani's job isn't here at all.
    assert [j["id"] for j in worklist["closed"]] == ["REQ-F", "REQ-E"]
    assert worklist["counts"] == {"left": 4, "urgent": 1, "closed": 2}
    assert worklist["technician"]["id"] == "stf-haddad"


def test_an_empty_worklist(client: TestClient) -> None:
    worklist = client.get("/technicians/stf-kimani/worklist").json()
    assert worklist["next"] is None
    assert worklist["queued"] == worklist["closed"] == []
    assert worklist["counts"] == {"left": 0, "urgent": 0, "closed": 0}


def test_an_unknown_technician_has_no_worklist(client: TestClient) -> None:
    response = client.get("/technicians/stf-nobody/worklist")
    assert response.status_code == 404
    assert response.json() == {"detail": "No technician stf-nobody"}


# --- Jobs ----------------------------------------------------------------------


def test_a_job_reads_in_the_frontend_shape(session: Session, client: TestClient) -> None:
    session.add(job("REQ-A", {"submitted": day(1), "assigned": day(2)}))
    session.flush()

    body = client.get("/technicians/stf-haddad/jobs/REQ-A").json()

    assert body["stage"] == "assigned"
    assert body["createdAt"] == "2026-08-02T09:00:00Z"
    assert body["unitId"] == "unit-a"
    assert body["property"]["name"] == "Marina Heights"
    assert body["assignee"]["name"] == "Youssef Haddad"
    assert body["repeatFault"] is None
    assert "tier" not in body  # the frontend derives it with tierFor()


def test_a_job_is_refused_to_a_technician_who_does_not_hold_it(session: Session, client: TestClient) -> None:
    session.add(job("REQ-A", {"submitted": day(1), "assigned": day(1)}))
    session.flush()

    response = client.get("/technicians/stf-kimani/jobs/REQ-A")
    assert response.status_code == 403
    assert response.json() == {"detail": "REQ-A isn't assigned to you"}


def test_a_closed_job_stays_readable_by_whoever_closed_it(session: Session, client: TestClient) -> None:
    session.add(job("REQ-A", {"submitted": day(1), "assigned": day(1), "in-progress": day(2), "done": day(3)}))
    session.flush()

    assert client.get("/technicians/stf-haddad/jobs/REQ-A").json()["stage"] == "done"


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/technicians/stf-haddad/jobs/REQ-NOPE", "No job REQ-NOPE"),
        ("/technicians/stf-nobody/jobs/REQ-A", "No technician stf-nobody"),
    ],
)
def test_unknown_jobs_and_technicians_are_404(
    session: Session, client: TestClient, path: str, detail: str
) -> None:
    session.add(job("REQ-A", {"submitted": day(1), "assigned": day(1)}))
    session.flush()

    response = client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": detail}


# --- Repeat fault ------------------------------------------------------------------
# Called through the service with a fixed `now`, because the rule's window is
# measured back from the moment of the read.

NOW = day(300)
WINDOW_START = NOW - field.REPEAT_FAULT_WITHIN


def test_a_third_fault_of_one_kind_flags_that_kind_only(session: Session) -> None:
    session.add_all([
        job("REQ-1", {"submitted": day(100), "assigned": day(100), "in-progress": day(101), "done": day(102)}),
        job("REQ-2", {"submitted": day(200), "assigned": day(200), "in-progress": day(201), "done": day(202)}),
        job("REQ-3", {"submitted": day(290), "assigned": day(290)}),
        job("REQ-4", {"submitted": day(295), "assigned": day(295)}, category="electrical"),
    ])
    session.flush()

    plumbing = field.get_job(session, "REQ-3", "stf-haddad", now=NOW)
    electrical = field.get_job(session, "REQ-4", "stf-haddad", now=NOW)

    assert plumbing.repeat_fault is not None
    assert plumbing.repeat_fault.model_dump() == {"category": "plumbing", "count": 3, "spend": 0}
    # Same unit, but the ticket isn't the thing that keeps failing.
    assert electrical.repeat_fault is None


def test_faults_before_the_window_do_not_count(session: Session) -> None:
    before = WINDOW_START - timedelta(days=1)
    session.add_all([
        job("REQ-1", {"submitted": before, "assigned": before, "done": before}),
        job("REQ-2", {"submitted": day(200), "assigned": day(200), "done": day(201)}),
        job("REQ-3", {"submitted": day(290), "assigned": day(290)}),
    ])
    session.flush()

    assert field.get_job(session, "REQ-3", "stf-haddad", now=NOW).repeat_fault is None


def test_faults_on_another_unit_do_not_count(session: Session) -> None:
    session.add_all([
        job("REQ-1", {"submitted": day(250), "assigned": day(250)}, unit="unit-b"),
        job("REQ-2", {"submitted": day(260), "assigned": day(260)}, unit="unit-b"),
        job("REQ-3", {"submitted": day(290), "assigned": day(290)}),
    ])
    session.flush()

    assert field.get_job(session, "REQ-3", "stf-haddad", now=NOW).repeat_fault is None
