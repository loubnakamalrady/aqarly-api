"""The admin portals' writes and the reads they change: every guard refuses
with its message, and retiring keeps history intact."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import HousekeepingRate, Property, ServiceRequest, StageEntry, Staff, Tenant, Unit

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def request(id: str, stages: list[str], *, assignee: str | None = None, category: str = "plumbing",
            unit: str = "unit-a", priority: str = "normal") -> ServiceRequest:
    housekeeping = category in ("standard-clean", "deep-clean")
    return ServiceRequest(
        id=id, unit_id=unit, tenant_id="ten-a", type="housekeeping" if housekeeping else "maintenance",
        category=category, priority=priority, summary=f"{category} {id}", description="",
        assignee_id=assignee, charge=Decimal(120) if housekeeping else None,
        stage_history=[StageEntry(stage=s, at=T0 + timedelta(hours=i)) for i, s in enumerate(stages)],
    )


@pytest.fixture(autouse=True)
def world(session: Session) -> None:
    tenant = Tenant(id="ten-a", name="Layla Al Habsi", phone="+000 000 0001", email="layla@example.com")
    marina = Property(id="prop-marina", name="Marina Heights", address="Marina District")
    jade = Property(id="prop-jade", name="Jade Court", address="Al Nahda")
    session.add_all([
        Unit(id="unit-a", label="0101", status="occupied", bedrooms=1, bathrooms=1, property=marina, tenant=tenant),
        Unit(id="unit-b", label="0201", status="vacant", bedrooms=2, bathrooms=1, property=jade),
        Staff(id="stf-haddad", name="Youssef Haddad", phone="+000 000 0101", role="maintenance"),
        Staff(id="stf-kimani", name="Grace Kimani", phone="+000 000 0102", role="maintenance"),
        Staff(id="stf-castillo", name="Ana Castillo", phone="+000 000 0105", role="housekeeping"),
        HousekeepingRate(service_type="standard-clean", label="Standard clean", price=120, position=0),
        HousekeepingRate(service_type="deep-clean", label="Deep clean", price=320, position=1),
    ])
    session.flush()
    session.add_all([
        request("REQ-1", ["submitted"]),
        request("REQ-2", ["submitted", "assigned", "in-progress"], assignee="stf-haddad"),
        request("REQ-3", ["submitted", "assigned", "in-progress", "done"], assignee="stf-kimani"),
        request("REQ-4", ["submitted"], category="standard-clean"),
    ])
    session.commit()


def refused(response, status: int, detail: str) -> None:
    assert (response.status_code, response.json()) == (status, {"detail": detail})


# --- Raising requests --------------------------------------------------------------


def test_raising_maintenance_numbers_it_and_can_assign_it(client: TestClient) -> None:
    response = client.post("/requests", json={
        "unitId": "unit-a", "category": "plumbing", "priority": "urgent", "summary": " Leak ",
        "photos": [{"dataUrl": "data:,x"}, {"name": "empty"}], "assigneeId": "stf-haddad",
    })
    assert response.status_code == 201
    body = response.json()
    assert (body["id"], body["summary"], body["stage"], body["assigneeId"]) == ("REQ-5", "Leak", "assigned", "stf-haddad")
    assert (body["tenantId"], body["origin"], body["charge"]) == ("ten-a", "ops", None)
    assert [p["name"] for p in body["photos"]] == ["Photo"]  # the one without bytes is dropped


def test_a_booking_takes_the_card_price_and_is_never_urgent(client: TestClient) -> None:
    body = client.post("/requests", json={
        "unitId": "unit-a", "category": "deep-clean", "priority": "urgent", "summary": "Deep clean",
        "scheduledDate": "2026-09-30", "scheduledSlot": "9AM–1PM", "origin": "tenant",
    }).json()
    assert (body["type"], body["priority"], body["charge"]) == ("housekeeping", "normal", 320)
    assert body["schedule"] == {"date": "2026-09-30", "slot": "9AM–1PM"}


@pytest.mark.parametrize(("body", "detail"), [
    ({"unitId": "unit-x", "category": "plumbing", "summary": "x"}, "Unknown unit unit-x"),
    ({"unitId": "unit-a", "category": "plumbing", "summary": "  "}, "A request needs a summary"),
    ({"unitId": "unit-a", "category": "gardening", "summary": "x"}, "Unknown category gardening"),
    ({"unitId": "unit-a", "category": "deep-clean", "summary": "x", "scheduledSlot": "1PM–9AM"}, "Unknown time slot 1PM–9AM"),
    ({"unitId": "unit-a", "category": "plumbing", "summary": "x", "assigneeId": "stf-nobody"}, "Unknown staff member stf-nobody"),
])
def test_raising_refuses_with_a_reason(client: TestClient, body: dict, detail: str) -> None:
    refused(client.post("/requests", json=body), 400, detail)
    assert client.get("/requests/REQ-5").status_code == 404  # nothing half-made


# --- Assigning, priority, removal ------------------------------------------------


def test_assigning_moves_unassigned_work_and_skips_closed_work(client: TestClient) -> None:
    touched = client.post("/requests/assign", json={"ids": ["REQ-1", "REQ-3", "NOPE"], "assigneeId": "stf-kimani"}).json()
    assert [(r["id"], r["stage"]) for r in touched] == [("REQ-1", "assigned")]


def test_reassigning_work_in_flight_does_not_send_it_back(client: TestClient) -> None:
    [moved] = client.post("/requests/assign", json={"ids": ["REQ-2"], "assigneeId": "stf-kimani"}).json()
    assert (moved["stage"], moved["assigneeId"]) == ("in-progress", "stf-kimani")
    assert [e["stage"] for e in moved["stageHistory"]] == ["submitted", "assigned", "in-progress"]


def test_housekeeping_cannot_be_made_urgent(client: TestClient) -> None:
    refused(client.post("/requests/priority", json={"ids": ["REQ-4"], "priority": "urgent"}), 400,
            "Housekeeping is booked into a slot and has no emergency tier")
    refused(client.post("/requests/priority", json={"ids": ["REQ-1"], "priority": "asap"}), 400, "Unknown priority asap")
    assert client.post("/requests/priority", json={"ids": ["REQ-1"], "priority": "urgent"}).json()[0]["priority"] == "urgent"


def test_deleting_returns_what_was_removed(client: TestClient, session: Session) -> None:
    removed = client.post("/requests/delete", json={"ids": ["REQ-3", ""]}).json()
    assert [(r["id"], r["stage"]) for r in removed] == [("REQ-3", "done")]
    assert session.scalars(select(StageEntry).where(StageEntry.request_id == "REQ-3")).all() == []


# --- The rate card ---------------------------------------------------------------


def test_a_new_rate_goes_at_the_end_of_the_card(client: TestClient) -> None:
    rate = client.post("/housekeeping-rates", json={"label": " Window clean ", "price": "95.5"}).json()
    assert (rate["serviceType"], rate["label"], rate["price"]) == ("window-clean", "Window clean", 96)
    assert [r["serviceType"] for r in client.get("/housekeeping-rates").json()] == ["standard-clean", "deep-clean", "window-clean"]


@pytest.mark.parametrize(("body", "detail"), [
    ({"label": "  ", "price": 10}, "A rate needs a service name"),
    ({"label": "Ironing", "price": "0"}, "A rate needs a price above zero"),
    ({"label": "Ironing", "price": "ten"}, "A rate needs a price above zero"),
    ({"label": "deep clean", "price": 10}, "deep clean is already a service"),
    ({"label": "Plumbing", "price": 10}, "Plumbing is already a service"),
])
def test_adding_a_rate_refuses_with_a_reason(client: TestClient, body: dict, detail: str) -> None:
    refused(client.post("/housekeeping-rates", json=body), 400, detail)


def test_a_rate_with_open_bookings_cannot_leave_the_card(client: TestClient) -> None:
    refused(client.delete("/housekeeping-rates/standard-clean"), 409, "1 open booking uses this service — close them first")
    refused(client.delete("/housekeeping-rates/gardening"), 404, "Unknown service gardening")


def test_a_retired_rate_leaves_the_card_but_keeps_its_name(client: TestClient) -> None:
    retired = client.delete("/housekeeping-rates/deep-clean").json()
    assert retired["retiredAt"] is not None
    assert [r["serviceType"] for r in client.get("/housekeeping-rates").json()] == ["standard-clean"]
    everything = client.get("/housekeeping-rates?includeRetired=true").json()
    assert [(r["serviceType"], r["label"]) for r in everything][1] == ("deep-clean", "Deep clean")
    # It can't be booked any more…
    refused(client.post("/requests", json={"unitId": "unit-a", "category": "deep-clean", "summary": "x"}), 400,
            "Deep clean is no longer on the rate card")
    # …and adding it again brings it back, at the end, with the new price.
    back = client.post("/housekeeping-rates", json={"label": "Deep clean", "price": 350}).json()
    assert (back["price"], back["retiredAt"]) == (350, None)


# --- The roster --------------------------------------------------------------------


def test_new_staff_get_an_id_from_their_surname(client: TestClient) -> None:
    first = client.post("/staff", json={"name": "Sara Haddad", "phone": "050 123 4567", "role": "maintenance"}).json()
    assert first["id"] == "stf-haddad-2"  # stf-haddad is taken


@pytest.mark.parametrize(("body", "detail"), [
    ({"name": " ", "phone": "0501234567", "role": "maintenance"}, "A staff member needs a name"),
    ({"name": "X Y", "phone": "0501234567", "role": "gardener"}, "Unknown trade gardener"),
    ({"name": "X Y", "phone": "12ab", "role": "maintenance"}, "Enter a valid mobile number"),
    ({"name": "X Y", "phone": "123", "role": "maintenance"}, "Enter a valid mobile number"),
])
def test_adding_staff_refuses_with_a_reason(client: TestClient, body: dict, detail: str) -> None:
    refused(client.post("/staff", json=body), 400, detail)


def test_updating_staff_keeps_their_photo_unless_given_one(client: TestClient, session: Session) -> None:
    session.get_one(Staff, "stf-kimani").photo = "data:,old"
    session.commit()
    updated = client.put("/staff/stf-kimani", json={"name": "Grace K", "phone": "0501234567", "role": "maintenance"}).json()
    assert (updated["name"], updated["photo"]) == ("Grace K", "data:,old")


def test_staff_holding_open_work_cannot_leave(client: TestClient) -> None:
    refused(client.delete("/staff/stf-haddad"), 409, "1 open request is still assigned — reassign it first")


def test_retired_staff_leave_the_roster_but_keep_their_closed_work(client: TestClient) -> None:
    assert client.delete("/staff/stf-kimani").json()["retiredAt"] is not None

    assert "stf-kimani" not in [m["id"] for m in client.get("/staff/roster").json()]
    assert "stf-kimani" not in [c["id"] for c in client.get("/requests/REQ-1/candidates").json()]
    closed = client.get("/requests/REQ-3").json()
    assert closed["assignee"]["name"] == "Grace Kimani"  # still says who did it
    refused(client.post("/requests/assign", json={"ids": ["REQ-1"], "assigneeId": "stf-kimani"}), 400,
            "Unknown staff member stf-kimani")
    refused(client.delete("/staff/stf-kimani"), 404, "Unknown staff member stf-kimani")


# --- Reads the writes feed ----------------------------------------------------------


def test_candidates_rank_by_capacity_then_building_then_load(client: TestClient) -> None:
    candidates = client.get("/requests/REQ-1/candidates").json()
    # Kimani closed work in this building and holds nothing open; Haddad holds
    # open work here. Castillo is housekeeping, so isn't a candidate at all.
    assert [(c["id"], c["inBuilding"], c["load"], c["score"]) for c in candidates] == [
        ("stf-kimani", True, 0, 10),
        ("stf-haddad", True, 1, 9),
    ]


def test_the_queue_filters_narrow_together(client: TestClient) -> None:
    ids = lambda q: [r["id"] for r in client.get(f"/requests{q}").json()]
    assert ids("?assigneeId=unassigned") == ["REQ-1", "REQ-4"]
    assert ids("?type=maintenance&open=true") == ["REQ-1", "REQ-2"]
    assert ids("?search=HADDAD") == ["REQ-2"]
    assert ids("?search=100%25") == []  # a % in the search is text, not a wildcard
    assert ids("?propertyId=prop-jade") == []
    assert ids("?sort=newest&type=maintenance") == ["REQ-3", "REQ-2", "REQ-1"]


def test_the_dashboard_counts_one_trade(client: TestClient) -> None:
    stats = client.get("/reports/dashboard").json()
    assert (stats["open"], stats["unassigned"], stats["inProgress"], stats["closed"]) == (2, 1, 1, 1)
    assert client.get("/reports/dashboard?type=housekeeping").json()["open"] == 1
