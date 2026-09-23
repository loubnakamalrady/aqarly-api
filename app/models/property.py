from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import UnitStatus

if TYPE_CHECKING:
    from app.models.people import Tenant


class Property(Base):
    """A building the operator manages. Not a marketing listing: see `Listing`."""

    __tablename__ = "properties"

    # String ids carried over from the seed ("prop-marina-heights"), so the
    # frontend's URLs keep working.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str] = mapped_column(String(300))

    units: Mapped[list["Unit"]] = relationship(
        back_populates="property", order_by="Unit.label"
    )


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("property_id", "label", name="uq_units_property_id_label"),
        CheckConstraint("bedrooms >= 0", name="bedrooms_not_negative"),
        CheckConstraint("bathrooms >= 0", name="bathrooms_not_negative"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    property_id: Mapped[str] = mapped_column(ForeignKey("properties.id"), index=True)
    label: Mapped[str] = mapped_column(String(32))
    status: Mapped[UnitStatus]
    # The current occupant; null when vacant.
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    bedrooms: Mapped[int]
    bathrooms: Mapped[int]

    property: Mapped[Property] = relationship(back_populates="units")
    tenant: Mapped["Tenant | None"] = relationship()
