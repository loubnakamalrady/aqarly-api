from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_database_connected() -> None:
    # Hits the real Postgres from docker-compose, so this also proves the
    # connection string in .env works.
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "database": "connected"}
