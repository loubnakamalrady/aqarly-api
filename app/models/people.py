from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import RequestType


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    # Tenants who registered themselves haven't given one.
    email: Mapped[str | None] = mapped_column(String(254))


class Staff(Base):
    """A technician or housekeeper. `role` is the trade they work in.

    How loaded they are is derived from their open requests at read time,
    never stored here.
    """

    __tablename__ = "staff"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    role: Mapped[RequestType]
    # A data URL until Phase 8 gives photos a file store.
    photo: Mapped[str | None] = mapped_column(Text)
    # Set when the member is removed from the roster. The row stays so closed
    # work still says who did it; retired members leave every list and can't
    # be assigned. Only possible once they hold no open work.
    retired_at: Mapped[datetime | None]
