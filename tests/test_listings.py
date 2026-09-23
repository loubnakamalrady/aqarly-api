from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Listing


def listing(slug: str, **overrides: object) -> Listing:
    fields: dict[str, object] = {
        "slug": slug,
        "title": slug.replace("-", " ").title(),
        "type": "apartment",
        "purpose": "sale",
        "price": Decimal("1250000"),
        "currency": "AED",
        "bedrooms": 2,
        "bathrooms": 2,
        "area_sqft": 1100,
        "city": "Dubai",
        "area": "Marina",
        "images": ["/img/a.jpg", "/img/b.jpg"],
        "description": "",
        "featured": False,
    }
    return Listing(**(fields | overrides))


@pytest.fixture(autouse=True)
def listings(session: Session) -> None:
    session.add_all([
        listing("marina-2br"),
        listing("jlt-villa", type="villa", purpose="rent", price=Decimal("180000"), featured=True),
        listing("downtown-studio", featured=True),
    ])
    session.flush()


def test_lists_every_listing_in_the_frontend_shape(client: TestClient) -> None:
    response = client.get("/listings")

    assert response.status_code == 200
    assert [item["slug"] for item in response.json()] == ["downtown-studio", "jlt-villa", "marina-2br"]
    assert response.json()[2] == {
        "slug": "marina-2br",
        "title": "Marina 2Br",
        "type": "apartment",
        "purpose": "sale",
        "price": 1250000,
        "currency": "AED",
        "bedrooms": 2,
        "bathrooms": 2,
        "areaSqft": 1100,
        "location": {"city": "Dubai", "area": "Marina"},
        "images": ["/img/a.jpg", "/img/b.jpg"],
        "description": "",
        "featured": False,
    }


@pytest.mark.parametrize(
    ("query", "slugs"),
    [
        ("?purpose=rent", ["jlt-villa"]),
        ("?type=apartment", ["downtown-studio", "marina-2br"]),
        ("?featured=true", ["downtown-studio", "jlt-villa"]),
        ("?featured=false", ["marina-2br"]),
        ("?purpose=sale&featured=true", ["downtown-studio"]),
        ("?purpose=lease", []),
    ],
)
def test_filters_combine(client: TestClient, query: str, slugs: list[str]) -> None:
    assert [item["slug"] for item in client.get(f"/listings{query}").json()] == slugs


def test_gets_one_listing_by_slug(client: TestClient) -> None:
    response = client.get("/listings/jlt-villa")
    assert response.status_code == 200
    assert response.json()["price"] == 180000


def test_an_unknown_slug_is_404_with_a_reason(client: TestClient) -> None:
    response = client.get("/listings/nowhere")
    assert response.status_code == 404
    assert response.json() == {"detail": "No listing nowhere"}
