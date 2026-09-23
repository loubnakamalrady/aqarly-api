import base64

import pytest
from fastapi.testclient import TestClient

from app.settings import Settings, get_settings


def basic(user: str, password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


@pytest.fixture
def locked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "staging_password", "correct horse")


def test_open_when_no_password_is_set(client: TestClient) -> None:
    assert client.get("/listings").status_code == 200


def test_locked_asks_for_the_password(locked: None, client: TestClient) -> None:
    response = client.get("/listings")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")  # the browser's prompt
    assert client.get("/docs").status_code == 401  # Swagger too


@pytest.mark.parametrize(
    "headers",
    [
        basic("staging", "wrong"),
        basic("someone", "correct horse"),
        {"Authorization": "Bearer correct horse"},
        {"Authorization": "Basic not-base64!"},
    ],
)
def test_locked_refuses_wrong_credentials(locked: None, client: TestClient, headers: dict) -> None:
    assert client.get("/listings", headers=headers).status_code == 401


def test_locked_lets_the_right_credentials_in(locked: None, client: TestClient) -> None:
    assert client.get("/listings", headers=basic("staging", "correct horse")).status_code == 200


def test_health_stays_open_for_the_host(locked: None, client: TestClient) -> None:
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "given",
    [
        "postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
        "postgres://u:p@ep-x.neon.tech/db?sslmode=require",
        "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require",
    ],
)
def test_a_hosts_connection_string_is_used_as_given(given: str) -> None:
    settings = Settings(database_url=given)
    assert settings.database_url == "postgresql+psycopg://u:p@ep-x.neon.tech/db?sslmode=require"
