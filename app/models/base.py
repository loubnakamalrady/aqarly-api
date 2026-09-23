from datetime import datetime
from decimal import Decimal
from typing import Any, get_args

from sqlalchemy import DateTime, Enum, MetaData, Numeric
from sqlalchemy.orm import DeclarativeBase

from app.models.enums import Origin, Priority, RequestType, Stage, UnitStatus

# Named constraints, so migrations can drop or rename them by a name that is
# the same on every database rather than one Postgres made up.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _checked_text(literal: Any, name: str) -> Enum:
    """A `Literal` stored as VARCHAR with a CHECK listing its values.

    Not a native Postgres ENUM: adding a value to one of those needs its own
    migration dance, while a CHECK is just dropped and re-created.
    """
    return Enum(
        *get_args(literal),
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
    )


class Base(DeclarativeBase):
    """The parent of every table. Its `metadata` is what Alembic migrates."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # How a Python annotation becomes a column type, so models can write
    # `Mapped[Stage]` or `Mapped[datetime]` and get the right one.
    type_annotation_map = {
        Stage: _checked_text(Stage, "stage"),
        RequestType: _checked_text(RequestType, "request_type"),
        Priority: _checked_text(Priority, "priority"),
        Origin: _checked_text(Origin, "origin"),
        UnitStatus: _checked_text(UnitStatus, "unit_status"),
        # Every timestamp is an absolute instant: TIMESTAMPTZ, stored in UTC.
        datetime: DateTime(timezone=True),
        # Money is exact decimal, never float.
        Decimal: Numeric(12, 2),
    }
