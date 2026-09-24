"""Signing in with a phone and a code, registering, and who may see what."""

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import (
    Admin,
    HousekeepingRate,
    LoginCode,
    Property,
    ServiceRequest,
    StageEntry,
    Staff,
    Tenant,
    Unit,
)
from app.settings import get_settings

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
LAYLA, OMAR, HADDAD, OPS, HK = "+000 000 0001", "+000 000 0002", "+000 000 0101", "+000 000 0900", "+000 000 0901"
NEWCOMER = "+971 50 555 0100"


def request(id: str, tenant: str, unit: str, category: str = "plumbing") -> ServiceRequest:
    housekeeping = category == "standard-clean"
    return ServiceRequest(
        id=id, unit_id=unit, tenant_id=tenant, type="housekeeping" if housekeeping else "maintenance",
        category=category, priority="normal", summary=f"{category} {id}", description="",
        assignee_id=None, charge=Decimal(120) if housekeeping else None,
        stage_history=[StageEntry(stage="submitted", at=T0)],
    )


@pytest.fixture(autouse=True)
def world(session: Session) -> None:
    marina = Property(id="prop-marina", name="Marina Heights", address="Marina District")
    layla = Tenant(id="ten-alhabsi", name="Layla Al Habsi", phone=LAYLA, email="layla@example.com")
    omar = Tenant(id="ten-farouk", name="Omar Farouk", phone=OMAR, email="omar@example.com")
    session.add_all([
        Unit(id="unit-a", label="0101", status="occupied", bedrooms=1, bathrooms=1, property=marina, tenant=layla),
        Unit(id="unit-b", label="0102", status="vacant", bedrooms=2, bathrooms=1, property=marina),
        Unit(id="unit-c", label="0103", status="occupied", bedrooms=2, bathrooms=2, property=marina, tenant=omar),
        Staff(id="stf-haddad", name="Youssef Haddad", phone=HADDAD, role="maintenance"),
        Admin(id="adm-ops", name="Property admin", phone=OPS, trade="maintenance"),
        Admin(id="adm-hk", name="Housekeeping admin", phone=HK, trade="housekeeping"),
        HousekeepingRate(service_type="standard-clean", label="Standard clean", price=120, position=0),
    ])
    session.flush()
    session.add_all([
        request("REQ-1", "ten-alhabsi", "unit-a"),
        request("REQ-2", "ten-farouk", "unit-c"),
        request("REQ-3", "ten-alhabsi", "unit-a", category="standard-clean"),
    ])
    session.commit()


def code(client: TestClient, phone: str, app: str) -> str:
    response = client.post("/auth/code", json={"phone": phone, "app": app})
    assert response.status_code == 200, response.json()
    return response.json()["shownCode"]


def sign_in_as(client: TestClient, phone: str, app: str) -> dict[str, str]:
    """The real flow: ask for a code, prove it, keep the token."""
    response = client.post("/auth/verify", json={"phone": phone, "app": app, "code": code(client, phone, app)})
    assert response.status_code == 200, response.json()
    return {"X-Session": response.json()["token"]}


def refused(response, status: int, detail: str) -> None:
    assert (response.status_code, response.json()) == (status, {"detail": detail})


# --- Codes -------------------------------------------------------------------------


def test_a_tenant_signs_in_with_a_code(client: TestClient) -> None:
    shown = code(client, LAYLA, "tenant")
    assert len(shown) == 6 and shown.isdigit()

    body = client.post("/auth/verify", json={"phone": LAYLA, "app": "tenant", "code": shown}).json()

    assert body["me"]["kind"] == "tenant"
    assert body["me"]["tenant"]["name"] == "Layla Al Habsi"
    assert body["me"]["tenant"]["unit"]["label"] == "0101"
    me = client.get("/auth/me", headers={"X-Session": body["token"]}).json()
    assert me["kind"] == "tenant"


def test_a_phone_written_differently_is_the_same_phone(client: TestClient) -> None:
    shown = code(client, "+000-000-0001", "tenant")
    body = client.post("/auth/verify", json={"phone": "+0000000001", "app": "tenant", "code": shown}).json()
    assert body["me"]["tenant"]["id"] == "ten-alhabsi"


def test_a_wrong_code_is_refused_and_five_wrong_ones_end_it(client: TestClient) -> None:
    right = code(client, LAYLA, "tenant")
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(5):
        refused(client.post("/auth/verify", json={"phone": LAYLA, "app": "tenant", "code": wrong}), 400,
                "That code isn't right. Check it and try again.")
    # Even the right code is dead now.
    refused(client.post("/auth/verify", json={"phone": LAYLA, "app": "tenant", "code": right}), 400,
            "Too many wrong tries. Ask for a new code.")


def test_asking_again_kills_the_earlier_code(client: TestClient) -> None:
    first = code(client, LAYLA, "tenant")
    second = code(client, LAYLA, "tenant")
    if first != second:
        refused(client.post("/auth/verify", json={"phone": LAYLA, "app": "tenant", "code": first}), 400,
                "That code isn't right. Check it and try again.")
    assert client.post("/auth/verify", json={"phone": LAYLA, "app": "tenant", "code": second}).status_code == 200


def test_an_expired_code_is_refused(client: TestClient, session: Session) -> None:
    shown = code(client, LAYLA, "tenant")
    session.execute(update(LoginCode).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    session.commit()
    refused(client.post("/auth/verify", json={"phone": LAYLA, "app": "tenant", "code": shown}), 400,
            "That code has expired. Ask for a new one.")


def test_too_many_codes_are_refused_for_a_while(client: TestClient) -> None:
    for _ in range(5):
        code(client, LAYLA, "tenant")
    refused(client.post("/auth/code", json={"phone": LAYLA, "app": "tenant"}), 429,
            "Too many codes asked for. Wait a few minutes and try again.")


def test_a_number_that_cannot_be_a_phone_is_refused(client: TestClient) -> None:
    refused(client.post("/auth/code", json={"phone": "12", "app": "tenant"}), 400, "Enter a valid mobile number")


def test_codes_are_not_shown_once_texting_is_on(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "login_code_delivery", "sms")
    body = client.post("/auth/code", json={"phone": LAYLA, "app": "tenant"}).json()
    assert body == {"sent": True, "shownCode": None}


# --- Which account a phone opens ------------------------------------------------------


def test_the_field_app_needs_a_staff_account(client: TestClient) -> None:
    shown = code(client, NEWCOMER, "field")
    refused(client.post("/auth/verify", json={"phone": NEWCOMER, "app": "field", "code": shown}), 403,
            "There's no staff account for this number. Ask your manager to add you.")
    me = client.get("/auth/me", headers=sign_in_as(client, HADDAD, "field")).json()
    assert (me["kind"], me["staff"]["id"]) == ("staff", "stf-haddad")


def test_each_admin_portal_needs_its_own_admin(client: TestClient) -> None:
    shown = code(client, OPS, "housekeeping")
    refused(client.post("/auth/verify", json={"phone": OPS, "app": "housekeeping", "code": shown}), 403,
            "This number isn't an admin of this portal.")
    me = client.get("/auth/me", headers=sign_in_as(client, OPS, "ops")).json()
    assert (me["kind"], me["admin"]["trade"]) == ("admin", "maintenance")


def test_signing_out_ends_the_session(client: TestClient) -> None:
    session_headers = sign_in_as(client, LAYLA, "tenant")
    assert client.post("/auth/logout", headers=session_headers).status_code == 204
    refused(client.get("/auth/me", headers=session_headers), 401, "Sign in to continue.")


def test_nobody_signed_in_sees_nothing(client: TestClient) -> None:
    for path in ("/requests", "/units", "/reports/dashboard", "/staff/roster", "/properties", "/auth/me"):
        refused(client.get(path), 401, "Sign in to continue.")
    assert client.get("/listings").status_code == 200  # the marketing site is public


# --- Tenants see their own ------------------------------------------------------------


def test_a_tenant_sees_only_their_own_requests(client: TestClient) -> None:
    layla = sign_in_as(client, LAYLA, "tenant")

    assert sorted(r["id"] for r in client.get("/requests", headers=layla).json()) == ["REQ-1", "REQ-3"]
    refused(client.get("/requests?tenantId=ten-farouk", headers=layla), 403, "You can only see your own requests.")
    refused(client.get("/requests/REQ-2", headers=layla), 403, "That request isn't yours.")
    refused(client.get("/tenants/ten-farouk", headers=layla), 403, "You can only see your own account.")
    refused(client.get("/units", headers=layla), 403, "This is for the admin portals.")


def test_a_tenant_raises_requests_for_their_own_home_only(client: TestClient) -> None:
    layla = sign_in_as(client, LAYLA, "tenant")
    refused(client.post("/requests", headers=layla, json={"unitId": "unit-c", "category": "plumbing", "summary": "x"}),
            403, "You can only raise requests for your own home.")

    body = client.post("/requests", headers=layla, json={
        "unitId": "unit-a", "category": "plumbing", "summary": "Tap drips", "assigneeId": "stf-haddad", "origin": "ops",
    }).json()
    # Marked as theirs and left for ops to assign, whatever was sent.
    assert (body["origin"], body["assigneeId"], body["stage"]) == ("tenant", None, "submitted")


def test_a_tenant_can_read_the_rate_card_but_not_change_it(client: TestClient) -> None:
    layla = sign_in_as(client, LAYLA, "tenant")
    assert [r["serviceType"] for r in client.get("/housekeeping-rates", headers=layla).json()] == ["standard-clean"]
    refused(client.post("/housekeeping-rates", headers=layla, json={"label": "Ironing", "price": 50}), 403,
            "This is for the admin portals.")


# --- Admins see their own trade ------------------------------------------------------------


def test_an_admin_works_in_their_own_trade_only(client: TestClient) -> None:
    ops, hk = sign_in_as(client, OPS, "ops"), sign_in_as(client, HK, "housekeeping")

    assert [r["id"] for r in client.get("/requests", headers=ops).json()] == ["REQ-1", "REQ-2"]
    assert [r["id"] for r in client.get("/requests", headers=hk).json()] == ["REQ-3"]
    refused(client.get("/requests?type=housekeeping", headers=ops), 403, "This portal manages maintenance only.")
    refused(client.get("/requests/REQ-3", headers=ops), 403, "That request belongs to the other portal.")
    refused(client.post("/requests/delete", headers=hk, json={"ids": ["REQ-1"]}), 403,
            "Some of those requests belong to the other portal.")
    refused(client.post("/housekeeping-rates", headers=ops, json={"label": "Ironing", "price": 50}), 403,
            "The rate card belongs to the housekeeping portal.")
    refused(client.post("/requests/assign", headers=hk, json={"ids": ["REQ-3"], "assigneeId": "stf-haddad"}), 400,
            "Youssef Haddad works in maintenance, not housekeeping.")


# --- Registering --------------------------------------------------------------------------


def test_a_new_tenant_registers_and_waits_for_ops(client: TestClient) -> None:
    newcomer = sign_in_as(client, NEWCOMER, "tenant")
    assert client.get("/auth/me", headers=newcomer).json()["kind"] == "new"

    # They can see buildings and units to pick theirs…
    assert [p["id"] for p in client.get("/properties", headers=newcomer).json()] == ["prop-marina"]
    assert [u["label"] for u in client.get("/properties/prop-marina/units", headers=newcomer).json()] == ["0101", "0102", "0103"]

    me = client.post("/auth/register", headers=newcomer, json={"name": " Sara Nabil ", "unitId": "unit-b"}).json()
    assert (me["kind"], me["registration"]["name"], me["registration"]["decision"]) == ("registration", "Sara Nabil", None)

    # …but nothing of the unit's until ops confirms them.
    refused(client.get("/requests", headers=newcomer), 403, "Your building hasn't confirmed you yet.")
    refused(client.post("/auth/register", headers=newcomer, json={"name": "Sara", "unitId": "unit-b"}), 409,
            "Your registration is waiting for your building to confirm it.")


def test_ops_approving_makes_them_the_units_tenant(client: TestClient, session: Session) -> None:
    newcomer = sign_in_as(client, NEWCOMER, "tenant")
    client.post("/auth/register", headers=newcomer, json={"name": "Sara Nabil", "unitId": "unit-b"})
    ops = sign_in_as(client, OPS, "ops")

    [pending] = client.get("/registrations", headers=ops).json()
    assert (pending["unit"]["label"], pending["currentTenant"]) == ("0102", None)
    approved = client.post(f"/registrations/{pending['id']}/approve", headers=ops).json()
    assert approved["decision"] == "approved"

    # The same session sees their home now, without signing in again.
    me = client.get("/auth/me", headers=newcomer).json()
    assert (me["kind"], me["tenant"]["name"], me["tenant"]["unit"]["label"]) == ("tenant", "Sara Nabil", "0102")
    assert session.get_one(Unit, "unit-b").status == "occupied"
    refused(client.post(f"/registrations/{pending['id']}/approve", headers=ops), 409,
            "That registration was already approved.")


def test_approving_onto_an_occupied_unit_shows_and_replaces_its_tenant(client: TestClient) -> None:
    newcomer = sign_in_as(client, NEWCOMER, "tenant")
    client.post("/auth/register", headers=newcomer, json={"name": "Sara Nabil", "unitId": "unit-a"})
    ops = sign_in_as(client, OPS, "ops")

    [pending] = client.get("/registrations", headers=ops).json()
    assert pending["currentTenant"]["name"] == "Layla Al Habsi"  # ops sees who they'd replace
    client.post(f"/registrations/{pending['id']}/approve", headers=ops)

    layla = sign_in_as(client, LAYLA, "tenant")
    assert client.get("/auth/me", headers=layla).json()["tenant"]["unit"] is None


def test_a_declined_registration_can_be_made_again(client: TestClient) -> None:
    newcomer = sign_in_as(client, NEWCOMER, "tenant")
    client.post("/auth/register", headers=newcomer, json={"name": "Sara Nabil", "unitId": "unit-b"})
    ops = sign_in_as(client, OPS, "ops")
    [pending] = client.get("/registrations", headers=ops).json()
    client.post(f"/registrations/{pending['id']}/decline", headers=ops)

    assert client.get("/auth/me", headers=newcomer).json()["registration"]["decision"] == "declined"
    again = client.post("/auth/register", headers=newcomer, json={"name": "Sara Nabil", "unitId": "unit-b"}).json()
    assert again["registration"]["decision"] is None


@pytest.mark.parametrize(
    ("phone", "app", "detail"),
    [(HK, "housekeeping", "This is for the ops portal."), (LAYLA, "tenant", "This is for the admin portals.")],
)
def test_only_ops_decides_registrations(client: TestClient, phone: str, app: str, detail: str) -> None:
    refused(client.get("/registrations", headers=sign_in_as(client, phone, app)), 403, detail)


@pytest.mark.parametrize(
    ("body", "detail"),
    [({"name": " ", "unitId": "unit-b"}, "Enter your full name"), ({"name": "Sara"}, "Choose your building and unit")],
)
def test_registering_needs_a_name_and_a_unit(client: TestClient, body: dict, detail: str) -> None:
    refused(client.post("/auth/register", headers=sign_in_as(client, NEWCOMER, "tenant"), json=body), 400, detail)


# --- Alongside the staging lock ------------------------------------------------------------


def test_a_session_works_behind_the_staging_lock(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    layla = sign_in_as(client, LAYLA, "tenant")
    monkeypatch.setattr(get_settings(), "staging_password", "s3cret")
    basic = {"Authorization": "Basic " + base64.b64encode(b"staging:s3cret").decode()}

    assert client.get("/auth/me", headers=layla).status_code == 401  # the lock first
    assert client.get("/auth/me", headers=layla | basic).json()["kind"] == "tenant"
