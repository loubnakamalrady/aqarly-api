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


# --- Writes ------------------------------------------------------------------------

OPEN = {"submitted": day(1), "assigned": day(1)}
STARTED = {**OPEN, "in-progress": day(2)}
CLOSED = {**STARTED, "done": day(3)}
PHOTO = {"name": "after.jpg", "dataUrl": "data:image/jpeg;base64,AA=="}


def post(client: TestClient, action: str, job_id: str = "REQ-A", tech: str = "stf-haddad", **body):
    return client.post(f"/technicians/{tech}/jobs/{job_id}/{action}", json=body)


def add(session: Session, *requests: ServiceRequest) -> None:
    session.add_all(requests)
    session.commit()


def test_start_moves_a_job_in_progress(session: Session, client: TestClient) -> None:
    add(session, job("REQ-A", OPEN))

    response = post(client, "start")

    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "in-progress"
    assert [e["stage"] for e in body["stageHistory"]] == ["submitted", "assigned", "in-progress"]


def test_starting_twice_changes_nothing(session: Session, client: TestClient) -> None:
    add(session, job("REQ-A", STARTED))

    body = post(client, "start").json()

    assert body["stage"] == "in-progress"
    assert body["stageHistory"][2]["at"] == "2026-08-03T09:00:00Z"  # not re-dated


def test_complete_closes_a_started_job_with_its_photos(session: Session, client: TestClient) -> None:
    add(session, job("REQ-A", STARTED))

    response = post(
        client, "complete",
        notes="  Replaced the washer.  ",
        photos=[PHOTO, {"dataUrl": "data:image/png;base64,AA=="}, {"name": "empty.jpg"}],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "done"
    assert body["completionNotes"] == "Replaced the washer."
    # The photo without bytes is dropped; one without a name is called "Photo".
    assert [p["name"] for p in body["completionPhotos"]] == ["after.jpg", "Photo"]
    assert body["photos"] == []  # the tenant's photos of the fault are separate


def test_complete_keeps_at_most_ten_photos_and_blank_notes_are_none(session: Session, client: TestClient) -> None:
    add(session, job("REQ-A", STARTED))

    body = post(client, "complete", notes="   ", photos=[PHOTO] * 12).json()

    assert len(body["completionPhotos"]) == field.MAX_COMPLETION_PHOTOS
    assert body["completionNotes"] is None


def test_completing_never_changes_the_charge(session: Session, client: TestClient) -> None:
    session.add(Staff(id="stf-castillo", name="Ana Castillo", phone="+000 000 0105", role="housekeeping"))
    booking = job("REQ-A", STARTED, assignee="stf-castillo", category="deep-clean")
    booking.type = "housekeeping"
    booking.charge = 320
    add(session, booking)

    body = post(client, "complete", tech="stf-castillo", photos=[PHOTO, PHOTO]).json()

    assert body["stage"] == "done"
    assert body["charge"] == 320


def test_hand_back_returns_the_job_to_the_office(session: Session, client: TestClient) -> None:
    add(session, job("REQ-A", STARTED))

    response = post(client, "hand-back", reason="  Needs a part we don't carry  ")

    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "submitted"
    assert body["assigneeId"] is None
    assert [e["stage"] for e in body["stageHistory"]] == ["submitted"]
    assert body["handBack"]["reason"] == "Needs a part we don't carry"
    assert body["handBack"]["by"] == "stf-haddad"
    assert body["handBack"]["byName"] == "Youssef Haddad"
    # And it has left the technician's hands.
    assert client.get("/technicians/stf-haddad/worklist").json()["counts"]["left"] == 0
    assert client.get("/technicians/stf-haddad/jobs/REQ-A").status_code == 403


@pytest.mark.parametrize(
    ("stages", "action", "body", "status", "detail"),
    [
        (OPEN, "complete", {"photos": [PHOTO, PHOTO]}, 409, "Start the job before closing it"),
        (STARTED, "complete", {"photos": [PHOTO, {"name": "no-bytes.jpg"}]}, 400, "2 photos are needed to close a job"),
        (STARTED, "complete", {}, 400, "2 photos are needed to close a job"),
        (STARTED, "hand-back", {"reason": "   "}, 400, "Say why you can't do it"),
        (STARTED, "hand-back", {}, 400, "Say why you can't do it"),
        (CLOSED, "start", {}, 409, "That job is already closed"),
        (CLOSED, "complete", {"photos": [PHOTO, PHOTO]}, 409, "That job is already closed"),
        (CLOSED, "hand-back", {"reason": "Too late"}, 409, "That job is already closed"),
    ],
)
def test_writes_refuse_with_a_reason(
    session: Session, client: TestClient, stages, action, body, status, detail
) -> None:
    add(session, job("REQ-A", stages))

    response = post(client, action, **body)

    assert (response.status_code, response.json()) == (status, {"detail": detail})


@pytest.mark.parametrize("action", ["start", "complete", "hand-back"])
def test_only_the_holder_can_act_on_a_job(session: Session, client: TestClient, action: str) -> None:
    add(session, job("REQ-A", STARTED))

    response = post(client, action, tech="stf-kimani", reason="x", photos=[PHOTO, PHOTO])

    assert (response.status_code, response.json()) == (403, {"detail": "That job is not yours to change"})
    assert client.get("/technicians/stf-haddad/jobs/REQ-A").json()["stage"] == "in-progress"


@pytest.mark.parametrize(
    ("job_id", "tech", "detail"),
    [("REQ-NOPE", "stf-haddad", "No job REQ-NOPE"), ("REQ-A", "stf-nobody", "No technician stf-nobody")],
)
def test_writes_to_unknown_jobs_or_technicians_are_404(
    session: Session, client: TestClient, job_id: str, tech: str, detail: str
) -> None:
    add(session, job("REQ-A", STARTED))

    response = post(client, "start", job_id=job_id, tech=tech)

    assert (response.status_code, response.json()) == (404, {"detail": detail})
