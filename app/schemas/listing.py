from app.models import Listing
from app.schemas.common import CamelModel, Money


class LocationOut(CamelModel):
    city: str
    area: str


class ListingOut(CamelModel):
    """`Listing` in types.ts."""

    slug: str
    title: str
    type: str
    purpose: str
    price: Money
    currency: str
    bedrooms: int
    bathrooms: int
    area_sqft: int
    location: LocationOut
    images: list[str]
    description: str
    featured: bool

    @classmethod
    def from_model(cls, listing: Listing) -> "ListingOut":
        return cls.model_validate(
            {
                **{name: getattr(listing, name) for name in cls.model_fields if name != "location"},
                "location": LocationOut(city=listing.city, area=listing.area),
            }
        )
