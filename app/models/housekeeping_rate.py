from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class HousekeepingRate(Base):
    """One line of the housekeeping rate card.

    A booking copies `price` into its own `charge` when it is made, so a later
    change here never reprices it. A rate is retired rather than deleted once
    its bookings are closed: it leaves the card, but its label still names the
    bookings made against it. `category` on a request stays plain text rather
    than a foreign key, since maintenance categories aren't rates.
    """

    __tablename__ = "housekeeping_rates"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_not_negative"),
        UniqueConstraint("position", name="uq_housekeeping_rates_position"),
    )

    service_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    price: Mapped[Decimal]
    # Where the service sits on the card, as the admin listed it. A new rate
    # goes at the end.
    position: Mapped[int]
    # Set when the rate leaves the card. Retired rates can't be booked.
    retired_at: Mapped[datetime | None]
