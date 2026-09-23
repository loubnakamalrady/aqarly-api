from decimal import Decimal

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class HousekeepingRate(Base):
    """One line of the housekeeping rate card.

    A booking copies `price` into its own `charge` when it is made, so a later
    change here never reprices it. That is also why a request's `category` is
    not a foreign key to this table: a rate can be removed once its bookings
    are closed, and those bookings still say what they were.
    """

    __tablename__ = "housekeeping_rates"
    __table_args__ = (CheckConstraint("price >= 0", name="price_not_negative"),)

    service_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200))
    price: Mapped[Decimal]
