from decimal import Decimal

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Listing(Base):
    """A marketing-site listing: `Listing` in types.ts, from `properties.json`.

    Separate from `Property` (a building operations manages) even though the
    frontend's `getProperties()` returns these.
    """

    __tablename__ = "listings"

    slug: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(32))
    purpose: Mapped[str] = mapped_column(String(32))
    price: Mapped[Decimal]
    currency: Mapped[str] = mapped_column(String(3))
    bedrooms: Mapped[int]
    bathrooms: Mapped[int]
    area_sqft: Mapped[int]
    # `location: { city, area }` in types.ts, flattened into two columns.
    city: Mapped[str] = mapped_column(String(100))
    area: Mapped[str] = mapped_column(String(100))
    # Image URLs, in display order.
    images: Mapped[list[str]] = mapped_column(ARRAY(Text))
    description: Mapped[str] = mapped_column(Text)
    featured: Mapped[bool]
