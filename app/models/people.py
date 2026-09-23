from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import RequestType


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(254))


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
