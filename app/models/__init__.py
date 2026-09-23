"""SQLAlchemy tables.

Every model module must be imported here: Alembic compares `Base.metadata`
against the database, and a model it never imported is a table it never sees.
"""

from app.models.base import Base

__all__ = ["Base"]
